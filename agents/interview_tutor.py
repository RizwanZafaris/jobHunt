"""
agents/interview_tutor.py — The Interview Studio chat tutor.

Wraps a single Claude Opus 4.5 call into a small TypedDict-keyed flow so the
api/interview_studio.py router can hand off a conversation turn and get back
a response, a suggested next question, and a recommended concept_level.

Why a separate agent (not just a router-side llm call)?
  - The system prompt is load-bearing and we want it in one place.
  - The cost cap ($0.20/turn) and the JSON-or-fallback parsing are
    encapsulated here; the router just shoves params and persists.
  - We want testability: pass a TutorState dict, get a TutorResult dict,
    no hidden FastAPI / Supabase dependencies in the core function.

Persistence is OPT-IN: tutor_respond does NOT write to interview_tutor_messages.
The api/interview_studio.py endpoint persists both the user's input message
and the tutor's reply. This keeps the agent pure and easy to replay.

Cost cap
  We allow up to TUTOR_MAX_COST_USD per turn. If the call exceeds that
  (it shouldn't — Opus 4.5 at 1500 tokens out is ~$0.11) the response is
  trimmed and a flag bubbles up so the UI can surface "tutor was capped".

Concept level escalation/de-escalation
  We ask the model to recommend the next concept_level based on what the
  user is showing. The UI can act on it (auto-suggest the bump) or ignore.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional, TypedDict

from agents.llm_router import _parse_json_loose, get_router

logger = logging.getLogger(__name__)


# ─── tunables ─────────────────────────────────────────────────────────────
TUTOR_MAX_COST_USD = 0.20            # Hard per-turn cap (audit P1.3 spirit).
TUTOR_MAX_TOKENS_OUT = 1500          # ~ $0.022 at Sonnet 4.6 output pricing.
# 2026-05-12 right-sizing: Sonnet 4.6 matches Opus 4.5 on chat tutoring at
# ~5× less cost. Average tutoring response is 800-1500 tokens; the deep-
# reasoning premium of Opus isn't justified for this surface.
# See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §G3-1 (tutor portion).
TUTOR_MODEL_DEFAULT = "claude-sonnet-4-6"
TUTOR_PROVIDER_DEFAULT = "anthropic"
TUTOR_HISTORY_TURNS = 8              # Last 8 turns (~16 messages) of context.

ConceptLevel = Literal["basics", "intermediate", "advanced"]
PrepSection = Literal[
    "likely_questions",
    "star_stories",
    "company_hooks",
    "red_flag_questions",
    "salary_negotiation",
]


# ─── State shapes ─────────────────────────────────────────────────────────
class TutorPrepPack(TypedDict, total=False):
    """The subset of an interview_prep row the tutor needs."""
    company_name: str
    round_type: str
    round_number: int
    prep_pack_md: Optional[str]
    likely_questions: list[dict]
    star_stories: list[dict]
    company_hooks: list[str]
    red_flag_questions: list[str]
    salary_negotiation_notes: Optional[str]


class TutorChatTurn(TypedDict, total=False):
    role: Literal["user", "tutor", "system"]
    content: str
    concept_level: Optional[ConceptLevel]


class TutorState(TypedDict, total=False):
    """Input to tutor_respond — assembled by api/interview_studio.py."""
    interview_prep: TutorPrepPack
    user_message: str
    concept_level: ConceptLevel
    chat_history: list[TutorChatTurn]
    role_title: Optional[str]
    user_name: Optional[str]


class TutorResult(TypedDict, total=False):
    response: str
    suggested_next_question: Optional[str]
    concept_level_recommended: ConceptLevel
    cited_section: Optional[PrepSection]
    cost_usd: float
    latency_ms: int
    cost_capped: bool


# ─── System prompt (the load-bearing one) ────────────────────────────────
TUTOR_SYSTEM_PROMPT = """You are an interview tutor for a senior fintech / payments PM. The user is preparing for {round_type} at {company}. Their starting concept level is {concept_level} (basics → intermediate → advanced). Walk them through the topic at the requested depth. Cite the prep pack's STAR stories and company hooks. If they want to escalate ('move to advanced'), step up. If they're stuck, drop down.

You have access to the candidate's prep pack — likely questions, STAR stories, company hooks, red-flag questions, and salary-negotiation notes — produced by the G3 graph. Use them. When you reference a question or a STAR story, name it explicitly so the user can find it in the left pane.

Your operating rules:

1. ANCHOR EVERYTHING. Every answer must cite at least one item from the prep pack (a likely question, a STAR story, a company hook, or a red flag). If nothing fits, say so plainly and suggest the user fill the gap with their own example.

2. RESPECT THE LADDER.
   - basics: explain the concept in plain language, define jargon, walk through one worked example end-to-end. No name-dropping payments rails the user hasn't asked about.
   - intermediate: assume the user knows the vocabulary. Compare two approaches. Ask them what they'd choose and why. Bring in second-order tradeoffs.
   - advanced: stop teaching, start sparring. Push back. Steel-man the opposite view. Quote real fintech / payments precedents. Surface a contrarian take they likely haven't considered.

3. ESCALATE / DE-ESCALATE EXPLICITLY.
   - If the user nails an intermediate concept, suggest "ready for advanced?" with a concrete next prompt.
   - If they're confused at intermediate, drop one rung and rebuild from the worked example. Don't shame them — name it neutrally ("let's anchor with a simpler frame first").

4. NEVER FAKE EVIDENCE. If you don't know whether {company} actually does X, say "I don't know — your company hooks list says <Y>, which suggests …". Hallucinated payments-rail facts get the user blacklisted in five minutes; we'd rather sound humble.

5. KEEP IT TIGHT. Maximum 4 short paragraphs OR a worked-example walkthrough. Use bold for the concept name and bullets for tradeoffs. No long preambles.

6. END WITH A NEXT QUESTION. Always close by suggesting one specific follow-up the user should ask you (the tutor). Make it concrete enough they could click it as-is.

OUTPUT FORMAT — strict JSON, no prose preamble or trailing commentary:
{{
  "response": "<your tutoring answer in markdown — bold/bullets ok>",
  "suggested_next_question": "<one specific follow-up question, max 90 chars, or null>",
  "concept_level_recommended": "basics" | "intermediate" | "advanced",
  "cited_section": "likely_questions" | "star_stories" | "company_hooks" | "red_flag_questions" | "salary_negotiation" | null
}}

Always emit valid JSON. No backticks, no commentary outside the JSON object."""


# ─── Helpers ──────────────────────────────────────────────────────────────
def _format_prep_pack_brief(prep: TutorPrepPack) -> str:
    """Compact human-readable rendering of the prep pack for the system prompt.

    Trimmed so we don't blow the input window: top 8 of each list, 240 chars
    max per item.
    """
    parts: list[str] = []

    likely = prep.get("likely_questions") or []
    if likely:
        parts.append("LIKELY QUESTIONS:")
        for i, q in enumerate(likely[:8], 1):
            text = ""
            if isinstance(q, dict):
                text = q.get("question") or q.get("text") or ""
            elif isinstance(q, str):
                text = q
            text = (text or "").strip()
            if text:
                parts.append(f"  {i}. {text[:240]}")

    stars = prep.get("star_stories") or []
    if stars:
        parts.append("\nSTAR STORIES (titles + match):")
        for i, s in enumerate(stars[:8], 1):
            if isinstance(s, dict):
                title = (s.get("title") or s.get("question") or "").strip()
                quality = s.get("match_quality") or ""
                if title:
                    parts.append(f"  {i}. {title[:200]}{f' (match: {quality})' if quality else ''}")

    hooks = prep.get("company_hooks") or []
    if hooks:
        parts.append("\nCOMPANY HOOKS:")
        for i, h in enumerate(hooks[:8], 1):
            if isinstance(h, str) and h.strip():
                parts.append(f"  - {h.strip()[:240]}")
            elif isinstance(h, dict) and h.get("hook"):
                parts.append(f"  - {str(h['hook']).strip()[:240]}")

    red = prep.get("red_flag_questions") or []
    if red:
        parts.append("\nRED-FLAG QUESTIONS (for the user to ask THEM):")
        for i, r in enumerate(red[:6], 1):
            if isinstance(r, str) and r.strip():
                parts.append(f"  - {r.strip()[:240]}")
            elif isinstance(r, dict) and r.get("question"):
                parts.append(f"  - {str(r['question']).strip()[:240]}")

    salary = prep.get("salary_negotiation_notes")
    if salary and isinstance(salary, str) and salary.strip():
        parts.append("\nSALARY NEGOTIATION NOTES (compact):")
        parts.append(salary.strip()[:600])

    return "\n".join(parts) if parts else "(prep pack is empty — encourage the user to build it first)"


def _build_history_messages(history: list[TutorChatTurn]) -> list[dict]:
    """Map the persisted chat_history to Anthropic-compatible messages.

    'tutor' → 'assistant'. 'system'-typed turns become inline user notes
    (Anthropic only allows one system on the request).
    """
    msgs: list[dict] = []
    for turn in history[-(TUTOR_HISTORY_TURNS * 2):]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            msgs.append({"role": "user", "content": content})
        elif role == "tutor":
            msgs.append({"role": "assistant", "content": content})
        elif role == "system":
            # Inline as a user note so the assistant has the breadcrumb.
            msgs.append({"role": "user", "content": f"[note] {content}"})
    return msgs


def _coerce_concept_level(value: Any, fallback: ConceptLevel) -> ConceptLevel:
    if isinstance(value, str) and value in ("basics", "intermediate", "advanced"):
        return value  # type: ignore[return-value]
    return fallback


def _coerce_section(value: Any) -> Optional[PrepSection]:
    if isinstance(value, str) and value in (
        "likely_questions",
        "star_stories",
        "company_hooks",
        "red_flag_questions",
        "salary_negotiation",
    ):
        return value  # type: ignore[return-value]
    return None


_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)


def _safe_parse(text: str) -> dict:
    """Best-effort parse of the tutor JSON response.

    Falls back to: {"response": <raw>, recommended=basics, suggested=None}.
    """
    if not text:
        return {}
    try:
        return _parse_json_loose(text)
    except Exception:
        # Strip the obvious fences and try again.
        cleaned = _FENCE_RE.sub("", text).strip().rstrip("`")
        try:
            return json.loads(cleaned)
        except Exception:
            return {}


# ─── Public API ───────────────────────────────────────────────────────────
async def tutor_respond(state: TutorState) -> TutorResult:
    """Single tutor turn. Pure-ish: no DB writes here.

    Returns a TutorResult dict. Caller is responsible for persisting
    user_message + the returned response into interview_tutor_messages.
    """
    prep: TutorPrepPack = state.get("interview_prep") or {}  # type: ignore[assignment]
    user_message: str = (state.get("user_message") or "").strip()
    concept_level: ConceptLevel = _coerce_concept_level(state.get("concept_level"), "basics")
    history: list[TutorChatTurn] = state.get("chat_history") or []

    if not user_message:
        return TutorResult(
            response=(
                "I didn't catch a question. Try one of: "
                "'walk me through likely behavioral questions', "
                "'how should I frame my STAR for ambiguity', "
                "'what would you push back on at advanced level'."
            ),
            suggested_next_question="What's the first behavioral question you expect?",
            concept_level_recommended=concept_level,
            cited_section=None,
            cost_usd=0.0,
            latency_ms=0,
            cost_capped=False,
        )

    company = prep.get("company_name") or "the company"
    round_type = prep.get("round_type") or "interview"
    round_number = prep.get("round_number") or 1

    system = TUTOR_SYSTEM_PROMPT.format(
        round_type=f"{round_type} round #{round_number}",
        company=company,
        concept_level=concept_level,
    )

    prep_brief = _format_prep_pack_brief(prep)
    role_title = state.get("role_title") or ""
    user_name = state.get("user_name") or "the candidate"

    # Build the conversation messages. The first user message includes the
    # prep brief so the model can ground its first reply; subsequent turns
    # rely on the history (and the system instructions to keep citing).
    messages: list[dict] = []
    if not history:
        messages.append({
            "role": "user",
            "content": (
                f"PREP PACK BRIEF FOR {company} — {round_type.upper()} ROUND #{round_number}\n"
                f"(role: {role_title or 'senior PM, fintech / payments'})\n\n"
                f"{prep_brief}\n\n"
                f"Candidate's first message:\n{user_message}"
            ),
        })
    else:
        # Inject the brief once at the top of the user message so the model
        # never forgets it, even if the history rolls past the original brief.
        messages.extend(_build_history_messages(history))
        messages.append({
            "role": "user",
            "content": (
                f"(reminder — PREP PACK BRIEF for {company}; refer to items by name)\n"
                f"{prep_brief}\n\n"
                f"Candidate's next message (concept_level={concept_level}):\n{user_message}"
            ),
        })

    router = get_router()
    try:
        result = await router.ask(
            provider=TUTOR_PROVIDER_DEFAULT,
            model=TUTOR_MODEL_DEFAULT,
            system=system,
            messages=messages,
            max_tokens=TUTOR_MAX_TOKENS_OUT,
            temperature=0.4,
            agent_name="g3.tutor",
        )
    except Exception as e:
        logger.warning(f"tutor_respond LLM call failed: {type(e).__name__}: {e}")
        return TutorResult(
            response=(
                "I hit a snag reaching the model. Try again in a moment — your prep "
                "pack and chat history are saved."
            ),
            suggested_next_question=None,
            concept_level_recommended=concept_level,
            cited_section=None,
            cost_usd=0.0,
            latency_ms=0,
            cost_capped=False,
        )

    cost_capped = bool(result.cost_usd and result.cost_usd > TUTOR_MAX_COST_USD)
    parsed = _safe_parse(result.text or "")

    response_text = (parsed.get("response") or "").strip()
    if not response_text:
        # Model didn't return JSON — treat the whole text as the answer.
        response_text = (result.text or "").strip()
    if not response_text:
        response_text = (
            "I'm here. Tell me which prep section you want to dig into "
            "(likely questions, STAR stories, company hooks, red flags, salary)."
        )

    suggested = parsed.get("suggested_next_question")
    suggested = (suggested.strip() if isinstance(suggested, str) and suggested.strip() else None)
    if suggested and len(suggested) > 200:
        suggested = suggested[:200].rstrip() + "…"

    recommended = _coerce_concept_level(parsed.get("concept_level_recommended"), concept_level)
    cited = _coerce_section(parsed.get("cited_section"))

    return TutorResult(
        response=response_text,
        suggested_next_question=suggested,
        concept_level_recommended=recommended,
        cited_section=cited,
        cost_usd=float(result.cost_usd or 0.0),
        latency_ms=int(result.latency_ms or 0),
        cost_capped=cost_capped,
    )


__all__ = [
    "ConceptLevel",
    "PrepSection",
    "TutorChatTurn",
    "TutorPrepPack",
    "TutorResult",
    "TutorState",
    "TUTOR_MAX_COST_USD",
    "TUTOR_SYSTEM_PROMPT",
    "tutor_respond",
]
