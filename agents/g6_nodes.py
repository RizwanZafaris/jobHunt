"""
agents/g6_nodes.py — Six LangGraph node functions for the G6 Follow-up Graph.

Layout (matches G2 fan-out pattern at the critic layer):

    cadence_checker  (code)         — short-circuits when not URGENT/OVERDUE
            ↓
    context_builder  (code + RAG)    — fresh news + personal-win signals
            ↓
    draft_generator  (Sonnet 4.6, prompt-cached)
            ↓
    tone_calibrator  +  persona_critic   (parallel siblings, both Sonnet 4.6)
            ↘                ↙
            merge_critique  (code; may bounce back to draft_generator)
                  ↓
            urgency_scorer  (code)
                  ↓
            persist_and_notify  (code; writes follow_up_cadence)

Cost model (single follow-up):
    cadence_checker         $0.00  (pure code)
    context_builder         $0.00  (pgvector + DB reads)
    draft_generator         $0.05  (Sonnet 4.6, ~3K input + 1K output, cached system)
    tone_calibrator         $0.02  (Sonnet 4.6, small)
    persona_critic          $0.05  (Sonnet 4.6, ~2K input + 0.5K output)
    merge_critique          $0.00  (code)
    urgency_scorer          $0.00  (code)
    persist_and_notify      $0.00  (code)
    ──────────────────────────────
    total                  ~$0.12
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.g6_cadence import (
    CADENCE_RULES,
    compute_cadence,
    compute_next_follow_up_date,
)
from agents.g6_io import (
    close_application_cold,
    insert_cadence_row,
    load_recent_company_knowledge,
    load_recent_linkedin_personal_wins,
)
from agents.g6_state import FollowUpState, make_g6_turn
from agents.llm_router import _parse_json_loose, get_router
from config.settings import get_settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Tunables
# ──────────────────────────────────────────────────────────────────────────
# These match the spec in the roadmap. If you tweak them, update the
# cost-model doc in g6_nodes.py header and the spec in roadmap §3.3.
_FOLLOW_UP_MODEL = "claude-sonnet-4-6"
_MAX_DRAFT_RETRIES = 2

# Career-ops banned phrases — the persona_critic hard-fails on ANY of these.
# Lowercase; matched as a case-insensitive substring (not a regex) because
# we want to catch them inside paragraphs ("I'm just checking in to see if...")
# without forcing the user to memorise regex escaping.
_BANNED_FOLLOW_UP_PHRASES = frozenset({
    "just checking in",
    "checking in",
    "circling back",
    "touching base",
    "touch base",
    "wanted to follow up",
    "want to follow up",
    "following up on",
    "just wanted to",
    "any updates",
    "any news",
    "bumping this",
    "bumping this up",
})


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _scan_banned_phrases(draft: str) -> list[str]:
    """Return the banned phrases (verbatim, lowercase) that appear in `draft`.
    Empty list when clean. Case-insensitive substring scan."""
    if not draft:
        return []
    lower = draft.lower()
    hits: list[str] = []
    for phrase in _BANNED_FOLLOW_UP_PHRASES:
        if phrase in lower:
            hits.append(phrase)
    return hits


_CITE_RE = re.compile(r"cite:(knowledge|linkedin)_id=([\w\-]+)")


def _extract_cites_from_draft(draft: str) -> list[dict]:
    """Pull cite:kind_id=<uuid> breadcrumbs out of the draft text.

    The draft_generator is instructed to embed exactly one cite per signal
    sentence. We extract them here so they can be persisted to
    follow_up_cadence.draft_cites and used by Phase 3 outcome attribution.
    """
    if not draft:
        return []
    cites: list[dict] = []
    for kind, uid in _CITE_RE.findall(draft):
        cites.append({"kind": kind, "id": uid})
    return cites


# ══════════════════════════════════════════════════════════════════════════
# Node 1 — cadence_checker (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def cadence_checker_node(state: FollowUpState) -> dict:
    """Decide whether THIS application needs a follow-up today.

    Reads:   application_row, last_cadence_row, follow_up_count
    Writes:  urgency, next_action, days_since_event, last_event, short_circuit,
             cadence_reason
    Cost:    $0.00 — pure code via agents.g6_cadence.compute_cadence.
    """
    app = state["application_row"]
    last_cad = state.get("last_cadence_row") or {}

    decision = compute_cadence(
        status=app.get("status"),
        applied_date=app.get("applied_date"),
        last_contact_date=last_cad.get("last_contact_date"),
        follow_up_count=int(state.get("follow_up_count") or 0),
    )

    last_event = (
        "last_contact_date" if last_cad.get("last_contact_date") else "applied_date"
    )
    short_circuit = decision.next_action != "follow_up"

    return {
        "urgency": decision.urgency,
        "next_action": decision.next_action,
        "recommended_delay_days": decision.recommended_delay_days,
        "days_since_event": decision.days_since_last_event,
        "last_event": last_event,
        "short_circuit": short_circuit,
        "cadence_reason": decision.reason,
        "transcript": [
            make_g6_turn(
                node="cadence_checker",
                input_summary=(
                    f"status={app.get('status')} "
                    f"applied_date={app.get('applied_date')} "
                    f"follow_up_count={state.get('follow_up_count')}"
                ),
                output=decision.as_dict(),
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 2 — context_builder (pure code + RAG)
# ══════════════════════════════════════════════════════════════════════════
async def context_builder_node(state: FollowUpState) -> dict:
    """Gather the signal we'll cite in the follow-up.

    Short-circuit: if cadence_checker decided we're not drafting today, skip
    the RAG fetch — saves ~50-200ms per non-URGENT application in the cron.

    Reads:   application_row, short_circuit, user_id
    Writes:  rag_chunks
    Cost:    $0.00 — pgvector + Postgres only.
    """
    if state.get("short_circuit"):
        return {
            "rag_chunks": [],
            "transcript": [
                make_g6_turn(
                    node="context_builder",
                    input_summary="short-circuit skip — cadence_checker said no draft today",
                    output={"chunks": 0, "reason": "short_circuit"},
                )
            ],
        }

    app = state["application_row"]
    user_id = state["user_id"]
    company_name = app.get("company") or ""
    role = app.get("role") or ""

    # Build a RAG query oriented towards "what's new at this company we
    # could legitimately reference in a follow-up". The role is included so
    # the top-k chunks lean towards news + culture for the relevant function.
    rag_query = f"{company_name} {role} recent news product launch hiring funding"
    news_chunks = await load_recent_company_knowledge(
        company_name=company_name,
        query=rag_query,
        match_count=5,
        max_age_days=7,
    )
    personal_wins = load_recent_linkedin_personal_wins(
        user_id=user_id,
        max_age_days=14,
        limit=3,
    )

    rag_chunks: list[dict] = []
    for c in news_chunks:
        if not c.get("id"):
            continue
        rag_chunks.append({
            "kind": "knowledge",
            "id": str(c.get("id")),
            "section": c.get("section"),
            "content": (c.get("content") or "")[:1200],
            "similarity": c.get("similarity"),
        })
    for d in personal_wins:
        if not d.get("id"):
            continue
        rag_chunks.append({
            "kind": "linkedin",
            "id": str(d.get("id")),
            "section": d.get("angle") or "personal_win",
            "content": (d.get("hook") or "")[:600],
            "similarity": None,
        })

    return {
        "rag_chunks": rag_chunks,
        "transcript": [
            make_g6_turn(
                node="context_builder",
                input_summary=f"company={company_name} role={role}",
                output={
                    "knowledge_hits": len(news_chunks),
                    "personal_win_hits": len(personal_wins),
                    "total_signals": len(rag_chunks),
                },
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 3 — draft_generator (Sonnet 4.6, prompt-cached system)
# ══════════════════════════════════════════════════════════════════════════
DRAFT_GENERATOR_SYSTEM = """You write follow-up emails for a job seeker who
has already applied. You write ONE email. You output ONLY the email body —
no subject line, no preamble, no commentary.

────────────────────────────────────────────────────────────────────────
THE FRAMEWORK (every email follows this 3-sentence shape):
────────────────────────────────────────────────────────────────────────
Sentence 1: Reference the role + when the candidate applied.
              Example: "Following my 12 May application for the Head of
              Payments Product role at Tabby —"
Sentence 2: ONE new signal. EITHER fresh company news (cite:knowledge_id=
              <uuid>) OR a personal win posted in the last 14 days
              (cite:linkedin_id=<uuid>). Pick the strongest signal you
              have; never two.
Sentence 3: Soft ask + availability + thanks. No "any updates?" — a
              question recruiters skim past. Use "happy to share more on
              X if useful" framing.

────────────────────────────────────────────────────────────────────────
NEVER USE THESE PHRASES (the recruiter mentally tunes out):
────────────────────────────────────────────────────────────────────────
- "just checking in"      - "circling back"
- "touching base"          - "touch base"
- "wanted to follow up"    - "any updates"
- "any news"               - "bumping this"
- "following up on"        - "just wanted to"

────────────────────────────────────────────────────────────────────────
ALWAYS:
────────────────────────────────────────────────────────────────────────
- LEAD WITH VALUE, not the ask. Sentence 2 must give the recruiter
  something — context, signal, relevance.
- EVERY signal sentence carries a cite breadcrumb in the form
  "cite:knowledge_id=<uuid>" or "cite:linkedin_id=<uuid>". The cite goes
  INSIDE the sentence as a parenthetical so it's preserved through
  copy-paste. Example:
      "Saw the announcement on the Tabby–Daraz BNPL integration
       (cite:knowledge_id=abc-123) — that's exactly the merchant-side
       expansion my Daraz BNPL work shipped against ($X TPV in 8 months)."
- Plain prose. 4-7 sentences max. No bullets, no headers, no signature
  block — the user will append their own.
- One-paragraph form. The structure above describes content; the
  rendering is one flowing paragraph.

────────────────────────────────────────────────────────────────────────
HARD RULES:
────────────────────────────────────────────────────────────────────────
- If the SIGNAL POOL passed to you is empty, output the literal string
  "[NO_SIGNAL_AVAILABLE]" and stop. We won't send an empty follow-up;
  better to drop the row than send a generic ping.
- Never invent statistics. The personal-win sentence may reference the
  user's own shipped numbers ONLY when they appear in the SIGNAL POOL
  (linkedin_drafts rows). No "390M+ users" unless that's in the data.
"""


async def draft_generator_node(state: FollowUpState) -> dict:
    """Generate the follow-up email draft.

    Reads:   application_row, company_persona, rag_chunks, merge_outcome
             (when iterating), short_circuit
    Writes:  draft_email, draft_cites, draft_attempts
    Cost:    ~$0.05/iter (Sonnet 4.6, system prompt cached).
    """
    if state.get("short_circuit"):
        return {
            "draft_email": "",
            "draft_cites": [],
            "draft_attempts": 0,
            "transcript": [
                make_g6_turn(
                    node="draft_generator",
                    input_summary="short-circuit skip",
                    output={"skipped": True},
                )
            ],
        }

    app = state["application_row"]
    persona = state.get("company_persona") or {}
    rag_chunks = state.get("rag_chunks") or []
    merge_outcome = state.get("merge_outcome") or {}
    iteration = int(state.get("iteration") or 0)
    attempts = int(state.get("draft_attempts") or 0) + 1

    # If we have NO signal at all the prompt's HARD RULE returns
    # [NO_SIGNAL_AVAILABLE]; we save an LLM call by short-circuiting in code.
    if not rag_chunks:
        return {
            "draft_email": "",
            "draft_cites": [],
            "draft_attempts": attempts,
            "needs_retry": False,
            "transcript": [
                make_g6_turn(
                    node="draft_generator",
                    iteration=iteration,
                    input_summary="no rag_chunks available; skip LLM call",
                    output={"signal_pool_empty": True},
                )
            ],
        }

    signal_block_lines = ["SIGNAL POOL (pick the SINGLE strongest signal):"]
    for c in rag_chunks:
        kind = c.get("kind")
        cid = c.get("id")
        section = c.get("section") or "?"
        content = (c.get("content") or "")[:600]
        signal_block_lines.append(
            f"- [{kind} {section}] cite:{kind}_id={cid}\n  {content}"
        )
    signal_block = "\n".join(signal_block_lines)

    # Critique block when we're iterating after a failed merge.
    critique_block = ""
    if iteration > 0 and merge_outcome:
        critique_block = (
            "\n\nPRIOR CRITIQUE (the merge_critique node rejected the last "
            "draft — fix these specific issues):\n"
            + str(merge_outcome.get("reason") or "")[:1200]
        )

    # Persona idiom block — only if present. The career-ops voice already
    # captures most of the cadence rules; persona adds company-specific tone.
    persona_inline = ""
    success_patterns = (persona.get("success_patterns") or [])[:4] if persona else []
    if success_patterns:
        persona_inline = (
            "\n\nCOMPANY VOICE PATTERNS (light hint; do NOT copy verbatim):\n"
            + "\n".join(f"  - {p}" for p in success_patterns)
        )

    user = f"""ROLE: {app.get('role') or 'the role'}
COMPANY: {app.get('company') or '(unknown)'}
APPLIED ON: {app.get('applied_date') or 'recently'}
ITERATION: {iteration} (attempt {attempts}/{_MAX_DRAFT_RETRIES + 1})

{signal_block}{persona_inline}{critique_block}

Write the follow-up email body now. Follow the 3-sentence framework from
the system prompt. EXACTLY ONE signal sentence with EXACTLY ONE cite
breadcrumb. Output ONLY the email body — no subject, no signature.
"""

    # 2026-05-12 (G11 Tier 4): prepend the user's voice profile block to the
    # user message. No-op on cold-start (no calibration yet). We prepend
    # to the USER message (not system) so the large DRAFT_GENERATOR_SYSTEM
    # prompt cache stays warm across users.
    from agents.voice_injector import prepend_voice_block
    user = prepend_voice_block(user, user_id=state.get("user_id"))

    settings = get_settings()
    model = getattr(settings, "g6_draft_model", _FOLLOW_UP_MODEL)
    result = await get_router().ask(
        provider="anthropic",
        model=model,
        system=DRAFT_GENERATOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=1000,
        temperature=0.35,
        agent_name="g6.draft_generator",
    )

    draft = (result.text or "").strip()
    # The HARD RULE escape hatch — if the LLM thinks the signal pool is too
    # thin to make a credible follow-up, it returns this sentinel. Treat
    # exactly like the empty rag_chunks branch above.
    if draft.startswith("[NO_SIGNAL_AVAILABLE]"):
        draft = ""
    cites = _extract_cites_from_draft(draft)

    return {
        "draft_email": draft,
        "draft_cites": cites,
        "draft_attempts": attempts,
        "needs_retry": False,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_g6_turn(
                node="draft_generator",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                input_summary=(user[:480] + "...") if len(user) > 480 else user,
                output={"draft_chars": len(draft), "cites": cites},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 4a — persona_critic (Sonnet 4.6)
# ══════════════════════════════════════════════════════════════════════════
PERSONA_CRITIC_SYSTEM = """You are a hiring manager at ONE specific company.
You receive a follow-up email a candidate is about to send and score it
against TWO independent axes:

1. BANNED-PHRASE CHECK (HARD FAIL): the email must not contain any of
   "just checking in", "circling back", "touching base", "wanted to
   follow up", "any updates", "any news", "bumping this", "following up
   on", "just wanted to". If ANY appear, return banned_pass=false and
   list each hit.

2. VALUE-LEAD CHECK: the second sentence MUST cite a real signal — fresh
   company news (cite:knowledge_id=...) OR a personal win
   (cite:linkedin_id=...). Generic "I'm still interested" / "wanted to
   reiterate my interest" copy fails this gate.

3. COMPANY PERSONA ALIGNMENT: when the company's banned_keywords or
   success_patterns are non-empty, score how well the draft respects
   them (0-100; 70+ is a pass).

Output strict JSON only:
{
  "banned_pass": true|false,
  "banned_phrases_used": ["..."],
  "value_lead_pass": true|false,
  "value_lead_reason": "<one sentence>",
  "persona_alignment_score": 0-100,
  "persona_alignment_notes": "<one sentence>",
  "verdict": "accept" | "revise",
  "fixes": ["...", "..."]
}"""


async def persona_critic_node(state: FollowUpState) -> dict:
    """Persona-as-critic: banned-phrase scan + persona-bank alignment.

    Reads:   draft_email, company_persona, short_circuit
    Writes:  persona_critique
    Cost:    ~$0.05/iter (Sonnet 4.6, small).
    """
    if state.get("short_circuit") or not state.get("draft_email"):
        return {
            "persona_critique": {
                "skipped": True,
                "verdict": "accept",
                "banned_pass": True,
                "banned_phrases_used": [],
                "value_lead_pass": True,
            },
            "transcript": [
                make_g6_turn(
                    node="persona_critic",
                    input_summary="skip — no draft to critique",
                    output={"skipped": True},
                )
            ],
        }

    iteration = int(state.get("iteration") or 0)
    draft = state["draft_email"]
    persona = state.get("company_persona") or {}

    # Pure-code prescan first — passed into the LLM so it can't miss them.
    prescanned = _scan_banned_phrases(draft)

    persona_block = ""
    banned_keywords = (persona.get("ats_keyword_bank") or {}).get("banned") if isinstance(persona, dict) else None
    if banned_keywords:
        persona_block += (
            f"\nCOMPANY BANNED KEYWORDS: {', '.join(str(k) for k in banned_keywords[:15])}"
        )
    persona_block += (
        "\nPRESCANNED BANNED-PHRASE HITS (pure-code scan already found these): "
        f"{prescanned}"
    )

    user = f"""COMPANY: {state['application_row'].get('company')}
ROLE: {state['application_row'].get('role')}
{persona_block}

DRAFT FOLLOW-UP EMAIL:
{draft}

Score the draft. Return strict JSON only."""

    settings = get_settings()
    model = getattr(settings, "g6_persona_critic_model", _FOLLOW_UP_MODEL)
    result = await get_router().ask(
        provider="anthropic",
        model=model,
        system=PERSONA_CRITIC_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=800,
        temperature=0.15,
        agent_name="g6.persona_critic",
        json_response=True,
    )

    try:
        parsed = _parse_json_loose(result.text)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception:
        parsed = {}

    # Pure-code override: if our prescan found ANY banned phrase, the LLM
    # CANNOT mark the draft as banned_pass=true. Structural enforcement.
    if prescanned:
        parsed["banned_pass"] = False
        existing = parsed.get("banned_phrases_used")
        if not isinstance(existing, list):
            existing = []
        union = sorted(set([str(x).lower() for x in existing] + prescanned))
        parsed["banned_phrases_used"] = union
        parsed["verdict"] = "revise"

    # Default verdict resolution.
    if "verdict" not in parsed:
        parsed["verdict"] = (
            "accept"
            if parsed.get("banned_pass", True) and parsed.get("value_lead_pass", True)
            else "revise"
        )

    return {
        "persona_critique": parsed,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_g6_turn(
                node="persona_critic",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                output=parsed,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 4b — tone_calibrator (Sonnet 4.6)
# ══════════════════════════════════════════════════════════════════════════
TONE_CALIBRATOR_SYSTEM = """You are the candidate's voice coach. You read a
draft follow-up email and score it against the candidate's voice.

The candidate's voice is captured by:
  - their success_patterns (bullet templates that historically work)
  - their company_persona for THIS company

Score on:
  - VOICE MATCH (0.0-1.0): does the draft read like the candidate?
  - TIGHTNESS:  is it 4-7 sentences, one paragraph? Too long = revise.
  - CONFIDENCE: assertive without being arrogant, no apology, no
    hedging like "if you have a moment" / "sorry to bother".

Output strict JSON:
{
  "voice_match_score": 0.0-1.0,
  "sentence_count": <int>,
  "tightness_pass": true|false,
  "confidence_pass": true|false,
  "verdict": "accept" | "revise",
  "fixes": ["...", "..."]
}"""


async def tone_calibrator_node(state: FollowUpState) -> dict:
    """Voice + tone gate (parallel sibling to persona_critic).

    Reads:   draft_email, company_persona, short_circuit
    Writes:  voice_critique
    Cost:    ~$0.02/iter.
    """
    if state.get("short_circuit") or not state.get("draft_email"):
        return {
            "voice_critique": {
                "skipped": True,
                "verdict": "accept",
                "voice_match_score": 1.0,
                "tightness_pass": True,
                "confidence_pass": True,
            },
            "transcript": [
                make_g6_turn(
                    node="tone_calibrator",
                    input_summary="skip — no draft to calibrate",
                    output={"skipped": True},
                )
            ],
        }

    iteration = int(state.get("iteration") or 0)
    draft = state["draft_email"]
    persona = state.get("company_persona") or {}
    success_patterns = (persona.get("success_patterns") or [])[:4] if persona else []

    success_block = ""
    if success_patterns:
        success_block = "\nCANDIDATE SUCCESS PATTERNS (the voice to match):\n" + "\n".join(
            f"  - {p}" for p in success_patterns
        )

    user = f"""COMPANY: {state['application_row'].get('company')}
ROLE: {state['application_row'].get('role')}
{success_block}

DRAFT FOLLOW-UP EMAIL:
{draft}

Score the voice match. Return strict JSON only."""

    settings = get_settings()
    model = getattr(settings, "g6_tone_calibrator_model", _FOLLOW_UP_MODEL)
    result = await get_router().ask(
        provider="anthropic",
        model=model,
        system=TONE_CALIBRATOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=500,
        temperature=0.2,
        agent_name="g6.tone_calibrator",
        json_response=True,
    )

    try:
        parsed = _parse_json_loose(result.text)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception:
        parsed = {}

    if "verdict" not in parsed:
        score = float(parsed.get("voice_match_score") or 0.0)
        parsed["verdict"] = (
            "accept"
            if score >= 0.6
            and parsed.get("tightness_pass", True)
            and parsed.get("confidence_pass", True)
            else "revise"
        )

    return {
        "voice_critique": parsed,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_g6_turn(
                node="tone_calibrator",
                iteration=iteration,
                provider=result.provider,
                model=result.model,
                output=parsed,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 5 — merge_critique (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def merge_critique_node(state: FollowUpState) -> dict:
    """Union the two critics' verdicts.

    Reads:   persona_critique, voice_critique, draft_attempts
    Writes:  merge_outcome, needs_retry, iteration
    Cost:    $0.00 — pure code.

    Decision matrix:
      - Either critic says "revise" AND we have retries left → bounce back
        to draft_generator with combined fixes (needs_retry=True).
      - Both say "accept" → proceed to urgency_scorer.
      - Either says "revise" but max retries reached → accept with caveat
        (the persist node logs the unresolved critique on the row).
    """
    persona = state.get("persona_critique") or {}
    voice = state.get("voice_critique") or {}
    attempts = int(state.get("draft_attempts") or 0)
    iteration = int(state.get("iteration") or 0)

    persona_verdict = persona.get("verdict", "accept")
    voice_verdict = voice.get("verdict", "accept")
    any_revise = persona_verdict == "revise" or voice_verdict == "revise"

    fixes: list[str] = []
    if persona.get("banned_phrases_used"):
        fixes.append(
            "Remove banned phrases: " + ", ".join(persona["banned_phrases_used"])
        )
    for f in (persona.get("fixes") or [])[:3]:
        fixes.append(f"[persona] {f}")
    for f in (voice.get("fixes") or [])[:3]:
        fixes.append(f"[voice] {f}")

    retries_left = (_MAX_DRAFT_RETRIES + 1) - attempts
    needs_retry = any_revise and retries_left > 0 and bool(state.get("draft_email"))

    merge = {
        "any_revise": any_revise,
        "persona_verdict": persona_verdict,
        "voice_verdict": voice_verdict,
        "fixes": fixes,
        "retries_left": retries_left,
        "needs_retry": needs_retry,
        "reason": "; ".join(fixes) if fixes else "both critics accepted",
    }

    patch: dict[str, Any] = {
        "merge_outcome": merge,
        "needs_retry": needs_retry,
        "transcript": [
            make_g6_turn(
                node="merge_critique",
                iteration=iteration,
                input_summary=(
                    f"persona={persona_verdict} voice={voice_verdict} "
                    f"attempts={attempts}"
                ),
                output=merge,
            )
        ],
    }
    if needs_retry:
        patch["iteration"] = iteration + 1
    return patch


# ══════════════════════════════════════════════════════════════════════════
# Node 6 — urgency_scorer (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def urgency_scorer_node(state: FollowUpState) -> dict:
    """Final urgency check before persistence.

    Reads:   urgency (from cadence_checker), draft_email, persona_critique
    Writes:  urgency (may be downgraded), pushed_to_today_queue
    Cost:    $0.00 — pure code.

    Adjustments:
      - If cadence said URGENT but we never produced a draft, downgrade
        to 'waiting' so the /today queue doesn't show a broken card.
      - If persona_critique still flags banned phrases AND we exhausted
        retries, keep urgency but flag the row as having unresolved issues.
    """
    urgency = state.get("urgency") or "waiting"
    has_draft = bool(state.get("draft_email"))
    short = bool(state.get("short_circuit"))

    if urgency in ("URGENT", "OVERDUE") and not has_draft and not short:
        # We were supposed to draft but ended up without one (no signal,
        # all retries failed, etc.). Downgrade so /today doesn't surface
        # an empty card. The cadence row still records the attempt.
        urgency = "waiting"

    pushed = urgency in ("URGENT", "OVERDUE") and has_draft

    return {
        "urgency": urgency,
        "pushed_to_today_queue": pushed,
        "transcript": [
            make_g6_turn(
                node="urgency_scorer",
                input_summary=f"urgency_in={state.get('urgency')} has_draft={has_draft}",
                output={"urgency_out": urgency, "pushed_to_today_queue": pushed},
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 7 — persist_and_notify (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def persist_and_notify_node(state: FollowUpState) -> dict:
    """Write the follow_up_cadence row + (when COLD) close the application.

    Reads:   user_id, application_id, application_row, current_status,
             follow_up_count, urgency, draft_email, draft_cites,
             persona_critique, voice_critique, cost_usd_total, next_action,
             recommended_delay_days
    Writes:  cadence_row_id, applications_closed_cold
    Cost:    $0.00 — pure Postgres writes.

    Note: we INSERT a new row every run (one row per drafted attempt).
    The /approve and /skip endpoints UPDATE this row in-place.
    """
    app = state["application_row"]
    user_id = state["user_id"]
    application_id = state["application_id"]
    current_status = app.get("status") or "applied"
    urgency = state.get("urgency")
    follow_up_count = int(state.get("follow_up_count") or 0)

    # next_follow_up_date for the NEXT cycle. Stash now so the cron's
    # eligibility query (next_follow_up_date <= NOW()) works on subsequent
    # passes. For COLD rows we leave it NULL.
    if urgency in ("URGENT", "OVERDUE") and state.get("draft_email"):
        next_fu = compute_next_follow_up_date(status=current_status)
    elif urgency == "waiting":
        delay = int(state.get("recommended_delay_days") or 0)
        next_fu = datetime.now(timezone.utc) + timedelta(days=max(1, delay))
    else:
        next_fu = None

    critique_blob = {
        "persona_critique": state.get("persona_critique"),
        "voice_critique": state.get("voice_critique"),
        "merge_outcome": state.get("merge_outcome"),
        "cadence_reason": state.get("cadence_reason"),
    }

    try:
        row = insert_cadence_row(
            user_id=user_id,
            application_id=application_id,
            current_status=current_status,
            follow_up_count=follow_up_count,
            urgency=urgency,
            next_follow_up_date=next_fu,
            draft_email=state.get("draft_email") or None,
            draft_cites=state.get("draft_cites") or [],
            draft_persona_critique=critique_blob,
            cost_usd=float(state.get("cost_usd_total") or 0.0),
        )
        cadence_row_id = row.get("id")
    except Exception as e:
        logger.exception(f"G6 persist failed for application {application_id}")
        return {
            "cadence_row_id": None,
            "applications_closed_cold": False,
            "error": f"persist_failed:{type(e).__name__}:{str(e)[:200]}",
            "transcript": [
                make_g6_turn(
                    node="persist_and_notify",
                    input_summary=f"application_id={application_id}",
                    output={"error": str(e)[:200]},
                    error=str(e)[:500],
                )
            ],
        }

    applications_closed_cold = False
    # The cadence rules say "next_action='close' AND urgency=COLD" means
    # we recommend closure. We do NOT auto-close here — the spec says
    # the user must confirm via /skip. The cadence row records the
    # recommendation; the API surfaces it as a "close this?" card.
    # This guard exists in case a future cron caller wants auto-close.
    if state.get("auto_close_on_cold") and urgency == "COLD" and state.get("next_action") == "close":
        try:
            close_application_cold(application_id, user_id)
            applications_closed_cold = True
        except Exception as e:
            logger.warning(f"G6 auto-close failed for application {application_id}: {e}")

    return {
        "cadence_row_id": cadence_row_id,
        "applications_closed_cold": applications_closed_cold,
        "transcript": [
            make_g6_turn(
                node="persist_and_notify",
                input_summary=(
                    f"application_id={application_id} urgency={urgency} "
                    f"draft_chars={len(state.get('draft_email') or '')}"
                ),
                output={
                    "cadence_row_id": cadence_row_id,
                    "applications_closed_cold": applications_closed_cold,
                    "next_follow_up_date": (
                        next_fu.isoformat() if next_fu else None
                    ),
                },
            )
        ],
    }
