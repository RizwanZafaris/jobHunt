"""
agents/g7_nodes.py — Seven LangGraph nodes (+ persona_critic sibling) for
the G7 Application Graph (Tier 3 / crown jewel).

Layout (mirrors G2's parallel-critic fan-out at the answer layer):

    form_scanner          (code, Playwright)
          ↓
    question_classifier   (Sonnet 4.6, $0.05; single batched call)
          ↓
    answer_retriever      (code + RAG, $0.00 base + embeddings on miss)
          ↓
    answer_generator + persona_critic   (parallel siblings, Sonnet 4.6)
          ↘                  ↙
        merge_critique      (code; loops back if persona flagged
                             fabricated metrics AND attempts left)
                  ↓
        human_review        (interrupt() — graph pauses; dashboard surfaces
                             drafts; user approves per-field; POST /approve
                             resumes the graph with the user_approvals
                             patched into state)
                  ↓
        form_filler         (code, Playwright; stops BEFORE submit)
                  ↓
        persist             (code; INSERTs follow_up_cadence row;
                             runs structural identity-lock scan)

Cost model (single form):
    form_scanner            $0.00   (Playwright + pure HTML scan)
    question_classifier     $0.05   (Sonnet 4.6, batch over all fields)
    answer_retriever        $0.00 + embeddings (negligible on cache hit)
    answer_generator        $0.15   (Sonnet 4.6; one batched call → answers)
    persona_critic          $0.05   (Sonnet 4.6; one batched call → flags)
    merge_critique          $0.00
    human_review            $0.00   (state pause)
    form_filler             $0.00
    persist                 $0.00
    ──────────────────────────────
    base total              $0.25
    + retry round (rare)    +$0.20
    target per roadmap      $0.30

Prompt caching: every Sonnet call uses `cache_system=True` so the long
career-ops form-filling framework + the persona idioms stay in the
provider cache across nodes (and across runs for the same persona).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from agents.form_scanner import (
    load_ats_pattern,
    scan_form,
    scan_from_html,
)
from agents.g7_state import (
    ApplicationFormState,
    G7Question,
    QUESTION_CATEGORIES,
    make_g7_turn,
)
from agents.llm_router import _parse_json_loose, get_router
from config.settings import get_settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Tunables — kept here so a single grep finds the cost knobs.
# ──────────────────────────────────────────────────────────────────────────
_G7_MODEL = "claude-sonnet-4-6"
_MAX_GENERATOR_RETRIES = 1   # one retry round, then we ship the draft as-is

# Identity-lock regex — any $N, $NM, $NB, 3x, 300%, "team of 12" or similar
# quantified claim needs to trace back to source_text. The persist_node
# runs this scan and redacts unmatched metrics.
_QUANT_METRIC_RE = re.compile(
    r"(\$\s*\d[\d,\.]*\s*(?:k|m|b|million|billion)?)"
    r"|(\b\d+(?:\.\d+)?\s*[xX]\b)"
    r"|(\b\d+(?:\.\d+)?\s*%\b)"
    r"|(\bteam of \d+\b)",
    re.IGNORECASE,
)

# Banned-keyword scan (filled in per-company from company_persona).
# Returned empty when the persona has no banned_keywords entry.
def _persona_banned_keywords(persona: dict) -> list[str]:
    if not isinstance(persona, dict):
        return []
    bank = persona.get("ats_keyword_bank") or {}
    banned = bank.get("banned") or persona.get("banned_keywords") or []
    return [str(b).strip().lower() for b in banned if b]


# ══════════════════════════════════════════════════════════════════════════
# Node 1 — form_scanner (Playwright, code)
# ══════════════════════════════════════════════════════════════════════════
async def form_scanner_node(state: ApplicationFormState) -> dict:
    """Drive Playwright to read the live form. Pure code; no LLM.

    Reads:  form_url, ats_type
    Writes: questions (unclassified), scanner_meta
    Cost:   $0.00

    Test override: if state carries a pre-baked `_test_html` field, we
    skip Playwright entirely and parse that HTML — keeps tests offline.
    """
    url = state.get("form_url") or ""
    ats_type = state.get("ats_type") or "greenhouse"

    test_html: Optional[str] = state.get("_test_html") if "_test_html" in state else None  # type: ignore[arg-type]
    if test_html is not None:
        result = scan_from_html(test_html, ats_type=ats_type, url=url, page_title="(test)")
    else:
        try:
            result = await scan_form(url, ats_type=ats_type)
        except Exception as e:
            logger.exception("G7 form_scanner failed for url=%s", url)
            return {
                "questions": [],
                "scanner_meta": {"error": f"{type(e).__name__}:{str(e)[:200]}"},
                "error": f"form_scanner_failed:{type(e).__name__}",
                "transcript": [make_g7_turn(
                    node="form_scanner",
                    input_summary=f"url={url} ats={ats_type}",
                    output={"error": str(e)[:200]},
                    error=str(e)[:500],
                )],
            }

    patch = result.as_state_patch()
    patch["transcript"] = [make_g7_turn(
        node="form_scanner",
        input_summary=f"url={url} ats={ats_type}",
        output={"field_count": result.field_count, "page_title": result.page_title},
    )]
    return patch


# ══════════════════════════════════════════════════════════════════════════
# Node 2 — question_classifier (Sonnet 4.6, batched, prompt-cached)
# ══════════════════════════════════════════════════════════════════════════
QUESTION_CLASSIFIER_SYSTEM = """You classify application-form fields into
EXACTLY ONE of these 10 categories. You receive a list of (field_id, label,
type) tuples and return strict JSON.

CATEGORIES (use these EXACT strings — anything else is invalid):
- basic_info        — name / email / phone / city / work-auth / sponsorship
- cover_letter      — full cover-letter textarea
- why_this_company  — "why us?" prompts
- why_this_role     — "why this role?" prompts
- salary            — comp / range / expected pay
- behavioral        — "tell us about a time" STAR prompts
- technical         — "describe experience with X technology" prompts
- dei_values        — diversity / inclusion / belonging prompts
- portfolio_url     — LinkedIn / GitHub / website / portfolio URL
- custom            — anything that doesn't fit above

Hints (use these as strong priors; the LABEL text is the ground truth):
- Fields whose label is exactly "First name", "Last name", "Email",
  "Phone", "City", "Country", "Work authorization" → basic_info
- Fields whose label asks "Why" → match "company" vs "role" vs "us"
- Textareas with 500+ char hint → likely cover_letter or behavioral
- Multiple-choice radios about gender / race / veteran / disability →
  dei_values
- Fields whose name is "linkedin", "github", "portfolio", "website" →
  portfolio_url
- Salary-related: "salary expectations", "expected comp", "desired
  range" → salary

Output strict JSON only, in this exact shape:
{
  "classifications": [
    {"idx": 0, "field_id": "...", "classification": "basic_info",
     "confidence": 0.0-1.0, "reasoning": "<one sentence>"},
    ...
  ]
}"""


def _heuristic_classify(question: dict, pattern_hints: dict[str, list[str]]) -> Optional[str]:
    """Pure-code classification using regex hints from the YAML pattern.

    Returns a category string when a hint matches the label or field name,
    or None when no hint matched (LLM decides). Costs $0; runs first.
    """
    name = (question.get("field_id") or "").lower()
    label = (question.get("field_label") or "").lower()
    for category, regexes in (pattern_hints or {}).items():
        for rgx in regexes or []:
            try:
                if re.search(rgx, label, re.IGNORECASE) or re.search(rgx, name, re.IGNORECASE):
                    return category
            except re.error:
                continue
    return None


async def question_classifier_node(state: ApplicationFormState) -> dict:
    """Batch-classify every form field into one of the 10 categories.

    Two-tier strategy:
      1. Heuristic regex scan from the YAML pattern (free) — catches the
         canonical Greenhouse field names instantly.
      2. LLM batch call for whatever the heuristics missed — one call,
         not N calls, so cost stays at ~$0.05/form even with 25-field
         forms.

    Reads:  questions, ats_type
    Writes: questions[i].classification, classification_summary
    Cost:   ~$0.05/form.
    """
    questions = state.get("questions") or []
    if not questions:
        return {"classification_summary": {"total": 0}}

    pattern = load_ats_pattern(state.get("ats_type") or "greenhouse")
    hints = pattern.get("classification_hints", {})

    # First pass: heuristics for free.
    pending: list[dict] = []
    for q in questions:
        h = _heuristic_classify(q, hints)
        if h and h in QUESTION_CATEGORIES:
            q["classification"] = h
        else:
            pending.append(q)

    # Second pass: LLM only for what's left.
    if pending:
        rows = "\n".join(
            f"- idx={q['idx']} id={q.get('field_id','')!r} "
            f"type={q.get('field_type','')!r} required={q.get('required')} "
            f"label={q.get('field_label','')[:200]!r}"
            for q in pending
        )
        user = (
            "Classify each of the following form fields into ONE of the "
            "10 categories. Return strict JSON per the system prompt.\n\n"
            f"FIELDS:\n{rows}"
        )
        settings = get_settings()
        model = getattr(settings, "g7_classifier_model", _G7_MODEL)
        result = await get_router().ask(
            provider="anthropic",
            model=model,
            system=QUESTION_CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500,
            temperature=0.0,
            agent_name="g7.question_classifier",
            json_response=True,
            cache_system=True,
        )
        try:
            parsed = _parse_json_loose(result.text) or {}
            cls_list = parsed.get("classifications") or []
        except Exception:
            cls_list = []
        by_idx = {int(c.get("idx", -1)): c for c in cls_list if isinstance(c, dict)}
        for q in pending:
            c = by_idx.get(int(q["idx"]))
            label_cat = (c or {}).get("classification") or "custom"
            if label_cat not in QUESTION_CATEGORIES:
                label_cat = "custom"
            q["classification"] = label_cat
        llm_cost = result.cost_usd
        llm_latency = result.latency_ms
        llm_model = result.model
        llm_provider = result.provider
    else:
        llm_cost = 0.0
        llm_latency = 0
        llm_model = ""
        llm_provider = ""

    # Compute summary
    summary: dict[str, int] = {"total": len(questions)}
    for q in questions:
        cat = q.get("classification") or "custom"
        summary[cat] = summary.get(cat, 0) + 1

    return {
        "questions": questions,
        "classification_summary": summary,
        "cost_usd_total": llm_cost,
        "latency_ms_total": llm_latency,
        "transcript": [make_g7_turn(
            node="question_classifier",
            provider=llm_provider,
            model=llm_model,
            input_summary=f"total={len(questions)} heuristic_hits={len(questions)-len(pending)} llm_pending={len(pending)}",
            output=summary,
            cost_usd=llm_cost,
            latency_ms=llm_latency,
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 3 — answer_retriever (code + RAG)
# ══════════════════════════════════════════════════════════════════════════
async def answer_retriever_node(state: ApplicationFormState) -> dict:
    """For each question, pull the relevant context from the right source.

    Per roadmap §5 routing:
      behavioral     → search_story_bank_v2 (pgvector)
      why_this_*     → search_company_knowledge_v2 (pgvector)
      salary         → agents/comp_research.get_comp_band + suggest_salary_strategy
      basic_info /   → profile (yaml) — no LLM
      portfolio_url
      dei_values     → company_persona.success_patterns + dei prefs
      technical /    → cv.md (passed inline to generator) — no retrieval
      custom         → cv.md only

    Reads:  questions, application_row, job_row, company_persona, profile, cv_md
    Writes: questions[i].retrieved_context, questions[i].cites, retrieval_summary
    Cost:   $0.00 base; small embedding cost per RPC call on cache miss.
    """
    from db.client import search_company_knowledge, search_story_bank

    questions: list[dict] = state.get("questions") or []
    app = state.get("application_row") or {}
    persona = state.get("company_persona") or {}
    profile = state.get("profile") or {}
    job_row = state.get("job_row") or {}
    company_name = app.get("company") or job_row.get("company") or ""
    role = app.get("role") or job_row.get("role") or ""

    retrieval_summary: dict[str, int] = {}

    for q in questions:
        cat = q.get("classification") or "custom"
        label = q.get("field_label") or ""
        retrieved: list[dict] = []
        cites: list[dict] = []

        try:
            if cat == "behavioral":
                stories = await search_story_bank(label, match_count=3)
                for s in stories or []:
                    sid = str(s.get("id") or "")
                    if not sid:
                        continue
                    retrieved.append({
                        "kind": "story_bank",
                        "id": sid,
                        "title": s.get("title"),
                        "content": (s.get("source_text") or s.get("content") or "")[:1500],
                        "similarity": s.get("similarity"),
                    })
                    cites.append({"kind": "story_bank", "id": sid})

            elif cat in ("why_this_company", "why_this_role"):
                if company_name:
                    chunks = await search_company_knowledge(
                        company_name=company_name,
                        query=f"{company_name} {role} {label}",
                        match_count=5,
                    )
                    for c in chunks or []:
                        cid = str(c.get("id") or "")
                        if not cid:
                            continue
                        retrieved.append({
                            "kind": "company_knowledge",
                            "id": cid,
                            "section": c.get("section"),
                            "content": (c.get("content") or "")[:1200],
                            "similarity": c.get("similarity"),
                        })
                        cites.append({"kind": "company_knowledge", "id": cid})

            elif cat == "salary":
                try:
                    from agents.comp_research import (
                        band_to_dict,
                        get_comp_band,
                        strategy_to_dict,
                        suggest_salary_strategy,
                    )
                    band = await get_comp_band(
                        company=company_name,
                        role=role,
                        location=(app.get("location") or job_row.get("location") or "") or None,
                        user_id=state["user_id"],
                    )
                    strategy = suggest_salary_strategy(band)
                    retrieved.append({
                        "kind": "comp_band",
                        "id": (getattr(band, "cache_id", None) or "comp"),
                        "content": json.dumps({
                            "band": band_to_dict(band),
                            "strategy": strategy_to_dict(strategy),
                        })[:1500],
                    })
                    if getattr(band, "cache_id", None):
                        cites.append({"kind": "comp_cache", "id": str(band.cache_id)})
                except Exception as e:
                    logger.warning("G7 comp_research fetch failed: %s", e)

            elif cat in ("basic_info", "portfolio_url"):
                # Pure profile lookup. Map field name → profile key via the
                # YAML pattern's profile_field_map. Stash the value as
                # retrieved_context so the generator can copy-through.
                retrieved.append({
                    "kind": "profile",
                    "id": "profile",
                    "content": json.dumps({k: profile.get(k) for k in (
                        "full_name", "email", "phone", "location",
                        "linkedin_url", "github_url", "website_url",
                    ) if profile.get(k)})[:600],
                })

            elif cat == "dei_values":
                if persona:
                    success = (persona.get("success_patterns") or [])[:5]
                    if success:
                        retrieved.append({
                            "kind": "company_persona",
                            "id": str(persona.get("id") or ""),
                            "content": "\n".join(f"- {s}" for s in success)[:1500],
                        })
                        if persona.get("id"):
                            cites.append({
                                "kind": "company_persona",
                                "id": str(persona.get("id")),
                            })

            elif cat == "cover_letter":
                # Use the most recent resume_build cover_email if available.
                # The retrieval is performed lazily by g7_io; here we just
                # carry forward whatever the orchestrator pre-loaded.
                cover_md = state.get("cv_md") or ""  # fallback
                pre_cover = (state.get("application_row") or {}).get("cover_email_md")
                content = (pre_cover or cover_md or "")[:3000]
                if content:
                    retrieved.append({
                        "kind": "resume_build",
                        "id": "cover_email",
                        "content": content,
                    })
            # technical / custom → nothing to retrieve; generator uses cv_md only

        except Exception as e:
            logger.warning("G7 retrieval failed for idx=%s cat=%s: %s",
                           q.get("idx"), cat, e)

        q["retrieved_context"] = retrieved
        q["cites"] = cites
        retrieval_summary[cat] = retrieval_summary.get(cat, 0) + len(retrieved)

    return {
        "questions": questions,
        "retrieval_summary": retrieval_summary,
        "transcript": [make_g7_turn(
            node="answer_retriever",
            input_summary=f"company={company_name} role={role} questions={len(questions)}",
            output=retrieval_summary,
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 4a — answer_generator (Sonnet 4.6, batched, prompt-cached)
# ══════════════════════════════════════════════════════════════════════════
ANSWER_GENERATOR_SYSTEM = """You write application-form answers for a job
seeker. You receive a list of questions (with their retrieved context) and
return ONE answer per question. Strict JSON only.

────────────────────────────────────────────────────────────────────────
RULES PER CATEGORY:
────────────────────────────────────────────────────────────────────────
basic_info        — Copy the value verbatim from the profile context.
                    If the field is "first name", extract the first space-
                    separated token of profile.full_name. Same for last name.
cover_letter      — Use the resume_build cover_email_md context if present.
                    Otherwise generate a 3-paragraph letter grounded in cv.md.
why_this_company  — Cite ONE company_knowledge chunk. Sentence-2 must say
                    something concrete from the news (cite:company_knowledge_id=
                    <uuid>). No generic enthusiasm.
why_this_role     — Tie 1-2 cv.md highlights to the JD. Concrete.
salary            — Use the comp_band + strategy from context VERBATIM.
                    NEVER invent numbers. If strategy.approach='range',
                    output "$<low> – $<high>". If 'anchor', output
                    "$<anchor>". If 'decline', output the framing text.
behavioral        — STAR format using the SINGLE strongest story from
                    retrieved_context. Format: Situation (1 sentence),
                    Task (1 sentence), Action (2-3 sentences), Result
                    (1 sentence with the metric from story.source_text).
                    Add cite:story_bank_id=<uuid> at the end.
technical         — Pull specific examples from cv.md. Use phrases the
                    candidate has actually used. No hype words ("guru",
                    "ninja", "obsessed").
dei_values        — Personal + concrete. NEVER use boilerplate
                    ("diversity is critical to me"). One specific story.
portfolio_url     — Copy from profile context.
custom            — Pull from cv.md. Be specific.

────────────────────────────────────────────────────────────────────────
GLOBAL HARD RULES:
────────────────────────────────────────────────────────────────────────
- NEVER fabricate quantified metrics. Every $N, $NM, $NB, Nx, N%, "team
  of N" claim MUST appear verbatim in the retrieved_context (story_bank
  source_text or cv.md). If you can't find the metric in context, write
  the qualitative claim only.
- NEVER use banned company-persona keywords. They will be supplied per
  request as BANNED_KEYWORDS.
- Match length to field_type. textarea → can be longer; text → ≤2 sentences.
- If the field has options (select/radio), the answer MUST be one of the
  options. Otherwise the form_filler will fail.

────────────────────────────────────────────────────────────────────────
OUTPUT FORMAT (strict JSON only):
────────────────────────────────────────────────────────────────────────
{
  "answers": [
    {"idx": 0, "field_id": "...", "answer": "...", "cites": [{"kind":"story_bank","id":"uuid"}]},
    ...
  ]
}"""


async def answer_generator_node(state: ApplicationFormState) -> dict:
    """Batch-generate answers for all classified questions.

    Reads:  questions, cv_md, profile, company_persona, merge_outcome (on retry)
    Writes: questions[i].generated_answer, generation_iteration, generation_attempts
    Cost:   ~$0.15/form (Sonnet 4.6; system cached; one batched call).
    """
    questions: list[dict] = state.get("questions") or []
    if not questions:
        return {}

    cv_md = state.get("cv_md") or ""
    persona = state.get("company_persona") or {}
    banned = _persona_banned_keywords(persona)
    merge_outcome = state.get("merge_outcome") or {}
    iteration = int(state.get("generation_iteration") or 0)
    attempts = int(state.get("generation_attempts") or 0) + 1

    # Build the user-side request. Include cv.md as a shared block so the
    # generator can reach into experience for technical / custom questions.
    items_block: list[str] = []
    for q in questions:
        items_block.append(json.dumps({
            "idx": q["idx"],
            "field_id": q.get("field_id"),
            "field_label": q.get("field_label"),
            "field_type": q.get("field_type"),
            "required": q.get("required"),
            "options": q.get("options"),
            "classification": q.get("classification"),
            "retrieved_context": q.get("retrieved_context") or [],
        }, ensure_ascii=False))

    critique_block = ""
    if iteration > 0 and merge_outcome:
        critique_block = (
            "\n\nPRIOR CRITIQUE — the persona_critic flagged these issues "
            "on the last pass. Fix them:\n"
            + json.dumps(merge_outcome, ensure_ascii=False)[:1500]
        )

    banned_block = ""
    if banned:
        banned_block = "\n\nBANNED_KEYWORDS (do NOT use these): " + ", ".join(banned[:25])

    user = f"""CV (full background context):
\"\"\"{cv_md[:8000]}\"\"\"{banned_block}{critique_block}

QUESTIONS (one JSON line per item):
{chr(10).join(items_block)}

Generate the answers per the system prompt rules. Strict JSON output."""

    settings = get_settings()
    model = getattr(settings, "g7_generator_model", _G7_MODEL)
    result = await get_router().ask(
        provider="anthropic",
        model=model,
        system=ANSWER_GENERATOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=4_000,
        temperature=0.3,
        agent_name="g7.answer_generator",
        json_response=True,
        cache_system=True,
    )

    try:
        parsed = _parse_json_loose(result.text) or {}
        answers = parsed.get("answers") or []
    except Exception:
        answers = []

    by_idx = {int(a.get("idx", -1)): a for a in answers if isinstance(a, dict)}
    for q in questions:
        a = by_idx.get(int(q["idx"]))
        if a:
            q["generated_answer"] = (a.get("answer") or "").strip()
            # Merge cites from the model with the retrieval-side cites.
            extra_cites = a.get("cites") or []
            existing = q.get("cites") or []
            seen = {(c.get("kind"), c.get("id")) for c in existing}
            for c in extra_cites:
                key = (c.get("kind"), c.get("id"))
                if key not in seen and c.get("kind") and c.get("id"):
                    existing.append(c)
                    seen.add(key)
            q["cites"] = existing
        else:
            # Model missed the field — record an empty draft so the HITL
            # surface flags it for manual entry.
            q["generated_answer"] = q.get("generated_answer") or ""

    return {
        "questions": questions,
        "generation_iteration": iteration,
        "generation_attempts": attempts,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [make_g7_turn(
            node="answer_generator",
            iteration=iteration,
            provider=result.provider,
            model=result.model,
            input_summary=f"questions={len(questions)} banned={len(banned)}",
            output={"answer_count": len(answers), "attempts": attempts},
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 4b — persona_critic (Sonnet 4.6, parallel sibling)
# ══════════════════════════════════════════════════════════════════════════
PERSONA_CRITIC_SYSTEM = """You are a senior hiring manager at ONE specific
company. You receive the candidate's drafted answers to an application
form and score them against three independent axes:

1. FABRICATED-METRICS CHECK (HARD FAIL): for every quantified metric in
   an answer ($N, $NM, $NB, Nx, N%, "team of N"), verify the metric
   APPEARS VERBATIM in the question's retrieved_context (story_bank
   source_text or cv.md). If any metric can't be traced, flag it.

2. BANNED-KEYWORD CHECK: when the company supplies banned_keywords, the
   answers must not include them.

3. REQUIRED-KEYWORD CHECK: when the company supplies required_keywords
   (success_patterns), at least one cover_letter / why_this_* answer
   should reference them.

Per-answer verdict: 'accept' | 'revise'. Persona-level summary at the
top.

Output strict JSON only:
{
  "summary": {
    "fabricated_metric_count": <int>,
    "banned_keyword_hits": ["..."],
    "required_keyword_misses": ["..."],
    "overall_verdict": "accept" | "revise"
  },
  "per_question": [
    {"idx": 0, "verdict": "accept|revise",
     "fabricated_metrics": ["..."],
     "banned_keywords_in_answer": ["..."],
     "fix": "<one sentence or null>"}
  ]
}"""


def _scan_fabricated_metrics(answer: str, retrieved_context: list[dict], cv_md: str) -> list[str]:
    """Pure-code metric tracing. For each $N/Nx/N%/team-of-N hit in the
    answer, search the retrieved_context content blobs + cv_md for the
    same number. Returns the list of UNMATCHED metrics (i.e. likely
    fabrications)."""
    if not answer:
        return []
    haystack = (cv_md or "") + "\n" + "\n".join(
        (c.get("content") or "") for c in (retrieved_context or [])
    )
    haystack_norm = re.sub(r"\s+", "", haystack.lower())
    fabricated: list[str] = []
    for m in _QUANT_METRIC_RE.finditer(answer):
        metric = next((g for g in m.groups() if g), "").strip()
        if not metric:
            continue
        metric_norm = re.sub(r"\s+", "", metric.lower())
        # Allow generic patterns like "team" without numbers to pass
        if metric_norm and metric_norm not in haystack_norm:
            fabricated.append(metric)
    return fabricated


async def persona_critic_node(state: ApplicationFormState) -> dict:
    """Per-question persona critique (parallel sibling to answer_generator).

    Reads:  questions (post-generator), company_persona, cv_md
    Writes: questions[i].persona_critique, persona_critique_summary
    Cost:   ~$0.05/form (single Sonnet call, JSON output).
    """
    questions: list[dict] = state.get("questions") or []
    if not questions:
        return {"persona_critique_summary": {"skipped": True}}

    persona = state.get("company_persona") or {}
    banned = _persona_banned_keywords(persona)
    required = list(persona.get("success_patterns") or [])[:8] if persona else []
    cv_md = state.get("cv_md") or ""

    # Pre-scan: pure-code fabrication trace + banned-keyword scan.
    prescan_fabricated: dict[int, list[str]] = {}
    prescan_banned: dict[int, list[str]] = {}
    for q in questions:
        ans = q.get("generated_answer") or ""
        prescan_fabricated[int(q["idx"])] = _scan_fabricated_metrics(
            ans, q.get("retrieved_context") or [], cv_md,
        )
        if banned:
            low = ans.lower()
            prescan_banned[int(q["idx"])] = [b for b in banned if b in low]
        else:
            prescan_banned[int(q["idx"])] = []

    items: list[str] = []
    for q in questions:
        items.append(json.dumps({
            "idx": q["idx"],
            "classification": q.get("classification"),
            "field_label": q.get("field_label"),
            "answer": (q.get("generated_answer") or "")[:2000],
            "prescan_fabricated": prescan_fabricated[int(q["idx"])],
            "prescan_banned_hits": prescan_banned[int(q["idx"])],
        }, ensure_ascii=False))

    required_block = ""
    if required:
        required_block = "\nREQUIRED_KEYWORDS: " + ", ".join(required[:10])
    banned_block = ""
    if banned:
        banned_block = "\nBANNED_KEYWORDS: " + ", ".join(banned[:25])

    user = f"""COMPANY: {(state.get('application_row') or {}).get('company')}{required_block}{banned_block}

ANSWERS (one JSON line per item; prescan_fabricated + prescan_banned_hits
are pure-code results — your LLM-side verdict MUST treat any non-empty
prescan_fabricated as a hard revise):
{chr(10).join(items)}

Score per the system prompt. Strict JSON only."""

    settings = get_settings()
    model = getattr(settings, "g7_persona_critic_model", _G7_MODEL)
    result = await get_router().ask(
        provider="anthropic",
        model=model,
        system=PERSONA_CRITIC_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=2_000,
        temperature=0.15,
        agent_name="g7.persona_critic",
        json_response=True,
        cache_system=True,
    )

    try:
        parsed = _parse_json_loose(result.text) or {}
    except Exception:
        parsed = {}
    summary = parsed.get("summary") or {}
    per_q = parsed.get("per_question") or []
    per_q_by_idx = {int(p.get("idx", -1)): p for p in per_q if isinstance(p, dict)}

    fab_total = 0
    revised: list[int] = []
    for q in questions:
        idx = int(q["idx"])
        crit = per_q_by_idx.get(idx) or {}
        # Pure-code override: prescan fabrications + banned hits ALWAYS
        # take precedence over what the LLM said.
        fabricated = sorted(set((crit.get("fabricated_metrics") or []) + prescan_fabricated[idx]))
        banned_hits = sorted(set((crit.get("banned_keywords_in_answer") or []) + prescan_banned[idx]))
        verdict = crit.get("verdict") or "accept"
        if fabricated or banned_hits:
            verdict = "revise"
        q["persona_critique"] = {
            "verdict": verdict,
            "fabricated_metrics": fabricated,
            "banned_keywords_in_answer": banned_hits,
            "fix": crit.get("fix"),
        }
        fab_total += len(fabricated)
        if verdict == "revise":
            revised.append(idx)

    # Persona-level summary, with our prescan totals taking precedence.
    summary_out = {
        "fabricated_metric_count": fab_total,
        "banned_keyword_hits": sorted({b for blist in prescan_banned.values() for b in blist}),
        "required_keyword_misses": summary.get("required_keyword_misses") or [],
        "revise_idxs": revised,
        "overall_verdict": "revise" if revised else "accept",
    }

    return {
        "questions": questions,
        "persona_critique_summary": summary_out,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [make_g7_turn(
            node="persona_critic",
            provider=result.provider,
            model=result.model,
            input_summary=f"questions={len(questions)} banned={len(banned)} required={len(required)}",
            output=summary_out,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 5 — merge_critique (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def merge_critique_node(state: ApplicationFormState) -> dict:
    """Decide: do we loop back for another generator pass, or proceed to HITL?

    Reads:  persona_critique_summary, generation_attempts
    Writes: merge_outcome, needs_generator_retry, generation_iteration
    Cost:   $0.00.
    """
    summary = state.get("persona_critique_summary") or {}
    attempts = int(state.get("generation_attempts") or 0)
    iteration = int(state.get("generation_iteration") or 0)
    revise_idxs = list(summary.get("revise_idxs") or [])
    retries_left = (_MAX_GENERATOR_RETRIES + 1) - attempts
    needs_retry = bool(revise_idxs) and retries_left > 0

    merge = {
        "revise_idxs": revise_idxs,
        "fabricated_metric_count": int(summary.get("fabricated_metric_count") or 0),
        "banned_keyword_hits": list(summary.get("banned_keyword_hits") or []),
        "needs_retry": needs_retry,
        "attempts_used": attempts,
        "retries_left": retries_left,
        "reason": (
            "persona flagged fabricated metrics / banned keywords; "
            f"retrying generator (attempt {attempts+1}/{_MAX_GENERATOR_RETRIES+1})"
            if needs_retry
            else ("accepted by persona" if not revise_idxs else
                  "max retries reached — proceeding with caveats")
        ),
    }

    patch: dict[str, Any] = {
        "merge_outcome": merge,
        "needs_generator_retry": needs_retry,
        "transcript": [make_g7_turn(
            node="merge_critique",
            iteration=iteration,
            input_summary=f"attempts={attempts} revise={len(revise_idxs)}",
            output=merge,
        )],
    }
    if needs_retry:
        patch["generation_iteration"] = iteration + 1
    return patch


# ══════════════════════════════════════════════════════════════════════════
# Node 6 — human_review (interrupt — the HITL piece)
# ══════════════════════════════════════════════════════════════════════════
async def human_review_node(state: ApplicationFormState) -> dict:
    """Pause the graph for user approval.

    This is THE feature. LangGraph's `interrupt()` saves state to the
    Postgres checkpointer + returns control to the caller. The dashboard
    reads `status='awaiting_review'`, shows drafts, lets the user edit,
    and POSTs /approve. The /approve endpoint resumes the graph by
    passing the approvals as the resume value of interrupt() — see
    api/g7.py.

    On resume, this node merges the user_approvals payload into
    questions[i].approved / questions[i].user_edited and continues to
    form_filler.

    Reads:  questions, application_answer_id
    Writes: review_started_at, review_resumed_at, questions[i].approved,
            questions[i].user_edited
    Cost:   $0.00 (the pause itself is free; the resume call costs $0).
    """
    from langgraph.types import interrupt

    questions: list[dict] = state.get("questions") or []
    review_started_at = datetime.now(timezone.utc).isoformat()

    # Persist the awaiting_review state BEFORE the interrupt so the dashboard
    # sees the row. The checkpointer also persists it, but row-level status
    # is what the dashboard list query filters on.
    from agents.g7_io import update_status_and_questions
    try:
        update_status_and_questions(
            application_answer_id=state["application_answer_id"],
            status="awaiting_review",
            questions=questions,
            user_id=state["user_id"],
        )
    except Exception as e:
        logger.warning("G7 awaiting_review persistence failed: %s", e)

    # ── THE PAUSE ──────────────────────────────────────────────────────
    # The interrupt() call here suspends the graph and returns control to
    # the caller (api/g7.py). When the user POSTs /approve, the API
    # resumes via graph.invoke(Command(resume=approvals), config=...).
    # The `approvals` payload arrives here as `user_payload`.
    user_payload = interrupt({
        "status": "awaiting_review",
        "application_answer_id": state["application_answer_id"],
        "question_count": len(questions),
        "estimated_form_fill_time_seconds": len(questions) * 3,
    })

    # ── RESUME ─────────────────────────────────────────────────────────
    user_approvals: list[dict] = (user_payload or {}).get("approvals") or []
    by_idx = {int(a.get("idx", -1)): a for a in user_approvals if isinstance(a, dict)}
    for q in questions:
        a = by_idx.get(int(q["idx"]))
        if a is None:
            # User didn't approve this one — leave it unapproved. form_filler
            # will skip it (and the dashboard's "approve all" CTA always
            # sends per-field rows, so this branch is the partial-approve
            # case).
            continue
        q["approved"] = bool(a.get("approved"))
        edit = a.get("user_edited")
        if edit is not None:
            q["user_edited"] = str(edit)

    return {
        "questions": questions,
        "user_approvals": user_approvals,
        "review_started_at": review_started_at,
        "review_resumed_at": datetime.now(timezone.utc).isoformat(),
        "transcript": [make_g7_turn(
            node="human_review",
            input_summary=f"questions={len(questions)}",
            output={
                "approved_count": sum(1 for q in questions if q.get("approved")),
                "edited_count": sum(1 for q in questions if q.get("user_edited") is not None),
            },
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 7 — form_filler (Playwright, code)
# ══════════════════════════════════════════════════════════════════════════
async def form_filler_node(state: ApplicationFormState) -> dict:
    """Drive Playwright to fill approved answers, stop BEFORE submit.

    Tests monkey-patch agents.form_scanner.fill_form to avoid actually
    driving a browser. Production runs headless=False so the user can
    eyeball the loaded form, fix anything weird, and click Submit
    themselves.

    Reads:  questions (post-approval), form_url, ats_type
    Writes: questions[i].ats_filled_ok, filler_summary, stopped_before_submit
    Cost:   $0.00.
    """
    from agents.form_scanner import fill_form

    questions: list[dict] = state.get("questions") or []
    approved = [q for q in questions if q.get("approved")]
    if not approved:
        return {
            "filler_summary": {"filled": 0, "failed": 0, "skipped": True},
            "stopped_before_submit": True,
            "transcript": [make_g7_turn(
                node="form_filler",
                input_summary="no approved answers — skipping fill",
                output={"skipped": True},
            )],
        }

    answers_payload: list[dict] = []
    for q in approved:
        final = q.get("user_edited") if q.get("user_edited") is not None else q.get("generated_answer")
        answers_payload.append({
            "idx": q["idx"],
            "field_id": q.get("field_id"),
            "field_type": q.get("field_type"),
            "options": q.get("options"),
            "final_answer": final,
        })

    try:
        summary = await fill_form(
            state.get("form_url") or "",
            answers_payload,
            ats_type=state.get("ats_type") or "greenhouse",
            headless=False,
            skip_submit=True,
        )
    except Exception as e:
        logger.exception("G7 form_filler failed")
        return {
            "filler_summary": {
                "filled": 0, "failed": len(approved),
                "errors": [{"error": f"{type(e).__name__}:{str(e)[:200]}"}],
            },
            "stopped_before_submit": True,
            "transcript": [make_g7_turn(
                node="form_filler",
                input_summary=f"questions={len(approved)} url={state.get('form_url')!r}",
                output={"error": str(e)[:200]},
                error=str(e)[:500],
            )],
        }

    error_by_idx = {int(e.get("idx", -1)): e for e in (summary.get("errors") or [])}
    for q in approved:
        idx = int(q["idx"])
        if idx in error_by_idx:
            q["ats_filled_ok"] = False
            q["fill_error"] = error_by_idx[idx].get("error")
        else:
            q["ats_filled_ok"] = True
            q["fill_error"] = None

    return {
        "questions": questions,
        "filler_summary": summary,
        "stopped_before_submit": bool(summary.get("stopped_before_submit", True)),
        "transcript": [make_g7_turn(
            node="form_filler",
            input_summary=f"approved={len(approved)} url={state.get('form_url')!r}",
            output={
                "filled": summary.get("filled"),
                "failed": summary.get("failed"),
                "stopped_before_submit": summary.get("stopped_before_submit"),
            },
        )],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 8 — persist (pure code; final step + identity-lock scan + auto-enrol G6)
# ══════════════════════════════════════════════════════════════════════════
async def persist_node(state: ApplicationFormState) -> dict:
    """Final persistence: identity-lock scan, write the row, auto-enrol G6.

    Reads:  application_answer_id, user_id, application_id, questions,
            form_url, ats_type, cost_usd_total, cv_md
    Writes: status='filled', cadence_row_id, identity_lock_redactions
    Cost:   $0.00.
    """
    from agents.g7_io import (
        finalize_application_answers,
        ensure_g6_enrolment,
    )

    questions: list[dict] = state.get("questions") or []
    cv_md = state.get("cv_md") or ""

    # ── Identity lock: rescan EVERY final answer for $-metrics that don't
    # appear in retrieved_context or cv_md. Redact any that aren't grounded.
    # This is the structural enforcement layer per engineering principles
    # §11.3 — the LLM may have lied, the persona critic may have missed
    # one, but this last gate is pure regex + substring search.
    redactions: list[dict] = []
    for q in questions:
        final = q.get("user_edited") if q.get("user_edited") is not None else q.get("generated_answer")
        if not final:
            continue
        fabs = _scan_fabricated_metrics(final, q.get("retrieved_context") or [], cv_md)
        if fabs:
            for f in fabs:
                redactions.append({"idx": q["idx"], "metric": f, "reason": "no_source_match"})
                # Redact in place. We replace the metric with "[redacted]"
                # rather than dropping the answer entirely so the user can
                # still salvage what's there.
                target_field = "user_edited" if q.get("user_edited") is not None else "generated_answer"
                q[target_field] = q[target_field].replace(f, "[redacted]")
            logger.warning(
                "G7 identity-lock: redacted %s on idx=%s (no source match)",
                fabs, q.get("idx"),
            )

    # Persist final state with status='filled' and the redactions log.
    try:
        finalize_application_answers(
            application_answer_id=state["application_answer_id"],
            user_id=state["user_id"],
            questions=questions,
            total_cost_usd=float(state.get("cost_usd_total") or 0.0),
            status="filled",
        )
    except Exception as e:
        logger.exception("G7 persist failed for %s", state.get("application_answer_id"))
        return {
            "error": f"persist_failed:{type(e).__name__}:{str(e)[:200]}",
            "transcript": [make_g7_turn(
                node="persist",
                output={"error": str(e)[:200]},
                error=str(e)[:500],
            )],
        }

    # ── Auto-enrol in G6 follow-up cadence (one row, status='applied',
    # next_follow_up_date NOW()+7d). Best-effort: any failure here is
    # logged but does NOT bubble back — G6 enrolment can be re-tried by
    # the cron later, but the form-fill itself is already successful.
    cadence_row_id: Optional[str] = None
    try:
        cadence_row_id = ensure_g6_enrolment(
            user_id=state["user_id"],
            application_id=state["application_id"],
        )
    except Exception as e:
        logger.warning("G7 G6 enrolment failed (will retry via cron): %s", e)

    return {
        "questions": questions,
        "identity_lock_redactions": redactions,
        "cadence_row_id": cadence_row_id,
        "transcript": [make_g7_turn(
            node="persist",
            input_summary=f"application_answer_id={state.get('application_answer_id')} questions={len(questions)}",
            output={
                "redaction_count": len(redactions),
                "cadence_row_id": cadence_row_id,
                "status": "filled",
            },
        )],
    }
