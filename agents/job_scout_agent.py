"""
JobScoutAgent — finds new job postings using GPT-4.1 + Serper + ATS APIs.
Runs daily at 09:00 Dubai time.

Bug fixes (May 2026):
  - Expired job detection: HTTP 404 check + content-based expiry signals
  - ATS company list now loaded from portals.yml (not hardcoded)
  - Ashby GraphQL API properly implemented
  - Serper queries updated to current year + Gulf portal queries
  - Deduplication by company+title+location (not just URL)
  - ATS scan rotates by priority (P1 daily, P2 every 2 days, P3 every 5 days)
  - Workday scraping added for banks/traditional companies
  - Jobs marked expired in DB when URL returns 404
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime
from typing import Optional
import httpx
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.base_agent import BaseAgent
from config.settings import get_settings
from db.client import upsert_job, upsert_company, get_company_by_name, get_supabase

logger = logging.getLogger(__name__)


# ─── JobScout v2 helpers (2026-05-11; v1 deleted 2026-05-12) ───────────────
# The USE_JOBSCOUT_V2_DISCOVERY env flag and the legacy v1 URL-only validator
# were removed on 2026-05-12 — v2 (Perplexity per-target + 7 safeguards) is
# now the only discovery + validation path. Setting the env var has no effect.
# 2026-05-12: hard geo allowlist applied BEFORE the GPT-4.1 scoring step.
# JobScout v1 logged scoring weights for geo but didn't pre-filter, so the
# ATS scan (e.g. Stripe's 491 Greenhouse jobs) flooded scoring with US
# Dublin/SF/Remote roles. With this filter the scoring step only ever sees
# jobs whose location is empty (unknown — give scoring a chance), explicitly
# Remote (geo-flexible), or in one of the user's named target countries.
#
# Match is substring-insensitive against location/title/description, so
# "Dubai, UAE" and "Hybrid - Singapore" both pass; "Dublin, Ireland" and
# "New York, NY" both drop. Empty-string locations (Perplexity sometimes
# doesn't extract one) are PASSED so the GPT-4.1 scorer gets a chance.
TARGET_LOCATION_TOKENS = (
    "dubai", "abu dhabi", "uae", "united arab emirates",
    "saudi arabia", "riyadh", "jeddah", "ksa",
    "qatar", "doha",
    "bahrain", "manama",
    "london", "united kingdom", " uk ", ", uk", "uk)",
    "singapore",
    "remote", "hybrid",
)


def _is_target_geo(job: dict) -> bool:
    """Pre-filter on user's target geos (UAE/KSA/Qatar/UK/Singapore + Remote).

    Returns True if the job's location/title/description contains any
    target geo token — case-insensitive substring match. Returns True
    when location is empty so unknown-geo jobs aren't auto-dropped (let
    the scoring step decide based on title + description).
    """
    loc = (job.get("location") or "").strip().lower()
    if not loc:
        # Unknown location → don't drop here; let scoring evaluate.
        return True
    # Some ATS feeds glue multiple locations together ("US-Remote, Singapore");
    # any token match is good enough.
    return any(tok in loc for tok in TARGET_LOCATION_TOKENS)


def _classify_serper_source(url: str) -> str:
    """Map a Serper-discovered URL to a discovery_sources tag.

    Used so the validation pipeline can credit cross-source confirmations:
    a job found via both Serper-ATS and Perplexity gets confidence 90.
    """
    if not url:
        return "serper_unknown"
    lower = url.lower()
    if "boards.greenhouse.io" in lower:
        return "ats_greenhouse"
    if "jobs.ashbyhq.com" in lower:
        return "ats_ashby"
    if "jobs.lever.co" in lower:
        return "ats_lever"
    if "smartrecruiters.com" in lower:
        return "ats_smartrecruiters"
    if ".myworkdayjobs.com" in lower:
        return "ats_workday"
    if "linkedin.com/jobs" in lower:
        return "linkedin"
    return "serper_direct"

# ── ATS API endpoints ─────────────────────────────────────────────────────────
ATS_APIS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby":      "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams",
    "lever":          "https://api.lever.co/v0/postings/{slug}?mode=json",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?status=PUBLISHED",
    "workday":        "https://{slug}.wd3.myworkdayjobs.com/wday/cxs/{slug}/jobs",
}

# Signals that a job page is expired / no longer accepting applications
EXPIRY_SIGNALS = [
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
]

# Serper queries — JobScout v2 (2026-05-11):
# Bayt/NaukriGulf/GulfTalent/Indeed dropped — the user's audit found that
# these portals have a high rate of expired ghost listings (postings stay
# live for 6+ months after the role is filled), and the signal-to-noise
# ratio doesn't justify keeping them. LinkedIn is the only generic portal
# kept among non-ATS sources. The per-target Perplexity discovery layer
# (see _discover_via_perplexity below) replaces the dropped coverage with
# a higher-quality, per-company-curated discovery for all 71 targets.
SERPER_SEARCH_QUERIES = [
    # LinkedIn (only generic portal kept — best signal in this category)
    'site:linkedin.com/jobs "head of product" OR "chief product officer" Dubai OR "Abu Dhabi" OR Riyadh fintech',
    'site:linkedin.com/jobs "senior product manager" payments fintech UAE OR KSA 2026',
    'site:linkedin.com/jobs "head of product" OR "VP product" fintech London payments 2026',
    # ATS job boards — Greenhouse
    'site:boards.greenhouse.io "product manager" OR "head of product" fintech payments UAE Dubai 2026',
    'site:boards.greenhouse.io "product manager" payments BNPL OR neobank OR "digital banking" 2026',
    # ATS job boards — Ashby
    'site:jobs.ashbyhq.com "product manager" OR "head of product" fintech payments 2026',
    # ATS job boards — Lever
    'site:jobs.lever.co "senior product manager" UAE OR "Middle East" OR London payments 2026',
    # Direct high-priority company searches (catch sites not yet on
    # target_companies but worth scouting)
    '"head of product" OR "senior product manager" site:tabby.ai',
    '"product manager" OR "head of product" site:wio.io',
    '"product manager" payments site:checkout.com/careers',
    '"product manager" site:airwallex.com/careers',
    '"product manager" payments site:wise.com/jobs',
    '"product manager" fintech site:revolut.com/careers',
    # KSA-specific (no portal mention)
    '"product manager" OR "head of product" Riyadh "Saudi Arabia" fintech payments 2026',
]

# Ashby GraphQL query body
ASHBY_GRAPHQL_BODY = {
    "operationName": "ApiJobBoardWithTeams",
    "variables": {},
    "query": """
    query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
        jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
            teams {
                name
                parentTeamName
                jobs {
                    id
                    title
                    teamName
                    locationName
                    employmentType
                    isListed
                    publishedDate
                    jobUrl
                }
            }
        }
    }
    """
}


class JobScoutAgent(BaseAgent):
    """
    Scans portals for PM/Head of Product/PMO jobs.
    Uses GPT-4.1 for fit pre-scoring.
    Validates job URLs for expiry before storing.
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(name="JobScout", model=settings.job_scout_model)
        self.profile = self._load_profile()
        self.portals = self._load_portals()

    def _load_profile(self) -> dict:
        try:
            with open(get_settings().profile_path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}

    def _load_portals(self) -> list[dict]:
        """Load all companies from portals.yml — flattened across all sections."""
        try:
            with open("portals.yml") as f:
                data = yaml.safe_load(f)
            companies = []
            for section_name, entries in data.items():
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and entry.get("name"):
                            entry["_section"] = section_name
                            companies.append(entry)
            return companies
        except FileNotFoundError:
            logger.warning("portals.yml not found — ATS scan will be limited")
            return []

    async def run(
        self,
        target_company: Optional[str] = None,
        target_role: Optional[str] = None
    ) -> list[dict]:
        """Main entry: scan portals → validate → score → store → return high-fit jobs."""
        self.log("🔍 Starting job scan...")
        all_jobs = []

        # 1. ATS API direct scans (portals.yml-driven, priority-aware)
        ats_jobs = await self._scan_ats_portals()
        # 2026-05-12: hard geo pre-filter — Stripe alone returns 491
        # Greenhouse jobs and most are US/Dublin. The GPT-4.1 scoring
        # weights geo, but with thousands of off-geo candidates flooding
        # the scoring step, junk scored 43-73 was leaking through. Drop
        # ATS jobs whose location is explicitly NOT in the user's target
        # set (UAE/KSA/Qatar/UK/Singapore/Remote). Empty locations pass.
        ats_geo_filtered = [j for j in ats_jobs if _is_target_geo(j)]
        ats_dropped = len(ats_jobs) - len(ats_geo_filtered)
        for j in ats_geo_filtered:
            j.setdefault("_discovery_source", "ats_" + (j.get("ats_type") or "unknown"))
        all_jobs.extend(ats_geo_filtered)
        self.log(
            f"   ATS APIs: {len(ats_geo_filtered)} jobs after geo filter "
            f"(dropped {ats_dropped} non-target-geo jobs from {len(ats_jobs)} raw)"
        )

        # 2. Serper web search
        serper_jobs = await self._search_serper(target_company, target_role)
        for j in serper_jobs:
            # Classify by URL host so we know which sources cleared validation
            j.setdefault("_discovery_source", _classify_serper_source(j.get("url", "")))
        all_jobs.extend(serper_jobs)
        self.log(f"   Serper: {len(serper_jobs)} jobs found")

        # 2.5. Perplexity per-target discovery (covers the 65 currently-
        # under-served targets that have no explicit Serper query).
        perplexity_jobs = await self._discover_via_perplexity(target_company)
        for j in perplexity_jobs:
            j.setdefault("_discovery_source", "perplexity_sonar")
        all_jobs.extend(perplexity_jobs)
        self.log(f"   Perplexity: {len(perplexity_jobs)} candidates discovered")

        # 3. Deduplicate — by URL first, then by company+title+location hash
        unique_jobs = self._deduplicate(all_jobs)
        self.log(f"   Unique after dedup: {len(unique_jobs)}")

        # 4. Filter out already-known jobs (in DB with status != new/pass)
        fresh_jobs = await self._filter_already_known(unique_jobs)
        self.log(f"   Fresh (not already in DB): {len(fresh_jobs)}")

        # 5. Run the 7 hallucination/quality safeguards on every candidate.
        # confidence_score < 50 → DROP (per Q1=A decision). Legacy v1 URL-only
        # validator was deleted 2026-05-12; this is the only validation path.
        valid_jobs = await self._validate_with_safeguards(fresh_jobs)
        self.log(f"   Validated (7 safeguards passed): {len(valid_jobs)}")

        # 6. GPT-4.1 pre-scoring
        scored_jobs = await self._score_jobs_batch(valid_jobs)

        # 7. Apply evaluate.md red-flag penalties
        scored_jobs = self._apply_red_flag_penalties(scored_jobs)

        # 8. Filter by threshold
        threshold = get_settings().fit_score_threshold
        qualifying = [j for j in scored_jobs if j.get("match_score", 0) >= threshold]
        self.log(f"   Qualifying (score >= {threshold}): {len(qualifying)}", style="green")

        # 9. Store in Supabase
        for job in qualifying:
            try:
                company_name = job.get("company", "Unknown")
                company = get_company_by_name(company_name)
                if not company:
                    company = upsert_company({
                        "name": company_name,
                        "country": job.get("location", ""),
                        "ats_type": job.get("ats_type", ""),
                    })
                job["company_id"] = company.get("id") if company else None
                saved = upsert_job(job)
                job["db_id"] = saved.get("id")
            except Exception as e:
                logger.warning(f"Failed to store job {job.get('title')}: {e}")

        # 10. Mark expired jobs in DB
        await self._mark_expired_in_db()

        self.log(f"✅ Scan complete. {len(qualifying)} jobs stored.", style="green")
        return qualifying

    # ── ATS Scanning ──────────────────────────────────────────────────────────

    async def _scan_ats_portals(self) -> list[dict]:
        """Scan all companies from portals.yml, respecting priority-based rotation."""
        today = date.today()
        day_of_year = today.timetuple().tm_yday
        jobs = []

        for company in self.portals:
            priority = company.get("priority", 2)
            ats = company.get("ats_type", "")
            name = company.get("name", "")

            # Priority-based rotation: P1 daily, P2 every 2 days, P3 every 5 days
            if priority == 1:
                should_scan = True
            elif priority == 2:
                should_scan = (day_of_year % 2 == 0)
            else:
                should_scan = (day_of_year % 5 == 0)

            if not should_scan:
                continue

            # Only scan ATS types we can hit via API
            if ats not in ("greenhouse", "ashby", "lever", "smartrecruiters", "workday"):
                continue

            slug = company.get("ats_slug") or company.get("slug")
            if not slug:
                continue

            try:
                co_jobs = await self._fetch_ats_jobs(name, ats, slug)
                jobs.extend(co_jobs)
                await asyncio.sleep(0.3)  # Rate limit courtesy
            except Exception as e:
                logger.debug(f"ATS fetch failed for {name} ({ats}/{slug}): {e}")

        return jobs

    async def _fetch_ats_jobs(self, company_name: str, ats: str, slug: str) -> list[dict]:
        """Fetch jobs from Greenhouse, Ashby, or Lever API."""
        async with httpx.AsyncClient(timeout=15) as client:

            if ats == "greenhouse":
                url = ATS_APIS["greenhouse"].format(slug=slug)
                resp = await client.get(url)
                if resp.status_code == 404:
                    logger.debug(f"Greenhouse 404 for {company_name} slug={slug}")
                    return []
                resp.raise_for_status()
                data = resp.json()
                return [
                    {
                        "title": j.get("title", ""),
                        "company": company_name,
                        "url": j.get("absolute_url", ""),
                        "description": "",
                        "source": "greenhouse",
                        "ats_type": "greenhouse",
                        "location": j.get("location", {}).get("name", ""),
                        "match_score": 0,
                        "discovered_at": datetime.utcnow().isoformat(),
                    }
                    for j in data.get("jobs", [])
                    if self._is_relevant_title(j.get("title", ""))
                ]

            elif ats == "ashby":
                # Ashby uses GraphQL
                body = dict(ASHBY_GRAPHQL_BODY)
                body["variables"] = {"organizationHostedJobsPageName": slug}
                resp = await client.post(
                    ATS_APIS["ashby"],
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code in (404, 400):
                    # Try the simpler public API endpoint
                    fallback_url = f"https://jobs.ashbyhq.com/{slug}"
                    return await self._scrape_ashby_fallback(client, company_name, slug, fallback_url)
                resp.raise_for_status()
                data = resp.json()
                jobs = []
                teams = (data.get("data") or {}).get("jobBoard", {}).get("teams", [])
                for team in teams:
                    for j in team.get("jobs", []):
                        if not j.get("isListed", True):
                            continue
                        title = j.get("title", "")
                        if not self._is_relevant_title(title):
                            continue
                        jobs.append({
                            "title": title,
                            "company": company_name,
                            "url": j.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}"),
                            "description": "",
                            "source": "ashby",
                            "ats_type": "ashby",
                            "location": j.get("locationName", ""),
                            "match_score": 0,
                            "discovered_at": datetime.utcnow().isoformat(),
                        })
                return jobs

            elif ats == "lever":
                url = ATS_APIS["lever"].format(slug=slug)
                resp = await client.get(url)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                data = resp.json()
                return [
                    {
                        "title": j.get("text", ""),
                        "company": company_name,
                        "url": j.get("hostedUrl") or j.get("applyUrl", ""),
                        "description": (j.get("description") or j.get("descriptionPlain") or "")[:1000],
                        "source": "lever",
                        "ats_type": "lever",
                        "location": j.get("categories", {}).get("location", ""),
                        "match_score": 0,
                        "discovered_at": datetime.utcnow().isoformat(),
                    }
                    for j in data
                    if isinstance(j, dict) and self._is_relevant_title(j.get("text", ""))
                ]

            elif ats == "smartrecruiters":
                # GAP-01 fix: SmartRecruiters public REST API
                url = ATS_APIS["smartrecruiters"].format(slug=slug)
                resp = await client.get(url, headers={"Content-Type": "application/json"})
                if resp.status_code in (404, 403):
                    return []
                resp.raise_for_status()
                data = resp.json()
                jobs = []
                for j in data.get("content", []):
                    title = j.get("name", "")
                    if not self._is_relevant_title(title):
                        continue
                    loc = j.get("location", {})
                    location_str = ", ".join(filter(None, [
                        loc.get("city"), loc.get("country")
                    ]))
                    jobs.append({
                        "title": title,
                        "company": company_name,
                        "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
                        "description": (j.get("jobAd", {}).get("sections", {})
                                        .get("jobDescription", {}).get("text", ""))[:800],
                        "source": "smartrecruiters",
                        "ats_type": "smartrecruiters",
                        "location": location_str,
                        "match_score": 0,
                        "discovered_at": datetime.utcnow().isoformat(),
                    })
                return jobs

            elif ats == "workday":
                # GAP-02 fix: Workday JSON API (used by Mastercard, Visa, JPMorgan, HSBC etc.)
                # Workday has no public uniform API — we use the CXS endpoint with a POST search.
                # slug format expected: "mastercard" → subdomain + job board name (may differ per company)
                workday_slug = company.get("workday_slug", slug)  # some companies need separate board name
                url = ATS_APIS["workday"].format(slug=workday_slug)
                try:
                    resp = await client.post(
                        url,
                        json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "product manager"},
                        headers={"Content-Type": "application/json"},
                        timeout=20,
                    )
                    if resp.status_code in (404, 403, 405):
                        return []
                    resp.raise_for_status()
                    data = resp.json()
                    jobs = []
                    for j in data.get("jobPostings", []):
                        title = j.get("title", "")
                        if not self._is_relevant_title(title):
                            continue
                        job_path = j.get("externalPath", "")
                        jobs.append({
                            "title": title,
                            "company": company_name,
                            "url": f"https://{workday_slug}.wd3.myworkdayjobs.com{job_path}",
                            "description": j.get("locationsText", ""),
                            "source": "workday",
                            "ats_type": "workday",
                            "location": j.get("locationsText", ""),
                            "match_score": 0,
                            "discovered_at": datetime.utcnow().isoformat(),
                        })
                    return jobs
                except Exception as e:
                    logger.debug(f"Workday fetch failed for {company_name}: {e}")
                    return []

        return []

    async def _scrape_ashby_fallback(
        self,
        client: httpx.AsyncClient,
        company_name: str,
        slug: str,
        url: str
    ) -> list[dict]:
        """Fallback: scrape Ashby job board HTML when GraphQL fails."""
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                return []
            # Ashby injects job data as JSON in a <script> tag
            text = resp.text
            match = re.search(r'window\.__appData\s*=\s*(\{.*?\});', text, re.DOTALL)
            if not match:
                return []
            app_data = json.loads(match.group(1))
            jobs_data = app_data.get("jobPostings") or []
            return [
                {
                    "title": j.get("title", ""),
                    "company": company_name,
                    "url": f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}",
                    "description": "",
                    "source": "ashby",
                    "ats_type": "ashby",
                    "location": j.get("locationName", ""),
                    "match_score": 0,
                    "discovered_at": datetime.utcnow().isoformat(),
                }
                for j in jobs_data
                if self._is_relevant_title(j.get("title", ""))
            ]
        except Exception:
            return []

    # ── Serper Search ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _serper_search(self, query: str) -> list[dict]:
        """Run a single Serper search query."""
        s = get_settings()
        if not s.serper_api_key:
            return []
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                s.serper_endpoint,
                headers={"X-API-KEY": s.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 10}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("organic", [])

    async def _search_serper(
        self,
        target_company: Optional[str],
        target_role: Optional[str]
    ) -> list[dict]:
        """Run Serper queries in rotating batches to cover all queries over time."""
        s = get_settings()
        if not s.serper_api_key:
            self.log("  ⚠️  Serper key not set — skipping web search", style="yellow")
            return []

        queries = list(SERPER_SEARCH_QUERIES)
        if target_company:
            queries.insert(0, f'"{target_company}" "product manager" OR "head of product" careers jobs')
        if target_role:
            queries.insert(0, f'"{target_role}" UAE OR Dubai OR KSA OR London fintech payments 2026')

        # Rotate through queries: run 10 per day based on day-of-year offset
        # This ensures ALL queries are hit across the week, not just the first 8
        day_offset = date.today().timetuple().tm_yday % len(queries)
        # Reorder: start from today's offset, wrap around
        rotated = queries[day_offset:] + queries[:day_offset]
        # Run first 12 (more coverage than before)
        to_run = rotated[:12]

        raw_results = []
        for query in to_run:
            try:
                results = await self._serper_search(query)
                raw_results.extend(results)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"Serper query failed: {e}")

        # Parse raw Serper results into job records
        jobs = []
        for r in raw_results:
            title_str = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            if not link:
                continue
            company = self._extract_company_from_title(title_str)
            if not company:
                continue
            clean_title = title_str.split(" - ")[0].split(" | ")[0].strip()
            if not self._is_relevant_title(clean_title):
                continue
            jobs.append({
                "title": clean_title,
                "company": company,
                "url": link,
                "description": snippet,
                "source": self._detect_source(link),
                "ats_type": self._detect_ats_type(link),
                "location": self._extract_location(title_str + " " + snippet),
                "match_score": 0,
                "discovered_at": datetime.utcnow().isoformat(),
            })
        return jobs

    # ── Deduplication ─────────────────────────────────────────────────────────

    def _deduplicate(self, jobs: list[dict]) -> list[dict]:
        """
        Deduplicate by:
        1. Exact URL match
        2. company + normalised title + location hash (catches Workday reposts)
        """
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        unique = []

        for job in jobs:
            url = (job.get("url") or "").strip().rstrip("/")
            title_norm = re.sub(r"[^a-z0-9]", "", (job.get("title") or "").lower())
            company_norm = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
            location_norm = re.sub(r"[^a-z0-9]", "", (job.get("location") or "").lower())
            content_hash = hashlib.md5(f"{company_norm}|{title_norm}|{location_norm}".encode()).hexdigest()

            if url and url in seen_urls:
                continue
            if content_hash in seen_hashes:
                continue

            if url:
                seen_urls.add(url)
            seen_hashes.add(content_hash)
            unique.append(job)

        return unique

    async def _filter_already_known(self, jobs: list[dict]) -> list[dict]:
        """
        Remove jobs already in the DB that aren't 'new' status.
        This prevents re-processing jobs we've already handled.
        """
        try:
            urls = [j.get("url") for j in jobs if j.get("url")]
            if not urls:
                return jobs
            db = get_supabase()
            result = db.table("jobs").select("url, status").in_("url", urls).execute()
            existing = {row["url"]: row["status"] for row in (result.data or [])}

            fresh = []
            for job in jobs:
                url = job.get("url", "")
                status = existing.get(url)
                # Keep if: not in DB, or status is 'new' (unprocessed), or status is 'pass' (re-evaluate)
                if status is None or status == "new":
                    fresh.append(job)
                # Always re-check 'pass' jobs once a week (they might have expired from the right company)
            return fresh
        except Exception as e:
            logger.warning(f"DB freshness check failed: {e}")
            return jobs

    # ── Expiry Validation ─────────────────────────────────────────────────────

    # ── JobScout v2 — per-target Perplexity discovery + 7-safeguard validation ──
    async def _discover_via_perplexity(
        self,
        target_company: Optional[str] = None,
    ) -> list[dict]:
        """Per-target curated discovery via Perplexity Sonar.

        Iterates target_companies (or just the one passed in target_company)
        and for each calls perplexity_search.discover_jobs. Returns a flat
        list of candidate job dicts shaped like everything else in this
        agent (url, title, company, ats_type, source).

        Concurrency: 3 (Perplexity's default rate limit).
        Cost: ~$0.005 × N companies. For 71 targets ≈ $0.35/run.

        Caller is responsible for running validation on the output via
        _validate_with_safeguards (which is what run() does).
        """
        try:
            from agents.perplexity_search import discover_jobs as _discover
        except ImportError:
            logger.warning("perplexity_search.discover_jobs not available; skipping v2 discovery")
            return []

        # Pull target companies from the DB. If a target_company filter is
        # passed (single-company run), restrict to that one.
        db = get_supabase()
        try:
            q = db.table("companies").select("id, name, domain, careers_url").eq("is_target", True)
            if target_company:
                q = q.ilike("name", f"%{target_company}%")
            rows = q.execute().data or []
        except Exception as e:
            logger.exception("_discover_via_perplexity: failed to load targets: %s", e)
            return []

        if not rows:
            return []

        sem = asyncio.Semaphore(3)
        all_candidates: list[dict] = []
        total_cost = 0.0

        async def _one(company: dict) -> tuple[str, list[dict], float]:
            name = company.get("name") or ""
            domain = company.get("domain") or ""
            async with sem:
                try:
                    res = await _discover(name, company_domain=domain or None)
                    return name, res.get("candidates") or [], float(res.get("cost_usd") or 0.0)
                except Exception as e:
                    logger.warning("Perplexity discovery failed for %s: %s", name, e)
                    return name, [], 0.0

        results = await asyncio.gather(*(_one(c) for c in rows))
        for name, cands, cost in results:
            total_cost += cost
            for c in cands:
                url = c.get("url") or ""
                title = c.get("title") or f"{name} — product role"
                if not url:
                    continue
                all_candidates.append({
                    "title": title,
                    "company": name,
                    "url": url,
                    "location": "",
                    "source": "perplexity",
                    "ats_type": "",
                    "discovered_at": datetime.utcnow().isoformat(),
                    "_published_at": c.get("published_at"),
                })

        self.log(f"   Perplexity per-target discovery: {len(all_candidates)} candidates · ${total_cost:.4f}")
        return all_candidates

    async def _validate_with_safeguards(self, jobs: list[dict]) -> list[dict]:
        """Run the 7 hallucination/quality safeguards from agents.job_validation.

        Drops candidates that fail validation (Q1=A: drop, don't insert).
        Stamps surviving candidates with `confidence_score`, `freshness`,
        and `discovery_sources`. URLs that resolved through redirects get
        normalised to the final URL.
        """
        # 2026-05-12: v1 fallback removed — if agents.job_validation can't be
        # imported, that's a deployment bug and we should fail loudly rather
        # than silently degrade to URL-only validation (which produces lower-
        # quality jobs that would then ship to /today indistinguishably).
        from agents.job_validation import (
            DiscoveredJob,
            validate_candidate,
        )

        # Build a map of company_name → domains (for safeguard #2 whitelist).
        db = get_supabase()
        try:
            company_rows = db.table("companies").select("name, domain").execute().data or []
            domains_by_name: dict[str, list[str]] = {}
            for r in company_rows:
                name = (r.get("name") or "").lower()
                domain = r.get("domain") or ""
                if name and domain:
                    domains_by_name.setdefault(name, []).append(domain)
        except Exception as e:
            logger.exception("Failed to load company domains for whitelist: %s", e)
            domains_by_name = {}

        sem = asyncio.Semaphore(8)
        validated: list[dict] = []
        dropped_counts: dict[str, int] = {}
        closed_urls: list[str] = []

        async def _one(job: dict) -> Optional[dict]:
            async with sem:
                url = job.get("url") or ""
                if not url or url.startswith("manual-"):
                    # Manually-added jobs bypass validation
                    return job
                company_name = job.get("company") or ""
                source = job.get("_discovery_source") or "serper_unknown"

                candidate = DiscoveredJob(
                    url=url,
                    title=job.get("title") or "",
                    company_name=company_name,
                    source=source,
                    published_at=job.get("_published_at"),
                    snippet=None,
                    raw=job,
                )
                domains = domains_by_name.get(company_name.lower(), [])
                try:
                    result = await validate_candidate(
                        candidate,
                        target_company_domains=domains,
                    )
                except Exception as e:
                    logger.exception("validate_candidate threw for %s: %s", url, e)
                    return None

                if result.closed:
                    closed_urls.append(result.candidate.url)
                    return None
                if result.rejected_reason:
                    dropped_counts[result.rejected_reason] = (
                        dropped_counts.get(result.rejected_reason, 0) + 1
                    )
                    return None

                v = result.validated
                if not v:
                    return None
                # Stamp the job dict with validation outputs so upsert_job
                # can persist them (writes through to migration-011 columns).
                job["url"] = v.url
                job["confidence_score"] = v.confidence_score
                job["discovery_sources"] = v.discovery_sources
                job["freshness"] = v.freshness
                job["validated_at"] = v.validated_at.isoformat()
                return job

        results = await asyncio.gather(*(_one(j) for j in jobs))
        validated = [r for r in results if r is not None]

        if dropped_counts:
            self.log(f"   Validation dropped: {dict(dropped_counts)}")
        if closed_urls:
            self.log(f"   Detected closed postings: {len(closed_urls)}")

        return validated

    async def _check_url_validity(self, url: str) -> tuple[bool, str]:
        """
        Returns (is_valid, reason_if_invalid).
        Checks HTTP status code and page content for expiry signals.
        """
        try:
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0)"}
            ) as client:
                resp = await client.get(url)

                # 404 or 410 = definitely gone
                if resp.status_code in (404, 410):
                    return False, f"HTTP {resp.status_code}"

                # 403 = probably still active (just blocked), assume valid
                if resp.status_code == 403:
                    return True, "ok"

                # Check page content for expiry signals (case-insensitive)
                if resp.status_code == 200:
                    content_lower = resp.text[:5000].lower()
                    for signal in EXPIRY_SIGNALS:
                        if signal in content_lower:
                            return False, f"expiry signal: '{signal[:40]}'"

                # Redirected to a generic /jobs page = posting removed
                final_url = str(resp.url)
                if resp.history:
                    # If redirected away from specific job URL to generic listing
                    original_path = url.split("?")[0].rstrip("/")
                    final_path = final_url.split("?")[0].rstrip("/")
                    if original_path != final_path and any(
                        final_path.endswith(p) for p in ["/jobs", "/careers", "/job-search", "/openings"]
                    ):
                        return False, "redirected to generic jobs page"

                return True, "ok"

        except httpx.TimeoutException:
            # Timeout — assume still valid (server might just be slow)
            return True, "timeout-assume-valid"
        except httpx.ConnectError:
            return True, "connect-error-assume-valid"
        except Exception as e:
            logger.debug(f"URL check error for {url}: {e}")
            return True, "error-assume-valid"

    async def _mark_expired_in_db(self):
        """
        Check existing 'new' and 'evaluated' jobs in DB — mark expired ones.
        Runs after each scan to keep DB clean.
        """
        try:
            db = get_supabase()
            result = db.table("jobs") \
                .select("id, url") \
                .in_("status", ["new", "evaluated"]) \
                .order("discovered_at", desc=True) \
                .limit(50) \
                .execute()

            old_jobs = result.data or []
            if not old_jobs:
                return

            # Only check jobs older than 3 days (fresh jobs rarely expire instantly)
            semaphore = asyncio.Semaphore(5)

            async def check_and_mark(job: dict):
                url = job.get("url", "")
                if not url:
                    return
                async with semaphore:
                    is_valid, reason = await self._check_url_validity(url)
                    if not is_valid:
                        db.table("jobs").update({
                            "status": "expired",
                            "fit_details": {"expired_reason": reason}
                        }).eq("id", job["id"]).execute()
                        logger.info(f"Marked expired in DB: job_id={job['id']} reason={reason}")

            await asyncio.gather(*[check_and_mark(j) for j in old_jobs], return_exceptions=True)

        except Exception as e:
            logger.warning(f"DB expiry cleanup failed: {e}")

    # ── Scoring ───────────────────────────────────────────────────────────────

    async def _score_jobs_batch(self, jobs: list[dict]) -> list[dict]:
        """
        Use GPT-4.1 to score jobs in chunks of 25 in parallel.
        Each job gets: match_score, archetype, legitimacy_tier, legitimacy_signals.
        """
        if not jobs:
            return jobs

        profile_summary = self._get_profile_summary()
        chunk_size = 25
        chunks = [jobs[i:i + chunk_size] for i in range(0, len(jobs), chunk_size)]

        async def score_chunk(chunk: list[dict], offset: int) -> dict[int, dict]:
            jobs_json = json.dumps([
                {
                    "id": offset + i,
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "location": j.get("location"),
                    "description": (j.get("description") or "")[:800]
                }
                for i, j in enumerate(chunk)
            ], indent=2)

            system = """You are a precision job-fit scoring + classification engine.
For each job return:
  score: 0-100 fit score (be strict; 85+ only for excellent matches)
  archetype: one of [CPO, Head of Product, Senior PM, Group PM, Program Manager, PMO Director, VP Product, Solutions Architect, Other]
  legitimacy: one of [High Confidence, Proceed with Caution, Suspicious]
  legitimacy_signals: array of brief strings explaining the legitimacy tier
  reason: 1-line scoring rationale

Return ONLY valid JSON: {"scores":[{"id":N,"score":X,"archetype":"...","legitimacy":"...","legitimacy_signals":["..."],"reason":"..."}]}"""

            user = f"""Candidate Profile:
{profile_summary}

Jobs to score:
{jobs_json}

SCORING (0-100, strict):
- Role type: CPO/Head of Product/VP Product = 25; Senior/Group PM = 20; Program Manager/PMO = 15; adjacent = 8; wrong = 0
- Domain: fintech/payments/BNPL/digital banking/issuer/acquirer = 20; embedded finance/BaaS = 18; general tech = 5
- Location (the candidate's named target geos — strictly enforced):
    * UAE / KSA / Qatar / Singapore = 20  (primary target wedge)
    * UK / London = 15                    (secondary wedge)
    * Remote (truly geo-flexible) = 18
    * Other Asia (HK / India / Thailand / Vietnam) = 5
    * EU outside UK (Dublin / Amsterdam / Berlin) = 5
    * US (visa-required, off-wedge) = -20  ← was +5, now actively penalised
- Seniority: Senior/Head/Director/Chief = 20; mid = 10; junior = 0
- Company brand: tier-1 (Stripe/Adyen/Wise/Visa/Mastercard/Marqeta/Plaid/Revolut/Tabby/Airwallex) = +15
- Red flags: UAE Nationals only = -40; entry-level/grad = -30; commission-only = -30; US-only role = -30

ARCHETYPE: classify by JD focus (not just title)
- CPO / Head of Product / VP Product: full P&L + roadmap ownership signals
- Senior PM / Group PM: own a product line, team of PMs reports to them
- Program Manager / PMO: cross-team delivery, governance, no direct product ownership
- Solutions Architect: technical pre-sales, customer integration

LEGITIMACY: assess if this is a real, active opening
- High Confidence signals: specific tech named, named hiring manager, team context, recent JD, clear scope
- Caution signals: vague JD, generic boilerplate, no salary band where required, requirements contradict each other
- Suspicious signals: extremely old posting, requirements impossible (10y exp on 3yo tech), reposting pattern, "always hiring" language
List the actual signals in legitimacy_signals.

Return JSON only."""

            try:
                response = await self.ask_openai(
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    max_tokens=4500,
                    temperature=0.1,
                )
                result = json.loads(response.strip())
                return {s["id"]: s for s in result.get("scores", [])}
            except json.JSONDecodeError as e:
                logger.warning(f"Scoring JSON parse failed for chunk offset={offset}: {e}")
                return {}
            except Exception as e:
                logger.warning(f"Scoring chunk offset={offset} failed: {e}")
                return {}

        chunk_results = await asyncio.gather(
            *[score_chunk(chunk, i * chunk_size) for i, chunk in enumerate(chunks)]
        )
        score_map: dict[int, dict] = {}
        for partial in chunk_results:
            score_map.update(partial)

        for i, job in enumerate(jobs):
            scored = score_map.get(i, {})
            job["match_score"] = scored.get("score", 0)
            job["archetype"] = scored.get("archetype")
            job["legitimacy_tier"] = scored.get("legitimacy")
            job["legitimacy_signals"] = scored.get("legitimacy_signals", [])
            if job.get("fit_details") is None:
                job["fit_details"] = {}
            job["fit_details"]["gpt4_reason"] = scored.get("reason", "")
            job["fit_details"]["archetype"] = scored.get("archetype")

        scored_count = sum(1 for j in jobs if j.get("match_score", 0) > 0)
        self.log(f"   Scored: {scored_count}/{len(jobs)} jobs in {len(chunks)} chunks")
        return jobs

    def _apply_red_flag_penalties(self, jobs: list[dict]) -> list[dict]:
        """
        Apply rule-based score penalties from modes/evaluate.md.
        Handles cases GPT might miss: UAE Nationals preferred, graduate roles,
        Mastercard/Visa Dubai junior posts.
        """
        RED_FLAG_PATTERNS = [
            # (regex pattern in title/description, penalty, reason)
            (r"uae.{0,10}national", -30, "UAE Nationals preferred"),
            (r"gcc.{0,10}national", -20, "GCC Nationals preferred"),
            (r"emirati.{0,10}preferred", -30, "Emirati preferred"),
            (r"graduate.{0,10}programme|launch.{0,10}graduate|intern", -25, "Graduate/intern role"),
            (r"arabic.{0,10}(required|essential|must)", -20, "Arabic required"),
        ]

        # Mastercard/Visa Dubai = always check for junior posts
        NETWORK_DUBAI_JUNIORS = {"mastercard", "visa", "american express"}

        for job in jobs:
            title = (job.get("title") or "").lower()
            description = (job.get("description") or "").lower()
            company = (job.get("company") or "").lower()
            location = (job.get("location") or "").lower()
            combined = title + " " + description

            for pattern, penalty, reason in RED_FLAG_PATTERNS:
                if re.search(pattern, combined, re.IGNORECASE):
                    old_score = job.get("match_score", 0)
                    job["match_score"] = max(0, old_score + penalty)
                    if job.get("fit_details") is None:
                        job["fit_details"] = {}
                    flags = job["fit_details"].get("red_flags", [])
                    flags.append(reason)
                    job["fit_details"]["red_flags"] = flags

            # Network company Dubai posts: penalise unless director/VP level
            if any(n in company for n in NETWORK_DUBAI_JUNIORS):
                if "dubai" in location or "uae" in location:
                    is_senior = any(kw in title for kw in ["director", "vp ", "vice president", "head", "principal"])
                    if not is_senior:
                        job["match_score"] = max(0, job.get("match_score", 0) - 25)
                        fit = job.get("fit_details") or {}
                        flags = fit.get("red_flags", [])
                        flags.append("Network company Dubai post — likely junior/nationals-preferred")
                        fit["red_flags"] = flags
                        job["fit_details"] = fit

        return jobs

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_profile_summary(self) -> str:
        p = self.profile.get("candidate", {})
        r = self.profile.get("target_roles", {})
        exp = self.profile.get("experience", {})
        anchors = self.profile.get("positioning_anchors", {})
        metrics = anchors.get("headline_metrics", [])
        return f"""
Name: {p.get('full_name', 'Rizwan Zafar')}
Current: {exp.get('current_title')} at {exp.get('current_company')}
Experience: {exp.get('total_years')}+ years
Target roles: {', '.join(r.get('primary', []))}
Locations: {', '.join(self.profile.get('target_locations', []))}
Domains: {', '.join(self.profile.get('domains', {}).get('primary', []))}
Key metrics: {'; '.join(metrics[:4])}
Certifications: PMP, PMI-ACP, CSPO, CSM
""".strip()

    def _is_relevant_title(self, title: str) -> bool:
        """Filter: does this title match a PM/PMO/Head of Product role?"""
        title_lower = title.lower()
        positive = [
            "product manager", "head of product", "chief product",
            "programme manager", "program manager", "pmo",
            "vp product", "vp of product", "vice president product",
            "director of product", "director, product",
            "product lead", "product owner",
            "head of payments", "head of digital",
            "head of engineering",   # Sometimes Rizwan-relevant hybrid roles
        ]
        negative = ["intern", "graduate", "junior", "associate analyst",
                    "marketing manager", "account manager", "sales"]
        if any(neg in title_lower for neg in negative):
            return False
        return any(kw in title_lower for kw in positive)

    def _extract_company_from_title(self, title: str) -> Optional[str]:
        """Extract company name from search result title string."""
        # Pattern: "Job Title - Company | Portal" or "Job Title at Company"
        parts = re.split(r"\s+[-–|]\s+|\s+[Aa]t\s+", title)
        if len(parts) >= 2:
            company = parts[1].strip()
            company = re.sub(r"\s*[|].*", "", company).strip()
            # Filter out portal names masquerading as companies
            non_companies = {"linkedin", "indeed", "glassdoor", "bayt", "naukrigulf",
                             "gulftalent", "greenhouse", "lever", "ashby", "jobs"}
            if company.lower() in non_companies:
                return None
            return company if len(company) > 1 else None
        return None

    def _detect_source(self, url: str) -> str:
        for portal in ["linkedin", "bayt", "naukrigulf", "gulftalent", "indeed",
                       "greenhouse", "ashby", "lever", "workday"]:
            if portal in url.lower():
                return portal
        return "web"

    def _detect_ats_type(self, url: str) -> str:
        if "greenhouse.io" in url or "boards.greenhouse.io" in url:
            return "greenhouse"
        if "ashbyhq.com" in url:
            return "ashby"
        if "lever.co" in url:
            return "lever"
        if "workday.com" in url or "myworkday" in url:
            return "workday"
        return ""

    def _extract_location(self, text: str) -> str:
        locations = [
            "Dubai", "Abu Dhabi", "UAE", "United Arab Emirates",
            "Saudi Arabia", "Riyadh", "Jeddah", "KSA",
            "Qatar", "Doha",
            "Bahrain", "Manama",
            "London", "United Kingdom", "UK",
            # 2026-05-12: added Singapore to target-location list. User's
            # explicit geo wedge is UAE / KSA / Qatar / UK / Singapore.
            # Without Singapore here, the Singapore-based Adyen Product
            # Manager - Payments role landed at match_score 61 (Asia tier)
            # instead of the 80+ that the user's primary geo deserves.
            "Singapore",
            "Remote", "Hybrid",
        ]
        for loc in locations:
            if loc.lower() in text.lower():
                return loc
        return ""
