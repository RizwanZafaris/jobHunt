"""
agents/jd_extractor.py — FRD-14 URL Job Rater: fetch + extract a single JD.

Two responsibilities, both feeding the URL Job Rater (api/job_rater.py):

  1. fetch_jd_from_url(url)  → (markdown, failure_reason)
     Pulls a single job-posting page to clean markdown via the SAME Apify
     rag-web-browser actor the persona pipeline uses. URL is the primary
     input path. Returns a structured failure_reason (not an exception) so
     the endpoint can fall back to asking the user to paste the JD text:
        None           — success, markdown is usable
        'fetch_failed' — Apify returned nothing / errored
        'thin_content' — fetched but < MIN_JD_CHARS of text (login wall, JS app)
        'no_token'     — APIFY_TOKEN not configured

  2. extract_jd(raw_md)  → structured dict
     One Sonnet call (via the hardened router, JSON-validated) that pulls
     {title, company, seniority, location, comp_range, responsibilities[],
      requirements[], ats_keywords[], raw_jd_md} out of the raw JD text.

     CRITICAL anti-hallucination rule: the model extracts ONLY what is
     present in the JD. Absent fields are null / empty lists — never guessed.
     This is enforced in the system prompt and defended in parsing.

No new dependencies: reuses httpx (already imported by the persona module's
pattern), the llm_router, and config.settings. Mirrors the call pattern of
agents/scoring_agent.py::_score_growth.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from agents.llm_router import get_router

logger = logging.getLogger(__name__)

# Same actor + endpoint the persona deep-research pipeline uses.
APIFY_ACTOR_RUN_SYNC_URL = (
    "https://api.apify.com/v2/acts/apify~rag-web-browser/run-sync-get-dataset-items"
)

_LLM_PROVIDER = "anthropic"
_LLM_MODEL = "claude-sonnet-4-6"

# Below this many chars of fetched text we treat the page as unusable
# (login wall / bot block / JS-only shell) and ask the user to paste.
MIN_JD_CHARS = 200
# Hard cap on JD text sent to the extractor (cost + context control).
MAX_JD_CHARS = 12000


# ─────────────────────────────────────────────────────────────────────
# 1. URL → clean markdown (Apify rag-web-browser)
# ─────────────────────────────────────────────────────────────────────
async def fetch_jd_from_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch a single job-posting URL to clean markdown.

    Returns (markdown, failure_reason). Exactly one is non-None:
      - (md, None)          on success
      - (None, '<reason>')  on failure — reason in
        {'no_token','fetch_failed','thin_content'}

    Never raises — the caller turns a failure_reason into a
    `needs_jd_text` response so the user can paste the JD instead.
    """
    from config.settings import get_settings

    token = get_settings().apify_token
    if not token:
        logger.warning("fetch_jd_from_url: APIFY_TOKEN not configured")
        return None, "no_token"

    # rag-web-browser treats a URL `query` as a direct single-page fetch.
    payload = {"query": url, "maxResults": 1, "outputFormats": ["markdown"]}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                APIFY_ACTOR_RUN_SYNC_URL,
                params={"token": token},
                json=payload,
            )
            # run-sync-get-dataset-items returns 200 or 201 on success.
            if r.status_code not in (200, 201):
                logger.warning(
                    f"fetch_jd_from_url: Apify HTTP {r.status_code} for "
                    f"{url[:80]}: {r.text[:200]}"
                )
                return None, "fetch_failed"
            items = r.json() or []
    except Exception as e:
        logger.warning(
            f"fetch_jd_from_url: Apify call failed for {url[:80]}: "
            f"{type(e).__name__}: {e}"
        )
        return None, "fetch_failed"

    md = _first_markdown(items)
    if not md or len(md.strip()) < MIN_JD_CHARS:
        return None, "thin_content"
    return md.strip(), None


def _first_markdown(items: list[dict]) -> str:
    """Pull the markdown field from the first usable Apify dataset item.

    Items are shaped {markdown, metadata:{url,title}, searchResult:{...}}.
    We only need the markdown body here.
    """
    for item in items or []:
        md = item.get("markdown") or ""
        if md:
            return md
    return ""


# ─────────────────────────────────────────────────────────────────────
# 2. Raw JD markdown → structured fields (LLM, no fabrication)
# ─────────────────────────────────────────────────────────────────────
_EXTRACT_SYSTEM = """You are a precise job-description parser. You are given the raw text \
of ONE job posting. Extract ONLY information that is explicitly present in the text.

ABSOLUTE RULES:
- Never invent, infer, or guess. If a field is not stated in the JD, return null \
(for scalars) or an empty list (for lists). Do NOT fill gaps from world knowledge.
- comp_range: only if an explicit salary / compensation figure or range appears; else null.
- ats_keywords: concrete skills, tools, certifications, and domain terms the JD itself \
emphasizes (e.g. "tokenization", "ISO 8583", "SQL", "stakeholder management"). 8-15 items \
max, drawn from the text. Do not pad.
- responsibilities / requirements: short phrases lifted/condensed from the JD, not prose.

Return STRICT JSON only, no prose, with exactly these keys:
{
  "title": string|null,
  "company": string|null,
  "seniority": string|null,            // e.g. "Senior", "Director", "Lead" — only if stated/clear
  "location": string|null,
  "comp_range": string|null,
  "responsibilities": string[],
  "requirements": string[],
  "ats_keywords": string[]
}"""


async def extract_jd(raw_md: str) -> dict[str, Any]:
    """Extract structured fields from raw JD markdown via one Sonnet call.

    Returns a dict with the keys in _EXTRACT_SYSTEM plus `raw_jd_md` (the
    truncated source text the scorer will read as `description`). Absent
    fields come back null / []. Never raises — on parse failure returns a
    skeleton with the raw text preserved so scoring can still proceed.
    """
    text = (raw_md or "").strip()
    if not text:
        return _skeleton("")

    snippet = text[:MAX_JD_CHARS]
    user = f"JOB POSTING (raw):\n\n{snippet}\n\nExtract the fields as strict JSON."

    try:
        result = await get_router().ask(
            provider=_LLM_PROVIDER,
            model=_LLM_MODEL,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=900,
            temperature=0.0,  # deterministic extraction
            agent_name="jd_extractor.extract",
        )
        parsed = _parse_extract_json(result.text)
    except Exception as e:
        logger.warning(f"extract_jd: LLM/parse failed: {type(e).__name__}: {e}")
        return _skeleton(snippet)

    return {
        "title": _clean_str(parsed.get("title")),
        "company": _clean_str(parsed.get("company")),
        "seniority": _clean_str(parsed.get("seniority")),
        "location": _clean_str(parsed.get("location")),
        "comp_range": _clean_str(parsed.get("comp_range")),
        "responsibilities": _clean_list(parsed.get("responsibilities")),
        "requirements": _clean_list(parsed.get("requirements")),
        "ats_keywords": _clean_list(parsed.get("ats_keywords")),
        "raw_jd_md": snippet,
    }


def to_job_dict(extracted: dict[str, Any], *, url: Optional[str] = None) -> dict[str, Any]:
    """Map an extract_jd() result into the `job` dict shape the scorer reads.

    score_job_dict / the 6 dimension scorers read: company, title (role),
    description (the JD text), and optionally archetype/location. We pass the
    raw JD markdown as `description` so the LLM dimension scorers see the full
    posting, exactly as they do for a DB-loaded job.
    """
    return {
        "title": extracted.get("title") or "",
        "company": extracted.get("company") or "",
        "description": extracted.get("raw_jd_md") or "",
        "url": url,
        "location": extracted.get("location"),
        # no archetype/match_score — scorers derive what they need from text
    }


# ─────────────────────────────────────────────────────────────────────
# parse / clean helpers
# ─────────────────────────────────────────────────────────────────────
def _parse_extract_json(text: str) -> dict[str, Any]:
    """Tolerant JSON parse: prefer the router's loose parser if present,
    else strip code fences and json.loads. Returns {} on total failure."""
    import json

    if not text:
        return {}
    # Reuse the router's loose parser when available (handles fenced/with-prose).
    try:
        from agents.llm_router import _parse_json_loose  # type: ignore
        obj = _parse_json_loose(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Fallback: strip ```json fences and parse.
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t[4:].strip() if t.lower().startswith("json") else t.strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _clean_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"null", "none", "n/a", "not specified", "unknown"}:
        return None
    return s[:300]


def _clean_list(v: Any, cap: int = 20) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        s = str(item).strip()
        if s and s.lower() not in {"null", "none", "n/a"}:
            out.append(s[:200])
        if len(out) >= cap:
            break
    return out


def _skeleton(raw_md: str) -> dict[str, Any]:
    """Fallback structure when extraction can't run — preserves raw text so
    scoring can still operate on the description."""
    return {
        "title": None,
        "company": None,
        "seniority": None,
        "location": None,
        "comp_range": None,
        "responsibilities": [],
        "requirements": [],
        "ats_keywords": [],
        "raw_jd_md": (raw_md or "")[:MAX_JD_CHARS],
    }
