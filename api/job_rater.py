"""
api/job_rater.py — FRD-14 URL Job Rater.

Lets the user rate a job they found anywhere (not just scout-discovered):

    POST /jobs/rate-url        {url? , jd_text?}  → ephemeral 6-dim rating + rate_token
    POST /jobs/rate-url/save   {rate_token}        → promote the rated job into `jobs`

Design (per FRD-14, revised 2026-06-01):
  • URL is the primary input. If the Apify fetch fails or returns thin
    content, we DON'T error — we respond {needs_jd_text: true, reason} so
    the dashboard can reveal a "paste the JD text" box. jd_text-only also
    works (paste path).
  • Rating is EPHEMERAL: we score an in-memory extracted JD via
    scoring_agent.score_job_dict (the DB-free core) and stash the result in
    Redis under a short-TTL rate_token. Nothing is written to `jobs` until
    the user explicitly hits /save. This keeps "just checking" off /today.
  • /save promotes the stashed result into a real `jobs` row (dedup first),
    so it becomes a first-class tracked job (resume/interview buildable).

Reuses: agents/jd_extractor (fetch + extract), agents/scoring_agent
(score_job_dict), api.queue._get_redis (ephemeral store), the slowapi
limiter, and get_current_user. No new infra, no new table (jobs.source is
free TEXT — verified against db/schema.sql).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.context import get_current_user
from api.rate_limits import RATE_LIMITS, limiter
from api.users import User
from db.client import aexecute, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["job-rater"])

# Ephemeral rating store: Redis key prefix + TTL.
_RATE_TOKEN_PREFIX = "rate_url:"
_RATE_TOKEN_TTL_S = 3600  # 1 hour


# ─── Request / response models ──────────────────────────────────────────────
class RateUrlBody(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2000)
    jd_text: Optional[str] = Field(default=None, max_length=50000)


class SaveRatedBody(BaseModel):
    rate_token: str = Field(min_length=8, max_length=80)


# ─── Endpoints ───────────────────────────────────────────────────────────────
@router.post("/rate-url")
@limiter.limit(RATE_LIMITS["data_import"])
async def rate_url(
    request: Request,
    body: RateUrlBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch (or accept pasted) JD → extract → score, WITHOUT persisting.

    Returns one of:
      • {needs_jd_text: true, reason}   — URL fetch failed/thin; UI prompts for paste
      • {rating, extracted, rate_token} — full ephemeral rating
    """
    from agents.jd_extractor import extract_jd, fetch_jd_from_url, to_job_dict
    from agents.scoring_agent import score_job_dict

    url = (body.url or "").strip() or None
    jd_text = (body.jd_text or "").strip() or None

    if not url and not jd_text:
        raise HTTPException(
            status_code=422,
            detail="Provide either `url` or `jd_text`.",
        )

    # 1. Obtain the JD markdown. jd_text (paste) wins if present.
    raw_md = jd_text
    if not raw_md:
        raw_md, reason = await fetch_jd_from_url(url)  # type: ignore[arg-type]
        if reason is not None:
            # Not an error — tell the UI to offer the paste-text box.
            return {
                "needs_jd_text": True,
                "reason": reason,
                "url": url,
                "message": _reason_message(reason),
            }

    # 2. Extract structured fields (no fabrication; absent → null/[]).
    extracted = await extract_jd(raw_md or "")

    # 3. Score the in-memory job dict via the DB-free core.
    job = to_job_dict(extracted, url=url)
    breakdown = await score_job_dict(job=job, user_id=user.id)

    # 4. Stash ephemeral payload under a fresh token (Redis, 1h TTL).
    rate_token = uuid.uuid4().hex
    payload = {
        "user_id": str(user.id),
        "url": url,
        "source": "manual_url" if url else "manual_paste",
        "extracted": extracted,
        "breakdown": breakdown,
    }
    _store_rate_token(rate_token, payload)

    return {
        "rating": breakdown,
        "extracted": extracted,
        "rate_token": rate_token,
    }


@router.post("/rate-url/save")
@limiter.limit(RATE_LIMITS["data_import"])
async def save_rated_job(
    request: Request,
    body: SaveRatedBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Promote a previously-rated (ephemeral) job into the `jobs` pipeline."""
    payload = _load_rate_token(body.rate_token)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="rate_token not found or expired — re-run /jobs/rate-url.",
        )
    # Tenant guard: a token belongs to the user who created it.
    if payload.get("user_id") != str(user.id):
        raise HTTPException(status_code=404, detail="rate_token not found.")

    extracted = payload.get("extracted") or {}
    breakdown = payload.get("breakdown") or {}
    url = payload.get("url")
    source = payload.get("source") or ("manual_url" if url else "manual_paste")
    company = (extracted.get("company") or "").strip()
    title = (extracted.get("title") or "").strip()

    db = get_supabase()

    # Dedup: same URL, or same (company,title), already tracked for this user.
    existing_id = await _find_existing_job(
        db, user_id=str(user.id), url=url, company=company, title=title
    )
    if existing_id is not None:
        return {"job_id": existing_id, "deduped": True}

    row: dict[str, Any] = {
        "user_id": str(user.id),
        "title": title or "(untitled role)",
        "company": company or "(unknown company)",
        "description": extracted.get("raw_jd_md") or "",
        "status": "new",
        "source": source,
        "match_score": breakdown.get("composite"),
        "letter_grade": breakdown.get("letter_grade"),
        "fit_score_breakdown": breakdown,
    }
    if url:
        row["source_url"] = url

    # Async seam (db/client.py::aexecute) — never block the event loop.
    inserted = await aexecute(db.table("jobs").insert(row))
    rows = inserted.data or []
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to save job.")
    return {"job_id": rows[0].get("id"), "deduped": False}


# ─── helpers ─────────────────────────────────────────────────────────────────
def _reason_message(reason: str) -> str:
    return {
        "no_token": "Job fetching isn't configured on the server. Paste the job description text instead.",
        "fetch_failed": "Couldn't fetch that URL (it may be login-walled or blocking bots). Paste the job description text instead.",
        "thin_content": "That page didn't return enough text (it may be a login wall or JavaScript app). Paste the job description text instead.",
    }.get(reason, "Couldn't read that URL. Paste the job description text instead.")


def _store_rate_token(token: str, payload: dict[str, Any]) -> None:
    """Best-effort Redis stash of the ephemeral rating, TTL-bounded."""
    from api.queue import _get_redis

    r = _get_redis()
    r.setex(
        _RATE_TOKEN_PREFIX + token,
        _RATE_TOKEN_TTL_S,
        json.dumps(payload).encode("utf-8"),
    )


def _load_rate_token(token: str) -> Optional[dict[str, Any]]:
    """Load + decode an ephemeral rating payload, or None if missing/expired."""
    from api.queue import _get_redis

    r = _get_redis()
    raw = r.get(_RATE_TOKEN_PREFIX + token)
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception as e:
        logger.warning(f"rate_token decode failed: {type(e).__name__}: {e}")
        return None


async def _find_existing_job(
    db: Any,
    *,
    user_id: str,
    url: Optional[str],
    company: str,
    title: str,
) -> Optional[int]:
    """Return an existing jobs.id for this user matching url OR (company,title),
    else None. Best-effort — a dedup miss is non-fatal (we'd just insert).

    Uses the async seam (aexecute) so the lookup never blocks the event loop.
    """
    try:
        if url:
            resp = await aexecute(
                db.table("jobs")
                .select("id")
                .eq("user_id", user_id)
                .eq("source_url", url)
                .limit(1)
            )
            hit = resp.data or []
            if hit:
                return hit[0].get("id")
        if company and title:
            resp = await aexecute(
                db.table("jobs")
                .select("id")
                .eq("user_id", user_id)
                .eq("company", company)
                .eq("title", title)
                .limit(1)
            )
            hit = resp.data or []
            if hit:
                return hit[0].get("id")
    except Exception as e:
        logger.warning(f"dedup lookup failed (continuing to insert): {type(e).__name__}: {e}")
    return None
