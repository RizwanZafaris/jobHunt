"""
api/network.py — FastAPI router for the referral-graph surface.

Owns every endpoint under /network/*. The dashboard tab `/network`
calls these. Wire-up is documented in api/NETWORK.md (one line in
api/server.py once the rest of the SaaS-blocking work is on main).

Auth: every handler depends on `get_current_user` (api/context.py),
which falls back to single-user mode (Rizwan) when
RIZWAN_SINGLE_USER_MODE=1 — same pattern as every other multi-tenant
endpoint shipped this week.

Endpoints:
  GET  /network/paths              — ranked intro paths to a target
  GET  /network/people             — search the user's people graph
  POST /network/people             — manually add a person
  POST /network/import/linkedin-csv — multipart upload, returns counts
  POST /network/edges              — manual introduction / edge create
  GET  /network/target-coverage    — per-target paths_count_1hop / 2hop / top
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from agents.referral_graph import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MIN_STRENGTH,
    Person,
    ReferralGraph,
    ReferralPath,
)
from api.context import get_current_user
from api.users import User
from db.client import aexecute, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/network", tags=["network"])


def _escape_ilike(s: str) -> str:
    """Escape SQL LIKE/ILIKE wildcards so user input matches literally.

    Order matters: escape backslash FIRST so we don't double-escape the
    backslashes we add for ``%`` and ``_``.
    """
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── Request models ────────────────────────────────────────────────────────
class CreatePersonBody(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    # Optional current employment to seed in one shot.
    current_company_name: Optional[str] = None
    current_role: Optional[str] = None


class CreateEdgeBody(BaseModel):
    src_person: UUID
    dst_person: UUID
    kind: str = Field(
        description=(
            "One of: me_first_degree, colleague, classmate, friend, "
            "family, introduced_by, same_company_overlap, "
            "referenced_in_outreach"
        ),
    )
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: Optional[str] = None


class TargetCoverageRow(BaseModel):
    target_company_id: UUID
    name: Optional[str] = None
    paths_count_1hop: int
    paths_count_2hop: int
    top_path: Optional[ReferralPath] = None


class ImportSummary(BaseModel):
    imported: int
    skipped: int
    edges_created: int
    employments_created: int
    errors: list[str] = []


class DraftIntroBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_company_id: UUID
    introducer_person_id: UUID
    target_role_summary: Optional[str] = None  # 1-line role description if known


class DraftIntroResponse(BaseModel):
    subject: str
    body: str
    introducer_name: str
    target_company_name: str
    confidence: float
    cost_usd: float


# ── /network/paths ────────────────────────────────────────────────────────
@router.get("/paths", response_model=list[ReferralPath])
def get_paths(
    target_company_id: UUID = Query(..., description="target_companies.id"),
    max_hops: int = Query(DEFAULT_MAX_HOPS, ge=1, le=3),
    min_strength: float = Query(DEFAULT_MIN_STRENGTH, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
) -> list[ReferralPath]:
    """Return ranked intro paths from the current user to a target."""
    rg = ReferralGraph(user.id)
    return rg.find_paths(
        target_company_id=target_company_id,
        max_hops=max_hops,
        min_strength=min_strength,
        limit=limit,
    )


# ── /network/people ───────────────────────────────────────────────────────
@router.get("/people", response_model=list[Person])
def search_people(
    q: Optional[str] = Query(None, description="case-insensitive substring on full_name"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
) -> list[Person]:
    """Search the user's people. Empty `q` returns the most recent rows."""
    db = get_supabase()
    qb = (
        db.table("people")
        .select("id, full_name, linkedin_url, headline, profile_picture_url, source")
        .eq("user_id", str(user.id))
    )
    if q:
        qb = qb.ilike("full_name", f"%{_escape_ilike(q)}%")
    rows = (
        qb.order("created_at", desc=True)
          .limit(min(max(limit, 1), 100))
          .execute()
    ).data or []

    out: list[Person] = []
    rg = ReferralGraph(user.id)
    for row in rows:
        company, role = rg._current_employment(str(row["id"]))  # noqa: SLF001
        out.append(Person(
            id=UUID(str(row["id"])),
            full_name=row.get("full_name") or "(unknown)",
            linkedin_url=row.get("linkedin_url"),
            headline=row.get("headline"),
            profile_picture_url=row.get("profile_picture_url"),
            current_company=company,
            current_role=role,
            source=row.get("source"),
        ))
    return out


@router.post("/people", response_model=Person, status_code=201)
def create_person(
    body: CreatePersonBody,
    user: User = Depends(get_current_user),
) -> Person:
    """Manually add a person (and optionally an initial employment)."""
    rg = ReferralGraph(user.id)
    person_id = rg.upsert_person(
        full_name=body.full_name,
        linkedin_url=body.linkedin_url,
        email=body.email,
        headline=body.headline,
        notes=body.notes,
        location=body.location,
        source="manual",
    )
    if body.current_company_name:
        rg.upsert_employment(
            person_id=person_id,
            company_name=body.current_company_name,
            role=body.current_role,
            source="manual",
        )
    company, role = rg._current_employment(person_id)  # noqa: SLF001
    return Person(
        id=UUID(person_id),
        full_name=body.full_name,
        linkedin_url=body.linkedin_url,
        headline=body.headline,
        current_company=company,
        current_role=role,
        source="manual",
    )


# ── /network/import/linkedin-csv ──────────────────────────────────────────
# REMOVED 2026-05-27: CSV upload replaced by Perplexity-based people
# discovery. The new endpoint lives at /admin/today/trigger-people-discovery
# in api/actions.py — it auto-discovers Recruiters, Heads of Talent,
# Hiring Managers, and Senior PMs at all target companies without
# requiring a manual LinkedIn export.
#
# Returning 410 Gone (semantically: this resource intentionally deleted)
# so existing clients fail loudly instead of silently 404-ing.
@router.post("/import/linkedin-csv", deprecated=True)
async def import_linkedin_csv_removed(
    user: User = Depends(get_current_user),
) -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "LinkedIn CSV import was removed 2026-05-27. Use "
            "/admin/today/trigger-people-discovery instead — it queries "
            "Perplexity Sonar per target company, no CSV export needed."
        ),
    )


# ── /network/edges ────────────────────────────────────────────────────────
@router.post("/edges", response_model=dict)
def create_edge(
    body: CreateEdgeBody,
    user: User = Depends(get_current_user),
) -> dict:
    """Create a typed edge between two people you already track.

    Common case: kind='introduced_by' to record "Bob can introduce me
    to Alice". The dashboard's IntroDraftModal calls this before
    drafting the email.
    """
    if body.src_person == body.dst_person:
        raise HTTPException(status_code=400, detail="self_edges_not_allowed")
    rg = ReferralGraph(user.id)
    if body.kind == "introduced_by" and body.evidence:
        return rg.add_introduction_path(
            src_person_id=str(body.src_person),
            dst_person_id=str(body.dst_person),
            evidence=body.evidence,
        )
    edge_id = rg.upsert_edge(
        src_person=str(body.src_person),
        dst_person=str(body.dst_person),
        kind=body.kind,
        strength=body.strength,
        evidence={"note": body.evidence} if body.evidence else None,
    )
    return {
        "id": edge_id,
        "src_person": str(body.src_person),
        "dst_person": str(body.dst_person),
        "kind": body.kind,
        "strength": body.strength,
    }


# ── /network/target-coverage ──────────────────────────────────────────────
@router.get("/target-coverage", response_model=list[TargetCoverageRow])
def target_coverage(
    user: User = Depends(get_current_user),
) -> list[TargetCoverageRow]:
    """Per target company: how many 1-hop / 2-hop intro paths exist + top path.

    This is the headline number on the /network surface. Pulls every
    company the user has marked is_target=true and not a phantom, runs
    find_paths(max_hops=2) for each, aggregates counts.

    B11: was querying a non-existent `target_companies` table → 500 on
    every request. The real schema marks targets via `companies.is_target`;
    `target_company_employees` joins by `target_company_id = companies.id`.
    """
    db = get_supabase()
    targets = (
        db.table("companies")
        .select("id, name")
        .eq("user_id", str(user.id))
        .eq("is_target", True)
        .neq("is_phantom", True)
        .execute()
    ).data or []

    if not targets:
        return []  # empty state, not 500

    rg = ReferralGraph(user.id)
    out: list[TargetCoverageRow] = []
    for t in targets:
        try:
            paths = rg.find_paths(
                target_company_id=str(t["id"]),
                max_hops=2,
                min_strength=DEFAULT_MIN_STRENGTH,
                limit=10,
            )
        except Exception as exc:
            logger.warning("find_paths failed for target %s: %s", t.get("id"), exc)
            paths = []
        c1 = sum(1 for p in paths if p.hop_count == 1)
        c2 = sum(1 for p in paths if p.hop_count == 2)
        out.append(TargetCoverageRow(
            target_company_id=UUID(str(t["id"])),
            name=t.get("name"),
            paths_count_1hop=c1,
            paths_count_2hop=c2,
            top_path=paths[0] if paths else None,
        ))
    # Best targets first: more 1-hop, then 2-hop, then strongest top path.
    out.sort(
        key=lambda r: (
            -r.paths_count_1hop,
            -r.paths_count_2hop,
            -(r.top_path.total_strength if r.top_path else 0.0),
        )
    )
    return out


# ── /network/draft-intro ───────────────────────────────────────────────────
@router.post("/draft-intro", response_model=DraftIntroResponse)
async def draft_intro(
    body: DraftIntroBody,
    user: User = Depends(get_current_user),
) -> DraftIntroResponse:
    """B9: Draft a warm-intro email FOR the introducer to send to the target.

    Picks the highest-strength path through this introducer to the target
    company, calls agents.intro_email_agent.draft_intro_email, returns the
    drafted email. Never sends — the user reviews + sends manually.
    Cost-capped at $0.10.
    """
    db = get_supabase()

    # Verify the target company belongs to this user.
    tc_rows = (await aexecute(
        db.table("target_companies")
        .select("id, name, company_name")
        .eq("id", str(body.target_company_id))
        .eq("user_id", str(user.id))
        .limit(1)
    )).data or []
    if not tc_rows:
        raise HTTPException(status_code=404, detail="no_intro_path_to_target")
    target_company_name = tc_rows[0].get("company_name") or tc_rows[0].get("name") or ""

    # Find paths to the target.
    rg = ReferralGraph(user.id)
    try:
        paths = rg.find_paths(
            target_company_id=str(body.target_company_id),
            max_hops=2,
            min_strength=DEFAULT_MIN_STRENGTH,
            limit=20,
        )
    except Exception:
        paths = []

    # Filter to paths whose first hop matches the introducer.
    matching = [
        p for p in paths
        if len(p.path) >= 2 and str(p.path[1].id) == str(body.introducer_person_id)
    ]
    if not matching:
        raise HTTPException(status_code=404, detail="no_intro_path_to_target")

    # Pick the strongest path.
    best = max(matching, key=lambda p: p.total_strength)
    introducer_name = best.path[1].full_name if len(best.path) >= 2 else ""

    # Build candidate profile from user record.
    candidate_profile: dict[str, Any] = {
        "name": user.full_name or "",
        "headline": getattr(user, "headline", None) or "",
        "email": user.email,
    }
    # Try to enrich from users table row.
    try:
        u_rows = (await aexecute(
            db.table("users")
            .select("full_name, headline, linkedin_url, summary, location")
            .eq("id", str(user.id))
            .limit(1)
        )).data or []
        if u_rows:
            ur = u_rows[0]
            candidate_profile["name"] = ur.get("full_name") or candidate_profile["name"]
            candidate_profile["headline"] = ur.get("headline") or candidate_profile["headline"]
            candidate_profile["linkedin_url"] = ur.get("linkedin_url") or ""
            candidate_profile["summary"] = ur.get("summary") or ""
            candidate_profile["location"] = ur.get("location") or ""
    except Exception:
        pass  # profile enrichment is best-effort

    target_role: dict[str, Any] = {"company": target_company_name}
    if body.target_role_summary:
        target_role["summary"] = body.target_role_summary

    from agents.intro_email_agent import draft_intro_email
    result = await draft_intro_email(
        intro_path=best,
        target_role=target_role,
        candidate_profile=candidate_profile,
        max_cost_usd=0.10,
    )

    if result.get("error") == "cost_capped":
        raise HTTPException(status_code=422, detail="cost_capped")

    return DraftIntroResponse(
        subject=result.get("subject", ""),
        body=result.get("body", ""),
        introducer_name=introducer_name,
        target_company_name=target_company_name,
        confidence=best.total_strength,
        cost_usd=result.get("cost_usd", 0.0),
    )
