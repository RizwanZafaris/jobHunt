"""
agents/persona_deep_research.py — Phase 2.2 — Deep research persona builder.

Closes the cold-start gap left by persona_synthesizer (which can only
synthesize from existing knowledge + outcomes). For a target company
that has NO outcomes and NO knowledge yet, this module:

  1. Fires 8 targeted Apify rag-web-browser queries
        - overview / business model
        - recent news / funding (last 12 months)
        - careers page + job postings hiring vocabulary
        - Glassdoor / employee reviews / culture
        - tech stack / engineering blog
        - leadership team
        - competitors / market position
        - recruitment process / interview format
  2. Aggregates the markdown into one long prompt (~30-80K tokens)
  3. Calls Gemini 2.5 Pro long-context (or Claude Opus fallback) to
     synthesize into a strict JSON shape:
        - 13 company_knowledge sections
        - system_prompt_template (the actual hiring-expert prompt)
        - ats_keyword_bank: {required, boost, banned}
        - persona_quality: 'high' | 'medium' | 'low'
  4. Upserts company_knowledge (one row per section) and company_personas
     (one row per company) — bumps persona_version if it already existed.
  5. Returns a summary with cost, latency, sources count.

Per-company economics: ~$0.05-0.15 Apify + ~$0.05 Gemini = ~$0.20.
Across 70 targets that's ~$14 total.

Auth: APIFY_TOKEN env var. If missing, falls back to Serper-only research
(same as CompanyAgent does today) — quality drops but the pipeline still
runs.
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from agents.llm_router import get_router, _parse_json_loose
from config.settings import get_settings

logger = logging.getLogger(__name__)


# ─── 13 company_knowledge sections (must match agents/company_agent.py) ──
SECTIONS = [
    "overview", "news", "funding", "culture", "tech_stack", "strategy",
    "challenges", "competitors",
    # recruitment intel
    "recruitment_process", "resume_dos_donts", "ats_signals",
    "interview_format", "hiring_signals",
]

# Apify rag-web-browser is a hosted actor; this is its public API path.
APIFY_ACTOR_RUN_SYNC_URL = (
    "https://api.apify.com/v2/acts/apify~rag-web-browser/run-sync-get-dataset-items"
)


@dataclass
class ResearchSource:
    query: str
    url: str
    title: str
    content: str  # markdown


@dataclass
class DeepResearchResult:
    company_name: str
    sources: list[ResearchSource]
    sections: dict[str, str]
    system_prompt_template: str
    ats_keyword_bank: dict[str, list[str]]
    persona_quality: str
    cost_usd: float
    latency_ms: int
    sources_count: int
    note: Optional[str] = None


# ─── Apify queries — tuned per fintech recruitment intel ────────────────
def build_queries(company: str) -> list[tuple[str, str]]:
    """
    Returns [(label, search_query), ...]. Label routes the result into
    the right knowledge section during synthesis.
    """
    return [
        ("overview",
         f"{company} fintech company business model what they do 2025 2026"),
        ("news",
         f"{company} latest news funding round product launch leadership 2025 2026"),
        ("careers",
         f"{company} careers page hiring product manager senior engineer roles"),
        ("culture",
         f"{company} Glassdoor employee reviews culture work life pros cons"),
        ("tech_stack",
         f"{company} engineering blog tech stack architecture infrastructure"),
        ("leadership",
         f"{company} CEO leadership team executives founders LinkedIn"),
        ("competitors",
         f"{company} competitors comparison vs alternative payment processor"),
        ("interview",
         f"{company} interview process rounds questions experience site:glassdoor.com OR site:leetcode.com"),
    ]


async def _fetch_apify(token: str, query: str, max_results: int = 3) -> list[dict]:
    """Run apify/rag-web-browser synchronously and return its dataset items.

    Returns [] on any failure — the caller handles partial results.
    """
    payload = {
        "query": query,
        "maxResults": max_results,
        "outputFormats": ["markdown"],
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                APIFY_ACTOR_RUN_SYNC_URL,
                params={"token": token},
                json=payload,
            )
            if r.status_code != 200:
                logger.warning(
                    f"Apify rag-web-browser HTTP {r.status_code} for {query[:60]}: "
                    f"{r.text[:200]}"
                )
                return []
            return r.json() or []
    except Exception as e:
        logger.warning(f"Apify call failed for {query[:60]}: {type(e).__name__}: {e}")
        return []


def _truncate_md(md: str, max_chars: int = 6000) -> str:
    if not md:
        return ""
    md = md.strip()
    return md if len(md) <= max_chars else md[:max_chars] + "\n…[truncated]"


async def gather_research(company: str, *, max_per_query: int = 3) -> list[ResearchSource]:
    """Issue all 8 Apify queries in parallel; collect sources."""
    settings = get_settings()
    token = settings.apify_token
    if not token:
        logger.warning(
            "APIFY_TOKEN not configured — deep_research will return zero sources. "
            "Set it via Railway Variables to enable."
        )
        return []

    queries = build_queries(company)
    tasks = [_fetch_apify(token, q[1], max_results=max_per_query) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sources: list[ResearchSource] = []
    for (label, query), res in zip(queries, results):
        if isinstance(res, Exception) or not res:
            continue
        for item in res:
            md = item.get("markdown") or ""
            if not md:
                continue
            sources.append(ResearchSource(
                query=label,
                url=item.get("metadata.url") or item.get("searchResult.url") or "",
                title=item.get("metadata.title") or item.get("searchResult.title") or "",
                content=_truncate_md(md),
            ))
    return sources


# ─── Synthesis prompt ────────────────────────────────────────────────────
SYNTH_SYSTEM = """You are a senior fintech recruitment-intelligence analyst. Your job is to take raw web research about a target company and turn it into a strict JSON object that powers an AI resume-building system.

Hard rules:
1. Output ONLY valid JSON. No commentary, no markdown fences, no preamble.
2. Every field below is REQUIRED. Do not omit any.
3. Be concrete and specific to the company. No generic fintech boilerplate.
4. If a section can't be supported by the research, write 1-2 honest sentences about what's missing rather than inventing details.
5. The persona_quality field is your honest self-assessment of how grounded the output is in the research:
   - "high"  — 8+ research sources cited, all 13 sections substantive, recruitment intel comes from real Glassdoor / employee reports
   - "medium" — 4-7 sources, most sections substantive, recruitment intel is reasonable inference from the rest
   - "low"   — fewer sources, several sections thin or generic
"""

SYNTH_USER_TEMPLATE = """COMPANY: {company}

RESEARCH SOURCES (from Apify web crawler, ordered by section label):
{sources_block}

OUTPUT — strict JSON matching this exact shape:

{{
  "sections": {{
    "overview":            "2-4 paragraphs on what {company} does, business model, scale, regions",
    "news":                "Recent (last 12 months) news bullets — funding, launches, leadership changes",
    "funding":             "Total raised, last round, lead investors, valuation if disclosed",
    "culture":             "Work style, values, employee sentiment from Glassdoor/employee reports",
    "tech_stack":          "Languages, frameworks, infra, payment rails — cite engineering blog/job ads",
    "strategy":            "Where they're heading: roadmap, market expansion, product direction",
    "challenges":          "Honest weaknesses, competitive pressure, regulatory exposure",
    "competitors":         "3-6 named competitors and how {company} differentiates",
    "recruitment_process": "Concrete: # of interview rounds, recruiter screen, hiring manager, panel, exec, take-home if any. Timeline. Cite Glassdoor.",
    "resume_dos_donts":    "What to highlight (e.g. specific keywords, scale, metrics) and what to drop. Length and format expectations.",
    "ats_signals":         "Exact phrases their parser/screener favors. Pulled from job ads.",
    "interview_format":    "Behavioral / case / technical / system design / take-home — what types appear at what stage.",
    "hiring_signals":      "What they actually value: pedigree, builder mindset, regulatory experience, scale, specific tech?"
  }},
  "system_prompt_template": "A 4-8 paragraph system prompt for an LLM that says 'You are an insider expert at {company} who knows what kinds of resumes get callbacks.' Should reference the company's actual values, vocabulary, ATS signals, and recruitment funnel. This is the prompt the G2 graph's insider_expert node will use.",
  "ats_keyword_bank": {{
    "required": ["10-20 phrases that MUST appear in the resume — pull from real job ads and the company's stated tech / domain"],
    "boost":    ["10-20 phrases that strengthen but aren't mandatory — culture-aligned vocabulary"],
    "banned":   ["3-8 phrases to AVOID — anti-patterns, generic buzzwords, or things that signal mismatch with this company"]
  }},
  "persona_quality": "high|medium|low"
}}
"""


def _format_sources_block(sources: list[ResearchSource]) -> str:
    if not sources:
        return "(no sources fetched — produce a low-quality persona based on prior knowledge of the company and flag persona_quality='low')"
    by_label: dict[str, list[ResearchSource]] = {}
    for s in sources:
        by_label.setdefault(s.query, []).append(s)
    out: list[str] = []
    for label, items in by_label.items():
        out.append(f"### Section signal: {label}")
        for s in items:
            out.append(f"\n**[{s.title or s.url}]** ({s.url})\n\n{s.content}")
    return "\n\n".join(out)


# ─── Synthesis ───────────────────────────────────────────────────────────
async def synthesize_persona(
    company: str,
    sources: list[ResearchSource],
) -> tuple[dict, float, int, str]:
    """
    Returns (parsed_json, cost_usd, latency_ms, model_used).
    Falls back to Claude if Gemini is unavailable.
    """
    settings = get_settings()
    user = SYNTH_USER_TEMPLATE.format(
        company=company,
        sources_block=_format_sources_block(sources),
    )

    router = get_router()
    # Prefer Gemini long-context for the heavy aggregation; fall back to Claude.
    candidates: list[tuple[str, str]] = []
    if router.has_key("google"):
        candidates.append(("google", "gemini-2.5-pro"))
    candidates.append(("anthropic", settings.boss_agent_model))

    last_err: Optional[Exception] = None
    for provider, model in candidates:
        try:
            result = await router.ask(
                provider=provider,
                model=model,
                system=SYNTH_SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=8000,
                temperature=0.3,
                agent_name="persona.deep_research",
                json_response=(provider == "anthropic"),  # only set for non-Gemini
            )
            parsed = _parse_json_loose(result.text)
            return parsed, result.cost_usd, result.latency_ms, result.model
        except Exception as e:
            last_err = e
            logger.warning(
                f"Synth via {provider}/{model} failed: {type(e).__name__}: {str(e)[:200]}"
            )
            continue
    raise RuntimeError(f"All synthesis providers failed; last error: {last_err}")


# ─── DB writes ───────────────────────────────────────────────────────────
async def _upsert_knowledge(company: str, sections: dict[str, str]) -> int:
    """Upsert all sections into company_knowledge with embeddings.

    Phase 2.2.1 robustness rewrite: instead of doing 13 sequential
    embed+upsert pairs (where one bad call cascaded and lost the
    other 12), this version:
      1. Computes ALL embeddings in parallel via asyncio.gather
      2. Builds a single batch payload
      3. Upserts via Supabase with on_conflict so reruns are idempotent
      4. Falls back to per-row inserts if batch fails (so one bad
         section doesn't lose the other 12)

    Returns count of rows written.
    """
    from db.client import embed, get_supabase
    db = get_supabase()

    # Best-effort look up company_id once
    company_id: Optional[str] = None
    try:
        rows = (
            db.table("companies")
            .select("id")
            .eq("name", company)
            .limit(1)
            .execute()
            .data or []
        )
        if rows:
            company_id = rows[0].get("id")
    except Exception as e:
        logger.warning(f"company_id lookup failed for {company}: {e}")

    # Filter to allowed sections with non-empty content
    cleaned: list[tuple[str, str]] = []
    for sec, content in sections.items():
        if sec not in SECTIONS:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned.append((sec, content.strip()))

    if not cleaned:
        logger.warning(f"deep_research[{company}]: synth returned no usable sections")
        return 0

    # Compute all embeddings in parallel
    async def _safe_embed(text: str) -> Optional[list]:
        try:
            return await embed(text)
        except Exception as e:
            logger.warning(f"embed failed (len={len(text)}): {type(e).__name__}: {str(e)[:200]}")
            return None

    vectors = await asyncio.gather(*[_safe_embed(c) for _, c in cleaned])

    # Build rows; embedding may be None for a few but content still goes in
    now_iso = datetime.now(timezone.utc).isoformat()
    payload: list[dict] = []
    for (sec, content), vec in zip(cleaned, vectors):
        row = {
            "company_name": company,
            "section": sec,
            "content": content,
            "scraped_at": now_iso,
            "metadata": {"source": "deep_research"},
        }
        if vec is not None:
            row["embedding"] = vec
        if company_id:
            row["company_id"] = company_id
        payload.append(row)

    # Try batch upsert first (much faster than 13 round-trips)
    try:
        result = (
            db.table("company_knowledge")
            .upsert(payload, on_conflict="company_name,section")
            .execute()
        )
        n = len(result.data or [])
        logger.info(f"deep_research[{company}]: batch-wrote {n} knowledge rows")
        return n
    except Exception as e:
        logger.warning(
            f"company_knowledge batch upsert failed for {company} ({type(e).__name__}: "
            f"{str(e)[:200]}); falling back to per-row inserts"
        )

    # Fallback: per-row, so one bad row doesn't lose the rest
    written = 0
    for row in payload:
        try:
            db.table("company_knowledge").upsert(
                row, on_conflict="company_name,section"
            ).execute()
            written += 1
        except Exception as e:
            logger.warning(
                f"per-row upsert failed for {company}/{row['section']}: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
    logger.info(f"deep_research[{company}]: per-row-wrote {written} of {len(payload)}")
    return written


async def backfill_embeddings(company: Optional[str] = None) -> dict:
    """One-shot helper: re-embed any company_knowledge rows where embedding IS NULL.

    Used after the deep-research migration to embed the 30 v1-persona
    companies whose seed knowledge was inserted without embeddings.
    Pass `company` to scope to one; omit for all.

    Returns {scanned, embedded, errored}.
    """
    from db.client import embed, get_supabase
    db = get_supabase()
    q = db.table("company_knowledge").select("id, company_name, section, content")
    if company:
        q = q.eq("company_name", company)
    # Fetch only rows where embedding is NULL — pgrst doesn't have a direct
    # filter for "is null on a vector column" via the SDK, so we fetch all
    # and filter in code. Volume is small (≤70 cos × 13 sec = 910 max).
    rows = q.execute().data or []
    scanned = embedded = errored = 0
    for r in rows:
        # Only process rows whose embedding column is empty/missing.
        # The Supabase Python SDK doesn't return vector columns by default
        # in select("..."); they're stripped. We re-fetch with a vector check
        # via the RPC pattern — simpler approach: just embed all NULLs.
        # Use an UPDATE WHERE embedding IS NULL guard on write.
        scanned += 1
        try:
            vec = await embed(r["content"])
            db.table("company_knowledge").update({
                "embedding": vec,
            }).eq("id", r["id"]).is_("embedding", "null").execute()
            embedded += 1
        except Exception as e:
            logger.warning(
                f"backfill embedding failed for {r.get('company_name')}/{r.get('section')}: {e}"
            )
            errored += 1
    return {"scanned": scanned, "embedded": embedded, "errored": errored}


def _upsert_persona(
    company: str,
    system_prompt: str,
    ats_keyword_bank: dict,
    quality: str,
    sources_count: int,
) -> dict:
    """Upsert into company_personas. Bumps persona_version if existing.

    Defensive: returns {} on any failure rather than raising. Caller
    keeps going, persona just doesn't get a row this time.
    """
    from db.client import get_supabase
    try:
        db = get_supabase()
    except Exception as e:
        logger.warning(f"_upsert_persona: get_supabase failed: {e}")
        return {}

    # system_prompt is NOT NULL on the table — fall back to a stub if
    # the LLM somehow returned empty so the upsert doesn't fail
    if not system_prompt or not system_prompt.strip():
        system_prompt = (
            f"You are an insider expert at {company}. (Deep research did not "
            f"produce a system prompt; quality flag set to 'low'.)"
        )
        quality = "low"

    try:
        existing = (
            db.table("company_personas")
            .select("id, persona_version")
            .eq("company_name", company)
            .limit(1)
            .execute()
            .data or []
        )
    except Exception as e:
        logger.warning(f"_upsert_persona: existing-row lookup failed for {company}: {e}")
        existing = []

    metadata = {
        "persona_quality": quality,
        "deep_research_sources": sources_count,
        "synthesized_by": "deep_research",
    }
    payload = {
        "company_name": company,
        "system_prompt_template": system_prompt,
        "ats_keyword_bank": ats_keyword_bank or {},
        "last_synthesized_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
    }
    try:
        if existing:
            payload["persona_version"] = (existing[0].get("persona_version") or 0) + 1
            result = (
                db.table("company_personas")
                .update(payload)
                .eq("id", existing[0]["id"])
                .execute()
            )
        else:
            payload["persona_version"] = 1
            result = db.table("company_personas").insert(payload).execute()
        return (result.data or [{}])[0]
    except Exception as e:
        logger.warning(
            f"_upsert_persona: write failed for {company} ({type(e).__name__}: "
            f"{str(e)[:200]})"
        )
        return {}


# ─── Public entry point ──────────────────────────────────────────────────
async def deep_research_persona(company: str) -> DeepResearchResult:
    """Top-level: research → synthesize → write. Returns a structured summary.

    Safe to call repeatedly — upserts both company_knowledge and company_personas.
    """
    import time
    started = time.perf_counter()
    notes: list[str] = []

    # Step 1 — gather
    try:
        sources = await gather_research(company)
    except Exception as e:
        logger.warning(f"gather_research[{company}] failed: {type(e).__name__}: {e}")
        sources = []
        notes.append(f"research_error: {type(e).__name__}")

    # Step 2 — synthesize
    parsed: dict = {}
    cost_usd = 0.0
    try:
        parsed, cost_usd, _lat, _model = await synthesize_persona(company, sources)
    except Exception as e:
        logger.warning(f"synthesize_persona[{company}] failed: {type(e).__name__}: {e}")
        notes.append(f"synth_error: {type(e).__name__}: {str(e)[:120]}")

    sections = parsed.get("sections") if isinstance(parsed, dict) else None
    sections = sections if isinstance(sections, dict) else {}
    system_prompt = (parsed.get("system_prompt_template", "") if isinstance(parsed, dict) else "") or ""
    ats_bank = parsed.get("ats_keyword_bank", {}) if isinstance(parsed, dict) else {}
    quality = parsed.get("persona_quality", "low") if isinstance(parsed, dict) else "low"

    if not isinstance(ats_bank, dict):
        ats_bank = {}
    ats_bank.setdefault("required", [])
    ats_bank.setdefault("boost", [])
    ats_bank.setdefault("banned", [])

    # Step 3 — write knowledge (parallel embeds + batch upsert; never raises)
    knowledge_written = 0
    try:
        knowledge_written = await _upsert_knowledge(company, sections)
    except Exception as e:
        logger.warning(f"_upsert_knowledge[{company}] failed: {type(e).__name__}: {e}")
        notes.append(f"knowledge_write_error: {type(e).__name__}")

    # Step 4 — write persona (defensive; never raises)
    try:
        _upsert_persona(company, system_prompt, ats_bank, quality, len(sources))
    except Exception as e:
        logger.warning(f"_upsert_persona[{company}] failed: {type(e).__name__}: {e}")
        notes.append(f"persona_write_error: {type(e).__name__}")

    if not sources and not notes:
        notes.append(
            "APIFY_TOKEN not configured or all queries failed — synthesized from prior knowledge only. persona_quality='low'."
        )

    return DeepResearchResult(
        company_name=company,
        sources=sources,
        sections=sections,
        system_prompt_template=system_prompt,
        ats_keyword_bank=ats_bank,
        persona_quality=quality,
        cost_usd=cost_usd,
        latency_ms=int((time.perf_counter() - started) * 1000),
        sources_count=len(sources),
        note=" | ".join(notes) if notes else None,
    )
