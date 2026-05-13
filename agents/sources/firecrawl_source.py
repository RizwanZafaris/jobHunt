"""
agents/sources/firecrawl_source.py — Firecrawl pilot source adapter.

Pilot scope (BUG-049, 2026-05-13)
---------------------------------
Five enterprise career pages where the user manually spotted relevant
jobs that the existing Apify+httpx+LinkedIn pipeline missed:

    Visa        — https://www.visa.com/about-visa/working-at-visa/job-listings.html
    Mastercard  — https://careers.mastercard.com/us/en/search-results
    Stripe      — https://stripe.com/jobs/search
    Adyen       — https://careers.adyen.com/jobs
    Marqeta     — https://www.marqeta.com/about/careers

These sites are JS-rendered SPAs that don't expose a public Greenhouse/
Lever JSON endpoint. Apify can scrape them but pays for full DOM render
($0.02/page); Firecrawl is cheaper at ~$0.005/page AND returns clean
markdown ready for G5 letter_grade scoring without HTML-cleanup pass.

Why a separate adapter instead of folding into career_page_scraper.py?
----------------------------------------------------------------------
- Different cost profile: Firecrawl bills per credit, not per actor run.
- Different output shape: markdown + Pydantic-extracted fields vs raw HTML.
- Different cadence: pilot is daily-on-5-companies, not weekly-on-71.
- Independent kill switch: this pilot can be disabled by unsetting
  FIRECRAWL_API_KEY without affecting the existing Apify path.

Engineering principles
----------------------
1. **Budget cap** — every call increments a per-month counter in
   `firecrawl_spend` table; the source raises `FirecrawlBudgetError`
   if the projected cost would exceed `firecrawl_monthly_budget_usd`.
2. **Cost telemetry** — writes one `agent_call_log` row per scrape with
   graph='firecrawl_source', node='scrape', cost_usd from settings.
3. **Pydantic schema extraction** — every scrape returns a
   `ScrapedJobListing` with required fields; missing fields trigger
   `parse_warnings` rather than crashing.
4. **Idempotent dedup** — caller is responsible for `DiscoveredJob`
   dedup; this module just returns what Firecrawl found.

Public surface
--------------
    scrape_career_page(company: str, url: str) -> list[ScrapedJobListing]
    PILOT_COMPANIES: list[CareerPageTarget]
    FirecrawlBudgetError
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Pilot scope: 5 enterprise career pages ──────────────────────────────


@dataclass(frozen=True)
class CareerPageTarget:
    """A single career-page entry in the pilot."""
    company: str
    url: str
    notes: str = ""


PILOT_COMPANIES: list[CareerPageTarget] = [
    CareerPageTarget(
        company="Visa",
        url="https://www.visa.com/about-visa/working-at-visa/job-listings.html",
        notes="JS-rendered listing index; ATS is Workday-flavored.",
    ),
    CareerPageTarget(
        company="Mastercard",
        url="https://careers.mastercard.com/us/en/search-results",
        notes="Phenom People ATS, paginated.",
    ),
    CareerPageTarget(
        company="Stripe",
        url="https://stripe.com/jobs/search",
        notes="Custom React SPA; structured data available in __NEXT_DATA__.",
    ),
    CareerPageTarget(
        company="Adyen",
        url="https://careers.adyen.com/jobs",
        notes="Greenhouse JSON also available but listing page has richer context.",
    ),
    CareerPageTarget(
        company="Marqeta",
        url="https://www.marqeta.com/about/careers",
        notes="Greenhouse-backed; listing page is more reliable than the API for now.",
    ),
]


# ── Pydantic schema for Firecrawl extract API ───────────────────────────


class ScrapedJobListing(BaseModel):
    """One row Firecrawl extracted from a career-page listing.

    Field names match the `extract.schema` we send to Firecrawl so the
    LLM-side extraction can map directly.
    """
    title: Optional[str] = Field(None, description="Job title, e.g. 'Senior Product Manager, Payments'")
    location: Optional[str] = Field(None, description="City, country or 'Remote'.")
    department: Optional[str] = Field(None, description="Team or org, e.g. 'Engineering / Platform'.")
    posted_date: Optional[str] = Field(None, description="ISO date if visible, else None.")
    apply_url: Optional[str] = Field(None, description="Direct URL to the application page.")
    jd_excerpt: Optional[str] = Field(None, description="First 400 chars of the JD body.")
    salary_range: Optional[str] = Field(None, description="Comp range string if shown.")
    seniority: Optional[str] = Field(None, description="IC level / management band if shown.")
    # Provenance + warnings (not Firecrawl-supplied)
    source_company: str = ""
    source_url: str = ""
    scraped_at: str = ""
    parse_warnings: list[str] = Field(default_factory=list)


# ── Errors ──────────────────────────────────────────────────────────────


class FirecrawlBudgetError(RuntimeError):
    """Raised when a scrape would exceed `firecrawl_monthly_budget_usd`."""


class FirecrawlConfigError(RuntimeError):
    """Raised when FIRECRAWL_API_KEY is missing."""


# ── Budget tracking ─────────────────────────────────────────────────────


def _month_spend_usd() -> float:
    """Sum agent_call_log.cost_usd for the current calendar month for
    Firecrawl. Defensive — returns 0.0 on any DB error so a transient
    Supabase hiccup doesn't block the scrape.
    """
    try:
        from db.client import get_supabase

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = (
            get_supabase()
            .table("agent_call_log")
            .select("cost_usd")
            .eq("graph", "firecrawl_source")
            .gte("created_at", month_start.isoformat())
            .execute()
            .data
        ) or []
        return float(sum((r.get("cost_usd") or 0.0) for r in rows))
    except Exception as exc:
        logger.warning("[firecrawl] month-spend lookup failed (treating as $0): %s", exc)
        return 0.0


def _log_telemetry(
    *,
    company: str,
    url: str,
    cost_usd: float,
    latency_ms: float,
    status: str,
    error: Optional[str] = None,
) -> None:
    """One agent_call_log row per scrape — same shape as G6/G7/G8."""
    try:
        from db.client import get_supabase

        get_supabase().table("agent_call_log").insert({
            "graph": "firecrawl_source",
            "node": "scrape",
            "model": "firecrawl/v1",
            "provider": "firecrawl",
            "cost_usd": round(cost_usd, 6),
            "latency_ms": int(latency_ms),
            "status": status,
            "error": error,
            "input_summary": f"{company}:{url}"[:500],
            "user_id": os.environ.get("RIZWAN_USER_ID", "00000000-0000-0000-0000-000000000001"),
        }).execute()
    except Exception as exc:
        logger.debug("[firecrawl] telemetry write failed (non-fatal): %s", exc)


# ── Core scraping function ──────────────────────────────────────────────


_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "description": "All job listings visible on this career page.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "department": {"type": "string"},
                    "posted_date": {"type": "string"},
                    "apply_url": {"type": "string"},
                    "jd_excerpt": {"type": "string"},
                    "salary_range": {"type": "string"},
                    "seniority": {"type": "string"},
                },
            },
        }
    },
    "required": ["jobs"],
}


async def scrape_career_page(
    company: str,
    url: str,
    *,
    timeout_s: float = 60.0,
    enforce_budget: bool = True,
) -> list[ScrapedJobListing]:
    """Scrape one career-page index via Firecrawl.

    Returns a list of `ScrapedJobListing`, one per job Firecrawl extracted.
    Returns an empty list on parse failure (caller decides whether to
    retry / log).

    Raises:
        FirecrawlConfigError — if FIRECRAWL_API_KEY is unset.
        FirecrawlBudgetError — if the next call would exceed the monthly
                               budget cap and enforce_budget=True.
    """
    from config.settings import get_settings

    settings = get_settings()
    api_key = settings.firecrawl_api_key
    if not api_key:
        raise FirecrawlConfigError(
            "FIRECRAWL_API_KEY not set. Set it in .env or skip this source."
        )

    if enforce_budget:
        spent = _month_spend_usd()
        projected = spent + settings.firecrawl_cost_per_call_usd
        if projected > settings.firecrawl_monthly_budget_usd:
            raise FirecrawlBudgetError(
                f"Firecrawl budget cap hit: ${spent:.2f} spent + "
                f"${settings.firecrawl_cost_per_call_usd:.4f} would exceed "
                f"${settings.firecrawl_monthly_budget_usd:.2f}/mo. "
                f"Unblock by raising firecrawl_monthly_budget_usd."
            )

    endpoint = f"{settings.firecrawl_base_url.rstrip('/')}/scrape"
    payload = {
        "url": url,
        "formats": ["markdown", "extract"],
        "onlyMainContent": True,
        "waitFor": 2000,  # 2s for SPA JS hydration
        "extract": {
            "schema": _EXTRACT_SCHEMA,
            "prompt": (
                "Extract every job listing visible on the page. Return "
                "the structured JSON conforming to the schema. If a field "
                "isn't visible, omit it (don't fabricate)."
            ),
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = datetime.now(timezone.utc)
    t0 = started.timestamp()

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        latency_ms = (datetime.now(timezone.utc).timestamp() - t0) * 1000
        _log_telemetry(
            company=company,
            url=url,
            cost_usd=0.0,
            latency_ms=latency_ms,
            status="network_error",
            error=str(exc)[:300],
        )
        logger.error("[firecrawl] network error scraping %s: %s", url, exc)
        return []

    latency_ms = (datetime.now(timezone.utc).timestamp() - t0) * 1000

    if resp.status_code != 200:
        _log_telemetry(
            company=company,
            url=url,
            cost_usd=0.0,
            latency_ms=latency_ms,
            status=f"http_{resp.status_code}",
            error=resp.text[:300],
        )
        logger.error(
            "[firecrawl] %s -> HTTP %s: %s",
            url,
            resp.status_code,
            resp.text[:200],
        )
        return []

    try:
        body = resp.json()
    except Exception as exc:
        _log_telemetry(
            company=company,
            url=url,
            cost_usd=0.0,
            latency_ms=latency_ms,
            status="bad_json",
            error=str(exc)[:300],
        )
        logger.error("[firecrawl] bad JSON from %s: %s", url, exc)
        return []

    if not body.get("success"):
        _log_telemetry(
            company=company,
            url=url,
            cost_usd=0.0,
            latency_ms=latency_ms,
            status="firecrawl_error",
            error=str(body.get("error") or body)[:300],
        )
        logger.warning("[firecrawl] success=false for %s: %s", url, body.get("error"))
        return []

    # Charge the cost-per-call (we count one credit regardless of
    # how many jobs were extracted — that's Firecrawl's billing model).
    _log_telemetry(
        company=company,
        url=url,
        cost_usd=settings.firecrawl_cost_per_call_usd,
        latency_ms=latency_ms,
        status="success",
    )

    data = body.get("data") or {}
    extracted = (data.get("extract") or {}).get("jobs") or []

    out: list[ScrapedJobListing] = []
    iso_now = started.isoformat()
    for raw in extracted:
        if not isinstance(raw, dict):
            continue
        try:
            listing = ScrapedJobListing(
                title=raw.get("title"),
                location=raw.get("location"),
                department=raw.get("department"),
                posted_date=raw.get("posted_date"),
                apply_url=raw.get("apply_url"),
                jd_excerpt=raw.get("jd_excerpt"),
                salary_range=raw.get("salary_range"),
                seniority=raw.get("seniority"),
                source_company=company,
                source_url=url,
                scraped_at=iso_now,
                parse_warnings=[],
            )
        except Exception as exc:
            logger.debug("[firecrawl] row coercion failed: %s; raw=%r", exc, raw)
            continue

        if not listing.title and not listing.apply_url:
            listing.parse_warnings.append("no title or apply_url")
        out.append(listing)

    logger.info(
        "[firecrawl] %s (%s) -> %d listings, $%.4f, %dms",
        company,
        url,
        len(out),
        settings.firecrawl_cost_per_call_usd,
        int(latency_ms),
    )
    return out


async def scrape_pilot() -> dict[str, list[ScrapedJobListing]]:
    """Scrape all 5 PILOT_COMPANIES sequentially.

    Returns a {company: [listings...]} map. Continues on per-company
    failures so one timeout doesn't tank the batch. Halts the rest of
    the batch on FirecrawlBudgetError.
    """
    out: dict[str, list[ScrapedJobListing]] = {}
    for target in PILOT_COMPANIES:
        try:
            out[target.company] = await scrape_career_page(
                target.company, target.url
            )
        except FirecrawlBudgetError as exc:
            logger.warning("[firecrawl] budget hit; halting pilot: %s", exc)
            break
        except FirecrawlConfigError as exc:
            logger.error("[firecrawl] config error: %s", exc)
            break
        except Exception as exc:
            logger.error("[firecrawl] %s failed: %s", target.company, exc)
            out[target.company] = []
    return out
