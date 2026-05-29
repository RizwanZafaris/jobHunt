"""
Supabase client — singleton with vector search helpers.
"""

from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from supabase import create_client, Client
from openai import AsyncOpenAI
from config.settings import get_settings

_supabase: Optional[Client] = None
_openai: Optional[AsyncOpenAI] = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        s = get_settings()
        _supabase = create_client(s.supabase_url, s.supabase_service_key)
    return _supabase


def get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=get_settings().openai_api_key)
    return _openai


async def embed(text: str) -> list[float]:
    """Create an embedding vector for the given text using OpenAI."""
    s = get_settings()
    client = get_openai()
    response = await client.embeddings.create(
        model=s.embedding_model,
        input=text[:8000]  # Truncate to avoid token limit
    )
    return response.data[0].embedding


async def upsert_company_knowledge(
    company_name: str,
    company_id: Optional[str],
    section: str,
    content: str,
    source_url: Optional[str] = None,
    metadata: dict = None,
    user_id: str | None = None,
) -> dict:
    """Store a company intelligence chunk with its embedding.

    Multi-tenancy DB-1 (2026-05-29): the uniqueness on company_knowledge is
    now composite — (user_id, company_name, section) — so the on_conflict
    arbiter must include user_id, and the row MUST carry user_id. Before this
    change the writer never set user_id at all: at single-user scale every
    row already existed so the upsert always took the UPDATE branch and the
    NOT NULL user_id column was never exercised. Switching the conflict
    target to the composite key turns that latent gap into a hard 23502 on
    any first-insert for a new (user_id, company, section) tuple — so we add
    user_id here in the same change. user_id defaults to the seed-user UUID
    via env override, mirroring upsert_job / upsert_company; multi-tenant
    callers can pass an explicit user_id.
    """
    embedding = await embed(content)
    if user_id is None:
        import os
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
    db = get_supabase()

    row = {
        "company_name": company_name,
        "section": section,
        "content": content,
        "embedding": embedding,
        "source_url": source_url,
        "metadata": metadata or {},
        "user_id": user_id,
    }
    if company_id:
        row["company_id"] = company_id

    # Upsert: replace if same user + company + section (DB-1 composite key).
    result = db.table("company_knowledge").upsert(
        row,
        on_conflict="user_id,company_name,section"
    ).execute()
    return result.data[0] if result.data else {}


async def search_company_knowledge(
    company_name: str,
    query: str,
    match_count: int = 5
) -> list[dict]:
    """Semantic search within a company's knowledge base.

    Returns rows with: id (uuid), section, content, similarity, scraped_at.

    The `id` column is the company_knowledge primary key; downstream callers
    (notably resume_agents.g2_nodes.insider_expert_node) embed it back into
    agent_transcript as `cite:knowledge_id=<uuid>` markers so
    agents.outcome_to_persona.credit_outcome can attribute outcome credit
    to the exact rows the resume cited (Phase 3 outcome-conditioned RAG).

    For backwards compatibility with callers that pre-date migration 009,
    `id` will simply be missing on rows returned by the v1 RPC; we don't
    raise. Once 009 is applied, every row carries an id.
    """
    query_embedding = await embed(query)
    db = get_supabase()

    result = db.rpc("search_company_knowledge", {
        "query_embedding": query_embedding,
        "target_company": company_name,
        "match_count": match_count
    }).execute()
    rows = result.data or []
    # Normalise: ensure every row exposes an `id` key (None if RPC v1 still in
    # place). This lets callers do `row.get("id")` without KeyError handling.
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": row.get("id"),
            "section": row.get("section"),
            "content": row.get("content"),
            "similarity": row.get("similarity"),
            "scraped_at": row.get("scraped_at"),
            **{k: v for k, v in row.items() if k not in {"id", "section", "content", "similarity", "scraped_at"}},
        })
    return out


async def upsert_rizwan_profile(
    section: str,
    content: str,
    user_id: str | None = None,
) -> dict:
    """Store a section of Rizwan's profile with embedding.

    2026-05-12 fix: when migration 001_multi_tenancy added `user_id NOT NULL`
    to rizwan_profile, the upsert here kept its v1 payload shape
    (section/content/embedding only) and Postgres rejected every INSERT
    branch of the upsert with code 23502 — silently crashing every
    pipeline.run() call at startup. Surfaced 2026-05-12 in Railway logs
    while running JobScout v2.

    user_id defaults to the seed-user UUID via env override so the call
    sites in agents/rizwan_agent.py don't need plumbing. In multi-tenant
    mode the caller can pass an explicit user_id.
    """
    embedding = await embed(content)
    if user_id is None:
        import os
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
    db = get_supabase()
    result = db.table("rizwan_profile").upsert(
        {
            "section": section,
            "content": content,
            "embedding": embedding,
            "user_id": user_id,
        },
        # DB-1 (2026-05-29): uniqueness is now composite (user_id, section).
        on_conflict="user_id,section",
    ).execute()
    return result.data[0] if result.data else {}


async def search_rizwan_profile(query: str, match_count: int = 5) -> list[dict]:
    """Find the most relevant parts of Rizwan's profile for a query."""
    query_embedding = await embed(query)
    db = get_supabase()
    result = db.rpc("search_rizwan_profile", {
        "query_embedding": query_embedding,
        "match_count": match_count
    }).execute()
    return result.data or []


async def search_story_bank(topic: str, match_count: int = 3) -> list[dict]:
    """Find the most relevant STAR stories for an interview topic."""
    query_embedding = await embed(topic)
    db = get_supabase()
    result = db.rpc("search_story_bank", {
        "query_embedding": query_embedding,
        "match_count": match_count
    }).execute()
    return result.data or []


_JOBS_COLUMNS = {
    "id", "title", "company", "company_id", "location", "url", "description",
    "jd_embedding", "source", "match_score", "fit_details", "status",
    # BUG-030 (2026-05-12): `report_path` removed from this allow-list — the
    # column exists in db/schema.sql:63 for historical reasons but no code
    # path has ever written to it. Leaving it in the upsert allow-list let
    # callers silently pass a value that never reached the DB. The column
    # itself is intentionally NOT dropped (drops are irreversible).
    "resume_path", "email_path", "interview_path",
    "discovered_at", "applied_at", "updated_at",
    # Workflow v2
    "archetype", "legitimacy_tier", "legitimacy_signals",
    "resume_generated_at", "evaluation_blocks",
    # 2026-05-12: missing-columns audit while running JobScout v2.
    # Multi-tenancy (migration 001):
    "user_id", "org_id",
    # Job validator + posting lifecycle (migrations 007, 010):
    "posting_closed_at", "last_validated_at", "validation_status",
    # JobScout v2 quality (migration 011):
    "discovery_sources", "confidence_score", "freshness",
    "validation_failed", "validated_at",
}


_CONFIDENCE_BY_SOURCE = {
    # First-party ATS APIs — high confidence (structured data, employer-owned).
    "greenhouse": 80, "workday": 80, "lever": 80, "ashby": 80,
    "smartrecruiters": 80, "bamboohr": 80, "jobvite": 80, "recruitee": 80,
    "apify_career_page": 80,
    # LinkedIn — medium-high (good metadata, occasional aggregator noise).
    "linkedin": 70,
    # Regional aggregators — medium (some staleness, but employer-confirmed).
    "bayt": 60, "naukrigulf": 60, "gulftalent": 60, "indeed": 60,
    # Generic web scrape / LLM-grounded — low (more validation needed).
    "web": 50,
    "perplexity_sonar": 50,
}


def _default_confidence_score(source: str | None) -> int:
    """
    Source-based default for jobs.confidence_score. Migration 011 added
    the column but no backfill or writer-side default existed — 95% of
    rows landed NULL. We seed a conservative default at upsert time so
    downstream filters (e.g. confidence >= 60) work consistently.
    Caller-supplied values always win.
    """
    if not source:
        return 50
    if source.startswith("ats_"):
        return 80
    return _CONFIDENCE_BY_SOURCE.get(source, 50)


def upsert_job(job_data: dict, user_id: str | None = None) -> dict:
    """Insert or update a job record. Filters out keys that aren't real columns.

    2026-05-12: surfaced while running JobScout v2 end-to-end — every
    upsert returned 400 with `null value in column "user_id" of relation
    "jobs" violates not-null constraint`. Same shape as the
    upsert_rizwan_profile bug fixed earlier today. The
    multi-tenancy migration added user_id NOT NULL to jobs but the writer
    here was never updated. Also: migration 011 added 5 v2-quality columns
    (discovery_sources / confidence_score / freshness / validation_failed
    / validated_at) — all silently filtered out by _JOBS_COLUMNS before
    this patch.

    user_id defaults to the seed user UUID via env override so callers
    in single-user mode (JobScoutAgent.run loop) don't need plumbing.

    2026-05-12 (BUG-025): seed confidence_score from source when caller
    didn't supply one (migration 011 had no backfill — 385/405 rows were
    NULL pre-fix).

    2026-05-12 (BUG-026): preserve the earliest discovered_at on
    re-discovery. The previous behavior overwrote it every time JobScout
    re-saw the same URL, causing resume_generated_at < discovered_at on
    3 jobs (causally impossible). We now strip discovered_at from the
    payload when the job already exists.
    """
    db = get_supabase()
    if user_id is None:
        import os
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
    payload = dict(job_data)
    payload["user_id"] = user_id  # always set — never let job_data null-shadow

    # BUG-025: seed confidence_score from source if caller didn't supply.
    if payload.get("confidence_score") is None:
        payload["confidence_score"] = _default_confidence_score(payload.get("source"))

    # BUG-026: preserve earliest discovered_at on re-discovery.
    # If the URL already exists, never overwrite its discovered_at.
    url = payload.get("url")
    if url:
        try:
            existing = (
                db.table("jobs")
                .select("discovered_at")
                .eq("url", url)
                .limit(1)
                .execute()
            )
            if existing.data:
                # Drop discovered_at from the update payload — keep the
                # original first-discovery timestamp.
                payload.pop("discovered_at", None)
        except Exception:
            # Best-effort — if the lookup fails, fall through to upsert
            # behaviour. (Misses the protection but doesn't break inserts.)
            pass

    filtered = {k: v for k, v in payload.items() if k in _JOBS_COLUMNS}
    result = db.table("jobs").upsert(
        filtered,
        # DB-1 (2026-05-29): uniqueness is now composite (user_id, url)
        # [partial: WHERE url IS NOT NULL]. user_id is always set above.
        on_conflict="user_id,url"
    ).execute()
    return result.data[0] if result.data else {}


def get_job(job_id: int) -> Optional[dict]:
    db = get_supabase()
    result = db.table("jobs").select("*").eq("id", job_id).single().execute()
    return result.data


def update_job(job_id: int, updates: dict) -> dict:
    db = get_supabase()
    result = db.table("jobs").update(updates).eq("id", job_id).execute()
    return result.data[0] if result.data else {}


def get_company_by_name(name: str) -> Optional[dict]:
    db = get_supabase()
    result = db.table("companies").select("*").ilike("name", name).limit(1).execute()
    return result.data[0] if result.data else None


_COMPANIES_COLUMNS = {
    "id", "name", "domain", "careers_url", "ats_type", "country", "industry",
    "stage", "headcount", "created_at", "updated_at",
    # Multi-tenancy (migration 001) — companies also gained user_id NOT NULL.
    "user_id", "org_id",
}


def upsert_company(company_data: dict, user_id: str | None = None) -> dict:
    """Insert or update a company record.

    2026-05-12: same multi-tenancy gap as upsert_job — companies.user_id
    is NOT NULL but the writer here didn't pass it. Existing target rows
    have user_id set from prior backfills, but any first-discovery
    upsert (e.g. JobScout finding a brand-new company) crashed silently.

    BUG-013 (2026-05-12): also gate against phantom names here. Any caller
    passing a scraping-artifact name (e.g. "Adyen Careers",
    "68 Vacancies Apr 2026", "Merchant Acquiring ...") gets a ValueError
    instead of silently creating a row that would later be picked up by
    persona deep-research and burn LLM spend.
    """
    # Local import to avoid an import cycle (company_agent imports db.client).
    from agents.company_agent import _is_phantom_company_name
    name = (company_data or {}).get("name")
    if _is_phantom_company_name(name):
        raise ValueError(
            f"upsert_company: refusing to insert phantom company name "
            f"{name!r} (BUG-013 — looks like a job-listing fragment, date "
            f"stamp, or pure title/function token). Caller should filter "
            f"company names against agents.company_agent."
            f"_is_phantom_company_name before reaching this point."
        )
    db = get_supabase()
    if user_id is None:
        import os
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
    payload = dict(company_data)
    payload["user_id"] = user_id
    filtered = {k: v for k, v in payload.items() if k in _COMPANIES_COLUMNS}
    result = db.table("companies").upsert(
        # DB-1 (2026-05-29): uniqueness is now composite (user_id, name).
        filtered, on_conflict="user_id,name"
    ).execute()
    return result.data[0] if result.data else {}


# ── Supabase Storage helpers ──────────────────────────────────────────────
# Used by ResumeBuilderAgent / email / interview to persist artifacts
# across Railway's ephemeral container redeploys.

ARTIFACTS_BUCKET = "job-artifacts"


def upload_artifact(local_path: str, remote_path: str, content_type: str = "application/octet-stream") -> str | None:
    """
    Upload a file to Supabase Storage and return its signed URL (1-year expiry).
    Returns None if upload fails (caller should fall back to local path).
    """
    import logging
    import os
    log = logging.getLogger(__name__)
    if not os.path.exists(local_path):
        log.warning(f"upload_artifact: local file does not exist: {local_path}")
        return None
    try:
        db = get_supabase()
        with open(local_path, "rb") as f:
            data = f.read()
        # Ensure bucket exists (idempotent)
        try:
            db.storage.create_bucket(ARTIFACTS_BUCKET, options={"public": False})
        except Exception:
            pass  # already exists
        # Upload (overwrite if exists)
        try:
            db.storage.from_(ARTIFACTS_BUCKET).upload(
                path=remote_path,
                file=data,
                file_options={"content-type": content_type, "upsert": "true"},
            )
        except Exception as upload_err:
            # supabase-py raises on existing files even with upsert; ignore that
            if "Duplicate" not in str(upload_err) and "exists" not in str(upload_err).lower():
                raise

        # Return signed URL — 7-day expiry (HARDEN-P0-5: was 365 days).
        # 604800 seconds = 7 days.  After that the link is dead.
        # Callers that need longer access should refresh via the
        # /workspace/{id}/download endpoint which generates a fresh URL.
        SEVEN_DAYS = 60 * 60 * 24 * 7  # 604800
        signed = db.storage.from_(ARTIFACTS_BUCKET).create_signed_url(
            path=remote_path, expires_in=SEVEN_DAYS
        )
        # Try every known key
        if isinstance(signed, dict):
            url = (signed.get("signedURL")
                   or signed.get("signed_url")
                   or signed.get("signedUrl")
                   or signed.get("url"))
            if url:
                # Some versions return relative path; resolve to absolute
                if url.startswith("/"):
                    s = get_settings()
                    url = f"{s.supabase_url}{url}"
                return url
        # Fallback: build URL manually
        s = get_settings()
        # Manual signed URL via REST (works with service key)
        import httpx
        try:
            resp = httpx.post(
                f"{s.supabase_url}/storage/v1/object/sign/{ARTIFACTS_BUCKET}/{remote_path}",
                headers={
                    "apikey": s.supabase_service_key,
                    "Authorization": f"Bearer {s.supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json={"expiresIn": 60 * 60 * 24 * 7},  # HARDEN-P0-5: 7 days
                timeout=15,
            )
            if resp.status_code == 200:
                token = resp.json().get("signedURL") or resp.json().get("signedUrl")
                if token:
                    if token.startswith("/"):
                        return f"{s.supabase_url}/storage/v1{token}"
                    return token
        except Exception as fallback_err:
            log.warning(f"Manual signed URL fallback failed: {fallback_err}")

        log.warning(f"upload_artifact: uploaded but couldn't get signed URL. signed={signed}")
        return None
    except Exception as e:
        log.warning(f"Artifact upload failed for {remote_path}: {e}")
        return None


def get_stale_companies(hours: int = 24) -> list[dict]:
    """Return companies whose knowledge hasn't been refreshed recently.

    Bug fix: PostgREST .lt() compares values as strings — passing
    "NOW() - INTERVAL '24 hours'" is never evaluated as SQL and will
    return zero rows.  Instead we compute the cutoff in Python and pass
    an ISO-8601 timestamp string, which PostgREST compares correctly
    against the timestamptz column.
    """
    db = get_supabase()
    # Try the RPC first (it runs proper SQL server-side)
    try:
        result = db.rpc("get_stale_companies", {"hours_threshold": hours}).execute()
        if result.data:
            return result.data
    except Exception:
        pass

    # Fallback: compute cutoff in Python so PostgREST gets a real timestamp
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    result = db.table("company_knowledge") \
        .select("company_name, scraped_at") \
        .lt("scraped_at", cutoff) \
        .execute()

    # Return unique company names
    seen = set()
    stale = []
    for row in (result.data or []):
        if row["company_name"] not in seen:
            seen.add(row["company_name"])
            stale.append(row)
    return stale


def save_conversation_turn(
    job_id: int,
    company: str,
    role: str,
    turn: int,
    speaker: str,
    message: str,
    gap_identified: Optional[str] = None,
    gap_filled: bool = False
) -> dict:
    db = get_supabase()
    result = db.table("agent_conversations").insert({
        "job_id": job_id,
        "company": company,
        "role": role,
        "turn": turn,
        "speaker": speaker,
        "message": message,
        "gap_identified": gap_identified,
        "gap_filled": gap_filled
    }).execute()
    return result.data[0] if result.data else {}


def log_boss_audit(audit_data: dict) -> dict:
    db = get_supabase()
    result = db.table("boss_audit_log").insert(audit_data).execute()
    return result.data[0] if result.data else {}
