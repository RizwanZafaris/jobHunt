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
from agents.llm_hardening import get_hardened_router  # B-OR3: fallback resilience
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


# Audit §5.4 — meta-critic transcript trim.
#
# The previous behaviour json.dumps()'d up to 50,000 chars of raw past-
# transcripts into Gemini's context every iteration. That blob contained
# full input_summary + output payloads for every turn of every prior build —
# huge surface, mostly irrelevant for the question this node actually asks
# ("have past builds for THIS company shown recurring failure patterns?").
#
# We now summarise each past build into compact (iteration, node, model,
# score) tuples plus high-signal fields (polisher_score, n_fabrications,
# top critic specific_fixes). Capped to the last 3 builds (was effectively
# bounded by settings.g2_meta_critic_lookback=5). Raw transcripts remain
# queryable via load_past_transcripts for one-off offline analysis; the
# meta-critic LLM call itself simply doesn't need all of them.
_META_CRITIC_HISTORY_CAP = 3
_META_CRITIC_FIX_PREVIEW = 4          # specific_fixes per turn to forward


def _summarize_past_transcript(build: dict) -> dict:
    """Compress one resume_builds row into a meta-critic-friendly digest.

    Input:  {"source": "resume_builds", "agent_transcript": [...turns...],
             "polisher_score": int, "finalized_at": str}
            OR a fallback agent_conversations row (cold-start shape).
    Output: small dict with `turns: [{i, n, m, s, fix, fab}, ...]` plus
            top-level polisher_score / finalized_at hints. Keys are short
            because the digest goes verbatim into the LLM prompt.
    """
    source = build.get("source")
    if source == "agent_conversations":
        # Cold-start shape — flatten to a single conversational note.
        return {
            "source": "agent_conversations",
            "speaker": build.get("speaker"),
            "gap_identified": build.get("gap_identified"),
            "gap_filled": build.get("gap_filled"),
            "snippet": (build.get("message") or "")[:240],
        }

    turns = build.get("agent_transcript") or []
    out_turns: list[dict] = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        # Extract a score-ish signal where one exists. Different nodes
        # surface it under different keys in their `output` dict.
        out = t.get("output") if isinstance(t.get("output"), dict) else {}
        score = (
            out.get("ats_score")
            or out.get("merged_score")
            or out.get("persona_alignment_score")
            or out.get("persona_score")
            or out.get("final_score")
        )
        # Forward a tiny sample of the actionable feedback — that's the
        # field meta_critic actually mines for recurring failure patterns.
        fixes = out.get("specific_fixes")
        if isinstance(fixes, list):
            fixes_preview = [str(f)[:140] for f in fixes[:_META_CRITIC_FIX_PREVIEW]]
        else:
            fixes_preview = None
        fabrications = out.get("fabrications")
        n_fabs = len(fabrications) if isinstance(fabrications, list) else None
        out_turns.append({
            "i": t.get("iteration"),
            "n": t.get("node"),
            "m": t.get("model"),
            "s": score,
            "fix": fixes_preview,
            "fab": n_fabs,
        })
    return {
        "source": "resume_builds",
        "polisher_score": build.get("polisher_score"),
        "finalized_at": str(build.get("finalized_at") or "")[:19],
        "n_turns": len(turns),
        "turns": out_turns,
    }


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

    # Audit §5.4 — compact summary instead of the 50K-char raw dump. Cap
    # to the last 3 builds (was up to 5 via g2_meta_critic_lookback). Raw
    # transcripts remain available via load_past_transcripts for one-off
    # offline analysis; meta-critic itself doesn't need them.
    trimmed = past[:_META_CRITIC_HISTORY_CAP]
    summaries = [_summarize_past_transcript(b) for b in trimmed]
    history_block = json.dumps(summaries, indent=2, default=str)

    user = f"""COMPANY: {state['company_name']}
PAST BUILD SUMMARIES (most recent first, capped to last {_META_CRITIC_HISTORY_CAP}):

Each prior build is summarised as compact (iteration, node, model, score,
top specific_fixes, n_fabrications) tuples — full transcript bodies are
intentionally elided (irrelevant to recurring-pattern detection and add
~$5-15/mo at current build cadence).

{history_block}

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

THE CANDIDATE MASTER RESUME IS THE SOURCE OF TRUTH.
Your job is to ARRANGE and EMPHASISE the candidate's real history to match
the JD — not to invent details that make the candidate look like a better
fit than they are. A recruiter who phone-screens the candidate will catch
any fabrication and reject them. Hallucinated resumes are worse than
generic ones because they destroy trust.

────────────────────────────────────────────────────────────────────────
IDENTITY LOCK — these fields must match the master CV byte-for-byte:
────────────────────────────────────────────────────────────────────────
1. NAME: copy from the master CV's "# {NAME}" header verbatim.
2. PHONE: copy the phone number verbatim. NEVER replace with a placeholder
   (no "+44 7XXX XXXXXX", no "[phone]", no country re-mapping). If the
   master CV has "+971-58-9683970", the output MUST have "+971-58-9683970".
3. EMAIL: copy verbatim.
4. LINKEDIN: copy verbatim.
5. LOCATION (city, country): copy verbatim. Do NOT change the candidate's
   city to match the JD location. If the master CV says "Dubai, UAE" and
   the JD is in London, the candidate is STILL in Dubai. "Open to
   relocation" is a candidate decision, not a writer decision — only
   add it if the master CV explicitly says so.
6. DO NOT ADD: citizenship, nationality, visa status, work authorisation,
   "Immediate Availability", "Notice Period", or similar unless those
   exact statements appear in the master CV.

────────────────────────────────────────────────────────────────────────
FACT INTEGRITY — applies to every line below the header:
────────────────────────────────────────────────────────────────────────
- Job titles at past employers: copy from master CV. You may APPEND a
  scope qualifier in parens (e.g. "Senior PM (Payments)") if the CV
  itself supports it. Do NOT rewrite a "Technical Programme Lead" as
  "Group Product Manager" to match the JD title.
- Employer locations: copy from master CV. If SimPaisa is "Karachi" in
  the CV, the resume says "Karachi" — never "Dubai" because the JD is
  Dubai.
- Years of experience: copy from master CV ("14+" stays "14+", never
  becomes "12+" or "15+").
- Quantified metrics ($X, Y%, Z users): every number you put in a
  bullet MUST come from the master CV or from explicit "Insider Expert"
  notes about scope. NEVER invent statistics like "14% authorisation
  rate uplift" or "390M+ mobile wallets" if those numbers aren't in the
  source.
- Certifications, education, dates: copy verbatim.

You MAY:
- Reorder bullets within a role to put JD-relevant ones first.
- Compress two bullets into one for length.
- Rephrase a bullet's verb to be sharper ("Owned" → "Led").
- Inject a JD keyword INTO an existing bullet if the underlying work
  in the master CV genuinely covered that area.
- Re-write the Summary line to emphasise JD-relevant strengths the
  candidate actually has.

────────────────────────────────────────────────────────────────────────
STRUCTURE:
────────────────────────────────────────────────────────────────────────
- Header (verbatim from master per IDENTITY LOCK): name, current title,
  contact line.
- Summary: 3-4 lines. Lead with a specific real number from the candidate
  (e.g. "$1B+ TPV", "40 engineers", "100K BNPL users in 8 months").
- Experience: reverse chronological, role + company + dates, 3-6 bullets
  each. Every bullet starts with a strong action verb. At least 70% of
  bullets contain a quantified outcome (numbers that exist in the CV).
- Skills: grouped, prioritized by JD relevance.
- Education / Certifications: verbatim.

────────────────────────────────────────────────────────────────────────
ANTI-AI-TELL DISCIPLINE (these are why recruiters spot AI-written resumes):
────────────────────────────────────────────────────────────────────────
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
- NEVER use first person ("I", "my"). NEVER use "responsible for".

Output ONLY the resume markdown. No preamble, no commentary."""


# ─────────────────────────────────────────────────────────────────────────
# Direct persona injection helper (2026-05-12)
#
# The persona's ATS keyword bank + success/failure bullet shapes were
# previously only reachable by the writer through `insider_expert_notes`
# (two LLM hops: persona → insider_expert → writer). Even with the
# persona_critic gate on the back end, the writer kept producing
# non-persona-aligned bullets because the signal arrived diluted by an
# intervening summarisation.
#
# This helper builds a short, structured persona block that gets spliced
# DIRECTLY into the writer's user message — between the master CV
# (source of truth, first) and the JD (shape signal, last). The writer
# now anchors on:
#   1) facts from master CV
#   2) idiom from persona (this block)
#   3) shape from JD
# in that order.
#
# Returns "" when the persona is missing/empty so the f-string substitution
# is a no-op on cold-start builds (no template errors).
# ─────────────────────────────────────────────────────────────────────────
def _build_writer_persona_block(persona: dict | None) -> str:
    """Render the persona's ATS keyword bank + bullet-shape patterns as a
    structured block the writer can anchor on directly.

    Caps:
      - banned / required / boost lists: 20 each (sanity guard)
      - success / failure patterns:       6 each
    Returns "" when there is nothing useful to inject — caller can safely
    splice the return value into an f-string without conditional logic.
    """
    if not persona or not isinstance(persona, dict):
        return ""

    ats_bank = persona.get("ats_keyword_bank") or {}
    if not isinstance(ats_bank, dict):
        ats_bank = {}

    def _clean_list(raw: Any, cap: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            if len(out) >= cap:
                break
        return out

    banned = _clean_list(ats_bank.get("banned"), 20)
    required = _clean_list(ats_bank.get("required"), 20)
    boost = _clean_list(ats_bank.get("boost"), 20)
    success_patterns = _clean_list(persona.get("success_patterns"), 6)
    failure_patterns = _clean_list(persona.get("failure_patterns"), 6)

    # If literally none of the persona's keyword signals are populated,
    # skip the whole block — no point in printing empty headers.
    if not (banned or required or boost or success_patterns or failure_patterns):
        return ""

    lines: list[str] = [
        "",
        "────────────────────────────────────────────────────────────────────────",
        "COMPANY PERSONA — ATS KEYWORD BANK (THIS COMPANY'S RECRUITER VOCABULARY):",
        "────────────────────────────────────────────────────────────────────────",
    ]
    if banned:
        lines.append(f"  banned:   {', '.join(banned)}")
    if required:
        lines.append(f"  required: {', '.join(required)}")
    if boost:
        lines.append(f"  boost:    {', '.join(boost)}")

    if success_patterns:
        lines.append("")
        lines.append(
            "SUCCESS-PATTERN BULLET SHAPES (mimic these in YOUR bullets where the"
        )
        lines.append("candidate's evidence supports the underlying claim):")
        for p in success_patterns:
            lines.append(f"  - {p}")

    if failure_patterns:
        lines.append("")
        lines.append(
            "FAILURE-PATTERN BULLET SHAPES (NEVER write a bullet that looks like this —"
        )
        lines.append("the company's recruiters skim past it in the 6-second triage):")
        for p in failure_patterns:
            lines.append(f"  - {p}")

    lines.append("")
    return "\n".join(lines)


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

    # 2026-05-12: master CV moved to the TOP of the user message (and JD to
    # the bottom). The prior ordering put the JD first and the source CV
    # last, which biased the writer to anchor on JD language and treat the
    # CV as supporting context. Surfaced as identity-level hallucinations
    # (UK phone fabricated, location swapped to match JD geo, employer
    # office cities changed, citizenship invented). Putting the source of
    # truth FIRST gives the writer something concrete to start from.
    #
    # 2026-05-12 (audit §4.6): persona's banned/required/boost vocabulary
    # and success/failure bullet shapes were only reaching the writer
    # indirectly through `expert_notes` (two LLM hops). Splice a structured
    # persona block DIRECTLY into the user message between master CV and
    # JD. Writer anchor order is now: (1) facts from master CV,
    # (2) idiom from persona, (3) shape from JD.
    persona_block = _build_writer_persona_block(state.get("company_persona"))

    user = f"""CANDIDATE MASTER RESUME (source of truth — every fact in your
output must trace back to this):
{state['master_resume_md']}{warm_start_block}{intent_block}
{persona_block}
────────────────────────────────────────────────────────────────────────
TARGET JOB (use to decide WHICH parts of the CV to emphasise — NOT to
invent facts that fit the JD better):
────────────────────────────────────────────────────────────────────────
{state['job'].get('title', '')} @ {state['company_name']}
{state['job'].get('location', '') or ''}

JOB DESCRIPTION:
{(state['job'].get('description') or '')[:5000]}

INSIDER EXPERT NOTES (company-specific framing — never override candidate
facts):
{state.get('expert_notes', '')}

ADVOCATE NOTES (which of the candidate's real experiences to highlight):
{state.get('advocate_notes', '')}

META-CRITIC WARNINGS (avoid these patterns):
{json.dumps(state.get('meta_critic_warnings', []), indent=2)}

CRITIC FEEDBACK (if revising — iteration {iteration}):
{json.dumps(state.get('merged_critique', {}), indent=2)[:2000] if iteration > 0 else '(first draft)'}

PREVIOUS DRAFT (if revising):
{state.get('current_draft') or '(none)'}

Write the resume now. Apply IDENTITY LOCK and FACT INTEGRITY from the
system prompt. Output ONLY markdown.
"""

    # 2026-05-12 (G11 Tier 4): prepend the user's voice profile block to the
    # user message so generated bullets sound like the user, not generic
    # Sonnet. The block lives on profile_master.voice_calibration and is
    # populated by the G11 graph after the user uploads 5+ writing samples.
    # No-op (returns user unchanged) on cold-start — writers fall back to
    # generic style. We prepend to the USER message (not system) so the
    # large WRITER_SYSTEM prompt cache stays warm across users.
    from agents.voice_injector import prepend_voice_block
    user = prepend_voice_block(user, user_id=state.get("user_id"))

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g2_writer_model,
        system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=4000,
        temperature=0.3,
        agent_name="g2.writer",
    )

    # 2026-05-12: STRUCTURAL IDENTITY LOCK — pure-code, no LLM in the loop.
    # The prompt-level rules tell the writer to copy the header verbatim,
    # but Anthropic Opus has been observed (production job 103) to ignore
    # those rules under JD-fit pressure and fabricate identity fields
    # (UK phone placeholder, swapped city, invented citizenship). This
    # post-write splice extracts the canonical header from
    # master_resume_md and REPLACES whatever the writer produced for the
    # contact block — bypassing the LLM entirely for that section.
    raw_draft = result.text or ""
    locked_draft, header_action = _enforce_master_header(
        raw_draft,
        state.get("master_resume_md", ""),
    )
    fabrication_flags = _scan_for_obvious_fabrications(
        locked_draft,
        state.get("master_resume_md", ""),
    )
    return {
        "current_draft": locked_draft,
        "writer_raw_draft": raw_draft if header_action != "noop" else None,
        "header_enforcement": header_action,
        "fabrication_flags": fabrication_flags,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="writer",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={
                    "draft_chars": len(locked_draft),
                    "header_enforcement": header_action,
                    "fabrication_flags": fabrication_flags,
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Structural identity + fabrication guards (pure code, no LLM)
#
# These run AFTER the writer node and BEFORE the critics, replacing the
# header verbatim from master_resume_md and surfacing obvious fabrication
# patterns. Pure code so they cannot be overridden by an LLM instruction.
# ═════════════════════════════════════════════════════════════════════════
import re as _re_g2  # local alias to avoid clashing with module-level re

# Forbidden header phrases — added if writer invents them.
_FORBIDDEN_HEADER_ADDITIONS = (
    "british citizen",
    "uae national",
    "ksa national",
    "indian national",
    "pakistani national",
    "us citizen",
    "u.s. citizen",
    "uk citizen",
    "eu citizen",
    "immediate availability",
    "available immediately",
    "notice period:",
    "visa status:",
    "work permit:",
    "willing to relocate",  # only valid if in master CV
)

# Telltale placeholder phone formats — the writer should never emit these.
_PLACEHOLDER_PHONE_PATTERNS = (
    _re_g2.compile(r"\+\d{1,3}\s*\d?\s*[xX]{2,}", _re_g2.IGNORECASE),  # +44 7XXX XXXXXX
    _re_g2.compile(r"\[phone\]", _re_g2.IGNORECASE),
    _re_g2.compile(r"\[contact\]", _re_g2.IGNORECASE),
    _re_g2.compile(r"\[number\]", _re_g2.IGNORECASE),
)


def _extract_master_header(master_md: str) -> str:
    """Pull the canonical header block out of master_resume_md.

    Header = everything from the start of the file UP TO the first
    `## ` line (which marks the start of Summary / Experience / etc).
    For the seed user this is typically:
        # Rizwan Zafar — CV
        **Technical Programme Lead | Chief Product Officer | Senior PM**
        Dubai, UAE · rizwanzaffar.pk@gmail.com · +971-58-9683970
        [linkedin.com/in/rizwanzafar](https://linkedin.com/in/rizwanzafar)
        ---
    Returns "" if the master CV doesn't have an H1 header — the caller
    treats that as "skip enforcement, fall back to writer's output".
    """
    if not master_md:
        return ""
    lines = master_md.splitlines()
    # Find first `## ` line (start of body sections); header is everything before.
    end_idx = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            end_idx = i
            break
    if end_idx is None:
        return ""
    # Trim trailing horizontal rule / blank lines so the splice is clean.
    header_lines = lines[:end_idx]
    while header_lines and header_lines[-1].strip() in ("", "---", "***", "___"):
        header_lines.pop()
    return "\n".join(header_lines).rstrip()


def _enforce_master_header(draft: str, master_md: str) -> tuple[str, str]:
    """Replace whatever the writer produced for the header with the canonical
    header from master_resume_md.

    Returns (locked_draft, action_tag) where action_tag is one of:
        "replaced"        — header was different, replaced verbatim
        "preserved"       — writer already had the canonical header
        "noop"            — master CV had no parseable header (no enforcement)
        "no_body"         — writer's draft had no body section to splice onto

    Pure code, no LLM round-trip — runs in microseconds, no cost.
    """
    if not draft.strip():
        return draft, "noop"

    canonical = _extract_master_header(master_md)
    if not canonical:
        return draft, "noop"

    # Find where the writer's body starts (first `## `).
    body_match = _re_g2.search(r"(?m)^## ", draft)
    if not body_match:
        return draft, "no_body"

    writer_header = draft[: body_match.start()].rstrip()
    body = draft[body_match.start():]

    if writer_header.strip() == canonical.strip():
        return draft, "preserved"

    return f"{canonical}\n\n{body}", "replaced"


def _scan_for_obvious_fabrications(draft: str, master_md: str) -> list[dict]:
    """Catch the easy fabrication patterns without an LLM round-trip.

    This is the structural counterpart to the FACT VERIFIER LLM node
    (next ship). Today we catch the highest-confidence patterns:
      - Placeholder phone formats (+44 7XXX XXXXXX, [phone], etc.)
      - Forbidden header additions (British Citizen, Immediate
        Availability, Visa Status:) if not in master CV
    The LLM-based FACT VERIFIER (planned) will scan the body for
    invented metrics + employer-location mismatches that can't be
    regex-detected without per-fact context.
    """
    if not draft:
        return []

    master_lower = (master_md or "").lower()
    draft_lower = draft.lower()
    flags: list[dict] = []

    # Placeholder phone formats.
    for pat in _PLACEHOLDER_PHONE_PATTERNS:
        m = pat.search(draft)
        if m:
            flags.append({
                "kind": "placeholder_phone",
                "text": m.group(0),
                "fix": "use the candidate's real phone from master CV",
            })

    # Forbidden header additions — only flag if NOT in master.
    for phrase in _FORBIDDEN_HEADER_ADDITIONS:
        if phrase in draft_lower and phrase not in master_lower:
            flags.append({
                "kind": "fabricated_header_field",
                "text": phrase,
                "fix": f"remove '{phrase}' — not in master CV",
            })

    return flags


# ═════════════════════════════════════════════════════════════════════════
# Node 6 + 7 — ATS Critic ensemble (DeepSeek-R1 + Kimi K2, parallel)
# ═════════════════════════════════════════════════════════════════════════
ATS_CRITIC_SYSTEM = """You are an ATS, recruiter-screening, AND fact-integrity
expert. You score the resume against the JD AND verify every claim against
the candidate's master CV.

Score the resume against:
- Keyword density vs. JD (extract top 20 JD terms; check coverage)
- Parseability (no tables, no images, no headers/footers, clean section labels)
- 6-second skim test: can a recruiter spot fit in 6 seconds?
- Length appropriateness (1 page <10y exp; 2 pages 10y+)
- Action-verb diversity, quantification rate (% bullets with numbers)
- Title/seniority alignment with target role
- FABRICATION CHECK (2026-05-12 — load-bearing): for EVERY claim in the
  draft, ask "is this in the master CV?". A claim is anything that's
  verifiable: phone number, city, country, employer location, job title
  held at a past employer, years of experience, certifications,
  quantified metrics ($X, Y%, Z users), product names shipped. If a
  claim is in the draft but NOT supported by the master CV, it's a
  fabrication and the resume must lose points hard.

ats_score must drop sharply (target floor: 40) when ANY of the
following are detected:
  • Identity fields (name, phone, email, location) don't match master CV
  • Past employer locations don't match master CV
  • Job titles at past employers don't match master CV
  • Quantified metrics not present in master CV are invented
  • Citizenship / nationality / availability statements appear that
    aren't in master CV

Output a JSON critique:
{
  "ats_score": 0-100,
  "keyword_coverage": [{"term": "...", "in_resume": true|false}],
  "missing_keywords": ["..."],
  "parseability_issues": ["..."],
  "skim_test_pass": true|false,
  "quantification_rate": 0.0-1.0,
  "fabrications": [
    {"claim": "<exact text in draft>", "issue": "not in master CV"}
  ],
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
    # 2026-05-12: critic now sees the master CV so it can run the
    # FABRICATION CHECK (system prompt). Without this the critics scored
    # high on fabricated resumes (ATS A:95, ATS B:92 on a resume that
    # invented a UK phone number, "British Citizen", and metrics like
    # "390M+ mobile wallets" that weren't in the source CV).
    #
    # Pre-flagged fabrications (from the pure-code _scan_for_obvious_fabrications
    # helper run after writer_node) are surfaced so the critic doesn't have
    # to re-discover them and so its score reflects them.
    prescanned_flags = state.get("fabrication_flags") or []
    prescanned_block = (
        "\n\nPRE-SCANNED FABRICATION FLAGS (pure-code structural scan already "
        "found these — escalate them as fabrications in your output):\n"
        + json.dumps(prescanned_flags, indent=2)
        if prescanned_flags else ""
    )
    user = f"""MASTER CV (source of truth — flag anything in the draft that
isn't supported here):
{state.get('master_resume_md', '')}

────────────────────────────────────────────────────────────────────────

JOB DESCRIPTION:
{(state['job'].get('description') or '')[:4000]}

CURRENT RESUME DRAFT (the artefact you are critiquing):
{state['current_draft']}{prescanned_block}

Score and critique. Run the FABRICATION CHECK from the system prompt
exhaustively. Return strict JSON only.
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

    # B-OR3: Build provider-specific fallback chain. Each critic has a
    # primary native provider + OpenRouter fallback with the same model.
    # OR fallback only triggers when the native key is exhausted or the
    # provider is unhealthy (circuit breaker open).
    _or_fallbacks = {
        "deepseek": ("openrouter", "deepseek/deepseek-reasoner"),
        "moonshot": ("openrouter", "moonshot/kimi-k2.5"),
        "anthropic": ("openrouter", "anthropic/claude-opus-4-5"),
        "openai": ("openrouter", "openai/gpt-4.1"),
        "google": ("openrouter", "google/gemini-2.5-pro"),
    }
    fallback_chain = [_or_fallbacks[provider]] if provider in _or_fallbacks else []

    async def _attempt_call(retry: bool) -> tuple[dict, float, int, str, str] | None:
        """Returns (parsed, cost, latency, model_used, raw_text) on parse success,
        or None to signal the caller should retry with a larger budget."""
        try:
            # B-OR3: Use hardened router with fallback. ask_with_fallback
            # handles retry + circuit breaker per provider, then falls back
            # to OR if the native provider is failing.
            hrouter = get_hardened_router()
            result = await hrouter.ask_with_fallback(
                primary_provider=provider,
                primary_model=model,
                fallback_chain=fallback_chain,
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


# Audit §5.3 — adaptive ATS critic ensemble.
# Critic B (Kimi K2) costs ~$0.05/iter. Empirically, once critic A
# (DeepSeek-R1) has returned a high score with no fabrications, B's
# marginal value drops to ~zero — wasted spend. We skip B when the
# prior iteration's A signal indicates the draft is already in shape.
# On iter 0 we still run both to establish a baseline.
#
# We read A's score from state, which (because of LangGraph's parallel
# fan-out from writer → a + b + persona) is the value populated by
# the PREVIOUS iteration's critic_a. That's the right proxy here —
# writer was given that critique as input and tightened the draft
# against it, so this iteration's A is overwhelmingly likely to land
# at the same score band. When the proxy is wrong (rare regression),
# the next loop re-engages B because merge → orchestrator routes
# back to writer with fresh state.
_ATS_CRITIC_B_SKIP_SCORE_FLOOR = 60


def _should_skip_ats_critic_b(state: ResumeState) -> tuple[bool, str]:
    """Decide whether ats_critic_b can be skipped this turn.

    Returns (skip, reason). The reason is a short tag the transcript
    records so the cost-savings decision is auditable per build.

    Skip when ALL hold:
      - iteration > 0                          (baseline iter always runs both)
      - critic_a from prev iter is present     (we need a signal to defer to)
      - critic_a has no _provider_error / _parse_error
      - critic_a.ats_score >= floor (60)
      - critic_a.fabrications is empty
    """
    iteration = state.get("iteration", 0) or 0
    if iteration <= 0:
        return False, "iter0_baseline"

    critic_a = state.get("critic_a") or {}
    if not critic_a:
        return False, "no_prior_critic_a"

    if critic_a.get("_provider_error") or critic_a.get("_parse_error"):
        return False, "prior_critic_a_failed"

    score_a = critic_a.get("ats_score") or 0
    if score_a < _ATS_CRITIC_B_SKIP_SCORE_FLOOR:
        return False, f"prior_score_a_below_floor:{score_a}"

    fabrications = critic_a.get("fabrications") or []
    if fabrications:
        return False, f"prior_critic_a_flagged_fabrications:{len(fabrications)}"

    return True, (
        f"adaptive_skip:iter={iteration},prior_score_a={score_a},"
        f"fabrications=0"
    )


async def ats_critic_b_node(state: ResumeState) -> dict:
    """ATS Critic B (Kimi K2, ~$0.05/iter) — adaptive (audit §5.3).

    On iter 1+ we early-return a sentinel
    ``{"ats_score": 0, "_skipped_adaptive": True}`` when critic_a's
    previous-iteration signal indicates the draft is already in shape.
    merge_critique already treats ats_score=0 from a critic as "defer to
    the other one" (see merge_critique_node — the ``score_a == 0`` /
    ``score_b == 0`` branches), so the skip is safe: A's verdict carries
    the merge. The skip decision is recorded in the transcript so the
    cost-savings behaviour is observable per build.
    """
    iteration = state.get("iteration", 0) or 0
    skip, reason = _should_skip_ats_critic_b(state)
    if skip:
        sentinel = {
            "ats_score": 0,
            "missing_keywords": [],
            "specific_fixes": [],
            "_skipped_adaptive": True,
            "_skip_reason": reason,
        }
        return {
            "critic_b": sentinel,
            "transcript": [
                make_turn(
                    node="ats_critic_b",
                    iteration=iteration,
                    input_summary=f"adaptive skip ({reason})",
                    output={
                        "skipped": True,
                        "reason": reason,
                        "saved_usd_approx": 0.05,
                    },
                    cost_usd=0.0,
                    latency_ms=0,
                )
            ],
        }

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
                iteration=iteration,
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
POLISHER_SYSTEM = """You are the final-pass quality gate AND last line of
defence against fabrication.

Step 1 — IDENTITY VERIFY (NON-NEGOTIABLE, before any polishing):
Compare the draft's header block against the master CV. The following
fields MUST appear in the draft EXACTLY as in the master CV:
  • Name (the line under "#")
  • Phone number (every digit, every separator)
  • Email address (verbatim)
  • LinkedIn URL (verbatim)
  • City + country in the contact line
If ANY of these differ from the master CV — replace them with the master
CV values. NEVER use placeholder phone numbers ("+44 7XXX XXXXXX",
"[phone]"). NEVER change the candidate's city to the JD's city. NEVER
add citizenship / availability / visa fields that aren't in the master.

Step 2 — FACT VERIFY:
For each bullet in Experience, check whether the underlying claim
(employer location, job title, dates, metric) is supported by the
master CV. Strike any sentence containing a fabricated metric and
replace it with the closest TRUE bullet from the master CV that
addresses the same JD signal. Do NOT keep a fabricated number even
if it makes the candidate look better.

Step 3 — POLISH:
Tighten language, fix awkward phrasing, ensure every line earns its
place. Apply ANTI-AI-TELL discipline (no "delve / tapestry / unpack /
journey / leveraged / spearheaded / passionate about", no em-dash
strings).

Step 4 — SELF-SCORE 0-100 across:
  fit (40%) — how well true CV evidence maps to JD requirements
  ats (20%) — keyword coverage + parseability
  impact (20%) — quantified outcomes per the master CV
  narrative (10%) — flow, sequence, story
  polish (10%) — clean prose, no AI tells
fit MUST be capped at 60 if any identity-level fabrication remained
through Step 1 (you couldn't fully fix it). Better to ship a 60-fit
honest resume than a 95-fit fabricated one.

Output strict JSON:
{
  "final_resume_md": "...",
  "final_score": 0-100,
  "score_breakdown": {"fit": ..., "ats": ..., "impact": ..., "narrative": ..., "polish": ...},
  "identity_fixes_applied": ["e.g. 'restored phone +971-58-9683970'", ...],
  "fact_fixes_applied": ["e.g. 'removed fabricated 14% auth uplift claim'", ...],
  "remaining_concerns": ["...", "..."]
}"""


async def polisher_node(state: ResumeState) -> dict:
    settings = get_settings()
    # 2026-05-12: polisher now sees master CV so Step 1 IDENTITY VERIFY +
    # Step 2 FACT VERIFY can actually compare against the source of truth.
    # Before this, the polisher only saw the JD + draft + critic feedback,
    # so it had no way to catch fabricated identity facts.
    prescanned = state.get("fabrication_flags") or []
    prescanned_block = (
        "\n\nPRE-SCANNED STRUCTURAL FABRICATION FLAGS (from pure-code scan; "
        "your Step-2 FACT VERIFY MUST remove every one of these from "
        "final_resume_md):\n" + json.dumps(prescanned, indent=2)
        if prescanned else ""
    )
    header_action = state.get("header_enforcement", "noop")
    header_block = (
        f"\n\nHEADER ENFORCEMENT NOTE: the writer's contact block was "
        f"replaced verbatim from master CV (action={header_action!r}). "
        f"DO NOT modify the header — it is already canonical."
    )
    user = f"""MASTER CV (source of truth for IDENTITY VERIFY + FACT VERIFY):
{state.get('master_resume_md', '')}

────────────────────────────────────────────────────────────────────────

JOB:
{state['job'].get('title', '')} @ {state['company_name']}

JD:
{(state['job'].get('description') or '')[:4000]}

CURRENT DRAFT (the artefact to polish — apply Steps 1-3 then score):
{state['current_draft']}

CRITIC FEEDBACK (merged, including any fabrications flagged):
{json.dumps(state.get('merged_critique', {}), indent=2)[:2000]}{prescanned_block}{header_block}

Run Steps 1-4 from the system prompt. Strict JSON only."""

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
