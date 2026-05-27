"""
agents/people_finder_agent.py — Discover people at target companies via Perplexity.

Replaces the LinkedIn CSV upload flow (which required manual export from
LinkedIn). Instead, asks Perplexity Sonar to find recruiters, talent leads,
hiring managers, and senior PMs at each target company in the user's
target geos. Persists results into the existing `people` +
`employments` + `target_company_employees` tables that the Network
surface + referral_graph.py path-finder already consume.

Why Perplexity (not Apollo):
  Apollo's people-search API requires a $49/mo paid plan. We already
  pay for Perplexity ($5/month for jobHunt's discovery layer), and the
  cost-per-company-query is low (~$0.005 grounded search fee + small
  token cost). For 71 target companies × 4 role-types = ~280 queries,
  monthly cost is < $2.

Discovery shape per company (4 role-types):
  1. Recruiters / Talent Acquisition       (for cold-outreach replies)
  2. Heads of Talent / People              (warm intro paths)
  3. Hiring Managers in target functions   (Head of Product / VP Eng)
  4. Director+/VP level senior peers       (alumni / network leads)

Output JSON shape (Perplexity is asked to emit this):
  [
    {"full_name": "Jane Doe",
     "linkedin_url": "https://linkedin.com/in/janedoe",
     "title": "Head of Product, MENA",
     "location": "Dubai, United Arab Emirates",
     "evidence": "linkedin.com/in/janedoe profile"},
    ...
  ]

Dedup strategy:
  Unique constraint on (user_id, linkedin_url) — if the same person
  surfaces again, we UPDATE last_refreshed_at instead of INSERT.
  Run nightly via cron / weekly via manual trigger.

API surface:
  await PeopleFinderAgent().run(user_id=...)            — all companies
  await PeopleFinderAgent().run_for_company(slug, user) — one company

Cron / endpoint:
  GET/POST /admin/trigger-people-discovery               — full sweep
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from db.client import get_supabase
from agents.perplexity_search import _post_chat  # reuse existing client

logger = logging.getLogger(__name__)


# ─── Role-type definitions ────────────────────────────────────────────────
# Each tuple = (slug, prompt-fragment describing who to find, role_seniority).
# Keep the prompts terse — Perplexity returns more usable JSON when the
# scope is narrow (one role-type per query).
ROLE_QUERIES: tuple[tuple[str, str, str], ...] = (
    (
        "recruiter",
        "in-house Recruiters or Talent Acquisition Partners (people who screen "
        "and source candidates — NOT external agency recruiters)",
        "manager",
    ),
    (
        "head_of_talent",
        "Heads of Talent / Heads of People / VP People (senior talent leadership)",
        "vp",
    ),
    (
        "head_of_product",
        "Heads of Product, VP Product, Director of Product Management, or "
        "Chief Product Officer (senior product leadership)",
        "vp",
    ),
    (
        "senior_pm",
        "Senior or Principal Product Managers (Director-track ICs)",
        "senior",
    ),
)

# Target geos — mirror profile.yml.target_locations. Hard-coded here so the
# agent doesn't depend on a yaml load on every cron tick.
TARGET_GEOS: tuple[str, ...] = (
    "Dubai", "United Arab Emirates", "Abu Dhabi",
    "Riyadh", "Saudi Arabia",
    "London", "United Kingdom",
    "Singapore",
    "Amsterdam",
    "remote",
)


# ─── Perplexity prompt templates ──────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a senior recruiting researcher. Find specific named individuals "
    "currently employed at the target company in the target geographies. "
    "Use only public sources (LinkedIn, company website, conference speaker "
    "bios, podcast guest lists). Never hallucinate names. Output ONLY valid "
    "JSON — no prose, no markdown fences. If you cannot find anyone reliably, "
    "return an empty array []."
)

USER_PROMPT_TEMPLATE = """Find current employees at {company} matching this role: {role_phrase}.

Restrict to these locations: {geos}.

Return a JSON array. Each object must have exactly these keys:
- full_name      (string, required)
- linkedin_url   (string, required — must start with https://linkedin.com/in/ or https://*.linkedin.com/in/ — no LinkedIn profiles? skip the person)
- title          (string, current title at {company})
- location       (string, city + country if known, else "")
- evidence       (string, ≤80 chars — citation source URL or "LinkedIn profile")

Maximum 5 people. Return [] if you can't verify anyone reliably. Reply with the JSON array only."""


# ─── Output container ─────────────────────────────────────────────────────
@dataclass
class DiscoveredPerson:
    full_name: str
    linkedin_url: str
    title: str
    location: str
    evidence: str
    role_query_slug: str
    company_name: str
    company_id: Optional[str] = None
    role_seniority: str = "senior"


@dataclass
class CompanySweepResult:
    company_name: str
    company_id: Optional[str]
    queries_ran: int = 0
    people_found: int = 0
    people_inserted: int = 0
    people_refreshed: int = 0
    errors: list[str] = field(default_factory=list)


# ─── Helpers ──────────────────────────────────────────────────────────────
_LINKEDIN_PROFILE_RE = re.compile(
    r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^\s/?#]+/?",
    re.IGNORECASE,
)


def _is_real_linkedin_profile(url: str) -> bool:
    """Cheap validator — reject obvious hallucinations."""
    return bool(url and _LINKEDIN_PROFILE_RE.match(url))


def _strip_linkedin_handle(url: str) -> str:
    """Extract the /in/{handle} segment as a stable dedup key."""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url, re.IGNORECASE)
    return m.group(1).lower().rstrip("/") if m else ""


def _parse_perplexity_json(content: str) -> list[dict[str, Any]]:
    """Extract a JSON array from Perplexity's response.

    The system prompt asks for raw JSON, but Sonar occasionally wraps
    the array in ```json fences or adds a sentence before the array.
    Strip both, then json.loads.
    """
    if not content:
        return []
    # Strip code fences
    txt = re.sub(r"^```(?:json)?\s*", "", content.strip())
    txt = re.sub(r"\s*```$", "", txt)
    # Find the outermost [ ... ] block
    start = txt.find("[")
    end = txt.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    blob = txt[start : end + 1]
    try:
        parsed = json.loads(blob)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError as e:
        logger.warning("perplexity returned unparseable JSON: %r — %s", blob[:120], e)
        return []


# ─── Main agent ───────────────────────────────────────────────────────────
class PeopleFinderAgent:
    """Discover senior contacts at target companies and persist to people + employments."""

    def __init__(self, *, model: str = "sonar"):
        self.model = model
        self.db = get_supabase()

    # ── Public entry points ──
    async def run(self, *, user_id: str, limit_companies: Optional[int] = None) -> dict[str, Any]:
        """Full sweep — all target companies. Returns aggregate stats."""
        companies = self._load_target_companies(limit_companies)
        logger.info("PeopleFinder: starting sweep across %d companies", len(companies))
        results: list[CompanySweepResult] = []
        for co in companies:
            try:
                r = await self.run_for_company(
                    company_name=co["name"],
                    company_id=co["id"],
                    user_id=user_id,
                )
                results.append(r)
            except Exception:
                logger.exception("PeopleFinder: company %r failed (continuing)", co["name"])
            await asyncio.sleep(0.5)  # courtesy delay between companies

        total_people = sum(r.people_inserted + r.people_refreshed for r in results)
        return {
            "ok": True,
            "companies_processed": len(results),
            "total_queries": sum(r.queries_ran for r in results),
            "total_people_persisted": total_people,
            "by_company": [
                {
                    "name": r.company_name,
                    "queries": r.queries_ran,
                    "found": r.people_found,
                    "inserted": r.people_inserted,
                    "refreshed": r.people_refreshed,
                    "errors": r.errors,
                }
                for r in results
            ],
        }

    async def run_for_company(
        self,
        *,
        company_name: str,
        user_id: str,
        company_id: Optional[str] = None,
    ) -> CompanySweepResult:
        result = CompanySweepResult(company_name=company_name, company_id=company_id)
        for slug, role_phrase, seniority in ROLE_QUERIES:
            try:
                people = await self._query_one(
                    company_name=company_name,
                    role_phrase=role_phrase,
                    role_slug=slug,
                    role_seniority=seniority,
                )
                result.queries_ran += 1
                result.people_found += len(people)
                # Persist
                for p in people:
                    p.company_id = company_id
                    inserted, refreshed = self._persist_person(
                        person=p, user_id=user_id, company_name=company_name,
                    )
                    result.people_inserted += inserted
                    result.people_refreshed += refreshed
            except Exception as exc:
                logger.warning("PeopleFinder: %s / %s failed: %r", company_name, slug, exc)
                result.errors.append(f"{slug}: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.3)  # ratelimit cushion
        logger.info(
            "PeopleFinder %s: q=%d found=%d ins=%d ref=%d errors=%d",
            company_name, result.queries_ran, result.people_found,
            result.people_inserted, result.people_refreshed, len(result.errors),
        )
        return result

    # ── Internals ──
    async def _query_one(
        self,
        *,
        company_name: str,
        role_phrase: str,
        role_slug: str,
        role_seniority: str,
    ) -> list[DiscoveredPerson]:
        prompt = USER_PROMPT_TEMPLATE.format(
            company=company_name,
            role_phrase=role_phrase,
            geos=", ".join(TARGET_GEOS),
        )
        payload = await _post_chat(model=self.model, system=SYSTEM_PROMPT, user=prompt)
        content = ""
        choices = payload.get("choices") or []
        if choices and isinstance(choices, list):
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""

        rows = _parse_perplexity_json(content)
        out: list[DiscoveredPerson] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = (r.get("full_name") or "").strip()
            url = (r.get("linkedin_url") or "").strip()
            if not name or not _is_real_linkedin_profile(url):
                continue
            out.append(
                DiscoveredPerson(
                    full_name=name,
                    linkedin_url=url,
                    title=(r.get("title") or "").strip(),
                    location=(r.get("location") or "").strip(),
                    evidence=(r.get("evidence") or "")[:200],
                    role_query_slug=role_slug,
                    company_name=company_name,
                    role_seniority=role_seniority,
                )
            )
        return out

    def _load_target_companies(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        q = self.db.table("companies").select("id, name").eq("is_target", True)
        if limit:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []

    def _persist_person(
        self,
        *,
        person: DiscoveredPerson,
        user_id: str,
        company_name: str,
    ) -> tuple[int, int]:
        """Insert person + employment + target_company_employee.

        Returns (inserted_count, refreshed_count) — 1+0 if new, 0+1 if existed.
        """
        handle = _strip_linkedin_handle(person.linkedin_url)
        # Try upsert into people via (user_id, linkedin_url) unique index
        person_row = {
            "user_id": user_id,
            "full_name": person.full_name,
            "linkedin_url": person.linkedin_url,
            "linkedin_handle": handle,
            "headline": person.title[:255] if person.title else None,
            "location": person.location or None,
            "source": "perplexity",
            "discovery_query": person.role_query_slug,
            "last_refreshed_at": "now()",
        }
        # Use upsert — on_conflict via the unique index
        try:
            resp = (
                self.db.table("people")
                .upsert(person_row, on_conflict="user_id,linkedin_url")
                .execute()
            )
            rows = resp.data or []
            if not rows:
                return 0, 0
            person_id = rows[0]["id"]
            # was_new is best-detected via created_at == updated_at within 1s
            # but for our purposes, treat all upserts as either new or refresh.
            existed = rows[0].get("created_at") != rows[0].get("updated_at")
        except Exception as e:
            logger.warning("upsert people failed for %s: %s", person.full_name, e)
            return 0, 0

        # Add employment row (if not already current for this company)
        try:
            self.db.table("employments").upsert({
                "person_id": person_id,
                "company_id": person.company_id,
                "company_name": company_name,
                "role": person.title or None,
                "role_seniority": person.role_seniority,
                "is_current": True,
                "source": "perplexity",
            }).execute()
        except Exception as e:
            logger.debug("employments upsert non-fatal for %s: %s", person.full_name, e)

        # Add target_company_employee row (if company_id present)
        if person.company_id:
            try:
                self.db.table("target_company_employees").upsert({
                    "user_id": user_id,
                    "target_company_id": person.company_id,
                    "person_id": person_id,
                    "role": person.title or None,
                    "role_seniority": person.role_seniority,
                }).execute()
            except Exception as e:
                logger.debug("target_company_employees upsert non-fatal: %s", e)

        return (0, 1) if existed else (1, 0)
