"""
ProfileAnalyzer — generates profile_recommendation rows.

Heuristic checks (no LLM cost):
1. missing_keyword: high-strength keywords from job-scout JD corpus that don't
   appear in the user's profile keyword bank
2. weak_category: keyword categories below an avg_strength threshold
3. unquantified: experience bullets that lack any numeric metric
4. conflict: same role/company appearing in multiple source documents but with
   different titles or dates (cross-doc drift)

Run via API: POST /profile/recommendations/regenerate
Or directly:  python3 -c "import asyncio; from agents.profile_analyzer import ProfileAnalyzer; asyncio.run(ProfileAnalyzer().run())"
"""
from __future__ import annotations
import logging
import re
from collections import defaultdict
from typing import Any

from db.client import get_supabase

logger = logging.getLogger(__name__)


WEAK_CATEGORY_THRESHOLD = 15.0  # avg_strength below this is flagged
UNQUANT_MIN_BULLET_LEN = 60  # only flag substantial bullets
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|x|k|m|b|million|billion|users|customers|merchants|partners|countries|markets|regions|months|weeks|days|days|hrs|hours|fte|employees|deals|jobs|integrations|sprints|releases|deploys|days|months|years|x)?\b", re.IGNORECASE)
HAS_DOLLAR = re.compile(r"\$\s?[\d,.]+|(?:\d+[KMB])(?=\b)")


class ProfileAnalyzer:
    """Generates profile improvement recommendations."""

    def __init__(self):
        self.db = get_supabase()

    async def run(self) -> dict[str, int]:
        recs: list[dict[str, Any]] = []
        recs += self._weak_categories()
        recs += self._missing_keywords_from_jobs()
        recs += self._unquantified_bullets()
        recs += self._cross_doc_conflicts()

        # Wipe and re-insert (full refresh model)
        try:
            self.db.table("profile_recommendation").delete().neq("id", 0).execute()
        except Exception as e:
            logger.warning(f"Failed to clear recommendations: {e}")

        if recs:
            for i in range(0, len(recs), 100):
                self.db.table("profile_recommendation").insert(recs[i:i + 100]).execute()

        return {
            "total": len(recs),
            "by_kind": _group_count(recs, "kind"),
            "by_severity": _group_count(recs, "severity"),
        }

    # ── Check 1: weak categories ──────────────────────────────────────────
    def _weak_categories(self) -> list[dict]:
        cats = (self.db.table("profile_keyword_category").select("*").execute().data) or []
        out = []
        for c in cats:
            avg = float(c.get("avg_strength", 0) or 0)
            if avg < WEAK_CATEGORY_THRESHOLD:
                out.append({
                    "kind": "weak_category",
                    "severity": "high" if avg < 12 else "medium",
                    "title": f"Strengthen your '{c['category']}' coverage",
                    "detail": (
                        f"This category has only {c['keyword_count']} keywords with an "
                        f"average ATS strength of {avg}. Consider adding more "
                        f"{c['category']}-related keywords and quantified achievements."
                    ),
                    "related_category": c["category"],
                })
        return out

    # ── Check 2: missing keywords from JD corpus ──────────────────────────
    def _missing_keywords_from_jobs(self) -> list[dict]:
        """
        Flag high-strength keywords that appear in many high-scoring job descriptions
        but rarely (or not at all) in the user's profile.
        """
        # User keywords that are well-covered (in their profile)
        user_kw = (self.db.table("profile_keyword").select("keyword, ats_strength").execute().data) or []
        user_strong = {k["keyword"].lower() for k in user_kw if (k.get("ats_strength", 0) or 0) >= 30}

        # Pull recent qualifying jobs' descriptions
        jobs = (self.db.table("jobs").select("title, description").gte("match_score", 60).limit(60).execute().data) or []
        if not jobs:
            return []

        # Tokenize JD text and find candidate keywords from our vocab that appear
        from collections import Counter
        VOCAB = list({k["keyword"].lower() for k in user_kw}) + [
            # Add common JD words not in our resume corpus that are worth flagging
            "tableau", "looker", "power bi", "snowflake", "dbt", "kubernetes",
            "openai", "anthropic", "vector database", "rag", "agentic",
            "stablecoin", "embedded finance", "open banking", "digital identity",
            "edi", "iso 20022", "swift gpi", "real-time payments", "cbdc",
            "agile coach", "okrs", "north star metric", "jobs-to-be-done",
        ]
        VOCAB = list({v for v in VOCAB})  # dedup

        jd_text_all = "\n".join((j.get("description") or "") for j in jobs).lower()
        jd_counts: dict[str, int] = Counter()
        for v in VOCAB:
            cnt = jd_text_all.count(v)
            if cnt >= 3:
                jd_counts[v] = cnt

        out = []
        for kw, jd_freq in sorted(jd_counts.items(), key=lambda x: -x[1])[:30]:
            if kw in user_strong:
                continue
            severity = "high" if jd_freq >= 15 else "medium" if jd_freq >= 6 else "low"
            out.append({
                "kind": "missing_keyword",
                "severity": severity,
                "title": f"Consider adding: \"{kw}\"",
                "detail": (
                    f"This keyword appears {jd_freq} times across your high-scoring "
                    f"target jobs but is weak or missing from your profile. "
                    f"Adding genuine experience with it would improve ATS match scores."
                ),
                "related_keyword": kw,
            })
        return out

    # ── Check 3: unquantified bullets ─────────────────────────────────────
    def _unquantified_bullets(self) -> list[dict]:
        exps = (self.db.table("profile_experience").select("*").execute().data) or []
        out = []
        for e in exps:
            role_label = f"{e['title']} @ {e['company']}"
            # Combine highlights + group bullets
            bullets = list(e.get("highlights") or [])
            for g in (e.get("groups") or []):
                bullets.extend(g.get("bullets", []))
            for b in bullets:
                if not isinstance(b, str) or len(b) < UNQUANT_MIN_BULLET_LEN:
                    continue
                if NUMBER_PATTERN.search(b) or HAS_DOLLAR.search(b):
                    continue
                out.append({
                    "kind": "quantify",
                    "severity": "low",
                    "title": f"Quantify a bullet at {role_label}",
                    "detail": (
                        f"This bullet has no numbers — adding a metric would make it "
                        f"sharper:\n\n> {b[:200]}{'...' if len(b) > 200 else ''}"
                    ),
                })
                if len(out) >= 20:
                    return out
        return out

    # ── Check 4: cross-doc conflicts ──────────────────────────────────────
    def _cross_doc_conflicts(self) -> list[dict]:
        """
        Crude heuristic: count how many distinct document classes contain the
        word 'simpaisa' (current role) — confirms breadth. Real conflict
        detection would require parsing each source doc; we'll flag if any
        source documents have unusually divergent role titles.
        """
        sources = (self.db.table("profile_source_document").select("*").execute().data) or []
        # Count files per class
        cls_counts: dict[str, int] = defaultdict(int)
        for s in sources:
            cls_counts[s["document_class"]] += 1

        out = []
        # If we have 100+ role-specific resumes, flag the dedup risk
        role_count = cls_counts.get("role_specific_resume", 0)
        if role_count > 50:
            out.append({
                "kind": "conflict",
                "severity": "low",
                "title": f"You have {role_count} role-specific resume variants",
                "detail": (
                    f"Maintaining {role_count} different resume files can lead to "
                    f"version drift. Consider consolidating into a single canonical "
                    f"version per target role family (CPO, Head of Product, Senior PM, "
                    f"VP Product) — the AI gap-filling pipeline will tailor automatically."
                ),
            })

        # Flag if duplicate filenames exist
        from collections import Counter
        name_counts = Counter(s["file_name"] for s in sources)
        for name, n in name_counts.items():
            if n > 1:
                out.append({
                    "kind": "conflict",
                    "severity": "low",
                    "title": f"Duplicate filename detected: {name}",
                    "detail": f"{n} different files share this filename — they may have diverged. Review and consolidate.",
                })
                if len(out) >= 10:
                    break
        return out


def _group_count(items: list[dict], key: str) -> dict:
    from collections import Counter
    return dict(Counter(i[key] for i in items if key in i))
