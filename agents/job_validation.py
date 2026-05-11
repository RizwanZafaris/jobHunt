"""
agents/job_validation.py — JobScout v2 quality safeguards.

The 7 safeguards (per the 2026-05-11 design decision):

  1. URL existence check        — httpx.head() rejects 404/410/timeout
  2. Domain whitelist match     — hostname must be target_company.domain OR
                                   a known ATS host OR linkedin.com/jobs/view/
  3. JD content fingerprint     — fetched page must contain ≥2 of: apply CTA,
                                   requirements section, salary/location,
                                   title in <h1>/<title>
  4. Expiry phrase scan         — body scanned against 12-phrase blacklist
  5. Cross-source confirmation  — discovery_sources jsonb drives confidence
  6. Freshness window           — published_at >90d → freshness='stale'
                                   (downscores but doesn't drop)
  7. Archetype pre-filter       — regex-check title before sending to scorer

Confidence-score scoring rules (Q1 = DROP if < 50):

    multi-source (2+ sources)       → 90
    ATS API direct (greenhouse etc) → 85
    Apify career-page only          → 80
    Perplexity + content fingerprint → 70
    Perplexity URL-only (no content) → 50  (threshold)
    Anything below                   → DROPPED

Stale freshness subtracts 15.

This module is **pure** — it does not touch the DB. The caller
(agents/job_scout_agent.py v2) handles persistence; here we just
classify candidates. Keeps the module unit-testable.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ─── Tunables ──────────────────────────────────────────────────────────────
HTTP_TIMEOUT_S = 5.0          # URL existence check
FETCH_TIMEOUT_S = 10.0        # content fingerprint
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Known applicant-tracking-system hosts. URLs on these hosts are treated as
# valid JD locations regardless of which target company they're for —
# because a target may use an external ATS (e.g. boards.greenhouse.io/marqeta).
ATS_HOSTS = {
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "api.smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "recruiting.adp.com",
}

# Workday is special — every customer has their own *.myworkdayjobs.com.
ATS_DOMAIN_SUFFIXES = (
    ".myworkdayjobs.com",
    ".my.workday.com",
    ".workday.com",
)

# LinkedIn jobs are at /jobs/view/{id}; any *.linkedin.com on /jobs/ is OK.
LINKEDIN_JOB_PATTERN = re.compile(r"^/jobs/view/", re.IGNORECASE)

# Phrases that signal an expired listing — extends the existing
# `EXPIRY_SIGNALS` list in agents/job_scout_agent.py so the two stay aligned.
EXPIRY_PHRASES = [
    "this job is no longer accepting applications",
    "this job has expired",
    "position has been filled",
    "this listing has expired",
    "job listing not found",
    "no longer available",
    "this role has been filled",
    "application closed",
    "vacancy closed",
    "posting has been removed",
    "job posting is no longer active",
    "this position has been closed",
    "this position is closed",
    "this opening is closed",
    "we are no longer accepting applications",
]

# JD-page content fingerprints. Each is a coarse regex; we require ≥2 matches.
JD_FINGERPRINTS = [
    re.compile(r"\bapply\s*(now)?\b", re.IGNORECASE),
    re.compile(r"submit\s*(your)?\s*application", re.IGNORECASE),
    re.compile(r"\b(qualifications|requirements|responsibilities|what\s*you'?ll\s*do)\b", re.IGNORECASE),
    re.compile(r"\$\s*\d{2,3}\s*[Kk]?\s*[-–]\s*\$?\s*\d{2,3}\s*[Kk]?"),   # salary range
    re.compile(r"location\s*[:|]", re.IGNORECASE),
    re.compile(r"(remote|hybrid|on-?site)", re.IGNORECASE),
    re.compile(r"<title>[^<]*(manager|engineer|director|head\s*of|lead|product|principal)[^<]*</title>", re.IGNORECASE),
]

# Freshness buckets.
FRESH_DAYS = 30
RECENT_DAYS = 90

# Confidence-score thresholds.
MIN_CONFIDENCE_THRESHOLD = 50  # below this → DROP (per Q1=A decision)

# Default archetype regex if no persona-specific list is provided.
DEFAULT_ARCHETYPE_PATTERN = re.compile(
    r"(?i)(product\s*manager|head\s*of\s*product|chief\s*product|cpo\b|"
    r"director.*product|VP\s*product|vice\s*president\s*product|"
    r"principal\s*(pm|product)|senior\s*(pm|product)|"
    r"group\s*product\s*manager|gpm\b|"
    r"product\s*lead|technical\s*program\s*manager|tpm\b|"
    r"program\s*manager|pmo)"
)


# ─── Data classes ──────────────────────────────────────────────────────────
@dataclass
class DiscoveredJob:
    """A raw candidate from a discovery source before validation."""
    url: str
    title: str
    company_name: str               # the target company name we discovered this FOR
    source: str                     # 'perplexity_sonar' | 'ats_greenhouse' | 'apify_career_page' | 'linkedin'
    published_at: Optional[str] = None  # ISO date string if known
    snippet: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidatedJob:
    """A candidate that passed all 7 safeguards. Ready to insert into `jobs`."""
    url: str                         # final URL after redirects
    original_url: str                # what the source returned (for audit)
    title: str
    company_name: str
    confidence_score: float          # 0..100
    discovery_sources: list[str]     # all sources that found this job
    freshness: str                   # 'fresh' | 'recent' | 'stale'
    published_at: Optional[str]
    validated_at: datetime
    body_excerpt: str                # first 500 chars of fetched body (audit + dedup)


@dataclass
class ValidationResult:
    """Outcome of validating one candidate.

    Exactly one of `validated` and `rejected_reason` is set.
    `closed` is set when we detected an expired listing.
    """
    candidate: DiscoveredJob
    validated: Optional[ValidatedJob] = None
    rejected_reason: Optional[str] = None       # short code: url_not_resolvable, domain_mismatch, ...
    closed: bool = False                         # expiry phrase detected
    detail: str = ""


# ─── Safeguard helpers ─────────────────────────────────────────────────────
def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _path(url: str) -> str:
    try:
        return urlparse(url).path or "/"
    except Exception:
        return "/"


def _domain_in_whitelist(
    final_url: str,
    target_company_domains: list[str],
) -> bool:
    """Safeguard #2 — domain whitelist match.

    Hostname must match either:
      - One of the target company's domains (from `companies.domain`)
      - A known ATS host (greenhouse, ashby, lever, smartrecruiters, adp)
      - *.myworkdayjobs.com (Workday)
      - linkedin.com/jobs/view/* (LinkedIn jobs only)
    """
    host = _hostname(final_url)
    if not host:
        return False

    # LinkedIn — narrow to /jobs/view/
    if host.endswith("linkedin.com"):
        return bool(LINKEDIN_JOB_PATTERN.match(_path(final_url)))

    # Known ATS hosts.
    if host in ATS_HOSTS:
        return True

    # Workday and other host-suffix ATS providers.
    if any(host.endswith(s) for s in ATS_DOMAIN_SUFFIXES):
        return True

    # Target company domains.
    for d in target_company_domains:
        if not d:
            continue
        d_clean = d.lower().lstrip("www.").lstrip(".")
        if host == d_clean or host.endswith("." + d_clean):
            return True

    return False


def _has_jd_fingerprint(body: str) -> bool:
    """Safeguard #3 — JD content fingerprint.

    Page body must match ≥2 fingerprints. Cheap and effective.
    """
    matches = sum(1 for p in JD_FINGERPRINTS if p.search(body))
    return matches >= 2


def _has_expiry_phrase(body: str) -> Optional[str]:
    """Safeguard #4 — expiry phrase scan.

    Returns the matched phrase, or None.
    """
    lowered = body.lower()
    for phrase in EXPIRY_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def _matches_archetype(
    title: str,
    persona_archetypes: Optional[list[str]] = None,
) -> bool:
    """Safeguard #7 — archetype pre-filter.

    If persona_archetypes is provided, build a regex from it.
    Otherwise use the default product-manager-family pattern.
    """
    if not title:
        return False
    if persona_archetypes:
        joined = "|".join(re.escape(a) for a in persona_archetypes)
        pattern = re.compile(f"(?i)({joined})")
        return bool(pattern.search(title))
    return bool(DEFAULT_ARCHETYPE_PATTERN.search(title))


def _compute_freshness(
    published_at_iso: Optional[str],
    body: str,
) -> str:
    """Safeguard #6 — freshness window.

    Buckets: fresh (<30d) / recent (30-90d) / stale (>90d).
    If published_at is missing, try to parse <meta article:published_time>;
    otherwise default to 'recent'.
    """
    now = datetime.now(timezone.utc)

    parsed: Optional[datetime] = None
    if published_at_iso:
        try:
            parsed = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    if parsed is None:
        m = re.search(
            r'<meta\s+property="article:published_time"\s+content="([^"]+)"',
            body or "",
            re.IGNORECASE,
        )
        if m:
            try:
                parsed = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

    if parsed is None:
        return "recent"  # unknown → assume recent, downscore later if needed

    age = (now - parsed).days
    if age <= FRESH_DAYS:
        return "fresh"
    if age <= RECENT_DAYS:
        return "recent"
    return "stale"


def _score_confidence(
    sources: list[str],
    fingerprint_passed: bool,
    freshness: str,
) -> float:
    """Safeguard #5 — cross-source confidence scoring.

    Rules:
      - 2+ sources                  → 90
      - ATS-API direct (single)     → 85
      - Apify career-page (single)  → 80
      - Perplexity + fingerprint    → 70
      - Perplexity URL-only         → 50
      - Stale freshness             → -15

    Floor at 0, cap at 100.
    """
    n_sources = len(set(sources))
    score = 0.0

    if n_sources >= 2:
        score = 90.0
    elif any(s.startswith("ats_") for s in sources):
        score = 85.0
    elif "apify_career_page" in sources:
        score = 80.0
    elif "perplexity_sonar" in sources:
        score = 70.0 if fingerprint_passed else 50.0
    elif "linkedin" in sources:
        score = 75.0 if fingerprint_passed else 55.0
    else:
        score = 40.0  # unknown source — DROP

    if freshness == "stale":
        score -= 15

    return max(0.0, min(100.0, score))


# ─── HTTP probes (safeguards #1, #3, #4 hit the network) ──────────────────
async def _http_head_or_get(url: str) -> tuple[int, str, str]:
    """Returns (status_code, final_url_after_redirects, body_or_empty).

    HEAD first; falls back to GET if HEAD returns 405/501. For 200 we also
    GET the body (truncated to 200kb) for fingerprint + expiry scans.
    """
    headers = DEFAULT_HEADERS
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers=headers,
        ) as client:
            try:
                head = await client.head(url)
                if head.status_code not in (405, 501) and head.status_code != 200:
                    return head.status_code, str(head.url), ""
            except (httpx.HTTPError, ValueError):
                pass
            # Need body for fingerprint / expiry. Use longer timeout.
            resp = await client.get(url, timeout=FETCH_TIMEOUT_S)
            body = (resp.text or "")[:200_000]  # cap at 200kb
            return resp.status_code, str(resp.url), body
    except httpx.TimeoutException:
        return 0, url, ""  # 0 = network timeout
    except httpx.HTTPError as e:
        logger.debug("http error on %s: %s", url, e)
        return 0, url, ""
    except Exception as e:
        logger.debug("unexpected error on %s: %s: %s", url, type(e).__name__, e)
        return 0, url, ""


# ─── Public API ────────────────────────────────────────────────────────────
async def validate_candidate(
    candidate: DiscoveredJob,
    *,
    target_company_domains: list[str],
    persona_archetypes: Optional[list[str]] = None,
) -> ValidationResult:
    """Run all 7 safeguards on one candidate.

    Returns a ValidationResult. Caller decides what to do based on
    `result.validated` (insert) vs `result.rejected_reason` (drop) vs
    `result.closed` (mark posting_closed_at).
    """
    # Safeguard #7 — archetype pre-filter (free, do first)
    if not _matches_archetype(candidate.title, persona_archetypes):
        return ValidationResult(
            candidate=candidate,
            rejected_reason="archetype_mismatch",
            detail=f"title '{candidate.title[:60]}' does not match archetypes",
        )

    # Safeguard #1 — URL existence
    status, final_url, body = await _http_head_or_get(candidate.url)
    if status in (404, 410):
        return ValidationResult(
            candidate=candidate,
            rejected_reason="url_not_resolvable",
            detail=f"http {status} on {candidate.url}",
        )
    if status == 0:
        return ValidationResult(
            candidate=candidate,
            rejected_reason="url_timeout_or_error",
            detail=f"network failure on {candidate.url}",
        )
    if status >= 500:
        return ValidationResult(
            candidate=candidate,
            rejected_reason="upstream_error",
            detail=f"http {status} on {candidate.url} (retry later)",
        )

    # Safeguard #2 — domain whitelist
    if not _domain_in_whitelist(final_url, target_company_domains):
        return ValidationResult(
            candidate=candidate,
            rejected_reason="domain_mismatch",
            detail=f"final host {_hostname(final_url)} not in whitelist for {candidate.company_name}",
        )

    # Safeguard #3 — JD content fingerprint
    fingerprint_passed = _has_jd_fingerprint(body) if body else False
    if not fingerprint_passed:
        # Allow Perplexity URL-only candidates to slip through with lower
        # confidence — they'll score 50 and may still pass. But pure
        # garbage pages get caught here.
        if "perplexity_sonar" not in candidate.source.lower():
            return ValidationResult(
                candidate=candidate,
                rejected_reason="not_a_jd",
                detail="page resolved but no JD fingerprints matched",
            )

    # Safeguard #4 — expiry phrase scan
    expiry = _has_expiry_phrase(body) if body else None
    if expiry:
        return ValidationResult(
            candidate=candidate,
            closed=True,
            rejected_reason="posting_closed",
            detail=f"matched expiry phrase: '{expiry}'",
        )

    # Safeguard #6 — freshness
    freshness = _compute_freshness(candidate.published_at, body)

    # Safeguard #5 — cross-source confidence
    confidence = _score_confidence([candidate.source], fingerprint_passed, freshness)
    if confidence < MIN_CONFIDENCE_THRESHOLD:
        return ValidationResult(
            candidate=candidate,
            rejected_reason="low_confidence",
            detail=f"confidence={confidence:.1f} < {MIN_CONFIDENCE_THRESHOLD}",
        )

    return ValidationResult(
        candidate=candidate,
        validated=ValidatedJob(
            url=final_url,
            original_url=candidate.url,
            title=candidate.title,
            company_name=candidate.company_name,
            confidence_score=confidence,
            discovery_sources=[candidate.source],
            freshness=freshness,
            published_at=candidate.published_at,
            validated_at=datetime.now(timezone.utc),
            body_excerpt=body[:500] if body else "",
        ),
    )


def merge_validated_with_existing(
    new: ValidatedJob,
    existing_sources: list[str],
    existing_confidence: Optional[float],
) -> tuple[list[str], float]:
    """When the same URL is discovered via a different source, merge.

    Returns (deduped_sources, new_confidence). Used by JobScout v2 to
    bump confidence when a Perplexity-discovered URL is later confirmed
    by Apify career-page scrape.
    """
    merged = sorted(set((existing_sources or []) + new.discovery_sources))
    confidence = _score_confidence(merged, fingerprint_passed=True, freshness=new.freshness)
    if existing_confidence is not None:
        confidence = max(confidence, existing_confidence)
    return merged, confidence
