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


# ─── Public surface ───────────────────────────────────────────────────────
async def recency_check(company: str, days: int = 30) -> dict[str, Any]:
    """Find news from the last `days` days about `company`.

    Returns:
        {
            summary: str,
            citations: [{url, title?, published_at?, snippet?}],
            cost_usd: float,
            model: str,
            raw_tokens: {input: int, output: int},
        }
    """
    system = (
        f"You are a research assistant. Find news from the last {days} days "
        f"about {company}. Return citations with URLs and published_at. "
        "Focus on hiring direction, product launches, leadership changes, "
        "financial results, or strategic shifts that would matter to a "
        "senior product manager preparing to apply for a role there."
    )
    user = (
        f"What's new at {company} in the last {days} days? "
        "Give me a tight bulleted summary, then end with a Citations: section "
        "listing every source URL on its own line."
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
