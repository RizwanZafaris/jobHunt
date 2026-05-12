"""
Re-derive `profile_keyword` + `profile_keyword_category` from the *current*
DB-truth `document_class` on `profile_source_document`.

Why this exists
===============
BUG-016 reclassified 14 of 174 `role_specific_resume` rows into
`cover_letter` / `job_description` / `roadmap` / `interview_prep` /
`misc_short`.  But `profile_keyword` was built earlier (by
`profile_build/04_keywords.py`) when all 174 were still "resumes" — its
ATS-strength aggregates therefore include keyword occurrences from
non-resume documents.

Schema note (treated as a follow-up bug, NOT fixed here)
--------------------------------------------------------
`profile_keyword` is an aggregate table (one row per (keyword, category))
with NO `source_document_id` FK back to `profile_source_document`. So
there is no way to delete *only* the contribution of an individual doc
without re-running the extraction from scratch. That's what this script
does. The missing FK is a real schema gap — once we want per-doc
attribution (e.g. "which doc earned us the 'PCI DSS' keyword?") we'll
need a `profile_keyword_occurrence(keyword_id, source_document_id, count)`
table. Bug Log §17 entry pending.

What it does
============
1. Pulls every row of `profile_source_document` from the DB. The DB's
   `document_class` is authoritative (post-BUG-016).
2. Loads the local `profile_build/output/extracted.json` manifest to
   resolve `file_hash → on-disk text file`.
3. Keeps only resume-tier classes:
       role_specific_resume, executive_resume, master_cv,
       linkedin_optimization, linkedin_headline_about, linkedin_audit,
       company_assessment, application_answer
   (Matches the original `RESUME_CLASSES` set in 04_keywords.py minus the
    classes now redirected: interview_prep + cover_letter — see Bug Log
    BUG-016 follow-up rationale.)
4. Re-runs the canonical keyword extractor (vocabulary + counting code
   copied verbatim from `profile_build/04_keywords.py` so this script is
   self-contained and does not require importing that module).
5. Upserts `profile_keyword` and rewrites `profile_keyword_category`.

Idempotent: re-running with the same inputs yields the same DB state.

Usage
-----
    cd jobHunt
    set -a && . ./.env && set +a
    python3 scripts/rederive_profile_keywords.py --dry-run    # preview
    python3 scripts/rederive_profile_keywords.py              # apply
"""
from __future__ import annotations
import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.client import get_supabase  # noqa: E402

log = logging.getLogger("rederive_profile_keywords")

EXTRACTED_JSON = Path(
    "/Users/rizwanzafar/Desktop/jobHunt-t1-classifier/profile_build/output/extracted.json"
)

# Resume tier — keep keyword occurrences only from these classes. This is
# the BUG-016-corrected version of the set defined in 04_keywords.py:
# `interview_prep` and `cover_letter` are intentionally OUT (their content
# is not the user's experience and was inflating ATS scores). Same goes
# for `job_description`, `roadmap`, `misc_short`, `needs_review`.
RESUME_TIER_CLASSES = {
    "role_specific_resume",
    "executive_resume",
    "master_cv",
    "general_resume",
    "linkedin_optimization",
    "linkedin_headline_about",
    "linkedin_audit",
    "company_assessment",
    "application_answer",
}

# Vocabulary copied verbatim from profile_build/04_keywords.py so the
# script is self-contained. If 04_keywords.py changes its vocab in the
# future, sync this list (or refactor both to import from a single
# canonical source — separate follow-up).
VOCAB: dict[str, list[str]] = {
    "Product Management": [
        "product management", "product manager", "product owner", "product strategy",
        "product roadmap", "product roadmap", "product vision", "product lifecycle",
        "product discovery", "product launch", "product market fit", "product-market fit",
        "go-to-market", "GTM", "product portfolio", "feature prioritization",
        "user research", "user stories", "product analytics", "product KPI",
        "north star metric", "OKR", "MVP", "product requirements", "PRD",
        "stakeholder management", "customer journey", "user journey",
        "value proposition", "voice of customer", "product positioning",
        "competitive analysis", "feature flag", "A/B testing", "ab testing",
        "product backlog", "product hypothesis", "discovery to delivery",
    ],
    "Fintech": [
        "fintech", "financial services", "digital banking", "neobank",
        "embedded finance", "open banking", "BNPL", "buy now pay later",
        "lending", "credit", "wealth management", "insurance tech",
        "regtech", "wealthtech", "financial inclusion", "challenger bank",
        "stablecoin", "agentic commerce", "financial infrastructure",
    ],
    "Payments": [
        "payment", "payments", "card", "cards", "issuing", "issuer",
        "acquiring", "acquirer", "merchant", "merchants", "card-on-file",
        "tokenization", "tokenized", "MDES", "VTS", "DPAN", "FPAN",
        "ISO 8583", "ISO 20022", "SWIFT", "MPGS", "3DS", "3DS2",
        "EMV", "PCI DSS", "settlement", "reconciliation", "chargeback",
        "interchange", "scheme", "Visa", "Mastercard", "AMEX", "JCB",
        "Discover", "RuPay", "wallet", "mobile wallet", "digital wallet",
        "POS", "checkout", "authorization", "authentication", "decline code",
        "approval rate", "authorization rate", "auth rate", "GTV", "TPV",
        "transaction volume", "remittance", "cross-border", "corridor",
        "FX", "foreign exchange", "real-time payments", "RTP", "instant payment",
        "ACH", "BACS", "SEPA", "wire transfer", "card network", "scheme rules",
        "smart retry", "retry logic", "decline recovery", "fraud",
        "payment gateway", "payment service provider", "PSP", "orchestration",
        "card-not-present", "card not present", "CNP", "POS", "BIN sponsor",
        "principal member", "issuer enablement", "issuer acceptance",
        "scheme certification", "scheme tokenization",
    ],
    "Compliance": [
        "compliance", "regulatory", "regulation", "PCI DSS", "SOC 2",
        "ISO 27001", "ISO 9001", "GDPR", "AML", "anti-money laundering",
        "CFT", "KYC", "KYB", "sanctions screening", "PEP", "OFAC",
        "FATF", "MAS", "FCA", "SAMA", "CBUAE", "VARA",
        "FinCEN", "central bank", "audit", "auditor", "audit trail",
        "regulatory reporting", "MSB", "money services business",
        "license", "licensing", "regulatory alignment",
    ],
    "Agile": [
        "agile", "scrum", "kanban", "sprint", "sprint planning",
        "backlog refinement", "retrospective", "stand-up", "standup",
        "user story", "epic", "story points", "velocity", "burndown",
        "lean", "SAFe", "scaled agile", "scrum master", "CSM", "CSPO",
        "ACP", "PSM", "PSPO", "agile coach", "agile transformation",
    ],
    "PMO": [
        "PMO", "project management office", "PMP", "PMBOK", "PRINCE2",
        "stage gate", "project charter", "RAID", "risk register",
        "earned value", "WBS", "work breakdown structure", "Gantt",
        "MS project", "milestone", "deliverable", "scope management",
        "schedule management", "resource management", "RAID log",
        "change control", "project governance", "steering committee",
        "program management", "portfolio management",
    ],
    "Technical": [
        "REST", "RESTful", "API", "APIs", "GraphQL", "gRPC", "webhook",
        "microservice", "microservices", "monolith", "SDK", "iOS SDK",
        "Android SDK", "JavaScript", "TypeScript", "Python", "Java",
        "Node", "Node.js", "React", "Next.js", "Postgres", "PostgreSQL",
        "MongoDB", "Redis", "Kafka", "RabbitMQ", "AWS", "GCP", "Azure",
        "Kubernetes", "Docker", "CI/CD", "GitHub Actions", "Jenkins",
        "OAuth", "JWT", "SAML", "TLS", "SSL", "encryption", "hashing",
        "Jira", "Confluence", "Postman", "Swagger", "OpenAPI",
        "system design", "distributed systems", "event-driven",
        "event driven", "data pipeline", "ETL",
    ],
    "Leadership": [
        "leader", "leadership", "head of", "VP", "vice president",
        "director", "chief", "CPO", "CEO", "CTO", "CFO", "COO", "CMO",
        "executive", "founding", "founder", "co-founder",
        "team leadership", "team building", "people management",
        "1:1", "performance management", "talent development",
        "mentor", "mentoring", "coaching", "succession planning",
        "hiring", "recruiting", "growing teams", "scaled organization",
        "C-suite", "board", "board reporting", "executive communication",
        "executive presence", "stakeholder influence",
    ],
    "Analytics": [
        "analytics", "data analytics", "business intelligence", "BI",
        "Tableau", "Looker", "Power BI", "PowerBI", "Metabase",
        "Mixpanel", "Amplitude", "Heap", "Snowflake", "BigQuery",
        "Redshift", "dbt", "ETL", "data warehouse", "data lake",
        "SQL", "Python", "R", "pandas", "matplotlib", "Jupyter",
        "cohort analysis", "funnel analysis", "retention analysis",
        "churn", "LTV", "CAC", "DAU", "MAU", "WAU", "north star",
        "dashboard", "KPI tracking", "experimentation", "A/B testing",
        "statistical significance", "predictive analytics",
    ],
    "AI": [
        "AI", "artificial intelligence", "ML", "machine learning",
        "deep learning", "neural network", "LLM", "large language model",
        "GenAI", "generative AI", "RAG", "retrieval augmented generation",
        "retrieval-augmented", "vector database", "vector db", "embedding",
        "embeddings", "fine-tuning", "fine tuning", "prompt engineering",
        "OpenAI", "GPT-4", "GPT-3.5", "Claude", "Anthropic", "Gemini",
        "LangChain", "LlamaIndex", "MLOps", "AI strategy", "AI consulting",
        "AI agent", "agentic", "computer vision", "NLP", "natural language",
        "speech recognition", "recommendation engine", "model deployment",
    ],
    "Risk & Security": [
        "risk management", "risk mitigation", "operational risk",
        "credit risk", "fraud", "fraud prevention", "fraud detection",
        "transaction monitoring", "cybersecurity", "infosec",
        "information security", "security architecture", "threat modeling",
        "penetration testing", "pen testing", "vulnerability", "incident response",
        "SOC", "security operations center", "encryption", "tokenization",
        "data protection", "privacy", "PII", "GDPR", "DLP",
        "zero trust", "OWASP", "secure coding", "SAST", "DAST",
    ],
}

KEYWORD_CATEGORY: dict[str, str] = {}
for cat, kws in VOCAB.items():
    for kw in kws:
        KEYWORD_CATEGORY.setdefault(kw.lower(), cat)


def _find_keyword(text_norm: str, kw: str) -> int:
    pattern = r"(?<![a-zA-Z0-9])" + re.escape(kw.lower()) + r"(?![a-zA-Z0-9])"
    return len(re.findall(pattern, text_norm))


def _resolve_text_path(file_hash: str, manifest: dict) -> Path | None:
    entry = manifest.get(file_hash)
    if not entry:
        return None
    p = Path(entry["text_path"])
    if not p.exists():
        # Fall back to local staging dir (worktree path differs from the
        # absolute path baked into extracted.json).
        local = Path(
            "/Users/rizwanzafar/Desktop/jobHunt-t1-classifier/profile_build/output"
        ) / p.name
        if local.exists():
            return local
        return None
    return p


def _compute_keywords(text_by_hash: dict[str, str]) -> tuple[list[dict], dict[str, dict]]:
    counts: dict[str, int] = defaultdict(int)
    files_with: dict[str, set] = defaultdict(set)
    for h, text in text_by_hash.items():
        text_norm = text.lower()
        for kw in KEYWORD_CATEGORY:
            n = _find_keyword(text_norm, kw)
            if n > 0:
                counts[kw] += n
                files_with[kw].add(h)

    total_files = len(text_by_hash)
    keywords_out: list[dict] = []
    for kw, total in counts.items():
        cat = KEYWORD_CATEGORY[kw]
        files_count = len(files_with[kw])
        coverage = files_count / max(total_files, 1)
        avg_per_file = total / max(files_count, 1)
        strength = round(coverage * 60 + min(avg_per_file, 10) * 4, 1)
        keywords_out.append({
            "keyword": kw,
            "category": cat,
            "total_occurrences": total,
            "files_count": files_count,
            "coverage_pct": round(coverage * 100, 1),
            "avg_per_file": round(avg_per_file, 2),
            "ats_strength": strength,
        })
    keywords_out.sort(key=lambda k: (-k["ats_strength"], -k["total_occurrences"]))

    cat_summary: dict[str, dict] = {}
    for cat in VOCAB:
        items = [k for k in keywords_out if k["category"] == cat]
        if not items:
            continue
        cat_summary[cat] = {
            "category": cat,
            "keyword_count": len(items),
            "top_keywords": [k["keyword"] for k in items[:10]],
            "avg_strength": round(
                sum(k["ats_strength"] for k in items) / len(items), 1
            ),
            "total_occurrences": sum(k["total_occurrences"] for k in items),
        }
    return keywords_out, cat_summary


def _fetch_current_db_state(db) -> tuple[list[dict], list[dict]]:
    kws = (
        db.table("profile_keyword")
        .select("keyword, category, total_occurrences, ats_strength, files_count")
        .execute()
        .data
        or []
    )
    cats = (
        db.table("profile_keyword_category")
        .select("category, keyword_count, total_occurrences, avg_strength")
        .execute()
        .data
        or []
    )
    return kws, cats


def _print_top_deltas(old_kws: list[dict], new_kws: list[dict], n: int = 10) -> None:
    old_by_k = {(k["keyword"], k["category"]): k for k in old_kws}
    new_by_k = {(k["keyword"], k["category"]): k for k in new_kws}
    all_keys = set(old_by_k) | set(new_by_k)
    rows = []
    for key in all_keys:
        o = old_by_k.get(key, {})
        n_ = new_by_k.get(key, {})
        delta = (n_.get("total_occurrences", 0) or 0) - (o.get("total_occurrences", 0) or 0)
        if delta != 0:
            rows.append((key, o, n_, delta))
    rows.sort(key=lambda r: abs(r[3]), reverse=True)
    log.info("Top %d keyword deltas (|new - old occurrences|):", n)
    log.info(
        "  %-30s %-18s   old_occ -> new_occ   (Δ occ)   old_str -> new_str",
        "keyword",
        "category",
    )
    for (kw, cat), o, n_, d in rows[:n]:
        oo = o.get("total_occurrences", 0)
        no = n_.get("total_occurrences", 0)
        os_ = o.get("ats_strength", 0)
        ns = n_.get("ats_strength", 0)
        log.info(
            "  %-30s %-18s   %5s -> %5s   (%+d)   %5s -> %5s",
            kw[:30], cat[:18], oo, no, d, os_, ns,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute new aggregates and print deltas but do NOT write to DB.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )

    db = get_supabase()

    # 1) Pull DB-truth document classes.
    rows = (
        db.table("profile_source_document")
        .select("id, file_hash, file_name, document_class")
        .execute()
        .data
        or []
    )
    log.info("Pulled %d profile_source_document rows", len(rows))
    db_class_by_hash = {r["file_hash"]: r["document_class"] for r in rows}
    db_name_by_hash = {r["file_hash"]: r["file_name"] for r in rows}

    # 2) Load extracted.json manifest (hash -> text_path).
    if not EXTRACTED_JSON.exists():
        log.error("extracted.json not found at %s", EXTRACTED_JSON)
        return 2
    manifest = json.loads(EXTRACTED_JSON.read_text())
    log.info("Loaded extracted.json with %d entries", len(manifest))

    # 3) Build (hash -> text) for resume-tier classes ONLY (DB truth).
    text_by_hash: dict[str, str] = {}
    excluded_counts: dict[str, int] = defaultdict(int)
    missing_text = 0
    for h, cls in db_class_by_hash.items():
        if cls not in RESUME_TIER_CLASSES:
            excluded_counts[cls] += 1
            continue
        text_path = _resolve_text_path(h, manifest)
        if text_path is None:
            missing_text += 1
            log.debug("No text file for hash=%s name=%s", h, db_name_by_hash.get(h))
            continue
        try:
            text_by_hash[h] = text_path.read_text(errors="replace")
        except Exception as e:
            log.warning("Failed to read %s: %s", text_path, e)
            missing_text += 1

    log.info(
        "Resume-tier docs included: %d  |  excluded by class: %d  |  missing-text: %d",
        len(text_by_hash),
        sum(excluded_counts.values()),
        missing_text,
    )
    for cls, n in sorted(excluded_counts.items(), key=lambda x: -x[1]):
        log.info("  excluded %s: %d", cls, n)

    # 4) Recompute keyword + category aggregates.
    new_kws, new_cats = _compute_keywords(text_by_hash)
    log.info(
        "Recomputed %d keyword rows / %d category rows",
        len(new_kws), len(new_cats),
    )

    # 5) Compare to DB.
    old_kws, old_cats = _fetch_current_db_state(db)
    old_total = sum(k.get("total_occurrences", 0) or 0 for k in old_kws)
    new_total = sum(k["total_occurrences"] for k in new_kws)
    log.info(
        "Aggregate occurrences:  old=%d  new=%d  delta=%+d",
        old_total, new_total, new_total - old_total,
    )
    log.info("Keyword rows:  old=%d  new=%d  delta=%+d",
             len(old_kws), len(new_kws), len(new_kws) - len(old_kws))
    _print_top_deltas(old_kws, new_kws, n=10)

    # Category-level delta
    old_cat_by = {c["category"]: c for c in old_cats}
    log.info("Category strength deltas:")
    for c in sorted(new_cats.values(), key=lambda x: -x["total_occurrences"]):
        cat = c["category"]
        o = old_cat_by.get(cat, {})
        log.info(
            "  %-18s  occ %5s -> %5s  (%+d)   avg_str %5s -> %5s",
            cat,
            o.get("total_occurrences", 0),
            c["total_occurrences"],
            c["total_occurrences"] - (o.get("total_occurrences", 0) or 0),
            o.get("avg_strength", 0),
            c["avg_strength"],
        )

    if args.dry_run:
        log.info("--dry-run: not writing to the DB.")
        return 0

    # 6) Apply. Strategy: wipe both tables then bulk-insert. The tables
    # have no FKs pointing at them so this is safe and atomic-enough for
    # a single-user MVP.
    log.info("Writing new aggregates to DB...")
    try:
        db.table("profile_keyword").delete().neq("id", 0).execute()
    except Exception as e:
        log.warning("profile_keyword wipe warning: %s", e)
    try:
        db.table("profile_keyword_category").delete().neq("category", "").execute()
    except Exception as e:
        log.warning("profile_keyword_category wipe warning: %s", e)

    # Insert in batches of 100 to stay under any payload limits.
    if new_kws:
        for i in range(0, len(new_kws), 100):
            db.table("profile_keyword").insert(new_kws[i:i + 100]).execute()
    if new_cats:
        db.table("profile_keyword_category").insert(list(new_cats.values())).execute()

    log.info("Done. Inserted %d keyword rows + %d category rows.",
             len(new_kws), len(new_cats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
