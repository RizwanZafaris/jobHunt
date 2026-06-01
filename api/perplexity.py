"""
api/perplexity.py — FastAPI router for the P1.3 Perplexity recency layer.

Wire-up belongs in api/server.py once the rest of P1.3 lands; the
one-line include is staged in _pending_server_includes_perplexity.txt
so a follow-up patch can paste it without re-thinking the import.

Endpoints:
  POST /perplexity/check-news/{company_name}
        Run a single recency check synchronously (~5s). Persists every
        new citation as a company_knowledge row and stamps the persona.

  POST /perplexity/strategic-posture/{company_name}
        Run a sonar-pro deep summary. On success, also stores the
        markdown on company_personas.last_strategic_posture_md and
        bumps last_strategic_posture_at.

  POST /perplexity/verify-claim
        Body: {claim: str, context?: str}
        Cheap fact-check. Returns {verified, evidence, citations, ...}.
        Does NOT persist anything — caller decides what to do with the
        verdict. Surface for resume meta-critic + interview-prep.

Auth: every endpoint depends on api.context.get_current_user. We pass
the user's UUID through to the agent so all writes are tenant-scoped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents import perplexity_search
from agents.persona_news_check import check_company_news
from api.context import get_current_user
from api.users import User
from db.client import aexecute, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/perplexity", tags=["perplexity"])


class VerifyClaimBody(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    context: Optional[str] = Field(default=None, max_length=4000)


class CheckNewsResponse(BaseModel):
    company: str
    new_anchors: int
    new_knowledge_rows: int
    cost_usd: float
    citations: list[dict[str, Any]]
    summary: Optional[str] = None
    error: Optional[str] = None


class StrategicPostureResponse(BaseModel):
    company: str
    persona_updated: bool
    cost_usd: float
    model: str
    content: str
    citations: list[dict[str, Any]]


class VerifyClaimResponse(BaseModel):
    verified: bool
    evidence: str
    citations: list[dict[str, Any]]
    cost_usd: float
    model: str


# ── /perplexity/check-news/{company_name} ─────────────────────────────────
@router.post("/check-news/{company_name}", response_model=CheckNewsResponse)
async def check_news(
    company_name: str,
    user: User = Depends(get_current_user),
) -> CheckNewsResponse:
    """Synchronous recency check. ~5s. Persists fresh citations + stamps persona."""
    if not company_name.strip():
        raise HTTPException(status_code=400, detail="company_name required")
    try:
        result = await check_company_news(user.id, company_name.strip())
    except Exception as exc:
        logger.exception("check_company_news failed for %s/%s", user.id, company_name)
        raise HTTPException(status_code=502, detail=f"perplexity_call_failed: {exc!r}") from exc
    return CheckNewsResponse(**result)


# ── /perplexity/strategic-posture/{company_name} ──────────────────────────
@router.post("/strategic-posture/{company_name}", response_model=StrategicPostureResponse)
async def strategic_posture(
    company_name: str,
    user: User = Depends(get_current_user),
) -> StrategicPostureResponse:
    """Run a sonar-pro deep posture summary; persist on company_personas."""
    company_name = company_name.strip()
    if not company_name:
        raise HTTPException(status_code=400, detail="company_name required")

    try:
        result = await perplexity_search.strategic_posture(company_name)
    except Exception as exc:
        logger.exception("strategic_posture failed for %s/%s", user.id, company_name)
        raise HTTPException(status_code=502, detail=f"perplexity_call_failed: {exc!r}") from exc

    persona_updated = False
    db = get_supabase()
    try:
        update_resp = await aexecute(
            db.table("company_personas")
            .update({
                "last_strategic_posture_md": result.get("content") or "",
                "last_strategic_posture_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("user_id", str(user.id))
            .eq("company_name", company_name)
        )
        persona_updated = bool(update_resp.data)
    except Exception:
        # Don't 500 — the user got the answer. Log & report partial success.
        logger.exception("failed to persist strategic_posture for %s/%s", user.id, company_name)

    return StrategicPostureResponse(
        company=company_name,
        persona_updated=persona_updated,
        cost_usd=float(result.get("cost_usd") or 0.0),
        model=str(result.get("model") or ""),
        content=str(result.get("content") or ""),
        citations=list(result.get("citations") or []),
    )


# ── /perplexity/verify-claim ──────────────────────────────────────────────
@router.post("/verify-claim", response_model=VerifyClaimResponse)
async def verify_claim_route(
    body: VerifyClaimBody,
    user: User = Depends(get_current_user),  # noqa: ARG001 — tenant gate even though not used in payload
) -> VerifyClaimResponse:
    """Cheap fact-check. No persistence — caller decides what to do."""
    try:
        result = await perplexity_search.verify_claim(body.claim, body.context or "")
    except Exception as exc:
        logger.exception("verify_claim failed")
        raise HTTPException(status_code=502, detail=f"perplexity_call_failed: {exc!r}") from exc
    return VerifyClaimResponse(
        verified=bool(result.get("verified")),
        evidence=str(result.get("evidence") or ""),
        citations=list(result.get("citations") or []),
        cost_usd=float(result.get("cost_usd") or 0.0),
        model=str(result.get("model") or ""),
    )
