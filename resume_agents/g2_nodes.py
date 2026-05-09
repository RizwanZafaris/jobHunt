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
    """
    settings = get_settings()
    persona = state.get("company_persona")
    if persona and persona.get("system_prompt_template"):
        system = persona["system_prompt_template"]
    else:
        system = INSIDER_EXPERT_FALLBACK_SYSTEM

    job = state["job"]
    user = f"""TARGET ROLE:
{job.get('title', '')} — {state['company_name']}
{job.get('location', '') or ''}

JOB DESCRIPTION:
{(job.get('description') or '')[:6000]}

CANDIDATE MASTER RESUME (excerpts):
{state['master_resume_md'][:4000]}

Produce your positioning notes:
1. Top 5 keywords/phrases this resume MUST contain (use grounded search to verify current language)
2. Top 3 cultural/strategic signals to weave in (cite recent {state['company_name']} news)
3. 3 things to DOWNPLAY or remove
4. The exact summary statement (3-4 lines) you'd put at the top
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
    return {
        "expert_notes": result.text,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="insider_expert",
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

NEVER fabricate. If the master resume doesn't support a claim, don't make it.
NEVER use first person. NEVER use "responsible for".

Output ONLY the resume markdown. No preamble, no commentary."""


async def writer_node(state: ResumeState) -> dict:
    settings = get_settings()
    iteration = state.get("iteration", 0)
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
{state['master_resume_md']}

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
    try:
        result = await get_router().ask(
            provider=provider,
            model=model,
            system=ATS_CRITIC_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=2000,
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
    except Exception as e:
        logger.warning(f"{agent_name} JSON parse failed: {e}")
        parsed = {
            "ats_score": 0,
            "missing_keywords": [],
            "specific_fixes": [f"(parse error from {model}: {str(e)[:200]})"],
            "_parse_error": True,
        }
    return parsed, result.cost_usd, result.latency_ms, result.model, result.text


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
# Node 8 — merge_critique (pure code)
# ═════════════════════════════════════════════════════════════════════════
def _norm_keyword(kw: str) -> str:
    return (kw or "").strip().lower()


async def merge_critique_node(state: ResumeState) -> dict:
    """
    Merge critic_a + critic_b into one critique:
      - ats_score   = min(a, b)             — be strict
      - missing_keywords = union, deduped
      - specific_fixes   = union, deduped (loose token-set similarity)
      - parseability_issues = union
      - quantification_rate = min(a, b)
    """
    a = state.get("critic_a") or {}
    b = state.get("critic_b") or {}

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

    missing = list({
        _norm_keyword(k)
        for k in (a.get("missing_keywords", []) + b.get("missing_keywords", []))
        if k
    })

    # Dedupe specific_fixes by exact match first; tokenset overlap dedup is a TODO
    fixes_a = a.get("specific_fixes", []) or []
    fixes_b = b.get("specific_fixes", []) or []
    seen: set[str] = set()
    fixes: list[str] = []
    for f in fixes_a + fixes_b:
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
Convergence rules:
- merged ATS score >= 95 AND <= 2 outstanding specific_fixes  → converged
- iteration >= max_iterations                                  → forced converge (exhausted)
- otherwise                                                    → loop back to writer with focused brief

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
    if score >= settings.g2_target_ats_score and fixes <= 2:
        converged = True
    if iteration >= settings.g2_max_iterations:
        converged = True

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
the Insider Expert's notes. 4-7 sentences. No buzzwords."""


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
