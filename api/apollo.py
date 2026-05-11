"""
api/apollo.py — FastAPI router for the Apollo enrichment layer.

Endpoints (all auth-gated via Depends(get_current_user)):

  POST /apollo/enrich/{company_name}
      Body: {"domain": str}  (e.g. visa.com)
      Cost: 1 credit (0 if not found)
      Effect: inserts `company_knowledge` row with metadata.source='apollo_firmographics'
      Use when: you want firmographic + tech-stack data for a target

  POST /apollo/job-postings
      Body: {"org_id": str, "per_page": int=100}
      Cost: 1 credit per page
      Effect: returns Apollo's open-jobs view; backend can fan into the
      existing jobs scout if desired
      Use when: you want jobs Apollo sees that LinkedIn may not yet

  POST /apollo/search-people
      Body: {"organization_ids": [str], "person_titles": [str], "person_seniorities": [str]}
      Cost: 0 (paid plan only — free plan returns 403 ApolloPlanBlocked)
      Effect: returns up to 100 PMs/Eng-leaders at the target; seed for
      the referral graph (P1.1)
      Use when: building the warm-intro frontier for a target company

  POST /apollo/search-companies
      Body: {"q_organization_name": str, "q_organization_domains_list": [str]}
      Cost: 0 (paid plan only)
      Effect: resolves canonical Apollo org_id from name + domain
      Use when: you need to look up org_id before /enrich or /job-postings

ENV vars required:
    APOLLO_API_KEY    — get from app.apollo.io/settings/api

Free-plan callers receive a 402 (Payment Required) with a clear message
on the search endpoints. Enrich + job_postings work on free credits if
the account has any remaining.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.apollo_enrich import (
    ApolloError,
    ApolloPlanBlocked,
    ApolloRateLimited,
    enrich_company_into_knowledge,
    enrich_organization,
    get_organization_job_postings,
    search_companies,
    search_people,
)
from api.context import get_current_user
from api.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apollo", tags=["apollo"])


# ─── Request models ──────────────────────────────────────────────────────
class EnrichRequest(BaseModel):
    domain: str = Field(..., description="Apex domain — e.g. 'visa.com', NOT 'usa.visa.com'.")


class JobPostingsRequest(BaseModel):
    org_id: str
    per_page: int = Field(default=100, ge=1, le=10000)
    page: int = Field(default=1, ge=1)


class PeopleSearchRequest(BaseModel):
    organization_ids: Optional[list[str]] = None
    q_organization_domains_list: Optional[list[str]] = None
    person_titles: Optional[list[str]] = None
    person_seniorities: Optional[list[str]] = None  # senior / manager / director / vp / c_suite
    per_page: int = Field(default=25, ge=1, le=100)
    page: int = Field(default=1, ge=1)


class CompanySearchRequest(BaseModel):
    q_organization_name: Optional[str] = None
    q_organization_domains_list: Optional[list[str]] = None
    q_organization_keyword_tags: Optional[list[str]] = None
    per_page: int = Field(default=10, ge=1, le=100)


# ─── Endpoint helpers ────────────────────────────────────────────────────
def _wrap_apollo_errors(exc: Exception):
    """Convert ApolloError variants into clean HTTP status codes for the API."""
    if isinstance(exc, ApolloPlanBlocked):
        raise HTTPException(
            status_code=402,
            detail={
                "error": "apollo_plan_blocked",
                "message": str(exc),
                "hint": "Search endpoints require a paid Apollo plan. Use enrich_organization on the free tier (1 credit/call) or upgrade at app.apollo.io.",
            },
        )
    if isinstance(exc, ApolloRateLimited):
        raise HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, ApolloError):
        raise HTTPException(status_code=502, detail=f"apollo_upstream: {exc}")
    # Unknown — re-raise so the global handler returns 500.
    raise exc


# ─── Endpoints ───────────────────────────────────────────────────────────
@router.post("/enrich/{company_name}")
async def enrich(
    company_name: str,
    body: EnrichRequest,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """1-credit enrichment. Inserts firmographic row into company_knowledge."""
    try:
        result = await enrich_company_into_knowledge(
            user_id=str(user.id),
            company_name=company_name,
            domain=body.domain,
        )
        return result
    except Exception as e:
        _wrap_apollo_errors(e)


@router.post("/enrich-raw")
async def enrich_raw(
    body: EnrichRequest,
    user: User = Depends(get_current_user),  # noqa: ARG001 (kept for auth)
) -> dict[str, Any]:
    """1-credit enrichment, no DB write — useful for ad-hoc inspection."""
    try:
        return await enrich_organization(body.domain)
    except Exception as e:
        _wrap_apollo_errors(e)


@router.post("/job-postings")
async def job_postings(
    body: JobPostingsRequest,
    user: User = Depends(get_current_user),  # noqa: ARG001
) -> dict[str, Any]:
    """1-credit per page. Apollo's index of currently-open jobs at a company."""
    try:
        return await get_organization_job_postings(
            body.org_id, page=body.page, per_page=body.per_page,
        )
    except Exception as e:
        _wrap_apollo_errors(e)


@router.post("/search-people")
async def search_people_endpoint(
    body: PeopleSearchRequest,
    user: User = Depends(get_current_user),  # noqa: ARG001
) -> dict[str, Any]:
    """Free per-search on paid plan. 402 on free plan."""
    try:
        return await search_people(
            organization_ids=body.organization_ids,
            q_organization_domains_list=body.q_organization_domains_list,
            person_titles=body.person_titles,
            person_seniorities=body.person_seniorities,
            per_page=body.per_page,
            page=body.page,
        )
    except Exception as e:
        _wrap_apollo_errors(e)


@router.post("/search-companies")
async def search_companies_endpoint(
    body: CompanySearchRequest,
    user: User = Depends(get_current_user),  # noqa: ARG001
) -> dict[str, Any]:
    """Free per-search on paid plan. 402 on free plan."""
    try:
        return await search_companies(
            q_organization_name=body.q_organization_name,
            q_organization_domains_list=body.q_organization_domains_list,
            q_organization_keyword_tags=body.q_organization_keyword_tags,
            per_page=body.per_page,
        )
    except Exception as e:
        _wrap_apollo_errors(e)
