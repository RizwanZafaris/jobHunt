"""
agents/multi_source_discovery.py — multi-platform job ingestion (G1-B).

Why this exists
---------------
Discovery was LinkedIn-shaped: JobScout searched LinkedIn, and the apply
pipeline filtered for `apply_url LIKE '%linkedin.com%'`. Two problems with
that at Director/CPO level:

  1. LinkedIn is the most adversarial channel — automated access violates its
     User Agreement, detection tightened through late 2025, and the failure
     mode is Easy Apply restriction on the account you are job-hunting with.
  2. Senior roles frequently never reach LinkedIn at all. They sit on the
     company's own board, or go out through search firms.

This module widens ingestion across boards via JobSpy (MIT), normalises
everything into the shape `jobs` already expects, and hands it to the
existing G5 scorer. Nothing downstream changes.

Board coverage, and why Bayt matters
-------------------------------------
JobSpy covers LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter and
**Bayt**. Bayt is the dominant GCC board — for a Dubai-based search targeting
UAE/Saudi/Qatar it carries roles the US-centric boards simply do not list.

Licensing
---------
JobSpy is MIT, so it is a clean dependency. This module deliberately does NOT
vendor code from the AGPL projects in this space (JustHireMe, open-resume,
ApplyPilot, Skyvern); their good ideas are reimplemented rather than copied,
because AGPL would virally relicense this repository — which holds personal
career data that should stay private.

Install
-------
    pip install python-jobspy
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from agents.linkedin_easy_apply import _location_allowed, _title_allowed
from db.client import get_supabase

logger = logging.getLogger(__name__)

# Boards JobSpy can query. LinkedIn is included but deliberately NOT default —
# see DEFAULT_SITES. Bayt is the GCC board and matters for a Dubai-based search.
SUPPORTED_SITES = (
    "indeed", "linkedin", "glassdoor", "google", "zip_recruiter", "bayt",
)

# Default board set. LinkedIn is omitted on purpose: it is the one channel
# where automated access carries account risk, and it is also the one the
# operator can search by hand. Pass sites=[...] explicitly to include it.
#
# Verified against a live sweep on 2026-09-15:
#   indeed     — works
#   google     — works
#   glassdoor  — HTTP 400 "location not parsed" on every call. Its parser
#                rejects "City, Country" strings like "Dubai, UAE".
#   bayt       — HTTP 403 Forbidden on every call. It also discards the
#                location entirely and hits /en/international/, so even
#                unblocked it would not give GCC-scoped results.
# Both are still selectable via sites=[...] in case upstream fixes them, but
# leaving them on by default just spends two minutes collecting errors.
DEFAULT_SITES = ("indeed", "google")

# Search locations matching the target markets in linkedin_easy_apply's
# ALLOWED_LOCATION_TOKENS. Each is issued as a separate JobSpy query, since
# the boards expect one location per call.
DEFAULT_LOCATIONS = (
    "Dubai, UAE",
    "Abu Dhabi, UAE",
    "Riyadh, Saudi Arabia",
    "Doha, Qatar",
    "Singapore",
    "London, United Kingdom",
    "Amsterdam, Netherlands",
    "Berlin, Germany",
    "New York, United States",
    "Remote",
)

DEFAULT_SEARCH_TERMS = (
    "Director of Product",
    "Head of Product",
    "VP Product",
    "Chief Product Officer",
    "Principal Product Manager",
    "Group Product Manager",
    "Technical Program Manager",
    "Director of Program Management",
)


@dataclass
class DiscoveryResult:
    fetched: int = 0
    after_filters: int = 0
    inserted: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    by_site: dict[str, int] = field(default_factory=dict)
    # The matching roles themselves. Always populated, whether or not they
    # were persisted — a sweep takes ~90s of network time and that result
    # must survive a database that is missing or misconfigured.
    rows: list[dict[str, Any]] = field(default_factory=list)


def _normalise_row(raw: dict[str, Any], user_id: str) -> Optional[dict[str, Any]]:
    """Map a JobSpy row onto the `jobs` table shape.

    JobSpy column names have shifted across releases, so each field probes a
    few aliases rather than pinning one. Returns None when the row lacks the
    minimum needed to be useful (title + a URL to apply through).
    """
    def pick(*names: str) -> Optional[str]:
        for n in names:
            v = raw.get(n)
            if v is not None and str(v).strip() and str(v).lower() != "nan":
                return str(v).strip()
        return None

    title = pick("title", "job_title")
    apply_url = pick("job_url", "job_url_direct", "url", "link")
    if not title or not apply_url:
        return None

    description = pick("description", "job_description") or ""
    # JobSpy returns markdown descriptions; cap to keep row size sane. The G5
    # scorers read the first ~6k chars anyway.
    description = description[:20000]

    return {
        "user_id": user_id,
        "title": title,
        "company": pick("company", "company_name") or "",
        "location": pick("location", "job_location") or "",
        "apply_url": apply_url,
        "description": description,
        "source": pick("site", "source") or "jobspy",
        "posted_at": pick("date_posted", "posted_date"),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


def _existing_apply_urls(user_id: str, urls: list[str]) -> set[str]:
    """Return the subset of `urls` already present in `jobs` for this user.

    Chunked because PostgREST rejects very long `in.()` filters, and a wide
    multi-board sweep easily produces several hundred URLs.
    """
    if not urls:
        return set()
    db = get_supabase()
    found: set[str] = set()
    CHUNK = 100
    for i in range(0, len(urls), CHUNK):
        batch = urls[i:i + CHUNK]
        try:
            rows = (
                db.table("jobs")
                .select("apply_url")
                .eq("user_id", user_id)
                .in_("apply_url", batch)
                .execute()
                .data
            ) or []
            found.update(r["apply_url"] for r in rows if r.get("apply_url"))
        except Exception as exc:
            logger.warning("multi_source_discovery: dedupe lookup failed: %r", exc)
    return found


def discover_jobs(
    *,
    user_id: UUID,
    search_terms: tuple[str, ...] = DEFAULT_SEARCH_TERMS,
    locations: tuple[str, ...] = DEFAULT_LOCATIONS,
    sites: tuple[str, ...] = DEFAULT_SITES,
    results_per_search: int = 25,
    hours_old: int = 168,
    apply_filters: bool = True,
    persist: bool = True,
) -> DiscoveryResult:
    """Sweep the configured boards and return matching roles.

    Applies the same geo/role filters the apply pipeline uses, so a role that
    could never be applied to is never stored in the first place.

    Matches are always returned on `result.rows`, whether or not they reach
    the database. Pass `persist=False` to skip Supabase entirely — useful for
    looking at what a search turns up before any credentials are configured.

    Scoring is NOT triggered here — that stays the caller's decision, because
    G5 costs roughly $0.15 per role and a wide sweep can return hundreds.
    """
    result = DiscoveryResult()

    try:
        from jobspy import scrape_jobs
    except ImportError:
        result.errors.append(
            "python-jobspy not installed. Run: pip install python-jobspy"
        )
        return result

    bad_sites = [s for s in sites if s not in SUPPORTED_SITES]
    if bad_sites:
        result.errors.append(f"Unsupported sites ignored: {bad_sites}")
        sites = tuple(s for s in sites if s in SUPPORTED_SITES)
    if not sites:
        result.errors.append("No valid sites to search.")
        return result

    uid = str(user_id)
    collected: list[dict[str, Any]] = []

    for term in search_terms:
        for location in locations:
            try:
                df = scrape_jobs(
                    site_name=list(sites),
                    search_term=term,
                    location=location,
                    results_wanted=results_per_search,
                    hours_old=hours_old,
                    description_format="markdown",
                )
            except Exception as exc:
                # One board failing (rate limit, layout change) must not abort
                # the whole sweep — record it and continue.
                msg = f"{term} @ {location}: {type(exc).__name__}: {exc}"
                logger.warning("multi_source_discovery: %s", msg)
                result.errors.append(msg[:200])
                continue

            if df is None or len(df) == 0:
                continue

            for raw in df.to_dict("records"):
                result.fetched += 1
                row = _normalise_row(raw, uid)
                if not row:
                    continue

                if apply_filters:
                    if not _location_allowed(row["location"]):
                        continue
                    if not _title_allowed(row["title"]):
                        continue

                result.after_filters += 1
                site = row.get("source") or "unknown"
                result.by_site[site] = result.by_site.get(site, 0) + 1
                collected.append(row)

    # Dedupe within this sweep.
    by_url: dict[str, dict[str, Any]] = {}
    for row in collected:
        by_url.setdefault(row["apply_url"], row)

    # Surface the matches BEFORE touching the database. A sweep is ~90s of
    # network time; losing it because Supabase is unset or unreachable is
    # the worst possible failure mode, and it is the one this hit on the
    # first real run (get_settings() raised on four missing env vars after
    # every board had already been searched).
    result.rows = list(by_url.values())

    if not collected:
        return result

    if not persist:
        return result

    # Everything past here is best-effort persistence. Failures are recorded
    # on the result and never raised: the caller keeps its roles either way.
    try:
        already = _existing_apply_urls(uid, list(by_url))
    except Exception as exc:
        logger.warning("multi_source_discovery: dedupe lookup failed: %r", exc)
        result.errors.append(
            f"db_unavailable: {type(exc).__name__}: {exc}"[:200]
        )
        return result

    fresh = [r for url, r in by_url.items() if url not in already]
    result.duplicates = len(by_url) - len(fresh)
    if not fresh:
        return result

    try:
        db = get_supabase()
    except Exception as exc:
        logger.warning("multi_source_discovery: supabase unavailable: %r", exc)
        result.errors.append(f"db_unavailable: {type(exc).__name__}: {exc}"[:200])
        return result

    CHUNK = 50
    for i in range(0, len(fresh), CHUNK):
        batch = fresh[i:i + CHUNK]
        try:
            inserted = db.table("jobs").insert(batch).execute().data or []
            result.inserted += len(inserted)
        except Exception as exc:
            logger.warning("multi_source_discovery: insert failed: %r", exc)
            result.errors.append(f"insert: {type(exc).__name__}: {exc}"[:200])

    logger.info(
        "multi_source_discovery: fetched=%d kept=%d inserted=%d dupes=%d by_site=%s",
        result.fetched, result.after_filters, result.inserted,
        result.duplicates, result.by_site,
    )
    return result
