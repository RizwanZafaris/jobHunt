"""
agents/apollo_enrich.py — Apollo.io firmographic + hiring-intel layer.

Why this exists
---------------
The persona pipeline now has three layers:

    Apify (depth scrape)        →   long-form pages, blog posts, Glassdoor reviews
    Perplexity (recency)        →   news in last 30 days, strategic posture
    Apollo (this module)        →   firmographics + open-jobs index + people

Apollo fills the *gap between* "what's the news" (Perplexity) and "what's
the deep content" (Apify): it gives us **structured headcount / revenue /
funding / tech-stack data** AND **the company's full open-roles list as
Apollo sees it** (which often catches roles before LinkedIn indexes them).
Critically for the referral graph (P1.1), Apollo's people search returns
PMs / engineering leaders at a target company — the seed set for warm
intros.

The MCP audit (2026-05-10) of the user's Apollo plan showed:
  - `organizations_enrich`  : works, costs 1 credit per call (0 if not found)
  - `organizations_job_postings` : works, costs 1 credit per call
  - `mixed_companies_search` : FREE-PLAN BLOCKED → returns API_INACCESSIBLE
  - `mixed_people_api_search` : same
The wrapper supports all four endpoints but raises a clear error when
the plan blocks search.

Calling pattern
---------------
    from agents.apollo_enrich import (
        enrich_organization, get_organization_job_postings,
        search_people, search_companies,
    )

    # 1 credit (0 if not found)
    org = await enrich_organization("visa.com")

    # 1 credit per page (10000 rows max default)
    jobs = await get_organization_job_postings(org_id=org["id"], per_page=50)

    # Paid plan only
    people = await search_people(
        organization_ids=[org["id"]],
        person_titles=["Senior Product Manager", "Director of Product"],
        person_seniorities=["senior", "director", "vp"],
    )

ENV vars
--------
    APOLLO_API_KEY              — required; raises if missing
    APOLLO_BASE_URL             — default https://api.apollo.io
    APOLLO_HTTP_TIMEOUT         — default 30 (seconds)

Cost rules of thumb
-------------------
At Pro tier (~$100/mo for the typical pricing window):
  enrich 71 personas (one-shot)           = 71 credits   ≈ $9
  enrich job-postings batch monthly (71)  = 71 credits   ≈ $9
  people search 50 results × 71 personas  = paid plan, 0 per-search cost
  total monthly steady state              ≈ $18 + base plan
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ─── Config ───────────────────────────────────────────────────────────────
APOLLO_BASE_URL = os.getenv("APOLLO_BASE_URL", "https://api.apollo.io")
APOLLO_HTTP_TIMEOUT_S = float(os.getenv("APOLLO_HTTP_TIMEOUT", "30"))


def _api_key() -> str:
    key = os.environ.get("APOLLO_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "APOLLO_API_KEY not set. Add it to your .env / Railway env "
            "before calling agents.apollo_enrich. Sign-up: app.apollo.io"
        )
    return key


def _headers() -> dict[str, str]:
    # Apollo uses x-api-key, NOT Authorization: Bearer.
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": _api_key(),
    }


# ─── Exceptions ───────────────────────────────────────────────────────────
class ApolloError(RuntimeError):
    """Base for any Apollo API error we want callers to handle."""


class ApolloPlanBlocked(ApolloError):
    """Endpoint is paid-only and the caller's plan does not allow access.

    The Apollo response body looks like:
        {"error": "api/v1/... is not accessible with this access token on
         a free plan. Please upgrade your plan from https://app.apollo.io/.",
         "error_code": "API_INACCESSIBLE"}
    """


class ApolloRateLimited(ApolloError):
    """429 — retry with backoff. The wrapper handles 1 retry automatically."""


# ─── HTTP plumbing ────────────────────────────────────────────────────────
async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST to Apollo, raising structured errors on known failure modes."""
    url = f"{APOLLO_BASE_URL}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=APOLLO_HTTP_TIMEOUT_S) as client:
        for attempt in range(2):
            resp = await client.post(url, headers=_headers(), json=body)
            if resp.status_code == 200:
                return resp.json()
            # Plan-block: short-circuit, do not retry.
            if resp.status_code in (401, 403):
                try:
                    data = resp.json()
                except Exception:
                    data = {"error": resp.text[:200]}
                if data.get("error_code") == "API_INACCESSIBLE":
                    raise ApolloPlanBlocked(
                        f"{path} blocked by plan: {data.get('error', '')}"
                    )
                raise ApolloError(f"{resp.status_code} on {path}: {data}")
            # Rate-limit: one retry with 2s backoff, then raise.
            if resp.status_code == 429 and attempt == 0:
                import asyncio
                await asyncio.sleep(2.0)
                continue
            if resp.status_code == 429:
                raise ApolloRateLimited(f"429 on {path} after retry")
            # Anything else.
            try:
                err_body = resp.json()
            except Exception:
                err_body = {"error": resp.text[:300]}
            raise ApolloError(f"{resp.status_code} on {path}: {err_body}")
    # Should be unreachable.
    raise ApolloError(f"exhausted retries on {path}")


# ─── Public API: 4 endpoints ──────────────────────────────────────────────
async def enrich_organization(domain: str) -> dict[str, Any]:
    """1 credit if found, 0 if not. Returns firmographic + leadership + tech stack.

    Useful slice of the response: industry, estimated_num_employees,
    annual_revenue_printed, latest_funding_round_date, founded_year,
    organization_chart_root, current_technologies, primary_phone, raw_address.
    """
    return await _post("api/v1/organizations/enrich", {"domain": domain})


async def get_organization_job_postings(
    org_id: str,
    *,
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """1 credit per page. Returns Apollo's view of the company's open jobs.

    Often catches roles before they're indexed on LinkedIn — useful for the
    job_scout layer to seed jobs the regular scout misses.
    """
    return await _post(
        "api/v1/organizations/job_postings/search",
        {"id": org_id, "page": page, "per_page": min(per_page, 10000)},
    )


async def search_people(
    *,
    organization_ids: Optional[list[str]] = None,
    q_organization_domains_list: Optional[list[str]] = None,
    person_titles: Optional[list[str]] = None,
    person_seniorities: Optional[list[str]] = None,
    per_page: int = 25,
    page: int = 1,
) -> dict[str, Any]:
    """Free on paid plan, blocked on free plan. Seeds the referral-graph
    `people` table with PMs / engineering leaders at the target company.

    Last names + emails may be masked depending on plan; pass to the
    People Enrichment endpoint to unmask (separate per-record credit cost).
    """
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if organization_ids:
        body["organization_ids"] = organization_ids
    if q_organization_domains_list:
        body["q_organization_domains_list"] = q_organization_domains_list
    if person_titles:
        body["person_titles"] = person_titles
    if person_seniorities:
        body["person_seniorities"] = person_seniorities
    return await _post("api/v1/mixed_people/search", body)


async def search_companies(
    *,
    q_organization_name: Optional[str] = None,
    q_organization_domains_list: Optional[list[str]] = None,
    q_organization_keyword_tags: Optional[list[str]] = None,
    per_page: int = 10,
    page: int = 1,
) -> dict[str, Any]:
    """Free on paid plan, blocked on free plan. Lookup canonical org_id by
    name + domain. Use this before enrich_organization() to resolve
    ambiguous names.
    """
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if q_organization_name:
        body["q_organization_name"] = q_organization_name
    if q_organization_domains_list:
        body["q_organization_domains_list"] = q_organization_domains_list
    if q_organization_keyword_tags:
        body["q_organization_keyword_tags"] = q_organization_keyword_tags
    return await _post("api/v1/mixed_companies/search", body)


# ─── Convenience: enrich and upsert into company_knowledge ────────────────
async def enrich_company_into_knowledge(
    *,
    user_id: str,
    company_name: str,
    domain: str,
) -> dict[str, Any]:
    """One-shot: enrich a company via Apollo + insert a `company_knowledge`
    row tagged metadata.source='apollo_firmographics'.

    Returns:
        {
          "company_name": str,
          "rows_inserted": int,
          "credits_consumed": int,
          "firmographics": {industry, employees, revenue, founded_year, ...},
          "error": str | None,
        }

    Used by api/apollo.py (when wired) and persona_news_check.py's optional
    enrichment branch.
    """
    from db.client import get_supabase

    try:
        org = await enrich_organization(domain)
    except ApolloPlanBlocked as e:
        return {
            "company_name": company_name,
            "rows_inserted": 0,
            "credits_consumed": 0,
            "error": f"plan-blocked: {e}",
        }
    except ApolloError as e:
        logger.exception("Apollo enrich failed for %s", company_name)
        return {
            "company_name": company_name,
            "rows_inserted": 0,
            "credits_consumed": 0,
            "error": f"apollo-error: {e}",
        }

    org_data = org.get("organization") or {}
    if not org_data:
        # Apollo returns shape {"organization": null} when not found.
        return {
            "company_name": company_name,
            "rows_inserted": 0,
            "credits_consumed": 0,
            "firmographics": None,
        }

    # Distil the firmographic block we want to feed into the persona's RAG.
    firmographics = {
        "industry":             org_data.get("industry"),
        "estimated_num_employees": org_data.get("estimated_num_employees"),
        "annual_revenue":       org_data.get("annual_revenue_printed") or org_data.get("annual_revenue"),
        "founded_year":         org_data.get("founded_year"),
        "latest_funding_date":  org_data.get("latest_funding_round_date"),
        "latest_funding_amount": org_data.get("latest_funding_round_total_funding_printed"),
        "headquarters":         org_data.get("raw_address") or org_data.get("primary_address"),
        "current_technologies": [t.get("name") for t in (org_data.get("current_technologies") or []) if t.get("name")],
        "linkedin_url":         org_data.get("linkedin_url"),
        "website_url":          org_data.get("website_url"),
        "apollo_id":            org_data.get("id"),
    }

    # Write one row into company_knowledge tagged as apollo source.
    body_lines = [
        f"[firmographics] {company_name}",
        f"Industry: {firmographics['industry'] or 'unknown'}",
        f"Employees: {firmographics['estimated_num_employees'] or 'unknown'}",
        f"Revenue:   {firmographics['annual_revenue'] or 'unknown'}",
        f"Founded:   {firmographics['founded_year'] or 'unknown'}",
        f"HQ:        {firmographics['headquarters'] or 'unknown'}",
    ]
    if firmographics["current_technologies"]:
        body_lines.append(
            "Stack: " + ", ".join(firmographics["current_technologies"][:15])
        )
    content = "\n".join(body_lines)

    db = get_supabase()
    apollo_id = firmographics["apollo_id"] or "unknown"
    section_name = f"apollo_firmographics_{apollo_id[:12]}"

    db.table("company_knowledge").insert({
        "user_id":      user_id,
        "company_name": company_name,
        "section":      section_name,
        "content":      content,
        "source_url":   firmographics["website_url"] or f"https://{domain}",
        "metadata":     {
            "source":        "apollo_firmographics",
            "apollo_org_id": apollo_id,
            "firmographics": firmographics,
        },
    }).execute()

    return {
        "company_name":     company_name,
        "rows_inserted":    1,
        "credits_consumed": 1 if org_data else 0,
        "firmographics":    firmographics,
    }
