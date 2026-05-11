"""
agents/career_page_scraper.py — Weekly Apify-based career-page scrape.

Designed for JobScout v2's ground-truth layer. For each target company
with a `careers_url`, fire Apify's `rag-web-browser` actor (JS-rendered,
handles infinite scroll on modern SPAs like Workday/Lever/Ashby career
pages) and:

  1. Parse out individual job-posting URLs from the rendered page.
  2. Emit a `DiscoveredJob` per parsed URL with
     `source='apify_career_page'`.
  3. Caller (agents.job_scout_agent v2) feeds each into
     `agents.job_validation.validate_candidate` for the 7 safeguards.

We DON'T directly scrape JD pages here — that's per-URL validation work
done by job_validation. This module's job is "find all the URLs the
careers page links to".

Why weekly and not daily:
  - Most companies don't post jobs daily; daily rescrape wastes Apify
    credits.
  - The per-target Perplexity discovery (agents/perplexity_search.discover_jobs)
    is the fresh-daily layer; this is the ground-truth slower layer.
  - Combined: jobs found by BOTH sources get confidence_score 90
    (cross-source confirmation per job_validation._score_confidence).

Cost: typical ~$0.02 per page × 71 companies × weekly = ~$1.42/week
                                                       ≈ $6/mo.

ENV vars:
    APIFY_TOKEN          — required (raises if missing)
    APIFY_ACTOR_ID       — default `apify/rag-web-browser`
    APIFY_HTTP_TIMEOUT   — default 120s (scrape can take 30-90s)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from agents.job_validation import (
    ATS_DOMAIN_SUFFIXES,
    ATS_HOSTS,
    DiscoveredJob,
    LINKEDIN_JOB_PATTERN,
)

logger = logging.getLogger(__name__)


# ─── Config ────────────────────────────────────────────────────────────────
APIFY_BASE_URL = os.getenv("APIFY_BASE_URL", "https://api.apify.com/v2")
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify~rag-web-browser")
APIFY_HTTP_TIMEOUT_S = float(os.getenv("APIFY_HTTP_TIMEOUT", "120"))
APIFY_RUN_MAX_WAIT_S = float(os.getenv("APIFY_RUN_MAX_WAIT", "180"))


def _api_token() -> str:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "APIFY_TOKEN is not set. Sign up at apify.com and add the "
            "token to .env / Railway env."
        )
    return token


# ─── URL-extraction patterns ──────────────────────────────────────────────
# Match the kinds of URLs that look like single JD pages, not careers
# homes / search results.
JD_URL_PATTERNS = [
    # Greenhouse
    re.compile(r"https?://boards\.greenhouse\.io/[\w-]+/jobs/\d+", re.IGNORECASE),
    # Ashby
    re.compile(r"https?://jobs\.ashbyhq\.com/[\w-]+/[0-9a-f-]{12,}", re.IGNORECASE),
    # Lever
    re.compile(r"https?://jobs\.lever\.co/[\w-]+/[0-9a-f-]{12,}", re.IGNORECASE),
    # SmartRecruiters
    re.compile(r"https?://(api|jobs)\.smartrecruiters\.com/.+/(?:posting|jobs)/[\w-]+", re.IGNORECASE),
    # Workday — slug.myworkdayjobs.com/...
    re.compile(r"https?://[\w-]+\.myworkdayjobs\.com/[\w/-]+/job/[^?\s\"<]+", re.IGNORECASE),
    # LinkedIn jobs
    re.compile(r"https?://(www\.)?linkedin\.com/jobs/view/\d+", re.IGNORECASE),
    # Generic /jobs/{slug-or-id} or /careers/{slug-or-id} on company domain
    # (we validate the host later via domain whitelist)
    re.compile(r"https?://[\w.-]+/(?:jobs|careers|opportunities|openings)/[\w./-]+(?<!/)", re.IGNORECASE),
]

# Title patterns within plausible JD links (when the page provides
# anchor text near the URL).
TITLE_HINT_PATTERN = re.compile(
    r"(?i)(senior\s*product\s*manager|head\s*of\s*product|chief\s*product|"
    r"director\s*(?:of\s*)?product|VP\s*product|principal\s*(?:pm|product)|"
    r"product\s*manager|group\s*product\s*manager|technical\s*program\s*manager|"
    r"program\s*manager|product\s*lead|lead\s*product\s*manager)"
)


# ─── Apify run helpers ─────────────────────────────────────────────────────
async def _run_apify_actor(
    careers_url: str,
    *,
    actor_id: str = APIFY_ACTOR_ID,
) -> dict[str, Any]:
    """Trigger Apify actor and wait for the run to complete.

    Returns the dataset items (a list of pages). The rag-web-browser
    actor's default output shape is:
        [{"url": str, "markdown": str, "metadata": {...}}, ...]
    """
    token = _api_token()
    run_url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": token}
    body = {
        "query": careers_url,
        # Use the actor's default — small fan-out, JS rendered. Tunables
        # below override if needed.
        "maxResults": int(os.getenv("APIFY_MAX_RESULTS", "5")),
        "outputFormats": ["markdown", "html"],
        "removeElementsCssSelector": "nav, header, footer, .cookie-banner",
        "requestTimeoutSecs": int(APIFY_HTTP_TIMEOUT_S),
    }

    async with httpx.AsyncClient(timeout=APIFY_RUN_MAX_WAIT_S) as client:
        resp = await client.post(run_url, params=params, json=body)
        if resp.status_code != 201 and resp.status_code != 200:
            raise RuntimeError(
                f"Apify run-sync returned {resp.status_code}: "
                f"{resp.text[:300]}"
            )
        try:
            items = resp.json()
        except ValueError as e:
            raise RuntimeError(f"Apify response not valid JSON: {e}") from e
        if not isinstance(items, list):
            raise RuntimeError(
                f"Apify response shape unexpected: {type(items).__name__}"
            )
        return {"items": items}


# ─── URL extraction ───────────────────────────────────────────────────────
def _extract_jd_urls(
    page_markdown: str,
    page_html: str,
    company_hostname: str,
) -> list[tuple[str, str]]:
    """Return (url, title_hint) pairs found on the careers page.

    Strategy:
      1. Run each JD_URL_PATTERN against the markdown + html.
      2. For each match, scan ±200 chars for a TITLE_HINT_PATTERN match
         as the title (fallback to "unknown").
      3. Dedup by URL.
    """
    text = (page_markdown or "") + "\n\n" + (page_html or "")
    found: dict[str, str] = {}

    for pat in JD_URL_PATTERNS:
        for m in pat.finditer(text):
            url = m.group(0).rstrip(".,;)\"'>").strip()
            # For generic /jobs/{slug} match, require host == company host.
            # The pattern is greedy enough to match foreign hosts; filter here.
            host = (urlparse(url).hostname or "").lower()
            if host == company_hostname or host.endswith("." + company_hostname):
                ok = True
            elif host in ATS_HOSTS:
                ok = True
            elif any(host.endswith(s) for s in ATS_DOMAIN_SUFFIXES):
                ok = True
            elif host.endswith("linkedin.com") and LINKEDIN_JOB_PATTERN.match(
                urlparse(url).path or "/"
            ):
                ok = True
            else:
                ok = False
            if not ok:
                continue
            if url in found:
                continue
            # Look for a title hint near the URL match.
            window_start = max(0, m.start() - 200)
            window_end = min(len(text), m.end() + 200)
            window = text[window_start:window_end]
            title_match = TITLE_HINT_PATTERN.search(window)
            title = title_match.group(1) if title_match else "Product role"
            # Tidy title.
            title = re.sub(r"\s+", " ", title).strip()
            found[url] = title

    return [(url, title) for url, title in found.items()]


# ─── Public API ────────────────────────────────────────────────────────────
async def scrape_career_page(
    company_name: str,
    careers_url: str,
) -> list[DiscoveredJob]:
    """Scrape a target's careers page via Apify, return DiscoveredJob list.

    Each returned candidate has source='apify_career_page'. Caller is
    expected to feed into job_validation.validate_candidate for the
    safeguards.

    Cost: ~$0.02 per call. Wall-clock: 30-90s depending on page.
    """
    if not careers_url or not careers_url.startswith(("http://", "https://")):
        return []

    company_hostname = (urlparse(careers_url).hostname or "").lower()
    # Strip "www." so foo.com and www.foo.com both match.
    company_hostname = company_hostname.lstrip("www.").lstrip(".")

    try:
        result = await _run_apify_actor(careers_url)
    except Exception as e:
        logger.exception(
            "Apify scrape failed for %s (%s)", company_name, careers_url
        )
        return []

    candidates: list[DiscoveredJob] = []
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        markdown = item.get("markdown") or ""
        html = item.get("html") or ""
        pairs = _extract_jd_urls(markdown, html, company_hostname)
        for url, title in pairs:
            candidates.append(DiscoveredJob(
                url=url,
                title=title,
                company_name=company_name,
                source="apify_career_page",
                published_at=None,           # Apify doesn't give us posted_at
                snippet=None,
                raw={"apify_dataset_item_url": item.get("url")},
            ))

    # Dedup by URL (different items can yield the same URL).
    seen: set[str] = set()
    deduped: list[DiscoveredJob] = []
    for c in candidates:
        if c.url in seen:
            continue
        seen.add(c.url)
        deduped.append(c)

    logger.info(
        "career_page_scraper: %s → %d candidate URLs (apify)",
        company_name,
        len(deduped),
    )
    return deduped


async def scrape_all_targets(
    user_id: str,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Iterate all target_companies for a user; scrape each. Concurrency 3.

    Returns:
        {
            "companies_scraped": int,
            "total_candidates": int,
            "by_company": [{company_name, candidates_count, error?}, ...],
        }

    Used by the weekly cron AND by the on-demand /jobs/discover/all endpoint
    once wired.
    """
    import asyncio
    from db.client import get_supabase

    db = get_supabase()
    q = (
        db.table("companies")
        .select("id, name, careers_url, domain")
        .eq("user_id", str(user_id))
        .eq("is_target", True)
        .not_.is_("careers_url", None)
    )
    if limit:
        q = q.limit(limit)
    companies = q.execute().data or []

    sem = asyncio.Semaphore(3)

    async def _one(company: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            name = company.get("name") or ""
            careers_url = company.get("careers_url") or ""
            try:
                cands = await scrape_career_page(name, careers_url)
                return {
                    "company_name": name,
                    "candidates_count": len(cands),
                    "candidates": [
                        {"url": c.url, "title": c.title}
                        for c in cands
                    ],
                }
            except Exception as e:
                return {
                    "company_name": name,
                    "candidates_count": 0,
                    "error": f"{type(e).__name__}: {e}",
                }

    results = await asyncio.gather(*(_one(c) for c in companies))
    total = sum(r.get("candidates_count", 0) for r in results)
    return {
        "companies_scraped": len(companies),
        "total_candidates": total,
        "by_company": results,
    }


# ─── CLI for ad-hoc + weekly cron ─────────────────────────────────────────
def main() -> int:
    import argparse, asyncio, json, logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Scrape target-company careers pages via Apify."
    )
    parser.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Tenant (default = Rizwan)",
    )
    parser.add_argument("--company", help="Scrape a single company by name (skip --all)")
    parser.add_argument("--careers-url", help="Required when --company is set")
    parser.add_argument("--all", action="store_true", help="Iterate every target")
    parser.add_argument("--limit", type=int, default=None, help="Cap iteration count")
    args = parser.parse_args()

    if args.company:
        if not args.careers_url:
            print("ERROR: --company requires --careers-url")
            return 2
        cands = asyncio.run(scrape_career_page(args.company, args.careers_url))
        print(json.dumps({
            "company": args.company,
            "candidates_count": len(cands),
            "candidates": [{"url": c.url, "title": c.title} for c in cands],
        }, indent=2))
        return 0

    if args.all:
        out = asyncio.run(scrape_all_targets(args.user_id, limit=args.limit))
        print(json.dumps(out, indent=2))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
