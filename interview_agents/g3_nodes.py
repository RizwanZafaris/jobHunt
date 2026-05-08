"""
interview_agents/g3_nodes.py — The G3 LangGraph node functions.

Each node is an async function: InterviewPrepState -> dict (state patch).
Nodes call agents.llm_router via async wrappers; never instantiate
clients directly.

Node summary (9 functions, 7 logical):
  entry                  pure code — load inputs, create interview_prep row
  behavioral_predictor   Claude Opus 4.5 — competency + behavioral questions
  technical_predictor    Gemini 2.5 Pro + Google Search grounding
  domain_predictor       Claude Opus 4.5 — payments-domain questions per archetype
  merge_questions        pure code — union(3 predictors), Jaccard dedupe, sort, cap 20
  star_story_matcher     Claude Opus 4.5 — match story_bank rows; flag missing
  mock_interview_loop    single-node loop (Claude Opus 4.5 + DeepSeek-R1 internally)
  compile_prep_pack      pure code — render markdown
  export_node            pure code — write interview_prep + Storage upload
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any

from agents.llm_router import get_router, _parse_json_loose
from config.settings import get_settings
from interview_agents.g3_state import InterviewPrepState, make_turn, truncate

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# Node 1 — entry (pure code)
# ═════════════════════════════════════════════════════════════════════════
async def entry_node(state: InterviewPrepState) -> dict:
    """
    Hydrate InterviewPrepState from Supabase. Creates the interview_prep
    row in 'running' status. After this node runs, all subsequent nodes can
    rely on application, job, company_persona, story_bank, last_resume_build,
    interview_history being present.

    Expects in state: application_id, round_type. round_number defaults to 1.
    company_name is canonicalised by the caller.
    """
    from interview_agents.g3_io import (
        load_application,
        load_job,
        load_company_persona,
        load_story_bank,
        load_last_resume_build,
        load_interview_history,
        create_interview_prep,
    )

    application_id = state["application_id"]
    round_type = state.get("round_type") or "hm"
    round_number = state.get("round_number") or 1

    application = load_application(application_id)
    job_id = application.get("job_id")
    job = load_job(job_id) if job_id else {}
    company_name = state.get("company_name") or application.get("company") or job.get("company") or ""

    company_persona = load_company_persona(company_name)
    story_bank = load_story_bank()
    last_resume_build = load_last_resume_build(job_id) if job_id else None
    interview_history = load_interview_history(application_id)

    interview_prep = create_interview_prep(
        application_id=application_id,
        job_id=job_id,
        company_name=company_name,
        round_type=round_type,
        round_number=round_number,
    )

    return {
        "application": application,
        "job_id": job_id or 0,
        "job": job,
        "company_name": company_name,
        "round_type": round_type,
        "round_number": round_number,
        "company_persona": company_persona,
        "story_bank": story_bank,
        "last_resume_build": last_resume_build,
        "interview_history": interview_history,
        "interview_prep_id": interview_prep["id"],
        "iteration": 0,
        "mock_iteration": 0,
        "converged": False,
        "transcript": [
            make_turn(
                node="entry",
                input_summary=(
                    f"application_id={application_id} job_id={job_id} "
                    f"company={company_name} round={round_type}#{round_number}"
                ),
                output={
                    "persona_loaded": bool(company_persona),
                    "persona_version": (company_persona or {}).get("persona_version"),
                    "story_bank_count": len(story_bank),
                    "last_resume_build_id": (last_resume_build or {}).get("id"),
                    "prior_rounds_count": len(interview_history),
                    "interview_prep_id": interview_prep["id"],
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 2 — behavioral_predictor (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
BEHAVIORAL_PREDICTOR_FALLBACK_SYSTEM = """You are a senior interview coach with 20+ years inside fintech / payments hiring.

Predict the most likely behavioural and competency-based questions a candidate will face for this role at this company.

Without specific persona signal, draw on standard fintech-PM behavioural patterns:
- Stakeholder conflict / cross-functional alignment
- Strategic prioritisation under constraints
- Customer empathy + outcome ownership
- Hiring + scaling decisions
- Communication with execs / board

Return STRICT JSON only — no prose preamble, no commentary.
"""


async def behavioral_predictor_node(state: InterviewPrepState) -> dict:
    """
    Reads: job, company_persona (recruitment_process / interview_format /
    hiring_signals), interview_history.
    Outputs: behavioral_questions — list[{question, competency, importance}]
    """
    settings = get_settings()
    persona = state.get("company_persona") or {}
    persona_brief = ""
    if persona:
        rec = (persona.get("metadata") or {}).get("recruitment_process") or persona.get("recruitment_process")
        intf = (persona.get("metadata") or {}).get("interview_format") or persona.get("interview_format")
        sig = (persona.get("metadata") or {}).get("hiring_signals") or persona.get("hiring_signals")
        persona_brief = (
            f"RECRUITMENT PROCESS:\n{rec}\n\n"
            f"INTERVIEW FORMAT:\n{intf}\n\n"
            f"HIRING SIGNALS:\n{sig}\n"
        )

    if persona and persona.get("system_prompt_template"):
        system = persona["system_prompt_template"]
    else:
        system = BEHAVIORAL_PREDICTOR_FALLBACK_SYSTEM

    job = state.get("job") or {}
    history = state.get("interview_history") or []

    user = f"""ROLE: {job.get('title', '')} @ {state.get('company_name', '')}
ROUND: {state.get('round_type', 'hm')} (round #{state.get('round_number', 1)})

JOB DESCRIPTION:
{(job.get('description') or '')[:5000]}

{persona_brief}

PRIOR ROUNDS (questions remembered, feedback if any):
{json.dumps(history, indent=2, default=str)[:3000] if history else '(no prior rounds)'}

Predict the top 8-12 behavioural / competency questions the candidate is most likely to face in THIS round.

Output STRICT JSON only:
{{
  "questions": [
    {{"question": "Tell me about a time...", "competency": "stakeholder_management", "importance": 5}},
    ...
  ]
}}

`importance` is 1-5 (5 = almost certainly asked). No prose. No fences.
"""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g3_behavioral_predictor_model,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=2000,
        temperature=0.3,
        agent_name="g3.behavioral_predictor",
        json_response=True,
    )
    questions = _safe_parse_question_list(result.text, "behavioral_predictor")
    return {
        "behavioral_questions": questions,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="behavioral_predictor",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={"n_questions": len(questions)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 3 — technical_predictor (Gemini 2.5 Pro + grounding)
# ═════════════════════════════════════════════════════════════════════════
TECHNICAL_PREDICTOR_SYSTEM = """You are a technical interview coach for senior fintech / payments product roles.

Use live web grounding to find recent, specific signal about how this company conducts technical / case / system-design rounds. Search for things like:

  "{company} interview process site:reddit.com"
  "{company} {role} case interview site:blind.teamblind.com"
  "{company} interview prep glassdoor"
  "{company} system design interview"

Then return strict JSON predictions. The user will see the questions verbatim — be concrete (not "design a system" but "design a real-time payment risk system that scales to 10M tx/day").

Return STRICT JSON only — no prose preamble, no commentary.
"""


async def technical_predictor_node(state: InterviewPrepState) -> dict:
    """
    Reads: job, company_persona, last_resume_build (for keyword surface area).
    Outputs: technical_questions — list[{question, competency, importance}]
    """
    settings = get_settings()
    job = state.get("job") or {}
    company = state.get("company_name", "")
    last_build = state.get("last_resume_build") or {}
    resume_excerpt = (last_build.get("resume_md") or "")[:2500]

    user = f"""COMPANY: {company}
ROLE: {job.get('title', '')}
ROUND TYPE: {state.get('round_type', 'hm')}

JOB DESCRIPTION:
{(job.get('description') or '')[:4000]}

RESUME EXCERPT (showing what the candidate emphasised — likely probe areas):
{resume_excerpt or '(no prior resume build)'}

Use grounded search ("{company} interview process site:reddit.com OR site:blind.teamblind.com OR site:glassdoor.com") to find live signal about this company's technical / case / system-design interviews.

Then predict the top 6-10 technical / case / system-design questions the candidate is most likely to face.

Output STRICT JSON only:
{{
  "questions": [
    {{"question": "Design a tokenisation service for...", "competency": "system_design", "importance": 4}},
    ...
  ]
}}

`competency` is one of: system_design | case | technical_depth | metrics_design | product_sense.
`importance` is 1-5. No prose. No fences.
"""

    result = await get_router().ask(
        provider="google",
        model=settings.g3_technical_predictor_model,
        system=TECHNICAL_PREDICTOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=2500,
        temperature=0.25,
        tools=[{"type": "google_search"}],
        agent_name="g3.technical_predictor",
    )
    questions = _safe_parse_question_list(result.text, "technical_predictor")
    return {
        "technical_questions": questions,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="technical_predictor",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={"n_questions": len(questions)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 4 — domain_predictor (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
DOMAIN_PREDICTOR_SYSTEM = """You are a payments-domain expert interview coach.

You predict domain-specific questions tailored to:
- The candidate's archetype (CPO, Head of Product, PMO, Senior PM)
- The company's category (network, processor, BNPL, neobank, etc.)
- The role's actual JD focus (cross-border, risk, BNPL, treasury, ...)

Examples by category:
  - card networks (Visa/MC) → interchange, scheme rules, tokenisation, network economics
  - processors (Stripe/Adyen) → settlement, reconciliation, FX, MOR/MoR, fraud
  - BNPL (Klarna/Affirm/Tabby) → underwriting, collections, regulator dance
  - neobanks (Revolut/N26) → multi-product economics, KYC/AML, treasury

Return STRICT JSON only — no prose, no fences.
"""


async def domain_predictor_node(state: InterviewPrepState) -> dict:
    """
    Reads: job (for archetype + JD), company_persona (for category if present).
    Outputs: domain_questions — list[{question, competency, importance}]
    """
    settings = get_settings()
    job = state.get("job") or {}
    persona = state.get("company_persona") or {}
    archetype = job.get("archetype") or "Senior PM"
    company_category = (persona.get("metadata") or {}).get("category") or "fintech"

    user = f"""COMPANY: {state.get('company_name', '')}  (category: {company_category})
ROLE: {job.get('title', '')}
CANDIDATE ARCHETYPE: {archetype}

JOB DESCRIPTION:
{(job.get('description') or '')[:4500]}

Predict the top 6-10 payments-domain questions tailored to BOTH the company's category AND the candidate's archetype. Make them concrete and answerable in 4-7 minutes (not "explain payments" — "walk me through the lifecycle of a cross-border B2B payment from your perspective as a {archetype} owning treasury reconciliation").

Output STRICT JSON only:
{{
  "questions": [
    {{"question": "...", "competency": "domain_depth", "importance": 4}},
    ...
  ]
}}

`competency` is one of: domain_depth | strategy | regulatory | metrics | product_market_fit.
`importance` is 1-5. No prose. No fences.
"""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g3_domain_predictor_model,
        system=DOMAIN_PREDICTOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=2000,
        temperature=0.3,
        agent_name="g3.domain_predictor",
        json_response=True,
    )
    questions = _safe_parse_question_list(result.text, "domain_predictor")
    return {
        "domain_questions": questions,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="domain_predictor",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={"n_questions": len(questions)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Helpers — JSON parsing + Jaccard dedupe
# ═════════════════════════════════════════════════════════════════════════
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase token set, ignoring stopwords and punctuation."""
    if not text:
        return set()
    stop = {
        "the", "a", "an", "of", "to", "for", "in", "on", "at", "by", "with",
        "is", "are", "was", "were", "be", "been", "being", "do", "does",
        "did", "have", "has", "had", "and", "or", "but", "as", "if",
        "you", "your", "yours", "i", "me", "my", "we", "our",
        "tell", "describe", "explain", "walk", "share",
    }
    return {t for t in _TOKEN_RE.findall(text.lower()) if t and t not in stop}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _safe_parse_question_list(text: str, node_name: str) -> list[dict]:
    """
    Extract the `questions` list from a router response. Tolerates both
    `{"questions": [...]}` and bare-array `[...]` shapes. Returns [] on
    parse failure rather than crashing the whole graph.
    """
    if not text:
        return []
    try:
        parsed = _parse_json_loose(text)
    except Exception as e:
        logger.warning(f"{node_name} JSON parse failed: {e}")
        return []
    if isinstance(parsed, dict) and "questions" in parsed:
        items = parsed["questions"]
    elif isinstance(parsed, list):
        items = parsed
    else:
        items = []
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        q = (item.get("question") or "").strip()
        if not q:
            continue
        out.append({
            "question": q,
            "competency": (item.get("competency") or "").strip() or "general",
            "importance": int(item.get("importance") or 3),
        })
    return out


# ═════════════════════════════════════════════════════════════════════════
# Node 5 — merge_questions (pure code, no LLM)
# ═════════════════════════════════════════════════════════════════════════
async def merge_questions_node(state: InterviewPrepState) -> dict:
    """
    Union the three predictor outputs, dedupe via token-set Jaccard ≥ 0.7,
    sort by importance DESC, cap at 20.

    Pure code — no LLM, no I/O.
    """
    behav = state.get("behavioral_questions") or []
    tech = state.get("technical_questions") or []
    dom = state.get("domain_questions") or []

    # Tag each with its source so the prep pack can group later
    candidates: list[dict] = []
    for q in behav:
        candidates.append({**q, "source": "behavioral"})
    for q in tech:
        candidates.append({**q, "source": "technical"})
    for q in dom:
        candidates.append({**q, "source": "domain"})

    # Dedupe by token-set Jaccard ≥ 0.7. Higher-importance question wins;
    # ties broken by source preference (technical > domain > behavioral —
    # technical/grounded signal is the most distinguishing).
    source_weight = {"technical": 3, "domain": 2, "behavioral": 1}
    candidates.sort(
        key=lambda c: (-int(c.get("importance") or 0), -source_weight.get(c["source"], 0))
    )

    accepted: list[dict] = []
    accepted_tokens: list[set[str]] = []
    for c in candidates:
        toks = _tokenize(c["question"])
        is_dupe = False
        for prev in accepted_tokens:
            if _jaccard(toks, prev) >= 0.7:
                is_dupe = True
                break
        if not is_dupe:
            accepted.append(c)
            accepted_tokens.append(toks)

    likely_questions = accepted[:20]

    return {
        "likely_questions": likely_questions,
        "transcript": [
            make_turn(
                node="merge_questions",
                output={
                    "n_behavioral": len(behav),
                    "n_technical": len(tech),
                    "n_domain": len(dom),
                    "n_after_dedupe": len(accepted),
                    "n_capped": len(likely_questions),
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 6 — star_story_matcher (Claude Opus 4.5)
# ═════════════════════════════════════════════════════════════════════════
STAR_MATCHER_SYSTEM = """You are an interview prep assistant.

For each candidate question, find the BEST match from the candidate's STAR+R story bank, or flag that no story currently exists and the candidate must add one.

Match quality scale:
  strong   — story directly answers the question, can be used verbatim
  partial  — story is adjacent; needs reframing for this question
  weak     — same theme but different competency; flag for later
  missing  — no relevant story; needs_rizwan_input=true

Return STRICT JSON only.
"""


async def star_story_matcher_node(state: InterviewPrepState) -> dict:
    """
    For each top question, find best match from story_bank (text-similarity
    heuristic). Flag missing as `needs_rizwan_input: true`.

    Empty story_bank case: return all questions flagged needing input.
    Don't crash — that's the cold-start path (live DB has 0 stories today).
    """
    settings = get_settings()
    questions = state.get("likely_questions") or []
    stories = state.get("story_bank") or []

    if not questions:
        return {
            "star_stories": [],
            "transcript": [
                make_turn(
                    node="star_story_matcher",
                    output={"reason": "no questions to match against"},
                )
            ],
        }

    if not stories:
        # Cold-start path — no stories at all. Flag every question and skip
        # the LLM call entirely (saves ~$0.40).
        flagged = [
            {
                "question": q["question"],
                "competency": q.get("competency"),
                "story_id": None,
                "match_quality": "missing",
                "needs_rizwan_input": True,
            }
            for q in questions
        ]
        return {
            "star_stories": flagged,
            "transcript": [
                make_turn(
                    node="star_story_matcher",
                    output={
                        "reason": "story_bank empty (cold start)",
                        "n_flagged": len(flagged),
                    },
                )
            ],
        }

    # We have stories — use the LLM to do semantic matching
    story_list = [
        {
            "id": s.get("id"),
            "title": s.get("title", ""),
            "themes": s.get("themes", []),
            "summary": (
                f"S: {(s.get('situation') or '')[:120]} | "
                f"T: {(s.get('task') or '')[:80]} | "
                f"A: {(s.get('action') or '')[:120]} | "
                f"R: {(s.get('result') or '')[:80]}"
            ),
            "companies_used": s.get("companies_used", []),
        }
        for s in stories
    ]

    user = f"""COMPANY: {state.get('company_name', '')}

CANDIDATE STORY BANK:
{json.dumps(story_list, indent=2, default=str)[:15000]}

QUESTIONS TO MATCH:
{json.dumps(questions[:20], indent=2)}

For each question, return the best matching story id (or null), the match quality, and whether the candidate needs to add a new story.

Output STRICT JSON only:
{{
  "matches": [
    {{
      "question": "...",
      "competency": "...",
      "story_id": "uuid-or-null",
      "match_quality": "strong|partial|weak|missing",
      "rationale": "1 sentence",
      "needs_rizwan_input": false
    }}
  ]
}}

No prose. No fences.
"""

    result = await get_router().ask(
        provider="anthropic",
        model=settings.g3_star_matcher_model,
        system=STAR_MATCHER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=3500,
        temperature=0.2,
        agent_name="g3.star_story_matcher",
        json_response=True,
    )
    star_stories: list[dict] = []
    try:
        parsed = _parse_json_loose(result.text)
        matches = parsed.get("matches", []) if isinstance(parsed, dict) else parsed
        if isinstance(matches, list):
            for m in matches:
                if not isinstance(m, dict):
                    continue
                star_stories.append({
                    "question": (m.get("question") or "").strip(),
                    "competency": m.get("competency"),
                    "story_id": m.get("story_id"),
                    "match_quality": m.get("match_quality", "missing"),
                    "rationale": (m.get("rationale") or "")[:300],
                    "needs_rizwan_input": bool(m.get("needs_rizwan_input")),
                })
    except Exception as e:
        logger.warning(f"star_story_matcher JSON parse failed: {e}")
        # Fall back to flagging everything
        star_stories = [
            {
                "question": q["question"],
                "competency": q.get("competency"),
                "story_id": None,
                "match_quality": "missing",
                "needs_rizwan_input": True,
            }
            for q in questions
        ]

    return {
        "star_stories": star_stories,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_turn(
                node="star_story_matcher",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={
                    "n_matched": sum(
                        1 for s in star_stories
                        if s.get("match_quality") in ("strong", "partial")
                    ),
                    "n_missing": sum(
                        1 for s in star_stories if s.get("needs_rizwan_input")
                    ),
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 7 — mock_interview_loop (single-node loop: Claude Opus + DeepSeek-R1)
# ═════════════════════════════════════════════════════════════════════════
MOCK_INTERVIEWER_SYSTEM = """You are an executive interview coach drafting a model answer template for a candidate.

Produce a strong, structured answer (STAR+R format where appropriate, framework-driven for case/system-design):

- 3-6 sentence opening that frames the question and your approach
- The substantive content (numbers, named people, concrete framework — but mark anything the candidate must personalise with `[CANDIDATE TO INSERT: ...]`)
- A 2-3 sentence close with explicit reflection / what-I-learned

Output ONLY the answer markdown. No commentary.
"""

MOCK_CRITIC_SYSTEM = """You are a brutally honest, panel-veteran interview critic.

Score the candidate's mock answer on 0-100 across these axes (weight in parens):
  - Clarity (20%) — is the structure clear, easy to follow?
  - Relevance (25%) — does it actually answer the question?
  - Specificity (25%) — concrete numbers, named projects, real outcomes?
  - Reflection (15%) — what they learned, what they'd do differently
  - Brevity (15%) — < 4 minutes spoken, no filler

Output STRICT JSON only:
{
  "score": 0-100,
  "axes": {"clarity": 0-100, "relevance": 0-100, "specificity": 0-100, "reflection": 0-100, "brevity": 0-100},
  "improvements": ["concrete suggestion 1", "concrete suggestion 2", ...]
}

No prose. No fences.
"""


async def mock_interview_loop_node(state: InterviewPrepState) -> dict:
    """
    Internal loop, max `g3_max_iterations` (default 2). Single-node design
    so we don't have to teach LangGraph about the inner loop topology.

    Per iteration:
      1. mock_interviewer (Claude Opus 4.5) generates a strong-answer template
         for the top-importance question (or revises previous draft)
      2. mock_critic (DeepSeek-R1) scores 0-100, suggests improvements

    Stop when score ≥ g3_target_answer_score OR mock_iteration ≥ max OR
    cost cap hit (Phase 2 — orchestrator-equivalent).

    The single-node approach is simpler than two nodes + a conditional edge
    because we never need LangGraph to checkpoint between mock_interviewer
    and mock_critic — they're tightly coupled.
    """
    settings = get_settings()
    questions = state.get("likely_questions") or []
    if not questions:
        return {
            "mock_target_question": None,
            "mock_answer_md": "",
            "mock_critic_score": 0,
            "mock_iteration": 0,
            "transcript": [
                make_turn(
                    node="mock_interview_loop",
                    output={"reason": "no questions to rehearse"},
                )
            ],
        }

    # Pick the highest-importance question to rehearse
    target = sorted(
        questions,
        key=lambda q: -(int(q.get("importance") or 0)),
    )[0]
    job = state.get("job") or {}
    company = state.get("company_name", "")

    # Cost cap context (Phase 2 — mirrors G2 Phase 1.11 orchestrator)
    cost_so_far = state.get("cost_usd_total", 0.0) or 0.0
    cost_cap = state.get("cost_cap_usd") or settings.g3_max_cost_usd
    target_score = settings.g3_target_answer_score
    max_iter = settings.g3_max_iterations

    transcript: list = []
    mock_answer = ""
    score = 0
    feedback: list[str] = []
    iteration = 0
    cost_capped = False
    cost_total_local = 0.0
    latency_total_local = 0

    for i in range(max_iter):
        iteration = i + 1

        # Pre-call cost cap check — mirrors G2 orchestrator Phase 1.11
        if cost_so_far + cost_total_local >= cost_cap:
            logger.warning(
                f"G3 mock_loop: cost cap hit before iter {iteration} "
                f"(${cost_so_far + cost_total_local:.2f} >= ${cost_cap:.2f}) — exiting"
            )
            cost_capped = True
            transcript.append(
                make_turn(
                    node="mock_interview_loop",
                    iteration=iteration,
                    output={
                        "reason": "cost cap pre-check",
                        "cost_so_far": round(cost_so_far + cost_total_local, 4),
                        "cost_cap": cost_cap,
                    },
                )
            )
            break

        # ─── mock_interviewer (Claude Opus 4.5) ────────────────────────
        prev_block = (
            f"\n\nPREVIOUS DRAFT (revise):\n{mock_answer}\n\n"
            f"CRITIC IMPROVEMENTS NEEDED:\n- " + "\n- ".join(feedback)
        ) if mock_answer else ""

        interviewer_user = f"""COMPANY: {company}
ROLE: {job.get('title', '')}
ROUND: {state.get('round_type', 'hm')}
QUESTION (importance {target.get('importance')}, competency {target.get('competency')}):
"{target['question']}"

{prev_block}

Draft the strongest possible model answer for this question. Output ONLY the answer markdown.
"""
        interviewer = await get_router().ask(
            provider="anthropic",
            model=settings.g3_mock_interviewer_model,
            system=MOCK_INTERVIEWER_SYSTEM,
            messages=[{"role": "user", "content": interviewer_user}],
            max_tokens=2500,
            temperature=0.4,
            agent_name="g3.mock_interviewer",
        )
        mock_answer = interviewer.text
        cost_total_local += float(interviewer.cost_usd or 0)
        latency_total_local += int(interviewer.latency_ms or 0)
        transcript.append(
            make_turn(
                node="mock_interviewer",
                iteration=iteration,
                provider=interviewer.provider,
                model=interviewer.model,
                input_summary=truncate(interviewer_user),
                output=truncate(mock_answer, 800),
                cost_usd=interviewer.cost_usd,
                latency_ms=interviewer.latency_ms,
            )
        )

        # Mid-loop cost check — if interviewer alone tipped us over, stop
        if cost_so_far + cost_total_local >= cost_cap:
            cost_capped = True
            logger.warning(
                f"G3 mock_loop: cost cap hit after interviewer iter {iteration} "
                f"(${cost_so_far + cost_total_local:.2f}) — skipping critic"
            )
            break

        # ─── mock_critic (DeepSeek-R1) ─────────────────────────────────
        critic_user = f"""QUESTION:
"{target['question']}"

CANDIDATE'S MOCK ANSWER:
{mock_answer}

Score and suggest improvements. Strict JSON only.
"""
        critic = await get_router().ask(
            provider="deepseek",
            model=settings.g3_mock_critic_model,
            system=MOCK_CRITIC_SYSTEM,
            messages=[{"role": "user", "content": critic_user}],
            max_tokens=1500,
            temperature=0.1,
            agent_name="g3.mock_critic",
            json_response=True,
        )
        cost_total_local += float(critic.cost_usd or 0)
        latency_total_local += int(critic.latency_ms or 0)

        try:
            parsed = _parse_json_loose(critic.text)
            score = int(parsed.get("score") or 0)
            improvements = parsed.get("improvements") or []
            feedback = [str(x)[:300] for x in improvements if x][:6]
        except Exception as e:
            logger.warning(f"mock_critic JSON parse failed: {e}")
            score = 0
            feedback = [f"(critic parse error: {str(e)[:200]})"]

        transcript.append(
            make_turn(
                node="mock_critic",
                iteration=iteration,
                provider=critic.provider,
                model=critic.model,
                input_summary=truncate(critic_user),
                output={"score": score, "n_improvements": len(feedback)},
                cost_usd=critic.cost_usd,
                latency_ms=critic.latency_ms,
            )
        )

        if score >= target_score:
            logger.info(
                f"G3 mock_loop: converged at iter {iteration} (score {score} >= {target_score})"
            )
            break

    converged = score >= target_score
    return {
        "mock_target_question": target,
        "mock_answer_md": mock_answer,
        "mock_critic_score": score,
        "mock_critic_feedback": feedback,
        "mock_iteration": iteration,
        "iteration": iteration,
        "converged": converged,
        "cost_capped": cost_capped,
        "cost_usd_total": round(cost_total_local, 6),
        "latency_ms_total": latency_total_local,
        "transcript": transcript,
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 8 — compile_prep_pack (pure code, no LLM)
# ═════════════════════════════════════════════════════════════════════════
async def compile_prep_pack_node(state: InterviewPrepState) -> dict:
    """
    Render markdown prep pack from accumulated state. Also extracts
    company_hooks, red_flag_questions, salary_negotiation_notes from the
    persona if present.

    Pure code — no LLM, no I/O. The persona's `company_hooks` and similar
    arrays are surfaced verbatim; we don't paraphrase.
    """
    company = state.get("company_name", "")
    job = state.get("job") or {}
    persona = state.get("company_persona") or {}
    persona_meta = persona.get("metadata") or {}

    # Derive hooks / red flags / salary from persona — graceful fallbacks
    company_hooks: list[str] = []
    for key in ("company_hooks", "recent_news", "differentiators"):
        v = persona_meta.get(key) or persona.get(key)
        if isinstance(v, list):
            company_hooks.extend([str(x) for x in v if x])
        elif isinstance(v, str) and v.strip():
            company_hooks.append(v.strip())

    red_flag_questions: list[str] = []
    rf = persona_meta.get("red_flag_questions") or persona.get("red_flag_questions")
    if isinstance(rf, list):
        red_flag_questions = [str(x) for x in rf if x]

    salary_notes_lines = [
        f"- Target archetype: {job.get('archetype') or 'Senior PM'}",
        f"- Location: {job.get('location') or 'TBD'}",
        f"- Use the salary research agent to set anchor BEFORE this round.",
        f"- Default ask: market 75th percentile + 10-15% for relocation/visa friction.",
        f"- Do not anchor on current. Always anchor on market.",
    ]
    salary_negotiation_notes = "\n".join(salary_notes_lines)

    # ─── Render markdown ─────────────────────────────────────────────
    likely = state.get("likely_questions") or []
    by_source: dict[str, list[dict]] = {"behavioral": [], "technical": [], "domain": []}
    for q in likely:
        src = q.get("source") or "behavioral"
        by_source.setdefault(src, []).append(q)

    star = {s.get("question"): s for s in (state.get("star_stories") or [])}

    lines: list[str] = []
    lines.append(f"# Interview Prep — {company}")
    lines.append(f"**Role**: {job.get('title') or 'TBD'}  ·  "
                 f"**Round**: {state.get('round_type', 'hm')} (#{state.get('round_number', 1)})")
    lines.append("")
    lines.append(f"_Generated by G3 Interview Prep Graph._")
    lines.append("")

    # Top 20 questions grouped by type
    lines.append("## Top 20 Likely Questions")
    for src in ("behavioral", "technical", "domain"):
        bucket = by_source.get(src, [])
        if not bucket:
            continue
        lines.append(f"\n### {src.title()} ({len(bucket)})")
        for q in bucket:
            star_match = star.get(q["question"])
            star_tag = ""
            if star_match:
                mq = star_match.get("match_quality") or "missing"
                if mq == "strong":
                    star_tag = "  ✅ STAR story available"
                elif mq == "partial":
                    star_tag = "  ⚠️ partial STAR (reframe)"
                elif star_match.get("needs_rizwan_input"):
                    star_tag = "  ❌ NO STAR story — add one before round"
            imp = q.get("importance", 3)
            lines.append(f"- _(imp {imp})_  {q['question']}{star_tag}")

    # STAR stories matched
    matched = [s for s in (state.get("star_stories") or []) if s.get("match_quality") in ("strong", "partial")]
    if matched:
        lines.append("\n## STAR Stories Matched")
        for s in matched[:10]:
            lines.append(f"\n**Q**: {s['question']}")
            lines.append(f"_Match: {s.get('match_quality')}_  ·  story_id: `{s.get('story_id')}`")
            if s.get("rationale"):
                lines.append(f"\n{s['rationale']}")

    needs_input = [s for s in (state.get("star_stories") or []) if s.get("needs_rizwan_input")]
    if needs_input:
        lines.append("\n## Stories Rizwan Must Add Before This Round")
        for s in needs_input[:8]:
            lines.append(f"- {s['question']}")

    # Red flags
    if red_flag_questions:
        lines.append("\n## Red-Flag / Gotcha Questions")
        for rf in red_flag_questions[:10]:
            lines.append(f"- {rf}")

    # Mock answer transcript
    target = state.get("mock_target_question") or {}
    mock_md = state.get("mock_answer_md") or ""
    if mock_md and target:
        lines.append("\n## Mock Answer (high-importance question rehearsal)")
        lines.append(f"\n**Question**: {target.get('question', '')}")
        lines.append(f"_Mock-critic score: {state.get('mock_critic_score', 0)}/100  ·  "
                     f"iters: {state.get('mock_iteration', 0)}_")
        lines.append("\n" + mock_md)

    # Company hooks
    if company_hooks:
        lines.append("\n## Company Hooks (use during the round)")
        for h in company_hooks[:10]:
            lines.append(f"- {h}")

    # Salary
    lines.append("\n## Salary & Negotiation Notes")
    lines.append(salary_negotiation_notes)

    prep_pack_md = "\n".join(lines).strip()

    return {
        "company_hooks": company_hooks,
        "red_flag_questions": red_flag_questions,
        "salary_negotiation_notes": salary_negotiation_notes,
        "prep_pack_md": prep_pack_md,
        "transcript": [
            make_turn(
                node="compile_prep_pack",
                output={
                    "n_questions": len(likely),
                    "n_star_matched": len(matched),
                    "n_star_needs_input": len(needs_input),
                    "n_company_hooks": len(company_hooks),
                    "prep_pack_chars": len(prep_pack_md),
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 9 — export_node (pure code)
# ═════════════════════════════════════════════════════════════════════════
async def export_node(state: InterviewPrepState) -> dict:
    """
    1. Upload prep_pack_md to Supabase Storage 'interview-packs/' bucket → URL
    2. UPDATE interview_prep row with status='converged' (or one of
       exhausted/cost_capped/failed), all the prep fields, transcript,
       cost + latency totals.

    Status hierarchy (mirrors G2 Phase 1.11):
      cost_capped > exhausted > converged
    """
    from interview_agents.g3_io import finalize_interview_prep, upload_prep_pack
    settings = get_settings()

    company_name = state.get("company_name", "")
    interview_prep_id = state["interview_prep_id"]
    iteration = state.get("mock_iteration") or state.get("iteration", 0)
    max_iter = settings.g3_max_iterations
    prep_pack_md = state.get("prep_pack_md", "")

    if state.get("cost_capped"):
        status = "cost_capped"
    elif iteration >= max_iter and not state.get("converged"):
        status = "exhausted"
    else:
        status = "converged"

    prep_pack_url = upload_prep_pack(company_name, interview_prep_id, prep_pack_md) if prep_pack_md else None

    finalize_interview_prep(
        interview_prep_id=interview_prep_id,
        status=status,
        iterations=iteration,
        prep_pack_md=prep_pack_md,
        prep_pack_url=prep_pack_url,
        likely_questions=state.get("likely_questions"),
        star_stories=state.get("star_stories"),
        company_hooks=state.get("company_hooks"),
        red_flag_questions=state.get("red_flag_questions"),
        salary_negotiation_notes=state.get("salary_negotiation_notes"),
        agent_transcript=state.get("transcript", []),
        cost_usd_total=state.get("cost_usd_total", 0),
        latency_ms_total=state.get("latency_ms_total", 0),
    )

    return {
        "prep_pack_url": prep_pack_url or "",
        "transcript": [
            make_turn(
                node="export",
                output={
                    "status": status,
                    "iterations": iteration,
                    "mock_critic_score": state.get("mock_critic_score"),
                    "prep_pack_url": prep_pack_url,
                },
            )
        ],
    }
