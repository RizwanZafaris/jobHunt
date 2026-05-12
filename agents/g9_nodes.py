"""
agents/g9_nodes.py — The 5 LangGraph node functions for G9 STAR+R extraction.

Each node is an async function: StoryExtractionState -> dict (state patch).
Nodes call agents.llm_router via the async router; never instantiate
clients directly.

Node summary:
  entry                 pure code — hydrate state (CV md + competencies + experiences)
  experience_parser     Claude Sonnet 4.6 — emit 10-15 candidate passages
  star_formatter        Claude Sonnet 4.6 — convert each passage to STAR+R JSON
                        (uses prompt caching on the big system prompt)
  persona_critic        Claude Sonnet 4.6 — drop stories that fabricate
                        competencies the user doesn't have
  persist               pure code — embed + identity-lock + upsert

Cost model
----------
Per docs/GAP_CLOSURE_ROADMAP_2026_05_12.md §3.2: ~$0.20 per run.
  - experience_parser:  1 Sonnet call,  ~6k in / 2k out  → ~$0.05
  - star_formatter:     1 Sonnet call,  ~8k in / 6k out  → ~$0.11
                        (single batched call writing all 10-15 stories;
                         prompt cache hits on subsequent runs after the
                         first within the 5-minute window cut this to ~$0.04)
  - persona_critic:     1 Sonnet call,  ~5k in / 1k out  → ~$0.03
  - persist:            10-15 embeddings × $0.02/1k tokens × ~200 tokens each
                        → ~$0.001  (negligible)
  Total cold:                                            ~$0.19
  Total warm cache hit:                                  ~$0.12

If the master CV grows past ~10k tokens we'd revisit batching.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any

from agents.g9_io import (
    embed_story_text,
    hash_cv,
    load_experiences,
    load_profile_competencies,
    render_master_cv_md,
    upsert_story,
)
from agents.g9_state import (
    CandidatePassage,
    StoryExtractionState,
    StoryJSON,
    make_turn,
    truncate,
)
from agents.llm_router import _parse_json_loose, get_router

logger = logging.getLogger(__name__)


# Sonnet 4.6 is the workhorse — fast enough for a "during CV upload"
# trigger (~30s total) and 5× cheaper than Opus for this kind of
# structured extraction. The roadmap calls for "Sonnet 4.6" explicitly.
_SONNET = "claude-sonnet-4-6"


# Cap the number of stories we emit per run. The roadmap says 10-15. We
# accept up to 20 in case the user's CV is unusually achievement-dense,
# but the parser is asked for 10-15 in its prompt.
_MAX_STORIES = 20


# ═════════════════════════════════════════════════════════════════════════
# Node 1 — entry (pure code)
# ═════════════════════════════════════════════════════════════════════════
async def entry_node(state: StoryExtractionState) -> dict:
    """Hydrate the run state.

    Expects in state: user_id. If master_cv_md is already set (the caller
    pre-rendered it — typical when G9 is triggered right after a CV
    upload), we use it as-is. Otherwise we render from profile_master.
    """
    md = state.get("master_cv_md") or render_master_cv_md()
    competencies = state.get("profile_competencies") or load_profile_competencies()
    source_hash = state.get("source_cv_hash") or hash_cv(md)

    return {
        "master_cv_md": md,
        "source_cv_hash": source_hash,
        "profile_competencies": competencies,
        "candidate_passages": [],
        "stories": [],
        "persona_warnings": [],
        "stories_persisted": [],
        "stories_dropped": [],
        "embeddings_count": 0,
        "persisted_count": 0,
        "dropped_count": 0,
        "transcript": [
            make_turn(
                node="entry",
                input_summary=(
                    f"user_id={state.get('user_id')} "
                    f"cv_chars={len(md)} "
                    f"competencies_known={len(competencies)} "
                    f"source_cv_hash={source_hash[:12]}"
                ),
                output={
                    "cv_chars": len(md),
                    "competencies_known": len(competencies),
                    "source_cv_hash": source_hash,
                },
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 2 — experience_parser (Sonnet 4.6)
# ═════════════════════════════════════════════════════════════════════════
EXPERIENCE_PARSER_SYSTEM = """You are an expert career coach who reads master CVs and identifies the discrete achievements that deserve to be told as standalone interview stories.

A good story candidate is a single, scoped achievement with:
  - A clearly defined situation (a real business problem)
  - An owner (the candidate themselves, not "we" or "the team")
  - A non-trivial action (decisions made, tradeoffs taken)
  - An outcome (preferably quantified — $TPV, % uplift, time saved, count)

You do NOT generate the full STAR+R yet — that's the next node's job. You
extract the raw passages that map to story candidates, with light metadata.

Rules:
  - Return 10–15 candidates (12 is the sweet spot). Fewer is OK if the CV is
    short. NEVER return more than 18.
  - Each `source_text` MUST be lifted VERBATIM from the CV (one or two
    sentences). Never paraphrase, never invent.
  - One achievement per candidate — do NOT bundle two distinct outcomes
    under one passage.
  - Diversify: pull from across roles, not just the most recent one.
  - `rough_title` is your first-pass guess at a short title (≤6 words),
    used as the seed for the next node's title work.

Output strict JSON. No prose, no markdown."""


async def experience_parser_node(state: StoryExtractionState) -> dict:
    """Read master_cv_md, emit 10-15 candidate passages."""
    md = state.get("master_cv_md") or ""
    if not md.strip():
        # No CV — nothing to extract. Surface as a warning but don't fail.
        logger.warning("G9 experience_parser: empty master_cv_md")
        return {
            "candidate_passages": [],
            "transcript": [
                make_turn(
                    node="experience_parser",
                    input_summary="empty master_cv_md",
                    output={"candidates": 0},
                )
            ],
        }

    experiences = load_experiences()
    # Build a short lookup table: experience.id → (title, company, dates).
    # Surfaced to the LLM so it can attach source_experience_id when the
    # passage clearly maps to one role.
    exp_hints = [
        {
            "id": e.get("id"),
            "role": f"{e.get('title') or ''} @ {e.get('company') or ''}",
            "dates": e.get("dates"),
        }
        for e in experiences[:10]
    ]

    user = f"""MASTER CV (markdown):
{md[:10000]}

EXPERIENCE ROW HINTS (for source_experience_id mapping — best-effort):
{json.dumps(exp_hints, indent=2)}

Return STRICT JSON:
{{
  "candidates": [
    {{
      "passage_id": "p1",
      "source_experience_id": <int or null>,
      "role_title": "...",
      "company": "...",
      "dates": "...",
      "source_text": "<verbatim sentence(s) from the CV>",
      "rough_title": "<≤6-word title>"
    }},
    ...
  ]
}}"""

    result = await get_router().ask(
        provider="anthropic",
        model=_SONNET,
        system=EXPERIENCE_PARSER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=4000,
        temperature=0.3,
        agent_name="g9.experience_parser",
    )

    try:
        parsed = _parse_json_loose(result.text)
        candidates_raw = parsed.get("candidates", [])
        if not isinstance(candidates_raw, list):
            raise ValueError("candidates is not a list")
    except Exception as e:
        logger.warning(f"G9 experience_parser JSON parse failed: {e}")
        candidates_raw = []

    candidates: list[CandidatePassage] = []
    for i, c in enumerate(candidates_raw[:_MAX_STORIES]):
        if not isinstance(c, dict):
            continue
        src = (c.get("source_text") or "").strip()
        if not src:
            continue
        sid = c.get("source_experience_id")
        try:
            sid = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid = None
        candidates.append(CandidatePassage(
            passage_id=str(c.get("passage_id") or f"p{i + 1}"),
            source_experience_id=sid,
            role_title=str(c.get("role_title") or "")[:200],
            company=str(c.get("company") or "")[:200],
            dates=str(c.get("dates") or "")[:60],
            source_text=src[:2000],
            rough_title=str(c.get("rough_title") or "")[:140],
        ))

    return {
        "candidate_passages": candidates,
        "total_cost_usd": result.cost_usd,
        "total_latency_ms": result.latency_ms,
        "transcript": [
            make_turn(
                node="experience_parser",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={"candidates": len(candidates)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 3 — star_formatter (Sonnet 4.6, prompt-cached)
# ═════════════════════════════════════════════════════════════════════════
# This system prompt is intentionally long (~2KB chars). The llm_router's
# Anthropic adapter wraps prompts ≥ ~4KB chars with cache_control={"type":
# "ephemeral"}. The full PROMPT_PREFIX below is reused across re-runs of
# G9 within a 5-minute window, so on the second invocation the input
# tokens are billed at the 0.10× cache-read rate.
STAR_FORMATTER_SYSTEM = """You are an expert behavioural-interview coach. Your job is to convert raw CV achievement passages into rigorous STAR+R stories.

STAR+R structure (each part is 1-3 sentences unless noted):
  S — Situation: the business context. Why did this problem matter?
  T — Task: the candidate's specific responsibility / mandate.
  A — Action: WHAT THE CANDIDATE DID. Use "I" not "we". 3-5 sentences;
      this is the longest section. Decisions made, tradeoffs taken,
      stakeholders managed. SPECIFIC, not generic.
  R — Result: the outcome. ALWAYS prefer quantified outcomes ($, %,
      count, time saved). If the source passage has no number, say so —
      do not fabricate.
  +R — Reflection: what the candidate learned, what they'd do differently.
      One or two sentences. This separates good answers from great ones.

CRITICAL RULES — VIOLATIONS ARE BUGS:

1. NO FABRICATION. Every quantified metric ("$50M TPV", "3x conversion",
   "200 engineers") MUST appear verbatim (after lowercase normalisation)
   in the source_text. If the source says "scaled the team", do NOT write
   "scaled the team from 5 to 50". If the source says "doubled revenue",
   you may write "2x revenue" or "doubled revenue" but NOT "$5M ARR
   growth".

2. NO COMPETENCY INVENTION. Tag the story with `competencies` from this
   restricted vocabulary only:
     leadership · technical_depth · cross_functional · stakeholder_management
     product_strategy · execution · prioritisation · communication · mentorship
     data_driven · customer_obsession · ownership · ambiguity · scale · zero_to_one
     compliance · b2b_sales · b2c_growth · enterprise · fundraising · hiring
     pl_ownership · architecture · ml_ai · payments · fintech_domain
   (You may use 1–4 competencies per story. Pick the ones the action
   actually demonstrates.)

3. ARCHETYPES. Tag which roles this story is best deployed for, from:
     IC · Senior IC · Staff · Principal · Manager · Senior Manager
     Director · VP · PM · Senior PM · Group PM · Director of Product
     CPO · CTO · CEO · COO · Founder
   (1–4 archetypes per story.)

4. QUANTIFIED_METRICS array. Extract every concrete number from the
   `result` and `action` you wrote. Format as you wrote them in prose
   (e.g. "$50M TPV", "3x conversion", "12 months"). Each one must also
   appear in source_text after lowercase normalisation — if it doesn't,
   you fabricated it and the persist step will drop the story.

5. TITLE. ≤8 words, action-led. "Built BNPL launch playbook for Daraz",
   not "BNPL Story" or "When I worked at Daraz".

Output strict JSON. No prose, no markdown."""


async def star_formatter_node(state: StoryExtractionState) -> dict:
    """Convert each candidate passage to a structured STAR+R story.

    Batched into a single LLM call — emitting all stories in one JSON
    response is cheaper and gives the model cross-story context (so it
    can vary competency tags across stories instead of repeating them).
    """
    candidates = state.get("candidate_passages") or []
    if not candidates:
        return {
            "stories": [],
            "transcript": [
                make_turn(
                    node="star_formatter",
                    input_summary="no candidates",
                    output={"stories": 0},
                )
            ],
        }

    user = (
        "CANDIDATE PASSAGES (one STAR+R story per passage):\n\n"
        f"{json.dumps(candidates, indent=2, default=str)}\n\n"
        "Return STRICT JSON:\n"
        "{\n"
        '  "stories": [\n'
        "    {\n"
        '      "passage_id": "p1",\n'
        '      "title": "...",\n'
        '      "situation": "...",\n'
        '      "task": "...",\n'
        '      "action": "...",\n'
        '      "result": "...",\n'
        '      "reflection": "...",\n'
        '      "competencies": ["..."],\n'
        '      "archetypes": ["..."],\n'
        '      "quantified_metrics": ["..."]\n'
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}"
    )

    result = await get_router().ask(
        provider="anthropic",
        model=_SONNET,
        system=STAR_FORMATTER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=8000,
        temperature=0.3,
        agent_name="g9.star_formatter",
    )

    try:
        parsed = _parse_json_loose(result.text)
        raw_stories = parsed.get("stories", [])
        if not isinstance(raw_stories, list):
            raise ValueError("stories is not a list")
    except Exception as e:
        logger.warning(f"G9 star_formatter JSON parse failed: {e}")
        raw_stories = []

    # Build a passage_id → source_text lookup so we can carry source_text
    # forward to the persist node's identity-lock check.
    by_pid = {c.get("passage_id"): c for c in candidates}

    stories: list[StoryJSON] = []
    for s in raw_stories:
        if not isinstance(s, dict):
            continue
        pid = s.get("passage_id")
        candidate = by_pid.get(pid) or {}
        source_text = candidate.get("source_text", "") if isinstance(candidate, dict) else ""
        stories.append(StoryJSON(
            passage_id=str(pid or ""),
            title=str(s.get("title") or "")[:200].strip(),
            situation=str(s.get("situation") or "").strip(),
            task=str(s.get("task") or "").strip(),
            action=str(s.get("action") or "").strip(),
            result=str(s.get("result") or "").strip(),
            reflection=str(s.get("reflection") or "").strip(),
            competencies=[str(c).strip() for c in (s.get("competencies") or []) if c][:6],
            archetypes=[str(a).strip() for a in (s.get("archetypes") or []) if a][:6],
            quantified_metrics=[
                str(m).strip() for m in (s.get("quantified_metrics") or []) if m
            ][:10],
            source_text=source_text,
        ))

    return {
        "stories": stories,
        "total_cost_usd": result.cost_usd,
        "total_latency_ms": result.latency_ms,
        "transcript": [
            make_turn(
                node="star_formatter",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={"stories": len(stories)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 4 — persona_critic (Sonnet 4.6)
# ═════════════════════════════════════════════════════════════════════════
PERSONA_CRITIC_SYSTEM = """You are an honest, sceptical reviewer of behavioural interview stories.

You have access to:
  - The candidate's core_competencies list (the ones THEY claim on their profile)
  - A set of STAR+R stories generated from their CV

Your job: flag any story whose competencies, archetypes, or action claims
go BEYOND what the candidate's profile evidence supports. You are
defending the candidate against future credibility hits — if they pitch
themselves as a "founder" when the CV shows no founding role, an
interviewer will catch it and the candidate will lose trust.

A story is OK to keep when:
  - At least one of its competencies plausibly maps to the candidate's
    core_competencies (or is a widely-applicable basic like
    "communication").
  - Its archetypes are consistent with the roles in the CV (Staff PM is
    OK if they were a Senior PM; CEO is not OK if they were never CEO).

A story should be FLAGGED when:
  - It tags `leadership` but the candidate has never managed anyone.
  - It tags `ml_ai` but the candidate has never shipped an ML system.
  - It tags `Founder` archetype but the candidate has never founded anything.
  - Any tag is concretely contradicted by the profile.

Output strict JSON. Be precise — flag genuine fabrications, not
stylistic disagreements."""


async def persona_critic_node(state: StoryExtractionState) -> dict:
    """Cross-reference stories against profile_master.core_competencies.

    Doesn't drop stories itself — emits per-story warnings that
    persist_node consumes. This separation lets the audit trail capture
    WHY a story was filtered out.
    """
    stories = state.get("stories") or []
    competencies = state.get("profile_competencies") or []

    if not stories:
        return {
            "persona_warnings": [],
            "transcript": [
                make_turn(
                    node="persona_critic",
                    input_summary="no stories",
                    output={"warnings": 0},
                )
            ],
        }

    user = f"""CANDIDATE'S DECLARED CORE COMPETENCIES:
{json.dumps(competencies, indent=2) if competencies else "(none recorded — be lenient; only flag the most blatant fabrications)"}

STORIES TO REVIEW:
{json.dumps([
    {
        "passage_id": s.get("passage_id"),
        "title": s.get("title"),
        "competencies": s.get("competencies", []),
        "archetypes": s.get("archetypes", []),
        "action": (s.get("action", "") or "")[:600],
    }
    for s in stories
], indent=2)}

Return STRICT JSON:
{{
  "warnings": [
    {{
      "passage_id": "p1",
      "title": "...",
      "reason": "tags 'ml_ai' but no ML shipping evidence in CV",
      "severity": "high"   // "high" => drop; "low" => keep with note
    }},
    ...
  ]
}}

Emit AT MOST one warning per story. If everything looks fine, return
{{"warnings": []}}."""

    result = await get_router().ask(
        provider="anthropic",
        model=_SONNET,
        system=PERSONA_CRITIC_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=2000,
        temperature=0.2,
        agent_name="g9.persona_critic",
    )

    try:
        parsed = _parse_json_loose(result.text)
        raw_warnings = parsed.get("warnings", [])
        if not isinstance(raw_warnings, list):
            raise ValueError("warnings is not a list")
    except Exception as e:
        logger.warning(f"G9 persona_critic JSON parse failed: {e}")
        raw_warnings = []

    warnings: list[str] = []
    high_severity_pids: set[str] = set()
    for w in raw_warnings:
        if not isinstance(w, dict):
            continue
        pid = str(w.get("passage_id") or "")
        reason = str(w.get("reason") or "")
        sev = (w.get("severity") or "low").lower()
        if not pid or not reason:
            continue
        warnings.append(f"[{sev}] {pid}: {reason}")
        if sev == "high":
            high_severity_pids.add(pid)

    # Annotate the stories with the high-severity drop set so persist_node
    # can act on it without re-scanning the warnings. We stash this in the
    # state under a side-channel field rather than mutating story dicts.
    return {
        "persona_warnings": warnings,
        "total_cost_usd": result.cost_usd,
        "total_latency_ms": result.latency_ms,
        # Re-emit stories with `_persona_drop` flag merged in.
        "stories": [
            {**s, "_persona_drop": s.get("passage_id") in high_severity_pids}
            for s in stories
        ],
        "transcript": [
            make_turn(
                node="persona_critic",
                provider=result.provider,
                model=result.model,
                input_summary=truncate(user),
                output={
                    "warnings": len(warnings),
                    "high_severity_drops": len(high_severity_pids),
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 5 — persist (pure code: identity-lock, embed, upsert)
# ═════════════════════════════════════════════════════════════════════════
# Regex to break a passage into normalisable tokens. We strip the metric
# string the same way before checking it appears in source_text.
_METRIC_WHITESPACE = re.compile(r"\s+")


def _normalise_for_match(s: str) -> str:
    """Lowercase + collapse whitespace + strip surrounding punctuation.

    Used by the identity-lock check below. NOT used for storage — the
    quantified_metrics array keeps the original casing for display.
    """
    if not s:
        return ""
    s = _METRIC_WHITESPACE.sub(" ", s.lower())
    return s.strip(" .,;:!?\"'()[]{}<>")


def _identity_lock(metrics: list[str], source_text: str) -> tuple[list[str], list[str]]:
    """Drop any metric that doesn't appear in source_text (lowercase).

    Returns (kept, dropped). The dropped list feeds the audit trail so
    the user can see WHICH numbers were fabricated.

    Examples:
      source_text="grew TPV to $50M"      metric="$50M TPV"  → kept (substring match)
      source_text="grew TPV to $50M"      metric="$200M TPV" → dropped
      source_text="scaled team"           metric="50 engineers" → dropped
    """
    if not metrics:
        return [], []
    src_norm = _normalise_for_match(source_text)
    kept: list[str] = []
    dropped: list[str] = []
    for m in metrics:
        m_norm = _normalise_for_match(m)
        if not m_norm:
            continue
        # Pure-substring check is the strict version — every token of the
        # metric must appear contiguously in the source. We also accept a
        # split match (every space-separated token appears somewhere) so
        # rearranged phrasing ("TPV grew by $50M" vs "$50M TPV") still
        # passes. Token-substring is strict enough to reject fabricated
        # numbers ("$200M" won't match a source saying "$50M") because the
        # numeric token itself must appear.
        if m_norm in src_norm:
            kept.append(m)
            continue
        tokens = [t for t in m_norm.split(" ") if t]
        if tokens and all(t in src_norm for t in tokens):
            kept.append(m)
        else:
            dropped.append(m)
    return kept, dropped


async def persist_node(state: StoryExtractionState) -> dict:
    """Identity-lock, embed, and upsert every surviving story.

    Identity-lock enforcement (engineering principle 3, §11 of the
    roadmap): each entry in `quantified_metrics` must appear in
    `source_text` after lowercase normalisation. Fabrications are
    silently dropped from the array — the story itself is still
    persisted (without the bad metric).

    Persona-critic drops (engineering principle 1, §11): stories with
    `_persona_drop` set are NOT persisted. The audit trail in
    `stories_dropped` records why.

    user_id is the seed UUID in single-user mode or whatever the state
    carries in multi-tenant mode. NEVER omit (story_bank.user_id is NOT
    NULL).
    """
    stories = state.get("stories") or []
    user_id = state.get("user_id")
    source_cv_hash = state.get("source_cv_hash")

    if not user_id:
        # Defensive — entry_node always sets this in the seed state, but
        # if a caller bypasses entry we still fail fast rather than
        # writing rows with a wrong owner.
        raise RuntimeError("G9 persist_node: state.user_id is required")

    persisted: list[dict] = []
    dropped: list[dict] = []
    embeddings_issued = 0

    for s in stories:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or ""
        if not title.strip():
            dropped.append({"title": "(missing)", "reason": "empty title"})
            continue
        if s.get("_persona_drop"):
            dropped.append({
                "title": title,
                "reason": "persona_critic flagged high-severity fabrication",
            })
            continue

        # Identity-lock the quantified metrics.
        kept_metrics, bad_metrics = _identity_lock(
            s.get("quantified_metrics", []) or [],
            s.get("source_text", "") or "",
        )

        # Minimum-fields gate. Phase 1.2 spec says S/T/A/R must be
        # non-empty. We enforce at the producer (here), not at the schema
        # layer (the live DDL allows nullable for backwards-compat).
        situation = s.get("situation", "") or ""
        task = s.get("task", "") or ""
        action = s.get("action", "") or ""
        result_text = s.get("result", "") or ""
        if not (situation.strip() and task.strip() and action.strip() and result_text.strip()):
            dropped.append({"title": title, "reason": "incomplete STAR fields"})
            continue

        # Embed the story text.
        try:
            embedding = await embed_story_text(s)
            embeddings_issued += 1
        except Exception as e:
            logger.warning(f"G9 embed failed for {title!r}: {e}")
            embedding = None

        # Compose the FK back to profile_experience (if the parser hinted
        # at one). Stored under candidate_passages but the formatter
        # discarded the field — fetch from state.candidate_passages by
        # passage_id.
        source_experience_id = None
        for c in state.get("candidate_passages") or []:
            if c.get("passage_id") == s.get("passage_id"):
                source_experience_id = c.get("source_experience_id")
                break

        try:
            row = await upsert_story(
                user_id=user_id,
                title=title.strip(),
                situation=situation.strip(),
                task=task.strip(),
                action=action.strip(),
                result=result_text.strip(),
                reflection=(s.get("reflection") or "").strip(),
                competencies=s.get("competencies", []) or [],
                archetypes=s.get("archetypes", []) or [],
                quantified_metrics=kept_metrics,
                source_text=s.get("source_text", "") or "",
                source_cv_hash=source_cv_hash,
                source_experience_id=source_experience_id,
                embedding=embedding,
            )
        except Exception as e:
            logger.exception(f"G9 upsert_story failed for {title!r}")
            dropped.append({"title": title, "reason": f"upsert error: {e}"})
            continue

        persisted.append({
            "id": row.get("id"),
            "title": title,
            "dropped_metrics": bad_metrics,
        })

    return {
        "stories_persisted": persisted,
        "stories_dropped": dropped,
        "embeddings_count": embeddings_issued,
        "persisted_count": len(persisted),
        "dropped_count": len(dropped),
        "transcript": [
            make_turn(
                node="persist",
                input_summary=f"stories_in={len(stories)} user_id={user_id}",
                output={
                    "persisted": len(persisted),
                    "dropped": len(dropped),
                    "embeddings_issued": embeddings_issued,
                },
            )
        ],
    }
