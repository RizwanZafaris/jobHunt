"""
agents/perplexity_search.py — P1.3 persona recency layer.

Thin async wrapper around the Perplexity Sonar API. The Apify-based deep
research pipeline (agents/persona_deep_research.py) stays the canonical
"build the persona once" pass. Perplexity sits on top as a recency +
verification layer that complements but does not replace it:

  * recency_check(company)        — model "sonar"      — weekly cron
        "what's new at $COMPANY in the last 30 days, with sources?"
        Output feeds new anchors back into company_knowledge so the next
        Apify deep-scrape covers fresh ground.

  * strategic_posture(company)    — model "sonar-pro"  — monthly cron
        Two-paragraph senior-fintech-analyst summary of the company's
        bet / constraint / hiring direction, with citations on every
        factual claim. Snapshot is stored on company_personas.

  * verify_claim(claim, context)  — model "sonar"      — ad-hoc
        Cheap fact-check: returns {verified, evidence, citations, cost}.
        Used by the meta-critic before a resume bullet ships.

Cost target across all 71 personas (2026-05-10):
    weekly recency_check    ≈ $0.005 × 71 × 4 = $1.42 / month
    monthly strategic post  ≈ $0.05  × 71      = $3.55 / month
    ──────────────────────────────────────────────────────────
                                       total  ≈ $4.97 / month

API auth: PERPLEXITY_API_KEY in env. We DO NOT fall back to a stub —
callers should let RuntimeError surface so cron jobs don't silently
no-op for weeks. (Same posture llm_router.py takes for OpenAI/Anthropic.)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Endpoint ─────────────────────────────────────────────────────────────
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"

# ─── Pricing constants (Perplexity Sonar, public docs as of 2025) ─────────
# Source: https://docs.perplexity.ai/getting-started/pricing
# Sonar small: $1 / M input tokens, $1 / M output tokens, plus a flat $0.005
#              per request for grounded search. We approximate as "small per
#              query" because the search-fee dominates at our prompt sizes.
# Sonar Pro:   $3 / M input, $15 / M output, plus $0.005 search fee.
# Numbers are conservative — if Perplexity drops prices, our reported cost
# is a small over-estimate, not an under-estimate. Document before changing.
SONAR_INPUT_PER_M: float = 1.0
SONAR_OUTPUT_PER_M: float = 1.0
SONAR_PRO_INPUT_PER_M: float = 3.0
SONAR_PRO_OUTPUT_PER_M: float = 15.0
SONAR_REQUEST_FEE_USD: float = 0.005  # grounded-search flat fee per call

# ─── Models ───────────────────────────────────────────────────────────────
MODEL_RECENCY = os.getenv("PERPLEXITY_MODEL_RECENCY", "sonar")
MODEL_STRATEGIC = os.getenv("PERPLEXITY_MODEL_STRATEGIC", "sonar-pro")

# ─── HTTP defaults ────────────────────────────────────────────────────────
HTTP_TIMEOUT_S = 30.0
HTTP_RETRY_BACKOFF_S = 2.0
HTTP_RETRY_STATUS = {429, 500, 502, 503, 504}


def _api_key() -> str:
    key = os.getenv("PERPLEXITY_API_KEY")
    if not key:
        raise RuntimeError(
            "PERPLEXITY_API_KEY is not set. Sign up at "
            "https://www.perplexity.ai/settings/api and add the key to "
            ".env / Railway env. We deliberately do not fall back to a "
            "stub so cron jobs surface the misconfig instead of "
            "silently no-op'ing for weeks."
        )
    return key


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Map (model, tokens) → USD. Adds the flat search fee for grounded calls."""
    if model.startswith("sonar-pro"):
        in_rate = SONAR_PRO_INPUT_PER_M
        out_rate = SONAR_PRO_OUTPUT_PER_M
    else:
        in_rate = SONAR_INPUT_PER_M
        out_rate = SONAR_OUTPUT_PER_M
    token_cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
    return round(token_cost + SONAR_REQUEST_FEE_USD, 6)


def _normalise_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull citations out of a Perplexity response.

    Perplexity returns them in two shapes depending on the API version:
      1. Top-level `citations: [url1, url2, ...]` (older / sonar-online).
      2. Newer responses include `search_results: [{url, title, ...}]`
         alongside the bare-url list.
    We merge both into [{url, title?, published_at?}].
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    rich = payload.get("search_results") or []
    if isinstance(rich, list):
        for item in rich:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("link")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "url": url,
                "title": item.get("title") or item.get("name"),
                "published_at": item.get("published_at") or item.get("date"),
                "snippet": item.get("snippet") or item.get("description"),
            })

    bare = payload.get("citations") or []
    if isinstance(bare, list):
        for url in bare:
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": None, "published_at": None})

    return out


async def _post_chat(
    *,
    model: str,
    system: str,
    user: str,
) -> dict[str, Any]:
    """POST to /chat/completions with one retry on 429/5xx.

    Returns the raw decoded JSON. Re-raises on hard failures so callers
    see the same exception they'd see from any other httpx call.
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    last_exc: Optional[BaseException] = None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        for attempt in range(2):  # 1 retry on 429/5xx
            try:
                resp = await client.post(PERPLEXITY_API_URL, headers=headers, json=body)
                if resp.status_code in HTTP_RETRY_STATUS and attempt == 0:
                    logger.warning(
                        "perplexity %s returned %s — retrying once after %ss",
                        model, resp.status_code, HTTP_RETRY_BACKOFF_S,
                    )
                    await asyncio.sleep(HTTP_RETRY_BACKOFF_S)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning("perplexity %s transport error: %r — retrying", model, exc)
                    await asyncio.sleep(HTTP_RETRY_BACKOFF_S)
                    continue
                logger.exception("perplexity %s failed after retry", model)
                raise
    # Should not reach here, but keep mypy / pyright happy.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("perplexity request failed without an exception (unreachable)")


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices, list):
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def _extract_token_usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") or {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


# ─── Cache-aside wrapper (P1 audit, 2026-05-27) ──────────────────────────
#
# AI_SYSTEM_AUDIT.md §4 found that recency_check / strategic_posture
# fire Perplexity calls every invocation, even when company_knowledge
# already has a fresh row for the same (company, section). This wrapper
# checks the DB first and only calls Perplexity on miss, then writes
# back. Each section has its own TTL via the trigger applied in
# migration 041_company_knowledge_ttl (recency=30d, jobs=14d, posture=90d).
#
# Cost impact (per audit): $5-10/mo savings + 5-10× faster on cache hits.

from datetime import datetime, timezone
import json as _json


def _get_cached_section(company: str, section: str) -> Optional[dict[str, Any]]:
    """Return the freshest non-expired company_knowledge row for (company, section).

    Returns None on miss or if no fresh row exists. The TTL gate is
    `confidence_decays_at > NOW()` — the column is auto-populated by
    the trg_company_knowledge_default_ttl trigger.
    """
    try:
        from db.client import get_supabase  # local import — avoid hard dep at module load
    except Exception as exc:
        logger.debug("cache_get: db client unavailable (%r) — bypassing cache", exc)
        return None

    try:
        resp = (
            get_supabase()
            .table("company_knowledge")
            .select("content, source_url, scraped_at, confidence_decays_at, metadata")
            .eq("company_name", company)
            .eq("section", section)
            .gt("confidence_decays_at", datetime.now(timezone.utc).isoformat())
            .order("scraped_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        # Content may be stored as JSON-string OR plain text depending on caller.
        content = row.get("content")
        try:
            parsed = _json.loads(content) if isinstance(content, str) else content
        except (ValueError, TypeError):
            parsed = {"summary": content or ""}
        if not isinstance(parsed, dict):
            parsed = {"summary": str(parsed)}
        parsed["_cache"] = {
            "hit": True,
            "scraped_at": row.get("scraped_at"),
            "decays_at": row.get("confidence_decays_at"),
        }
        return parsed
    except Exception as exc:
        logger.warning("cache_get failed for %s/%s: %r", company, section, exc)
        return None


def _persist_section(
    company: str,
    section: str,
    payload: dict[str, Any],
    *,
    source_url: Optional[str] = None,
) -> None:
    """Write a Perplexity payload to company_knowledge.

    The TTL trigger sets confidence_decays_at automatically based on
    `section`. Failures are non-fatal — we'd rather make the LLM call
    twice than crash the caller.
    """
    try:
        from db.client import get_supabase
    except Exception as exc:
        logger.debug("cache_put: db client unavailable (%r) — skip persist", exc)
        return

    try:
        get_supabase().table("company_knowledge").insert({
            "company_name": company,
            "section": section,
            "content": _json.dumps(payload, default=str)[:32000],  # cap for safety
            "source_url": source_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"source": "perplexity_cached"},
            # confidence_decays_at: trigger fills based on section
        }).execute()
    except Exception as exc:
        logger.warning("cache_put failed for %s/%s: %r", company, section, exc)


async def cached_recency_check(
    company: str,
    days: int = 30,
    industry_hint: str = "fintech / payments / financial technology",
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Cache-aside wrapper around recency_check.

    DB-first lookup against company_knowledge (section='recency_check').
    On hit, returns the cached payload with `_cache.hit=True`. On miss,
    calls Perplexity and writes back. Pass force_refresh=True to bypass
    the cache (cron jobs that explicitly want fresh news).
    """
    if not force_refresh:
        cached = _get_cached_section(company, "recency_check")
        if cached:
            logger.debug("cached_recency_check: HIT for %s", company)
            cached.setdefault("cost_usd", 0.0)
            cached.setdefault("citations", cached.get("citations") or [])
            cached.setdefault("model", "cache")
            return cached

    fresh = await recency_check(company, days=days, industry_hint=industry_hint)
    _persist_section(company, "recency_check", fresh)
    fresh["_cache"] = {"hit": False}
    return fresh


async def cached_strategic_posture(
    company: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Cache-aside wrapper around strategic_posture (90d TTL)."""
    if not force_refresh:
        cached = _get_cached_section(company, "strategic_posture")
        if cached:
            logger.debug("cached_strategic_posture: HIT for %s", company)
            cached.setdefault("cost_usd", 0.0)
            cached.setdefault("citations", cached.get("citations") or [])
            cached.setdefault("model", "cache")
            return cached

    fresh = await strategic_posture(company)
    _persist_section(company, "strategic_posture", fresh)
    fresh["_cache"] = {"hit": False}
    return fresh


# ─── Public surface ───────────────────────────────────────────────────────
async def recency_check(
    company: str,
    days: int = 30,
    industry_hint: str = "fintech / payments / financial technology",
) -> dict[str, Any]:
    """Find news from the last `days` days about `company`.

    Returns:
        {
            summary: str,
            citations: [{url, title?, published_at?, snippet?}],
            cost_usd: float,
            model: str,
            raw_tokens: {input: int, output: int},
        }

    Disambiguation: ambiguous brand names (e.g. "Visa" the payments
    company collides with US travel/immigration visas) cause Sonar to
    return high-traffic off-topic citations. The system prompt below
    constrains both (a) the entity definition and (b) the allowed
    citation domains so we get press-releases / earnings / business-news
    rather than .gov travel / .edu / immigration sites.
    """
    company_lc = company.lower()
    system = (
        f"You are a research assistant. Find news from the last {days} days about "
        f"\"{company}\" — specifically the company / brand operating in the "
        f"{industry_hint} industry. NOT any similarly-named policy / "
        f"government / consumer-product / immigration term that may share the "
        f"name (e.g. for 'Visa' this means the payments company NASDAQ:V, NOT "
        f"US travel-visa / green-card / immigration policy).\n\n"
        f"Citation rules — return ONLY URLs from these source types:\n"
        f"  - the company's own domains (e.g. *.{company_lc}.com, "
        f"investor.{company_lc}.com, ir.{company_lc}.com)\n"
        f"  - business newswires: businesswire.com, prnewswire.com, "
        f"globenewswire.com, sec.gov\n"
        f"  - established business / fintech press: bloomberg.com, reuters.com, "
        f"wsj.com, ft.com, marketwatch.com, marketscreener.com, fool.com, "
        f"seekingalpha.com, pymnts.com, americanbanker.com, finextra.com, "
        f"techcrunch.com, theinformation.com\n\n"
        f"EXCLUDE: travel.state.gov, .edu pages, immigration / consular / "
        f"green-card / visa-bulletin / aila / boundless / immihelp / youtube "
        f"explainer videos, generic travel news.\n\n"
        f"Focus the summary on hiring direction, product launches, leadership "
        f"changes, financial results, or strategic shifts that would matter "
        f"to a senior product manager preparing to apply for a role there. "
        f"If you find fewer than 3 on-topic results, say so — never pad with "
        f"off-topic citations to reach a count."
    )
    user = (
        f"What's new at {company} (the {industry_hint} company) in the last "
        f"{days} days? Give me a tight bulleted summary, then end with a "
        f"Citations: section listing every source URL on its own line. "
        f"Skip any source that's about travel visas, immigration, or "
        f"government visa policy."
    )

    payload = await _post_chat(model=MODEL_RECENCY, system=system, user=user)
    in_tok, out_tok = _extract_token_usage(payload)
    return {
        "summary": _extract_content(payload),
        "citations": _normalise_citations(payload),
        "cost_usd": _estimate_cost(MODEL_RECENCY, in_tok, out_tok),
        "model": MODEL_RECENCY,
        "raw_tokens": {"input": in_tok, "output": out_tok},
    }


async def strategic_posture(company: str) -> dict[str, Any]:
    """Two-paragraph strategic-posture summary, sonar-pro grade."""
    system = (
        "You are a senior fintech analyst. In 2 paragraphs, summarise "
        f"{company}'s current strategic posture: their bet, their "
        "constraint, their hiring direction. Cite sources for every "
        "factual claim."
    )
    user = (
        f"Summarise {company}'s strategic posture in two tight paragraphs. "
        "Paragraph 1: their bet (what they're trying to win) and the "
        "constraint working against them. Paragraph 2: where they're "
        "hiring and what that says about the next 12 months. End with "
        "a Citations: section with every source URL on its own line."
    )

    payload = await _post_chat(model=MODEL_STRATEGIC, system=system, user=user)
    in_tok, out_tok = _extract_token_usage(payload)
    return {
        "content": _extract_content(payload),
        "citations": _normalise_citations(payload),
        "cost_usd": _estimate_cost(MODEL_STRATEGIC, in_tok, out_tok),
        "model": MODEL_STRATEGIC,
        "raw_tokens": {"input": in_tok, "output": out_tok},
    }


async def verify_claim(claim: str, context: str = "") -> dict[str, Any]:
    """Lightweight fact-check. Boolean verified flag + cited evidence."""
    system = (
        "You are a fact-checking assistant. Given a claim, search the web "
        "and decide whether it is supported by reputable sources. Reply "
        "with a strict structure:\n"
        "VERDICT: true | false | uncertain\n"
        "EVIDENCE: <one or two sentences explaining why>\n"
        "Then a Citations: section listing every URL you used, one per line."
    )
    user = (
        f"Claim: {claim}\n"
        f"Context: {context or '(none provided)'}\n"
        "Is the claim supported by reputable sources?"
    )

    payload = await _post_chat(model=MODEL_RECENCY, system=system, user=user)
    in_tok, out_tok = _extract_token_usage(payload)
    content = _extract_content(payload)

    # Best-effort parse of "VERDICT: true|false|uncertain".
    verified = False
    lowered = content.lower()
    if "verdict: true" in lowered:
        verified = True
    elif "verdict: uncertain" in lowered:
        verified = False  # caller can re-read content for nuance

    return {
        "verified": verified,
        "evidence": content,
        "citations": _normalise_citations(payload),
        "cost_usd": _estimate_cost(MODEL_RECENCY, in_tok, out_tok),
        "model": MODEL_RECENCY,
        "raw_tokens": {"input": in_tok, "output": out_tok},
    }


# ═══════════════════════════════════════════════════════════════════════════
# JobScout v2 — per-target curated job discovery
# ═══════════════════════════════════════════════════════════════════════════
async def discover_jobs(
    company_name: str,
    *,
    target_archetypes: list[str] | None = None,
    industry_hint: str = "fintech / payments / financial technology",
    company_domain: str | None = None,
) -> dict[str, Any]:
    """Per-target curated job discovery via Perplexity Sonar.

    This is the JobScout v2 discovery primary. Asks Sonar a focused
    question: "What product/engineering jobs is {company} hiring right
    now?" with strong domain + content constraints so we get URLs that
    actually resolve to JD pages.

    Args:
        company_name:        the target company (e.g. "Marqeta", "Visa")
        target_archetypes:   role types to focus on (e.g.
                              ["Senior Product Manager",
                               "Head of Product",
                               "Group Product Manager"])
                              Defaults to the standard PM family.
        industry_hint:       narrows the entity (same disambiguation
                              pattern as recency_check)
        company_domain:      if known, helps Perplexity ground its
                              answers on the company's own careers page

    Returns:
        {
            "company_name": str,
            "candidates": list[{
                "url": str,
                "title": str,
                "published_at": str | None,
                "snippet": str | None,
            }],
            "cost_usd": float,
            "model": str,
            "raw_tokens": {input, output},
        }

    The caller is expected to feed each `candidate` into
    `agents.job_validation.validate_candidate` BEFORE persisting — the
    safeguards there (URL existence, domain whitelist, JD fingerprint,
    expiry scan, archetype filter, freshness, cross-source confidence)
    drop hallucinated and stale candidates.

    Cost: typical ~$0.005 per call. 71 targets × daily = ~$0.35/day.
    """
    archetypes = target_archetypes or [
        "Senior Product Manager",
        "Group Product Manager",
        "Lead Product Manager",
        "Head of Product",
        "Chief Product Officer",
        "VP Product",
        "Director of Product",
        "Principal Product Manager",
        "Product Manager",
        "Technical Program Manager",
    ]
    archetype_str = " · ".join(archetypes)
    domain_clause = (
        f"The company's careers page is at https://{company_domain}/careers "
        f"or similar; prefer URLs from {company_domain}, an ATS host "
        f"(boards.greenhouse.io, jobs.ashbyhq.com, jobs.lever.co, "
        f"smartrecruiters.com, *.myworkdayjobs.com, recruiting.adp.com), "
        f"or linkedin.com/jobs/view/. "
    ) if company_domain else ""

    system = (
        f"You are a research assistant helping a senior product manager find "
        f"jobs at a specific company. The target company is \"{company_name}\" "
        f"— specifically the company / brand operating in the {industry_hint} "
        f"industry, NOT any similarly-named travel / immigration / consumer-"
        f"product term that may share the name (e.g. for 'Visa' this means "
        f"the payments company NASDAQ:V, NOT US travel visas).\n\n"
        f"GEOGRAPHIC SCOPE — STRICT:\n"
        f"The candidate is targeting roles based in: UAE (Dubai, Abu Dhabi), "
        f"Saudi Arabia (Riyadh, Jeddah), Qatar (Doha), United Kingdom "
        f"(London), Singapore, OR fully Remote roles open to any of those "
        f"timezones (GMT+0 to GMT+8).\n"
        f"PREFER jobs in these target locations. If {company_name} has open "
        f"roles in these locations matching the archetype list, return THOSE "
        f"first. Only fall back to US/EU/India offices if {company_name} has "
        f"no openings in the target geos. Mark each job's location explicitly "
        f"in the title or as a separate POSTED:LOCATION field so the caller "
        f"can filter downstream.\n\n"
        f"Your job: list every CURRENTLY OPEN job posting at {company_name} "
        f"that matches any of these role families: {archetype_str}.\n\n"
        f"Strict citation rules — return ONLY URLs from these sources:\n"
        f"  - The company's own careers / jobs pages (the canonical company "
        f"    domain).\n"
        f"  - Known ATS hosts: boards.greenhouse.io/{{slug}}/jobs/{{id}}, "
        f"    jobs.ashbyhq.com/{{slug}}/{{id}}, jobs.lever.co/{{slug}}/{{id}}, "
        f"    jobs.smartrecruiters.com, *.myworkdayjobs.com, "
        f"    recruiting.adp.com.\n"
        f"  - linkedin.com/jobs/view/{{numeric-id}} — actual job detail URLs.\n"
        f"{domain_clause}\n"
        f"DO NOT include: travel.state.gov, .edu pages, aggregator pages "
        f"(indeed.com/jobs, glassdoor.com, levels.fyi, dice.com), generic "
        f"careers-home pages without specific job IDs, search-result pages, "
        f"company news / blog / press releases.\n\n"
        f"Each URL you return MUST be a direct link to a SINGLE job posting "
        f"(not a search result, not a careers home, not an article). The URL "
        f"must include a specific job ID or slug, not just /careers or "
        f"/jobs.\n\n"
        f"If you cannot find at least 3 OPEN jobs at {company_name} matching "
        f"the archetype list, say so explicitly — never pad with off-topic "
        f"citations or made-up URLs."
    )

    user = (
        f"List every currently OPEN job posting at {company_name} (the "
        f"{industry_hint} company) that matches: {archetype_str}.\n\n"
        f"Output format — STRICT, one job per line:\n"
        f"  TITLE: <exact job title>\n"
        f"  URL: <direct job-posting URL with ID/slug>\n"
        f"  POSTED: <ISO date if known, else 'unknown'>\n"
        f"---\n\n"
        f"Then end with a Citations: section listing every URL on its own line."
    )

    payload = await _post_chat(model=MODEL_RECENCY, system=system, user=user)
    in_tok, out_tok = _extract_token_usage(payload)
    content = _extract_content(payload)
    citations = _normalise_citations(payload)

    # Parse the structured "TITLE: ... URL: ... POSTED: ..." blocks.
    # We're tolerant — Sonar sometimes adds extra markup; we just pull the
    # three fields per block.
    candidates: list[dict[str, Any]] = []
    blocks = content.split("---") if content else []
    for block in blocks:
        title_match = _re.search(r"TITLE\s*:\s*(.+)", block, _re.IGNORECASE)
        url_match = _re.search(r"URL\s*:\s*(\S+)", block, _re.IGNORECASE)
        posted_match = _re.search(r"POSTED\s*:\s*(\S+)", block, _re.IGNORECASE)
        if not (title_match and url_match):
            continue
        url = url_match.group(1).strip().rstrip(".,;)")
        if not url.startswith(("http://", "https://")):
            continue
        title = title_match.group(1).strip()
        posted = posted_match.group(1).strip() if posted_match else None
        if posted in ("unknown", "n/a", "N/A", "-", "none"):
            posted = None
        candidates.append({
            "url": url,
            "title": title,
            "published_at": posted,
            "snippet": None,
        })

    # Fallback: if no structured blocks parsed but we got citations,
    # use citations as candidates (with title=company name + role hint).
    if not candidates and citations:
        for c in citations:
            url = c.get("url")
            if not url or not url.startswith(("http://", "https://")):
                continue
            candidates.append({
                "url": url,
                "title": c.get("title") or f"{company_name} — product role",
                "published_at": c.get("published_at"),
                "snippet": c.get("snippet"),
            })

    return {
        "company_name": company_name,
        "candidates": candidates,
        "cost_usd": _estimate_cost(MODEL_RECENCY, in_tok, out_tok),
        "model": MODEL_RECENCY,
        "raw_tokens": {"input": in_tok, "output": out_tok},
    }


# Lazy-import re inside the module so the top of the file stays clean.
import re as _re  # noqa: E402
