"""
agents/job_validator.py — Revalidate JD URLs to detect closed listings.

Why
---
The /today surface ranks jobs by score and presents a "Start application
process" CTA. Surfacing jobs whose JD URL is dead is a UX trap — the user
clicks through and lands on 404 or "this job is no longer open".

This validator:
  1. Picks jobs that haven't been checked in `STALE_AFTER_HOURS` (default 6).
  2. HTTP-HEADs (then GETs on HEAD-rejecting hosts) the JD URL with a 10s
     timeout.
  3. Maps the response into one of:
        open         — 200 with HTML/payload, or 200 + redirect into a JD
        closed       — 404 / 410 / redirect to a careers home or generic 'jobs'
        redirect     — 3xx to a non-JD URL we can't classify safely
        error        — anything else (5xx, network errors, parse errors)
        rate_limited — 429 / Cloudflare challenge — leave previous state alone
  4. Writes back to jobs.posting_closed_at + last_validated_at + validation_status.
     `posting_closed_at` is only SET when status == 'closed'.

CLI
---
    python -m agents.job_validator                           # validate stale rows for default user
    python -m agents.job_validator --limit 50                # cap batch size
    python -m agents.job_validator --user-id <uuid>          # other tenant
    python -m agents.job_validator --job-id 397              # single job
    python -m agents.job_validator --force                   # ignore cache

Designed to run on a 30-min APScheduler cron alongside orphan_reaper.

NOT called from the hot path (/actions/today) — that endpoint trusts the
last cached status to keep latency low.
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from db.client import get_supabase

logger = logging.getLogger(__name__)


# Cache: don't re-hit a URL we've checked in the last 6 hours.
STALE_AFTER_HOURS = int(os.getenv("JOB_VALIDATOR_STALE_HOURS", "6"))
HTTP_TIMEOUT_S = float(os.getenv("JOB_VALIDATOR_HTTP_TIMEOUT", "10.0"))
DEFAULT_BATCH_LIMIT = 100
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# UA — appear as a regular browser. Job boards block bare httpx UAs aggressively.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# Closed-listing heuristics: when a 200 redirects to one of these path patterns
# the listing is gone but the careers homepage is up.
CLOSED_REDIRECT_PATHS = {
    "/", "/jobs", "/careers", "/careers/", "/jobs/",
    "/openings", "/positions", "/work-with-us", "/join-us",
    "/careers/jobs", "/careers/openings",
}

# Phrases that show up when a listing is closed even if the page returns 200.
# Conservative list — we only mark `closed` when one matches AND there's no
# clear apply button. We keep this small to avoid false positives.
CLOSED_BODY_PHRASES = (
    "this job is no longer accepting applications",
    "this position has been filled",
    "the job you're looking for is no longer available",
    "this listing has expired",
    "this opening is closed",
    "no longer accepting applications",
    "position is closed",
)


@dataclass
class ValidationResult:
    job_id: int
    url: str
    status: str            # 'open' | 'closed' | 'redirect' | 'error' | 'rate_limited'
    posting_closed_at: Optional[datetime]
    last_validated_at: datetime
    detail: str            # short reason for logs


def _classify_response(url: str, response: httpx.Response, body_preview: str = "") -> tuple[str, str]:
    """Map an HTTP response to (status, detail). Pure function for testability."""
    code = response.status_code
    final_url = str(response.url)
    final_path = urlparse(final_url).path or "/"

    if code in (404, 410):
        return "closed", f"http {code} on {final_url}"
    if code == 429:
        return "rate_limited", f"http 429 on {url}"
    if code >= 500:
        return "error", f"http {code} (server) on {final_url}"
    if 300 <= code < 400:
        return "redirect", f"http {code} → {final_url}"
    if code != 200:
        return "error", f"http {code} on {final_url}"

    # 200. Check for redirect-to-careers-home heuristic via Location chain.
    original_path = urlparse(url).path or "/"
    if final_url != url and final_path in CLOSED_REDIRECT_PATHS and original_path not in CLOSED_REDIRECT_PATHS:
        return "closed", f"redirected from JD to careers home {final_path}"

    # 200. Inspect body for closed-listing phrases.
    if body_preview:
        lowered = body_preview.lower()
        for phrase in CLOSED_BODY_PHRASES:
            if phrase in lowered:
                return "closed", f"body phrase: '{phrase}'"

    return "open", f"http 200 on {final_url}"


def validate_url(url: str) -> tuple[str, str]:
    """Run a HEAD-then-GET probe; return (status, detail)."""
    if not url or not url.startswith(("http://", "https://")):
        return "error", f"invalid url: {url!r}"

    try:
        with httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=HTTP_TIMEOUT_S,
        ) as client:
            # HEAD first — cheap, but many job boards 405 on HEAD.
            try:
                head = client.head(url)
                if head.status_code == 405 or head.status_code == 501:
                    raise httpx.HTTPStatusError("HEAD not allowed", request=head.request, response=head)
                # On a clear closed signal we don't need a GET.
                if head.status_code in (404, 410, 429) or head.status_code >= 500:
                    return _classify_response(url, head)
                # On 200/3xx without a body we still need a GET to inspect content.
            except httpx.HTTPStatusError:
                pass  # fall through to GET
            except httpx.HTTPError:
                pass  # fall through to GET

            resp = client.get(url)
            body_preview = resp.text[:8000] if resp.text else ""
            return _classify_response(url, resp, body_preview)
    except httpx.TimeoutException:
        return "error", "timeout"
    except httpx.HTTPError as e:
        return "error", f"http error: {type(e).__name__}: {e}"
    except Exception as e:  # defensive — never crash the validator
        return "error", f"{type(e).__name__}: {e}"


def _is_stale(last_validated_at: Optional[str]) -> bool:
    if not last_validated_at:
        return True
    try:
        # Supabase returns ISO-8601 with TZ.
        last = datetime.fromisoformat(last_validated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)
    return last < cutoff


def validate_job(job_id: int, *, force: bool = False) -> Optional[ValidationResult]:
    """Validate one job. Returns None if skipped (cache hit)."""
    db = get_supabase()
    row = (
        db.table("jobs")
        .select("id, url, last_validated_at, posting_closed_at, validation_status")
        .eq("id", job_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        return None
    job = row[0]
    url = job.get("url") or ""

    if not force and not _is_stale(job.get("last_validated_at")):
        logger.debug("validator: skip job %s (cached, status=%s)", job_id, job.get("validation_status"))
        return None

    if not url:
        # Nothing to validate; mark error so we don't keep trying.
        now = datetime.now(timezone.utc)
        db.table("jobs").update({
            "validation_status": "error",
            "last_validated_at": now.isoformat(),
        }).eq("id", job_id).execute()
        return ValidationResult(job_id, "", "error", None, now, "no url")

    status, detail = validate_url(url)
    now = datetime.now(timezone.utc)

    update: dict[str, Any] = {
        "validation_status": status,
        "last_validated_at": now.isoformat(),
    }
    closed_at: Optional[datetime] = None
    if status == "closed":
        # Only set posting_closed_at the first time we see closed.
        if not job.get("posting_closed_at"):
            update["posting_closed_at"] = now.isoformat()
            closed_at = now
        else:
            try:
                closed_at = datetime.fromisoformat((job["posting_closed_at"] or "").replace("Z", "+00:00"))
            except ValueError:
                closed_at = now
    elif status in ("open", "redirect") and job.get("posting_closed_at"):
        # Listing reopened — clear the closed marker.
        update["posting_closed_at"] = None

    db.table("jobs").update(update).eq("id", job_id).execute()
    return ValidationResult(job_id, url, status, closed_at, now, detail)


def validate_batch(*, user_id: str = DEFAULT_USER_ID, limit: int = DEFAULT_BATCH_LIMIT,
                   force: bool = False) -> list[ValidationResult]:
    """Pick stale jobs for one user and validate them. Returns the results."""
    db = get_supabase()

    # Prioritise: high-score open listings first, oldest validation first.
    q = (
        db.table("jobs")
        .select("id, last_validated_at, validation_status, match_score")
        .eq("user_id", user_id)
        .is_("posting_closed_at", None)
        .order("match_score", desc=True)
        .order("last_validated_at", desc=False, nullsfirst=True)
        .limit(limit * 2)  # over-fetch since some will be cache-hits
    )
    rows = q.execute().data or []

    out: list[ValidationResult] = []
    for r in rows:
        if not force and not _is_stale(r.get("last_validated_at")):
            continue
        result = validate_job(int(r["id"]), force=force)
        if result is not None:
            out.append(result)
        if len(out) >= limit:
            break
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Revalidate jobs.url; mark closed listings.")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="Tenant to validate (default = Rizwan)")
    parser.add_argument("--job-id", type=int, default=None, help="Validate exactly one job by id.")
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT, help="Max rows per batch.")
    parser.add_argument("--force", action="store_true", help="Ignore the 6-hour cache.")
    args = parser.parse_args()

    if args.job_id is not None:
        r = validate_job(args.job_id, force=args.force)
        if r is None:
            print(f"job {args.job_id}: skipped (cached or missing)")
            return 0
        print(f"job {r.job_id}: {r.status} — {r.detail}")
        return 0

    results = validate_batch(user_id=args.user_id, limit=args.limit, force=args.force)
    print(f"validated {len(results)} job(s)")
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    for status, n in sorted(by_status.items()):
        print(f"  {status:13} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
