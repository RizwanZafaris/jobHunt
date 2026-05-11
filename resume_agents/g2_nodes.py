"""
resume_agents/g2_nodes.py — The 12 LangGraph node functions.

Each node is an async function: ResumeState -> dict (state patch).
Nodes call agents.llm_router via async wrappers; never instantiate
clients directly.

Node summary:
  entry              pure code — load inputs, create resume_builds row
  insider_expert     Gemini 2.5 Pro + Google Search grounding
  advocate           Claude Opus 4.5 — career-arc framing
  meta_critic        Gemini 2.5 Pro — long context, reads past transcripts
  writer             Claude Opus 4.5 — executive prose
  ats_critic_a       DeepSeek-R1 — ATS scoring (reasoning model)
  ats_critic_b       Kimi K2 — ATS scoring (independent second opinion)
  merge_critique     pure code — union(a, b), strictest score, deduped fixes
  orchestrator       Claude Opus 4.5 — converged? loop or proceed
  polisher           Claude Opus 4.5 — final voice gate, self-score
  cover_email        Claude Opus 4.5 — personalized email
  export             pure code — DOCX render + Storage upload + DB update
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any

from agents.llm_router import get_router, _parse_json_loose
from config.settings import get_settings
from resume_agents.g2_state import ResumeState, make_turn, truncate

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# Node 1 — entry_point (pure code)
# ═════════════════════════════════════════════════════════════════════════
async def entry_node(state: ResumeState) -> dict:
    """
    Hydrate ResumeState from Supabase. Creates the resume_builds row in
    'running' status. After this node runs, all subsequent nodes can rely
    on master_resume_md, company_persona, past_transcripts being present.

    Expects in state: job_id, company_name (canonicalised by caller).
    """
    from resume_agents.g2_io import (
        load_job,
        load_company_persona,
        load_past_transcripts,
        render_master_resume_md,
        create_resume_build,
    )
    settings = get_settings()

    job_id = state["job_id"]
    company_name = state["company_name"]

    job = load_job(job_id)
    company_persona = load_company_persona(company_name)
    past_transcripts = load_past_transcripts(
        company_name, n=settings.g2_meta_critic_lookback
    )
    master_resume_md = render_master_resume_md()
    persona_version = (company_persona or {}).get("persona_version")
    resume_build = create_resume_build(
        job_id=job_id,
        company_name=company_name,
        persona_version=persona_version,
    )
    return {
        "job": job,
        "company_persona": company_persona,
        "past_transcripts": past_transcripts,
        "master_resume_md": master_resume_md,
        "resume_build_id": resume_build["id"],
        "iteration": 0,
        "converged": False,
        "transcript": [
            make_turn(
                node="entry",
                input_summary=f"job_id={job_id} company={company_name}",
                output={
                    "persona_loaded": bool(company_persona),
                    "persona_version": persona_version,
                    "past_transcripts_count": len(past_transcripts),
                    "past_transcripts_source": (
                        past_transcripts[0]["source"] if past_transcripts else None
                    ),
                    "master_resume_chars": len(master_resume_md),
                    "resume_build_id": resume_build["id"],
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 2 — insider_expert (Gemini 2.5 Pro + grounding)
# ═════════════════════════════════════════════════════════════════════════
INSIDER_EXPERT_FALLBACK_SYSTEM = """You are a senior fintech hiring expert with 20+ years inside payments companies.

Without specific company knowledge to draw on, apply best-practice fintech hiring norms:
- Standard ATS keyword density: ≥70% of JD's top-20 terms appearing in the resume
- Quantified outcomes: ≥70% of bullets contain numbers (%, $, count, time saved)
- Action-verb diversity, no 'responsible for'
- Reverse-chronological with 3-6 bullets per role, 1 page if <10y exp / 2 pages if 10y+

Be direct, terse, prescriptive. No hedging."""


async def insider_expert_node(state: ResumeState) -> dict:
    """
    Reads: job, company_persona, master_resume_md
    Outputs: expert_notes — top 5 keywords, 3 cultural signals, 3 things to drop, summary line.

    Phase 2.2.2: now uses pgvector RAG to fetch the top-k most relevant
    research chunks for the SPECIFIC JD instead of dumping the full
    static system prompt. Context is sharper, prompt is tighter, latency
    drops, and the persona's success/failure_patterns get injected
    inline so the writer downstream can pattern-match against real
    bullets that historically convert at this company.
    """
    settings = get_settings()
    persona = state.get("company_persona") or {}
    job = state["job"]
    company_name = state["company_name"]

    # ─── RAG: top-k relevant research chunks for THIS jd ────────────────
    # Falls back gracefully if the search RPC errors or returns nothing.
    #
    # Phase 3: each retrieved row contributes a `cite:knowledge_id=<uuid>`
    # breadcrumb in the prompt (so the LLM can refer back to a specific row)
    # AND we collect the IDs into `cited_knowledge_ids` for the transcript
    # turn. agents/outcome_to_persona.credit_outcome reads either the
    # structured field OR regexes the free text — defence in depth so a
    # later prompt edit can't silently break credit assignment.
    rag_block = ""
    cited_knowledge_ids: list[str] = []
    try:
        from db.client import search_company_knowledge
        jd_query = f"{job.get('title', '')} {(job.get('description') or '')[:1500]}"
        chunks = await search_company_knowledge(
            company_name=company_name,
            query=jd_query,
            match_count=5,
        )
        if chunks:
            rag_lines = []
            for c in chunks:
                sec = c.get("section", "?")
                sim = c.get("similarity", 0)
                content = (c.get("content") or "")[:1000]
                kid = c.get("id")
                # Append the cite marker only when we actually have a real
                # uuid (post-migration-009). If the RPC is still v1 we skip
                # the marker and the fallback path in outcome_to_persona
                # handles credit assignment via "top-k for company".
                if kid:
                    cited_knowledge_ids.append(str(kid))
                    rag_lines.append(
                        f"### [{sec}] (relevance {sim:.2f}) "
                        f"cite:knowledge_id={kid}\n{content}"
                    )
                else:
                    rag_lines.append(f"### [{sec}] (relevance {sim:.2f})\n{content}")
            rag_block = "\n\n".join(rag_lines)
    except Exception as e:
        logger.warning(f"insider_expert RAG fetch failed for {company_name}: {e}")

    # ─── Persona — system prompt + ATS bank + success/failure patterns ─
    system = persona.get("system_prompt_template") or INSIDER_EXPERT_FALLBACK_SYSTEM
    ats_bank = persona.get("ats_keyword_bank") or {}
    success_patterns = persona.get("success_patterns") or []
    failure_patterns = persona.get("failure_patterns") or []

    persona_inline = ""
    if isinstance(ats_bank, dict) and (ats_bank.get("required") or ats_bank.get("boost") or ats_bank.get("banned")):
        persona_inline += "\n\nATS KEYWORD BANK:\n"
        if ats_bank.get("required"):
            persona_inline += f"  Required: {', '.join(ats_bank['required'][:25])}\n"
        if ats_bank.get("boost"):
            persona_inline += f"  Boost:    {', '.join(ats_bank['boost'][:25])}\n"
        if ats_bank.get("banned"):
            persona_inline += f"  Banned:   {', '.join(ats_bank['banned'][:10])}\n"

    if success_patterns:
        persona_inline += "\nSUCCESS PATTERNS (bullet templates that historically convert):\n"
        for p in success_patterns[:8]:
            persona_inline += f"  ✓ {p}\n"

    if failure_patterns:
        persona_inline += "\nFAILURE PATTERNS (anti-patterns to avoid):\n"
        for p in failure_patterns[:8]:
            persona_inline += f"  ✗ {p}\n"

    user = f"""TARGET ROLE:
{job.get('title', '')} — {company_name}
{job.get('location', '') or ''}

JOB DESCRIPTION:
{(job.get('description') or '')[:6000]}

CANDIDATE MASTER RESUME (excerpts):
{state['master_resume_md'][:4000]}

{("MOST RELEVANT COMPANY RESEARCH (pgvector top-5 for this JD):" + chr(10) + rag_block + chr(10)) if rag_block else ""}{persona_inline}

Produce your positioning notes:
1. Top 5 keywords/phrases this resume MUST contain (anchor on the ATS Required bank above; cite specific JD terms where they overlap)
2. Top 3 cultural/strategic signals to weave in (cite the most relevant research chunks you saw)
3. 3 things to DOWNPLAY or remove (use Failure Patterns as anti-targets)
4. The exact summary statement (3-4 lines) you'd put at the top — pattern-match against Success Patterns
"""

    result = await get_router().ask(
        provider="google",
        model=settings.g2_insider_expert_model,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=2500,
        temperature=0.25,
        tools=[{"type": "google_search"}],
        agent_name="g2.insider_expert",
    )
    # ─── Build the transcript turn ─────────────────────────────────────
    # The `user` prompt already contains the `cite:knowledge_id=<uuid>`
    # markers (one per RAG row) via rag_block. truncate(user) cuts to 500
    # chars and the markers live deep inside, so we append a compact
    # citation suffix to input_summary that always survives truncation.
    # This is what agents/outcome_to_persona._CITE_RE regexes against.
    cite_suffix = ""
    if cited_knowledge_ids:
        cite_suffix = " | citations: " + " ".join(
            f"cite:knowledge_id={k}" for k in cited_knowledge_ids
        )
    input_summary_text = truncate(user, 500 - len(cite_suffix)) + cite_suffix

    turn = make_turn(
        node="insider_expert",
        provider=result.provider,
        model=result.model,
        input_summary=input_summary_text,
        output=result.text,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
    # Structured field for forward-looking consumers (the regex path is the
    # current authoritative reader; this is defence in depth so a future
    # consumer can avoid free-text parsing).
    if cited_knowledge_ids:
        turn["cited_knowledge_ids"] = cited_knowledge_ids

    return {
        "expert_notes": result.text,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [turn],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 3 — advocate (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
ADVOCATE_SYSTEM = """You are the candidate's career advocate.

Your job: ensure the resume:
- Surfaces the candidate's strongest, most relevant achievements for THIS specific job
- Doesn't undersell them or strip out genuinely impressive context
- Frames career arc coherently (no 'why did you go from X to Y' gaps)
- Uses metrics and concrete outcomes wherever possible

You will push back on the Insider Expert when their suggestions would dilute true strengths.
You will agree when their suggestions genuinely improve fit.

Output style: collaborative but firm. Cite specific lines from the master resume."""


async def advocate_node(state: ResumeState) -> dict:
    settings = get_settings()
    user = f"""JOB:
{state['job'].get('title', '')} @ {state['company_name']}

CANDIDATE MASTER RESUME:
{state['master_resume_md'][:6000]}

PAST TRANSCRIPTS (what worked / didn't work for this candidate before):
{json.dumps(state.get('past_transcripts', [])[:3], indent=2, default=str)[:2000]}

Identify:
1. The 3-5 strongest, most JD-relevant achievements from the master resume
2. Anything the Insider's notes would strip that you think MUST stay (with rationale)
3. Career-arc framing recommendation (1-2 sentences)
"""
    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_advocate_model,
        system=ADVOCATE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=1500,
        temperature=0.3,
        agent_name="g2.advocate",
    )
    return {
        "advocate_notes": result.text,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="advocate",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output=result.text,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 4 — meta_critic (Gemini 2.5 Pro, long context)
# ═════════════════════════════════════════════════════════════════════════
META_CRITIC_SYSTEM = """You are an expert resume meta-reviewer.

You have access to the last several resumes built (or gap dialogues recorded) for
this same company. Your job: identify recurring failure patterns the Writer must
avoid in the upcoming draft.

Look for:
- Keywords that ATS Critics consistently flag as missing
- Bullet structures that consistently get critiqued
- Quantification gaps that recur
- Tone/voice issues called out repeatedly

Output a concise list of warnings (each one a single sentence). If past
transcripts are sparse or empty, output 1-3 generic fintech-PM ATS warnings
based on common pitfalls — don't fabricate company-specific patterns from
no data."""


async def meta_critic_node(state: ResumeState) -> dict:
    settings = get_settings()
    past = state.get("past_transcripts", [])
    if not past:
        # Cold-start: hand-coded warnings, no LLM call needed
        warnings = [
            "Ensure ≥70% of bullets contain a quantified outcome (%, $, count, time)",
            "Mirror the JD's exact phrasing for the top-5 must-have skills",
            "Lead each bullet with a strong action verb — never 'responsible for'",
        ]
        return {
            "meta_critic_warnings": warnings,
            "transcript": [
                make_turn(
                    node="meta_critic",
                    input_summary="cold start — no past transcripts",
                    output=warnings,
                )
            ],
        }

    user = f"""COMPANY: {state['company_name']}
PAST TRANSCRIPTS (most recent first):

{json.dumps(past, indent=2, default=str)[:50000]}

Produce a JSON array of recurring failure patterns to warn the Writer about.
Output strict JSON: {{"warnings": ["warning 1", "warning 2", ...]}}
"""
    result = await get_router().ask(
        provider="google",
        model=settings.g2_meta_critic_model,
        system=META_CRITIC_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=1500,
        temperature=0.2,
        agent_name="g2.meta_critic",
    )
    try:
        parsed = _parse_json_loose(result.text)
        warnings = parsed.get("warnings", [])
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
    except Exception as e:
        logger.warning(f"meta_critic JSON parse failed: {e}; using raw text")
        warnings = [result.text[:500]]

    return {
        "meta_critic_warnings": warnings,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="meta_critic",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output=warnings,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 5 — writer (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
WRITER_SYSTEM = """You are a top-tier executive resume writer.

Produce a tailored resume in clean markdown:
- Header: name, title, contact line
- Summary: 3-4 lines, role-targeted, packed with keywords
- Experience: reverse chronological, role + company + dates, 3-6 bullets each
  - Every bullet starts with a strong action verb
  - At least 70% of bullets contain a quantified outcome
  - Mirror JD vocabulary where truthful
- Skills: grouped, prioritized by JD relevance
- Education / Certifications

ANTI-AI-TELL DISCIPLINE (these are why recruiters spot AI-written resumes):
- NEVER use these words/phrases: "delve", "tapestry", "unpack", "journey",
  "at the end of the day", "a testament to", "in today's fast-paced world",
  "navigate the complexities", "in this digital age", "spearheaded",
  "leveraged" (use "used"), "synergised", "ideated", "championed",
  "passionate about", "driven", "dynamic".
- NEVER use em-dash strings to look thoughtful. Avoid em-dashes entirely
  inside bullets — recruiters scanning quickly read them as filler.
- NEVER write hype openers in the Summary ("Results-driven product leader
  with a passion for…"). Lead with a specific number, a specific company,
  or a specific shipped outcome.
- NEVER fabricate. If the master resume doesn't support a claim, don't make it.
- NEVER use first person ("I", "my"). NEVER use "responsible for".

Output ONLY the resume markdown. No preamble, no commentary."""


async def writer_node(state: ResumeState) -> dict:
    settings = get_settings()
    iteration = state.get("iteration", 0)

    # Phase 2.1: warm-start rebuild path. When the workspace editor calls
    # rebuild-section / full-rebuild, the user's CURRENT resume is passed
    # in as warm_start_md and the writer's job changes from "generate from
    # scratch" to "iterate on this seed using the new context". We surface
    # the seed and the intent into the brief so the model:
    #   1) preserves voice + structure (treats the warm start as canon)
    #   2) directs its edits at the named intent rather than re-rolling
    warm_start_md = state.get("warm_start_md")
    edit_intent = state.get("edit_intent")
    has_warm_start = bool(warm_start_md and warm_start_md.strip())

    if has_warm_start:
        warm_start_block = (
            f"\n\nWARM-START SEED (the candidate's current resume — "
            f"PRESERVE its structure and voice; iterate within it rather "
            f"than rewriting from scratch):\n{warm_start_md}\n"
        )
        if edit_intent:
            intent_block = (
                f"\n\nEDIT INTENT (the human instruction driving this "
                f"rebuild — focus your changes on this):\n{edit_intent}\n"
            )
        else:
            intent_block = ""
    else:
        warm_start_block = ""
        intent_block = ""

    user = f"""JOB:
{state['job'].get('title', '')} @ {state['company_name']}
{state['job'].get('location', '') or ''}

JOB DESCRIPTION:
{(state['job'].get('description') or '')[:5000]}

INSIDER EXPERT NOTES:
{state.get('expert_notes', '')}

ADVOCATE NOTES:
{state.get('advocate_notes', '')}

META-CRITIC WARNINGS (avoid these patterns):
{json.dumps(state.get('meta_critic_warnings', []), indent=2)}

CRITIC FEEDBACK (if revising — iteration {iteration}):
{json.dumps(state.get('merged_critique', {}), indent=2)[:2000] if iteration > 0 else '(first draft)'}

PREVIOUS DRAFT (if revising):
{state.get('current_draft') or '(none)'}

CANDIDATE MASTER RESUME:
{state['master_resume_md']}{warm_start_block}{intent_block}

Write the resume now. Output ONLY markdown.
"""
    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_writer_model,
        system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=4000,
        temperature=0.3,
        agent_name="g2.writer",
    )
    return {
        "current_draft": result.text,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="writer",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output=result.text,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 6 + 7 — ATS Critic ensemble (DeepSeek-R1 + Kimi K2, parallel)
# ═════════════════════════════════════════════════════════════════════════
ATS_CRITIC_SYSTEM = """You are an ATS and recruiter-screening expert.

Score the resume against:
- Keyword density vs. JD (extract top 20 JD terms; check coverage)
- Parseability (no tables, no images, no headers/footers, clean section labels)
- 6-second skim test: can a recruiter spot fit in 6 seconds?
- Length appropriateness (1 page <10y exp; 2 pages 10y+)
- Action-verb diversity, quantification rate (% bullets with numbers)
- Title/seniority alignment with target role

Output a JSON critique:
{
  "ats_score": 0-100,
  "keyword_coverage": [{"term": "...", "in_resume": true|false}],
  "missing_keywords": ["..."],
  "parseability_issues": ["..."],
  "skim_test_pass": true|false,
  "quantification_rate": 0.0-1.0,
  "specific_fixes": ["concrete edit 1", "concrete edit 2", ...]
}
Strict JSON only. No prose."""


async def _run_ats_critic(
    state: ResumeState,
    provider: str,
    model: str,
    agent_name: str,
) -> tuple[dict, float, int, str, str]:
    """Shared body for the two critics. Returns (parsed, cost, latency, model_used, raw_text).

    Resilient: if the LLM call itself fails (provider HTTP error, timeout,
    auth, response_format unsupported, etc.), we return a sentinel critique
    instead of letting the exception propagate. The G2 graph then continues
    — merge_critique already handles a critic with ats_score=0 by deferring
    to the other critic. Without this, one bad provider crashes the whole
    parallel branch and forces a legacy fallback.
    """
    user = f"""JOB DESCRIPTION:
{(state['job'].get('description') or '')[:4000]}

CURRENT RESUME DRAFT:
{state['current_draft']}

Score and critique. Return strict JSON only.
"""
    # Reasoning-mode models (deepseek-reasoner, kimi-k2.x) burn a large
    # share of max_tokens on internal chain-of-thought before emitting
    # the final JSON. With max_tokens=2000 they routinely truncate mid-
    # JSON, leaving _parse_json_loose with empty or malformed strings.
    # Bump generously for those models; 2000 is fine for chat-tuned ones.
    is_reasoning_model = (
        model.startswith("deepseek-reasoner")
        or model.startswith("kimi-k2")
    )
    max_tokens_budget = 8000 if is_reasoning_model else 2000

    async def _attempt_call(retry: bool) -> tuple[dict, float, int, str, str] | None:
        """Returns (parsed, cost, latency, model_used, raw_text) on parse success,
        or None to signal the caller should retry with a larger budget."""
        try:
            result = await get_router().ask(
                provider=provider,
                model=model,
                system=ATS_CRITIC_SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens_budget * (2 if retry else 1),
                temperature=0.1,
                agent_name=agent_name,
                json_response=True,
            )
        except Exception as e:
            logger.warning(
                f"{agent_name} LLM call failed ({type(e).__name__}: {str(e)[:200]}); "
                f"returning sentinel critique so the other critic can carry the merge"
            )
            sentinel = {
                "ats_score": 0,
                "missing_keywords": [],
                "specific_fixes": [
                    f"(provider error from {provider}/{model}: {type(e).__name__}: {str(e)[:200]})"
                ],
                "_provider_error": True,
            }
            return sentinel, 0.0, 0, model, ""

        try:
            parsed = _parse_json_loose(result.text)
            return parsed, result.cost_usd, result.latency_ms, result.model, result.text
        except Exception:
            # Truncated / empty / malformed JSON. For reasoning models this
            # almost always means the model hit max_tokens mid-output. Tell
            # the caller to retry with a 2× budget; fall through if already
            # retrying.
            return None

    out = await _attempt_call(retry=False)
    if out is None and is_reasoning_model:
        logger.warning(
            f"{agent_name} JSON parse failed on first attempt (likely max_tokens "
            f"truncation); retrying with budget={max_tokens_budget * 2}"
        )
        out = await _attempt_call(retry=True)

    if out is not None:
        return out

    # Both attempts failed to produce parseable JSON. Return sentinel so
    # merge_critique defers to the other critic.
    logger.warning(f"{agent_name} JSON parse failed on both attempts; sentinel returned")
    sentinel_parse = {
        "ats_score": 0,
        "missing_keywords": [],
        "specific_fixes": [
            f"(parse error from {model}: empty or truncated JSON after retry)"
        ],
        "_parse_error": True,
    }
    return sentinel_parse, 0.0, 0, model, ""


async def ats_critic_a_node(state: ResumeState) -> dict:
    settings = get_settings()
    parsed, cost, latency, model, _raw = await _run_ats_critic(
        state,
        provider="deepseek",
        model=settings.g2_ats_critic_a_model,
        agent_name="g2.ats_critic_a",
    )
    return {
        "critic_a": parsed,
        "cost_usd_total": cost,
        "latency_ms_total": latency,
        "transcript": [
            make_turn(
                node="ats_critic_a",
                iteration=state.get("iteration", 0),
                provider="deepseek",
                model=model,
                output=parsed,
                cost_usd=cost,
                latency_ms=latency,
            )
        ],
    }


async def ats_critic_b_node(state: ResumeState) -> dict:
    settings = get_settings()
    parsed, cost, latency, model, _raw = await _run_ats_critic(
        state,
        provider="moonshot",
        model=settings.g2_ats_critic_b_model,
        agent_name="g2.ats_critic_b",
    )
    return {
        "critic_b": parsed,
        "cost_usd_total": cost,
        "latency_ms_total": latency,
        "transcript": [
            make_turn(
                node="ats_critic_b",
                iteration=state.get("iteration", 0),
                provider="moonshot",
                model=model,
                output=parsed,
                cost_usd=cost,
                latency_ms=latency,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 7b — persona_critic (Sonnet 4.6, ~$0.02/iter)
#
# 2026-05-12: the persona was previously only fed INTO the writer (via
# insider_expert). Nothing in the graph ever asked "did the writer's draft
# actually respect THIS company's banned / required / success / failure
# bank?". So a Visa draft could include "Crypto", "Move fast and break
# things", or omit "ISO 8583" / "VisaNet" without any critic flagging it.
#
# This node is the persona-as-critic. Runs in parallel with the two ATS
# critics. Output flows into merge_critique like any other critique.
# When persona_score is low, orchestrator routes back to the writer with
# explicit persona feedback.
# ═════════════════════════════════════════════════════════════════════════
PERSONA_CRITIC_SYSTEM = """You are a recruiter for ONE specific company. You
score a resume draft against THIS COMPANY'S persona — its banned terms,
required terms, success-pattern bullet shapes, and failure-pattern bullet
shapes. You do NOT score against the JD; that's the ATS critics' job.
Your axis is "would this resume read as written-by-an-insider, or written-
by-a-generic-applicant?".

What you receive:
1. The company's name + persona (system_prompt_template + ats_keyword_bank
   {banned, required, boost} + success_patterns array + failure_patterns
   array)
2. The master CV (source of truth for what the candidate has actually done)
3. The current draft

What you score:
- banned_keyword_hits  — any draft text that contains a `banned` token.
                        Each hit = severity P0 (recruiter rejects the
                        resume in the 6-second skim).
- required_coverage    — for each `required` keyword: is it in the draft?
                        Coverage % = matched / total required.
- boost_coverage       — for each `boost` keyword: is it in the draft?
                        (Soft signal — not all need to appear.)
- success_pattern_match — for each draft bullet in Experience, does it
                        roughly mirror the SHAPE of a success_pattern
                        (specific noun + verb + quantified metric)?
                        Count how many.
- failure_pattern_match — for each draft bullet, does it match the SHAPE
                        of a failure_pattern (vague / generic / unquantified)?
                        Count how many.
- persona_alignment_score (0-100)
  Formula: 100
           − 20 × banned_keyword_hits
           − 30 × (1 − required_coverage)
           − 10 × (failure_pattern_matches > 0 ? failure_pattern_matches : 0)
           + 5  × (success_pattern_matches > total_bullets / 2 ? 1 : 0)
  Clamped to [0, 100].

When persona is empty / not loaded, return a neutral score of 60 with
`persona_loaded: false` so downstream nodes know to weight your output
lightly.

Output STRICT JSON only — no prose, no fences:
{
  "persona_loaded": true|false,
  "persona_alignment_score": 0-100,
  "banned_keyword_hits": [{"keyword": "...", "where_found": "<bullet|summary|header>"}],
  "required_keyword_coverage": {"total": N, "matched": M, "ratio": 0.0-1.0,
                                  "missing": ["..."]},
  "boost_keyword_coverage": {"total": N, "matched": M, "ratio": 0.0-1.0},
  "success_pattern_matches": N,
  "failure_pattern_matches": N,
  "specific_fixes": ["concrete edit — replace 'X' with 'Y' style bullet",
                     "add required keyword 'ISO 8583' in Experience section",
                     ...]
}
"""


def _persona_banned_scan(draft: str, persona: dict) -> list[dict]:
    """Pure-code regex scan for banned keywords. Runs BEFORE the LLM critic
    to surface zero-cost early signal. Pure substring case-insensitive
    match — no LLM round-trip.
    """
    if not draft or not persona:
        return []
    bank = persona.get("ats_keyword_bank") or {}
    banned = [b for b in (bank.get("banned") or []) if isinstance(b, str) and b.strip()]
    draft_lower = draft.lower()
    hits: list[dict] = []
    for kw in banned:
        if kw.lower() in draft_lower:
            hits.append({
                "keyword": kw,
                "fix": f"persona bans '{kw}' — remove from draft",
            })
    return hits


def _persona_required_coverage(draft: str, persona: dict) -> dict:
    """Pure-code coverage of `required` ATS keywords. Returns {missing, ratio}.
    Same case-insensitive substring match used by `_persona_banned_scan`.
    """
    if not draft or not persona:
        return {"matched": 0, "total": 0, "ratio": 0.0, "missing": []}
    bank = persona.get("ats_keyword_bank") or {}
    required = [r for r in (bank.get("required") or []) if isinstance(r, str) and r.strip()]
    draft_lower = draft.lower()
    matched: list[str] = []
    missing: list[str] = []
    for kw in required:
        if kw.lower() in draft_lower:
            matched.append(kw)
        else:
            missing.append(kw)
    return {
        "matched": len(matched),
        "total": len(required),
        "ratio": (len(matched) / len(required)) if required else 0.0,
        "missing": missing,
    }


async def persona_critic_node(state: ResumeState) -> dict:
    """Score the draft against the company's persona.

    Runs in parallel with ats_critic_a + ats_critic_b. Output flows into
    merge_critique via the new `persona_critique` state field.

    Cost: ~$0.02/iter on Sonnet 4.6 (cheap by design — this is a focused
    scoring task, not an open-ended generation).
    """
    settings = get_settings()
    persona = state.get("company_persona") or {}
    iteration = state.get("iteration", 0)
    started = time.perf_counter()

    # Pre-compute pure-code signals BEFORE the LLM call. Surface to the
    # critic so it doesn't have to re-discover them, and so the result
    # holds even if the LLM call fails (sentinel return path below).
    banned_hits = _persona_banned_scan(state.get("current_draft", ""), persona)
    required_cov = _persona_required_coverage(state.get("current_draft", ""), persona)

    if not persona:
        # Cold start / no persona row for this company. Return neutral so
        # merge_critique knows to weight us at zero rather than treating
        # this branch as failed.
        return {
            "persona_critique": {
                "persona_loaded": False,
                "persona_alignment_score": 60,
                "banned_keyword_hits": [],
                "required_keyword_coverage": {
                    "total": 0, "matched": 0, "ratio": 0.0, "missing": []
                },
                "boost_keyword_coverage": {"total": 0, "matched": 0, "ratio": 0.0},
                "success_pattern_matches": 0,
                "failure_pattern_matches": 0,
                "specific_fixes": [],
                "_neutral_no_persona": True,
            },
            "transcript": [
                make_turn(
                    node="persona_critic",
                    iteration=iteration,
                    input_summary="(no persona for this company; skipped LLM call)",
                    output={"persona_loaded": False, "score": 60},
                    cost_usd=0.0,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            ],
        }

    company_name = state.get("company_name", "")
    ats_bank = persona.get("ats_keyword_bank") or {}
    success_patterns = persona.get("success_patterns") or []
    failure_patterns = persona.get("failure_patterns") or []

    persona_block = json.dumps(
        {
            "company_name": company_name,
            "system_prompt_template": (persona.get("system_prompt_template") or "")[:1200],
            "ats_keyword_bank": ats_bank,
            "success_patterns": success_patterns,
            "failure_patterns": failure_patterns,
        },
        indent=2,
    )
    prescan_block = json.dumps(
        {
            "pure_code_banned_hits": banned_hits,
            "pure_code_required_coverage": required_cov,
        },
        indent=2,
    )

    user = f"""COMPANY PERSONA (source of recruiter idiom):
{persona_block}

────────────────────────────────────────────────────────────────────────

MASTER CV (what the candidate has actually done — facts only):
{state.get('master_resume_md', '')}

────────────────────────────────────────────────────────────────────────

CURRENT RESUME DRAFT (the artefact you are scoring):
{state.get('current_draft', '')}

────────────────────────────────────────────────────────────────────────

PURE-CODE PRESCAN (regex hits — surface and escalate):
{prescan_block}

────────────────────────────────────────────────────────────────────────

Score and critique against THIS company's persona. Strict JSON only."""

    try:
        result = await get_router().ask(
            provider="anthropic",
            model="claude-sonnet-4-6",
            system=PERSONA_CRITIC_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500,
            temperature=0.2,
            agent_name="g2.persona_critic",
            json_response=True,
        )
    except Exception as e:
        logger.exception("persona_critic LLM call failed")
        # Sentinel — return pure-code prescan as the score so merge_critique
        # still sees persona-shaped feedback even if LLM is down.
        sentinel_score = max(0, 100 - 20 * len(banned_hits)
                                 - int(30 * (1 - required_cov["ratio"])))
        return {
            "persona_critique": {
                "persona_loaded": True,
                "persona_alignment_score": sentinel_score,
                "banned_keyword_hits": banned_hits,
                "required_keyword_coverage": required_cov,
                "boost_keyword_coverage": {"total": 0, "matched": 0, "ratio": 0.0},
                "success_pattern_matches": 0,
                "failure_pattern_matches": 0,
                "specific_fixes": [f["fix"] for f in banned_hits]
                                 + [f"add missing required keyword: {kw}"
                                    for kw in required_cov.get("missing", [])[:5]],
                "_llm_failed": str(e)[:200],
            },
            "transcript": [
                make_turn(
                    node="persona_critic",
                    iteration=iteration,
                    provider="anthropic",
                    model="claude-sonnet-4-6",
                    input_summary=truncate(user),
                    output={"error": str(e)[:200],
                            "fallback_score": sentinel_score},
                    cost_usd=0.0,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error=str(e)[:300],
                )
            ],
        }

    try:
        parsed = _parse_json_loose(result.text)
    except Exception as e:
        logger.warning(f"persona_critic JSON parse failed: {e}")
        parsed = {
            "persona_loaded": True,
            "persona_alignment_score": 60,
            "banned_keyword_hits": banned_hits,
            "required_keyword_coverage": required_cov,
            "specific_fixes": [],
            "_parse_failed": str(e)[:200],
        }

    # Belt-and-suspenders: if LLM somehow missed pure-code banned hits,
    # union them in.
    llm_banned = parsed.get("banned_keyword_hits") or []
    llm_banned_kws = {b.get("keyword", "").lower() for b in llm_banned if isinstance(b, dict)}
    for hit in banned_hits:
        if hit["keyword"].lower() not in llm_banned_kws:
            llm_banned.append({"keyword": hit["keyword"], "where_found": "(regex)"})
    parsed["banned_keyword_hits"] = llm_banned

    return {
        "persona_critique": parsed,
        "cost_usd_total": float(result.cost_usd or 0.0),
        "latency_ms_total": int(result.latency_ms or 0),
        "transcript": [
            make_turn(
                node="persona_critic",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={
                    "persona_alignment_score": parsed.get("persona_alignment_score"),
                    "n_banned_hits": len(llm_banned),
                    "required_ratio": (parsed.get("required_keyword_coverage") or {}).get("ratio"),
                    "failure_pattern_matches": parsed.get("failure_pattern_matches"),
                    "success_pattern_matches": parsed.get("success_pattern_matches"),
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 8 — merge_critique (pure code)
# ═════════════════════════════════════════════════════════════════════════
def _norm_keyword(kw: str) -> str:
    return (kw or "").strip().lower()


async def merge_critique_node(state: ResumeState) -> dict:
    """
    Merge critic_a + critic_b + persona_critique into one critique:
      - ats_score              = min(a, b)        — be strict
      - persona_score          = persona_critique.persona_alignment_score
      - missing_keywords       = union(ATS critics) + persona.required.missing
      - specific_fixes         = union(all three) — persona fixes prepended
                                  (banned-keyword fixes are P0 — recruiter
                                  rejects the resume in the 6-sec skim)
      - parseability_issues    = union(ATS critics)
      - quantification_rate    = min(a, b)
      - persona_banned_hits    = persona_critique.banned_keyword_hits
                                  (surfaced separately so the orchestrator
                                  can short-circuit on banned-keyword
                                  violations even if ATS score is high)
    """
    a = state.get("critic_a") or {}
    b = state.get("critic_b") or {}
    p = state.get("persona_critique") or {}

    score_a = a.get("ats_score") or 0
    score_b = b.get("ats_score") or 0
    if score_a == 0 and score_b == 0:
        merged_score = 0
    elif score_a == 0:
        merged_score = score_b
    elif score_b == 0:
        merged_score = score_a
    else:
        merged_score = min(score_a, score_b)

    persona_score = int(p.get("persona_alignment_score") or 0)
    persona_loaded = bool(p.get("persona_loaded"))
    banned_hits = p.get("banned_keyword_hits") or []

    # 2026-05-12: persona-shaped missing keywords flow into the same
    # `missing_keywords` bucket the writer reads, so it picks them up on
    # the next iteration without separate plumbing.
    req_cov = p.get("required_keyword_coverage") or {}
    persona_missing_required = [
        f"{kw} (persona-required)" for kw in (req_cov.get("missing") or [])[:8]
    ]

    missing = list({
        _norm_keyword(k)
        for k in (
            a.get("missing_keywords", [])
            + b.get("missing_keywords", [])
            + persona_missing_required
        )
        if k
    })

    # Persona fixes prepended — banned-keyword violations are P0 (recruiter
    # rejects the resume in 6 seconds). Then ATS critics' fixes.
    persona_fixes = list(p.get("specific_fixes") or [])
    fixes_a = a.get("specific_fixes", []) or []
    fixes_b = b.get("specific_fixes", []) or []
    seen: set[str] = set()
    fixes: list[str] = []
    for f in persona_fixes + fixes_a + fixes_b:
        key = (f or "").strip().lower()[:120]
        if key and key not in seen:
            seen.add(key)
            fixes.append(f)

    parseability = list(set(
        (a.get("parseability_issues", []) or []) + (b.get("parseability_issues", []) or [])
    ))

    quant_a = a.get("quantification_rate") or 0
    quant_b = b.get("quantification_rate") or 0
    quant = min(quant_a, quant_b) if (quant_a and quant_b) else (quant_a or quant_b)

    merged = {
        "ats_score": merged_score,
        "persona_score": persona_score,
        "persona_loaded": persona_loaded,
        "persona_banned_hits": banned_hits,
        "missing_keywords": missing,
        "specific_fixes": fixes,
        "parseability_issues": parseability,
        "quantification_rate": quant,
        "skim_test_pass": bool(a.get("skim_test_pass") and b.get("skim_test_pass")),
        "_score_a": score_a,
        "_score_b": score_b,
    }
    return {
        "merged_critique": merged,
        "transcript": [
            make_turn(
                node="merge_critique",
                iteration=state.get("iteration", 0),
                output={
                    "merged_score": merged_score,
                    "persona_score": persona_score,
                    "persona_loaded": persona_loaded,
                    "n_banned_hits": len(banned_hits),
                    "n_missing_keywords": len(missing),
                    "n_specific_fixes": len(fixes),
                    "score_a": score_a,
                    "score_b": score_b,
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 9 — orchestrator (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
ORCHESTRATOR_SYSTEM = """You are a debate moderator.

Decide whether the resume is converged enough to send to the polisher.
Convergence rules (ALL must hold for converge):
- merged ATS score >= target AND <= 2 outstanding specific_fixes
- persona_alignment_score >= 80 (when persona_loaded=true)
- persona_banned_hits is EMPTY (banned keywords are P0 — never converge
  while a banned company term is still in the draft)
- iteration >= max_iterations  → forced converge (exhausted) regardless
- otherwise                    → loop back to writer with focused brief

Output strict JSON:
{
  "converged": true|false,
  "rationale": "1 sentence",
  "next_focus": "if not converged: 1-sentence focused instruction for next writer pass"
}"""


async def orchestrator_node(state: ResumeState) -> dict:
    """
    Decides whether to loop back to writer or proceed to polisher.

    Phase 1.11: also enforces the per-build cost cap. If cumulative
    cost_usd_total has reached `state.cost_cap_usd` (set at entry from
    settings.g2_max_cost_usd, optionally overridden per-build), force
    converge with `cost_capped=True` so export_node can mark the build's
    status correctly. Worst-case spend is bounded — runaway iterations
    can't burn more than the cap.
    """
    settings = get_settings()
    iteration = state.get("iteration", 0) + 1
    merged = state.get("merged_critique", {})
    cost_so_far = state.get("cost_usd_total", 0.0) or 0.0
    cost_cap = state.get("cost_cap_usd") or settings.g2_max_cost_usd

    # ── Phase 1.11: cost cap pre-check ─────────────────────────────────
    # If we're already at/over budget BEFORE asking the LLM what to do,
    # force converge immediately. Saves the orchestrator's ~$0.30 call too.
    if cost_so_far >= cost_cap:
        logger.warning(
            f"G2 orchestrator: cost cap hit before LLM call "
            f"(${cost_so_far:.2f} >= ${cost_cap:.2f}) — forcing converge"
        )
        score = (merged or {}).get("ats_score", 0) or 0
        fixes_n = len((merged or {}).get("specific_fixes", []))
        return {
            "iteration": iteration,
            "converged": True,
            "cost_capped": True,
            "transcript": [
                make_turn(
                    node="orchestrator",
                    iteration=iteration,
                    output={
                        "converged": True,
                        "cost_capped": True,
                        "rationale": f"cost cap hit (${cost_so_far:.2f} >= ${cost_cap:.2f})",
                        "score": score,
                        "outstanding_fixes": fixes_n,
                    },
                )
            ],
        }

    user = f"""ITERATION: {iteration}
MAX ITERATIONS: {settings.g2_max_iterations}
TARGET ATS SCORE: {settings.g2_target_ats_score}

MERGED CRITIQUE:
{json.dumps(merged, indent=2)[:2500]}

Decide. Output strict JSON only."""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_orchestrator_model,
        system=ORCHESTRATOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=600,
        temperature=0.1,
        agent_name="g2.orchestrator",
    )
    try:
        decision = _parse_json_loose(result.text)
    except Exception as e:
        logger.warning(f"orchestrator JSON parse failed: {e}; defaulting to not-converged")
        decision = {"converged": False, "rationale": "parse error"}

    # Hard caps
    converged = bool(decision.get("converged", False))
    score = (merged or {}).get("ats_score", 0) or 0
    fixes = len((merged or {}).get("specific_fixes", []))
    persona_score = int((merged or {}).get("persona_score") or 0)
    persona_loaded = bool((merged or {}).get("persona_loaded"))
    n_banned = len((merged or {}).get("persona_banned_hits") or [])

    # 2026-05-12: persona_critic gates the converge decision now.
    # Banned-keyword hits are P0 — recruiter rejects in the 6-sec skim
    # so we never converge while a banned company term is still in the
    # draft. If persona is loaded, persona_score must be >= 80 too.
    if score >= settings.g2_target_ats_score and fixes <= 2:
        if persona_loaded and (persona_score < 80 or n_banned > 0):
            converged = False  # ATS happy but persona disagrees — keep iterating
        else:
            converged = True
    if iteration >= settings.g2_max_iterations:
        converged = True  # exhaustion overrides persona disagreement

    # ── Phase 1.11: post-LLM cost-cap check ────────────────────────────
    # Account for the orchestrator's own cost (just incurred) when deciding.
    # If THIS turn would push us over the cap, force converge so we don't
    # loop back into another expensive writer pass.
    cost_capped = False
    cost_after_this = cost_so_far + float(result.cost_usd or 0)
    if cost_after_this >= cost_cap:
        logger.warning(
            f"G2 orchestrator: cost cap hit post-call "
            f"(${cost_after_this:.2f} >= ${cost_cap:.2f}) — forcing converge"
        )
        converged = True
        cost_capped = True

    patch: dict = {
        "iteration": iteration,
        "converged": converged,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="orchestrator",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                output={
                    "converged": converged,
                    "cost_capped": cost_capped,
                    "decision_raw": decision,
                    "score": score,
                    "outstanding_fixes": fixes,
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }
    if cost_capped:
        patch["cost_capped"] = True
    return patch


# ═════════════════════════════════════════════════════════════════════════
# Node 10 — polisher (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
POLISHER_SYSTEM = """You are the final-pass quality gate.

Polish the resume one last time — tighten language, fix any awkward phrasing,
ensure every line earns its place. Then self-score on 0-100 across:
  fit (40%), ats (20%), impact (20%), narrative (10%), polish (10%).

Output strict JSON:
{
  "final_resume_md": "...",
  "final_score": 0-100,
  "score_breakdown": {"fit": ..., "ats": ..., "impact": ..., "narrative": ..., "polish": ...},
  "remaining_concerns": ["...", "..."]
}"""


async def polisher_node(state: ResumeState) -> dict:
    settings = get_settings()
    user = f"""JOB:
{state['job'].get('title', '')} @ {state['company_name']}

JD:
{(state['job'].get('description') or '')[:4000]}

CURRENT DRAFT:
{state['current_draft']}

CRITIC FEEDBACK (merged):
{json.dumps(state.get('merged_critique', {}), indent=2)[:2000]}

Polish and self-score. Strict JSON only."""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_polisher_model,
        system=POLISHER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=4500,
        temperature=0.2,
        agent_name="g2.polisher",
    )
    try:
        out = _parse_json_loose(result.text)
    except Exception as e:
        logger.error(f"polisher JSON parse failed: {e}; falling back to raw draft")
        out = {
            "final_resume_md": state.get("current_draft", ""),
            "final_score": 0,
            "score_breakdown": {},
            "remaining_concerns": [f"polisher parse error: {str(e)[:200]}"],
        }
    return {
        "final_resume_md": out.get("final_resume_md", state.get("current_draft", "")),
        "final_score": int(out.get("final_score", 0) or 0),
        "final_breakdown": out.get("score_breakdown", {}),
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="polisher",
                provider=result.provider,
                model=result.model,
                output={
                    "final_score": out.get("final_score"),
                    "score_breakdown": out.get("score_breakdown"),
                    "remaining_concerns": out.get("remaining_concerns"),
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 11 — cover_email (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
COVER_EMAIL_SYSTEM = """You are an executive recruiter writing a cover email on behalf of the candidate.

Output ONLY the email body — no subject line, no signature.
Style: direct, value-first, concrete. Reference one specific company hook from
the Insider Expert's notes. 4-7 sentences. Length cap 130 words.

ANTI-AI-TELL DISCIPLINE (recruiters spot AI-written cover emails instantly):
- NEVER use: "delve", "tapestry", "unpack", "journey", "at the end of the day",
  "a testament to", "in today's fast-paced world", "navigate the complexities",
  "passionate about", "thrilled to", "excited to apply".
- NEVER open with "I hope this finds you well" or "I'm reaching out".
  Open with the SPECIFIC company hook in the first sentence.
- NEVER stack em-dashes — one max per email, none preferred.
- NEVER end with "Looking forward to hearing from you" or "Cheers". End on
  a specific concrete value claim or a 1-line forward-able teaser.
- Banked buzzwords from the candidate's persona MUST stay out (see banned
  list in user message)."""


async def cover_email_node(state: ResumeState) -> dict:
    settings = get_settings()
    user = f"""JOB: {state['job'].get('title', '')} @ {state['company_name']}

INSIDER EXPERT NOTES (for company hook):
{state.get('expert_notes', '')[:2000]}

FINAL RESUME (excerpts):
{state.get('final_resume_md', '')[:2500]}

Write the cover email body now."""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_polisher_model,    # reuse polisher model for voice
        system=COVER_EMAIL_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=1200,
        temperature=0.4,
        agent_name="g2.cover_email",
    )
    return {
        "cover_email_md": result.text,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="cover_email",
                provider=result.provider,
                model=result.model,
                output=result.text,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 12 — export (pure code: DOCX + Storage upload + DB finalize)
# ═════════════════════════════════════════════════════════════════════════
async def export_node(state: ResumeState) -> dict:
    """
    1. Render markdown → DOCX via pandoc subprocess
    2. Upload markdown + DOCX to Supabase Storage 'resumes/' bucket → signed URLs
    3. UPDATE resume_builds row with status='converged' (or 'exhausted' if iter capped),
       polisher_score, ats_score_a/b, resume_md, cover_email_md, URLs,
       cost_usd_total, latency_ms_total, agent_transcript
    """
    from resume_agents.g2_io import finalize_resume_build

    resume_md = state.get("final_resume_md", "")
    cover_email_md = state.get("cover_email_md", "")
    company_name = state["company_name"]
    job_id = state["job_id"]
    resume_build_id = state["resume_build_id"]
    iteration = state.get("iteration", 0)
    max_iter = get_settings().g2_max_iterations

    # Phase 1.11: status hierarchy
    #   cost_capped beats exhausted beats converged
    if state.get("cost_capped"):
        status = "cost_capped"
    elif iteration >= max_iter and not state.get("converged"):
        status = "exhausted"
    else:
        status = "converged"

    # ─── Render DOCX (via existing helper if present) ────────────────
    docx_url = None
    md_url = None
    try:
        from db.client import upload_artifact
        import os, tempfile

        # Markdown upload
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write(resume_md)
            md_path = f.name
        try:
            md_url = upload_artifact(
                local_path=md_path,
                remote_path=f"resumes/{company_name.lower().replace(' ', '-')}/{resume_build_id}.md",
                content_type="text/markdown",
            )
        finally:
            if os.path.exists(md_path):
                os.unlink(md_path)

        # DOCX via pandoc — reuse the helper from pipeline.py if it lives there
        try:
            from pipeline import JobHuntPipeline
            docx_bytes = JobHuntPipeline._md_to_docx_bytes(resume_md) if hasattr(
                JobHuntPipeline, "_md_to_docx_bytes"
            ) else _local_md_to_docx(resume_md)
        except Exception:
            docx_bytes = _local_md_to_docx(resume_md)

        if docx_bytes:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                f.write(docx_bytes)
                docx_path = f.name
            try:
                docx_url = upload_artifact(
                    local_path=docx_path,
                    remote_path=f"resumes/{company_name.lower().replace(' ', '-')}/{resume_build_id}.docx",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            finally:
                if os.path.exists(docx_path):
                    os.unlink(docx_path)
    except Exception as e:
        logger.warning(f"G2 export upload failed: {e}")

    # ─── Finalize the resume_builds row ──────────────────────────────
    finalize_resume_build(
        resume_build_id=resume_build_id,
        status=status,
        iterations=iteration,
        ats_score_a=(state.get("critic_a") or {}).get("ats_score"),
        ats_score_b=(state.get("critic_b") or {}).get("ats_score"),
        polisher_score=state.get("final_score"),
        resume_md=resume_md,
        resume_docx_url=docx_url,
        resume_pdf_url=None,
        cover_email_md=cover_email_md,
        agent_transcript=state.get("transcript", []),
        cost_usd_total=state.get("cost_usd_total", 0),
        latency_ms_total=state.get("latency_ms_total", 0),
    )

    # ─── Mirror onto jobs row so dashboard "has resume" check works ───
    try:
        from db.client import get_supabase
        from datetime import datetime, timezone
        get_supabase().table("jobs").update({
            "resume_path": docx_url or md_url,
            "resume_generated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).execute()
    except Exception as e:
        logger.warning(f"G2 export jobs.resume_path update failed: {e}")

    return {
        "resume_docx_url": docx_url,
        "transcript": [
            make_turn(
                node="export",
                output={
                    "status": status,
                    "iterations": iteration,
                    "polisher_score": state.get("final_score"),
                    "docx_url": docx_url,
                    "md_url": md_url,
                },
            )
        ],
    }


def _local_md_to_docx(md: str) -> bytes:
    """Local pandoc fallback. Returns empty bytes if pandoc unavailable."""
    import subprocess, tempfile, os
    try:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write(md)
            md_path = f.name
        docx_path = md_path.replace(".md", ".docx")
        subprocess.run(
            ["pandoc", md_path, "-o", docx_path],
            check=True, capture_output=True, timeout=30,
        )
        with open(docx_path, "rb") as f:
            data = f.read()
        os.unlink(md_path)
        os.unlink(docx_path)
        return data
    except Exception as e:
        logger.warning(f"pandoc unavailable for DOCX render: {e}")
        return b""
