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
    metadata: dict = None
) -> dict:
    """Store a company intelligence chunk with its embedding."""
    embedding = await embed(content)
    db = get_supabase()

    row = {
        "company_name": company_name,
        "section": section,
        "content": content,
        "embedding": embedding,
        "source_url": source_url,
        "metadata": metadata or {}
    }
    if company_id:
        row["company_id"] = company_id

    # Upsert: replace if same company + section
    result = db.table("company_knowledge").upsert(
        row,
        on_conflict="company_name,section"
    ).execute()
    return result.data[0] if result.data else {}


async def search_company_knowledge(
    company_name: str,
    query: str,
    match_count: int = 5
) -> list[dict]:
    """Semantic search within a company's knowledge base."""
    query_embedding = await embed(query)
    db = get_supabase()

    result = db.rpc("search_company_knowledge", {
        "query_embedding": query_embedding,
        "target_company": company_name,
        "match_count": match_count
    }).execute()
    return result.data or []


async def upsert_rizwan_profile(section: str, content: str) -> dict:
    """Store a section of Rizwan's profile with embedding."""
    embedding = await embed(content)
    db = get_supabase()
    result = db.table("rizwan_profile").upsert(
        {"section": section, "content": content, "embedding": embedding},
        on_conflict="section"
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
    "report_path", "resume_path", "email_path", "interview_path",
    "discovered_at", "applied_at", "updated_at",
    # Workflow v2
    "archetype", "legitimacy_tier", "legitimacy_signals",
    "resume_generated_at", "evaluation_blocks",
}


def upsert_job(job_data: dict) -> dict:
    """Insert or update a job record. Filters out keys that aren't real columns."""
    db = get_supabase()
    filtered = {k: v for k, v in job_data.items() if k in _JOBS_COLUMNS}
    result = db.table("jobs").upsert(
        filtered,
        on_conflict="url"
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
}


def upsert_company(company_data: dict) -> dict:
    db = get_supabase()
    filtered = {k: v for k, v in company_data.items() if k in _COMPANIES_COLUMNS}
    result = db.table("companies").upsert(
        filtered, on_conflict="name"
    ).execute()
    return result.data[0] if result.data else {}


# ── Supabase Storage helpers ──────────────────────────────────────────────
# Used by ResumeBuilderAgent / email / interview to persist artifacts
# across Railway's ephemeral container redeploys.

ARTIFACTS_BUCKET = "job-artifacts"


def upload_artifact(local_path: str, remote_path: str, content_type: str = "application/octet-stream") -> str | None:
    """
    Upload a file to Supabase Storage and return its public URL.
    Returns None if upload fails (caller should fall back to local path).
    """
    import os
    if not os.path.exists(local_path):
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
        db.storage.from_(ARTIFACTS_BUCKET).upload(
            path=remote_path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        # Return signed URL (1 year expiry)
        signed = db.storage.from_(ARTIFACTS_BUCKET).create_signed_url(
            path=remote_path, expires_in=60 * 60 * 24 * 365
        )
        return signed.get("signedURL") or signed.get("signed_url")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Artifact upload failed for {remote_path}: {e}")
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
