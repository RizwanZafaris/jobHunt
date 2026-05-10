"""
agents/resume_edit_assistant.py — In-browser resume editor's chat assistant.

Phase 2 (Workspace) ships ONE of the three planned modes:

    • quick_tweak()     — single Opus-4.7 call, ~$0.05, ~3s.
                          Surgical edit applied directly to current_md.
                          Default for "tighten this", "rephrase that bullet",
                          "swap MM/YYYY for YYYY", etc.
    • rebuild_section() — Phase 3+. Re-invokes G2 graph from `warm_start_md`
                          but only re-runs writer + critic on the targeted
                          section. ~$0.50, 2-3 min.
    • full_rebuild()    — Phase 3+. Full G2 from scratch using the same
                          job + persona. ~$1, 5 min. Calls into
                          `enqueue_g2_build` with force=True.

Only `quick_tweak` is implemented here. The other two raise
`NotImplementedError("phase 3")` and the API layer turns that into a 501
with a clear "coming next session" message — same pattern as the
PDF/DOCX placeholder in api/workspace.py.

Why this lives in `agents/` and not `resume_agents/`:
  resume_agents/ is the LangGraph-driven full G2 pipeline (writer, critic,
  polisher nodes in a graph). resume_edit_assistant.py is a single-shot
  agent — the simplest possible LLM call. Putting it next to G2 would
  invite confusion about graph state vs. flat call. It belongs with the
  one-shot agents (intro_email, profile_analyzer, etc.) in agents/.

Cost ceiling:
  Defensive cap of $0.10/call. A normal Opus tweak on a 2-page resume
  comes in around $0.04-0.06 ($15/M input, $75/M output, ~3K input
  tokens, ~3K output tokens for a full re-emission of the resume). If
  the model burns through that we 429 the user — no silent overspend.

Output contract (CONFIRMED with user):
  {
    "updated_md":    str,   # full resume markdown after the edit applied
    "response":      str,   # 1-3 sentence chat-style explanation, what
                            # changed and why. Surfaces in the chat panel.
    "fixes_applied": list,  # list of short bullet points naming each
                            # change. Empty list is fine for trivial edits.
  }

The `cost_usd` field is added by the caller from `LLMResult.cost_usd`.

Idempotency / safety:
  No DB writes happen here — saving is a separate POST in api/workspace.py.
  This module is pure compute: prompt → LLM → parse → return. Re-running
  the same instruction on the same current_md is fine; the user can
  always Cancel the edit or run a different instruction.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.llm_router import LLMResult, _parse_json_loose, get_router

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────
# Defensive ceiling. A normal quick tweak on a 2-page resume is ~$0.04-0.06.
QUICK_TWEAK_COST_CAP_USD = 0.10
QUICK_TWEAK_MODEL = "claude-opus-4-7"
QUICK_TWEAK_MAX_TOKENS = 4096
QUICK_TWEAK_TEMPERATURE = 0.2  # surgical, not creative

# ─── System prompt (THIS IS THE LOAD-BEARING TEXT) ────────────────────────
# Reviewed by the user — kept in this file so prompt edits live with the
# code that uses them. Mirrored in api/WORKSPACE.md for the design review.
QUICK_TWEAK_SYSTEM_PROMPT = """\
You are a senior resume editor sitting next to a job candidate. They have a complete, ATS-ready resume in front of them in markdown, and they have just given you ONE specific instruction. Your job is to apply that instruction surgically — and nothing more.

ABSOLUTE RULES
1. Apply ONLY the requested change. Do not "improve" other sections, fix unrelated typos, or rewrite phrasing the user did not ask you to touch.
2. Preserve all formatting EXACTLY — markdown headings, bullet markers, bold/italic, line breaks, blank lines between sections, tables, dividers (---), and section ordering must survive untouched outside the area you edited.
3. Never invent facts. If the candidate's instruction implies a fact that is not present in the resume (a metric, a company, a date, a tool), do NOT add it. Instead set `fixes_applied` to ["needs_user_input: <what's missing>"] and leave updated_md unchanged.
4. Keep the candidate's voice. Do not Americanise British spelling, do not switch tense, do not introduce buzzwords ("synergised", "leveraged", "spearheaded") unless the user explicitly asks for that register.
5. If the instruction is ambiguous, make the smallest reasonable interpretation and explain your read in `response`. Do not ask a clarifying question — the user can always send another message.
6. Respect the persona's banned-keyword list and required-keyword list (provided in the user message). Banned words must not appear in updated_md. Required keywords already in the resume must remain present unless the user explicitly asks to remove them.
7. NEVER change personal details — name, email, phone, links, location — unless the instruction is explicitly about them.

OUTPUT (STRICT JSON, no markdown fences, no prose before or after)
{
  "updated_md":    "<the full resume markdown after applying the edit; identical to the input outside the edited region>",
  "response":      "<1-3 sentences in plain English: what you changed, where, and why. Address the candidate directly: 'I tightened the…'>",
  "fixes_applied": ["<short bullet>", "<short bullet>"]
}

If the instruction cannot be safely applied without inventing a fact, return:
{
  "updated_md":    "<the input markdown verbatim>",
  "response":      "<one sentence explaining what input you'd need to do this safely>",
  "fixes_applied": ["needs_user_input: <what's missing>"]
}
"""


# ─── Public API ────────────────────────────────────────────────────────────
async def quick_tweak(
    *,
    current_md: str,
    instruction: str,
    persona: Optional[dict[str, Any]] = None,
    jd: Optional[dict[str, Any]] = None,
    chat_history: Optional[list[dict[str, str]]] = None,
    cost_cap_usd: float = QUICK_TWEAK_COST_CAP_USD,
) -> dict[str, Any]:
    """Single-shot resume edit using Opus 4.7.

    Args:
      current_md:     The markdown the user is looking at right now. The
                      result's `updated_md` will be a transformation of
                      THIS string, not whatever was last saved to the DB.
      instruction:    The free-text instruction the user typed in the
                      chat panel ("tighten the Daraz bullet to one line").
      persona:        Optional company persona dict. We pass through the
                      ATS keyword bank and any banned keywords so the
                      model respects them. If omitted, no keyword
                      constraints are applied.
      jd:             Optional job dict. We pass company + title +
                      description so the model can keep alignment in mind.
      chat_history:   Optional list of {role, content} from the chat
                      panel for follow-up edits ("now do the same for…").
                      Keep it short — last 6 turns is plenty.
      cost_cap_usd:   Defensive cap. Raises CostCapExceeded if the LLM
                      result exceeds this.

    Returns:
      {
        "updated_md":    str,
        "response":      str,
        "fixes_applied": list[str],
        "cost_usd":      float,
        "latency_ms":    int,
        "model":         str,
        "provider":      str,
      }

    Raises:
      CostCapExceeded if the call exceeds cost_cap_usd.
      ValueError if the LLM returns malformed JSON.
    """
    if not current_md or not current_md.strip():
        raise ValueError("current_md is empty — nothing to edit")
    if not instruction or not instruction.strip():
        raise ValueError("instruction is empty — nothing to do")

    user_message = _build_user_message(
        current_md=current_md,
        instruction=instruction,
        persona=persona,
        jd=jd,
    )

    messages: list[dict[str, str]] = []
    # Include trimmed chat history so follow-up edits feel coherent.
    if chat_history:
        for turn in chat_history[-6:]:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    router = get_router()
    result: LLMResult = await router.ask(
        provider="anthropic",
        model=QUICK_TWEAK_MODEL,
        system=QUICK_TWEAK_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=QUICK_TWEAK_MAX_TOKENS,
        temperature=QUICK_TWEAK_TEMPERATURE,
        agent_name="resume_edit_assistant.quick_tweak",
    )

    if result.cost_usd > cost_cap_usd:
        raise CostCapExceeded(
            f"quick_tweak cost ${result.cost_usd:.4f} exceeded cap "
            f"${cost_cap_usd:.2f}"
        )

    parsed = _parse_or_repair(result.text)

    # Defensive guards on the parsed shape — if the model returned a
    # malformed dict we coerce to the strict contract rather than crash.
    updated_md = parsed.get("updated_md")
    if not isinstance(updated_md, str) or not updated_md.strip():
        # No edit applied — fall back to current_md so the UI doesn't blank
        # the textarea, and surface the issue in the chat reply.
        updated_md = current_md
        fallback_response = (
            "I couldn't safely apply that without changing more than you asked. "
            "Try a more specific instruction (e.g. 'tighten the second Daraz bullet to one line')."
        )
        response = parsed.get("response") if isinstance(parsed.get("response"), str) else fallback_response
        fixes_applied = parsed.get("fixes_applied") if isinstance(parsed.get("fixes_applied"), list) else []
    else:
        response = parsed.get("response") if isinstance(parsed.get("response"), str) else ""
        fixes_applied = parsed.get("fixes_applied") if isinstance(parsed.get("fixes_applied"), list) else []

    return {
        "updated_md": updated_md,
        "response": response.strip() or "Done.",
        "fixes_applied": [str(f) for f in fixes_applied if f],
        "cost_usd": float(result.cost_usd),
        "latency_ms": int(result.latency_ms),
        "model": result.model,
        "provider": result.provider,
    }


async def rebuild_section(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Phase 3 — placeholder."""
    raise NotImplementedError(
        "rebuild_section is wired in Phase 3. Use quick_tweak for now."
    )


async def full_rebuild(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Phase 3 — placeholder. Will call enqueue_g2_build(force=True)."""
    raise NotImplementedError(
        "full_rebuild is wired in Phase 3. Use the 'Build resume' "
        "control on the Resume tab to enqueue a fresh G2 run."
    )


# ─── Errors ────────────────────────────────────────────────────────────────
class CostCapExceeded(Exception):
    """Raised when an LLM call exceeds its quick-tweak cost cap."""


# ─── Internals ─────────────────────────────────────────────────────────────
def _build_user_message(
    *,
    current_md: str,
    instruction: str,
    persona: Optional[dict[str, Any]],
    jd: Optional[dict[str, Any]],
) -> str:
    """Assemble the single user-turn message.

    We pack persona + JD as compact context blocks the model can scan
    without re-prompting. Order matters: instruction first so the model
    is anchored to the action; then the resume; then the constraints.
    """
    parts: list[str] = []
    parts.append("INSTRUCTION:")
    parts.append(instruction.strip())
    parts.append("")
    parts.append("CURRENT RESUME (markdown — return updated_md as a transformation of this):")
    parts.append("```markdown")
    parts.append(current_md)
    parts.append("```")

    # Job context — keep terse.
    if jd:
        company = (jd.get("company") or "").strip()
        title = (jd.get("title") or "").strip()
        description = (jd.get("description") or "").strip()
        if company or title or description:
            parts.append("")
            parts.append("TARGET JOB:")
            if company:
                parts.append(f"  Company: {company}")
            if title:
                parts.append(f"  Title:   {title}")
            if description:
                # Trim — the resume already encodes the alignment.
                trimmed = description[:2000]
                parts.append(f"  JD (truncated): {trimmed}")

    # Persona constraints — required + boost + banned keywords.
    if persona:
        bank = persona.get("ats_keyword_bank") or {}
        required = bank.get("required") or []
        boost = bank.get("boost") or []
        banned = bank.get("banned") or []
        if required or boost or banned:
            parts.append("")
            parts.append("ATS KEYWORD BANK (do not change these unless explicitly asked):")
            if required:
                parts.append(f"  Required (must remain in resume if present today): {', '.join(map(str, required))[:1000]}")
            if boost:
                parts.append(f"  Boost (prefer these phrasings): {', '.join(map(str, boost))[:1000]}")
            if banned:
                parts.append(f"  Banned (must NOT appear in updated_md): {', '.join(map(str, banned))[:1000]}")

    parts.append("")
    parts.append("Return JSON only. No prose, no fences.")
    return "\n".join(parts)


def _parse_or_repair(text: str) -> dict[str, Any]:
    """Try to parse the LLM output as JSON; fall back to a recovery pass.

    The system prompt asks for strict JSON, but Opus occasionally wraps
    the JSON in ```json fences. _parse_json_loose handles both cleanly.
    On total failure we raise ValueError — the API layer turns that into
    a 502 with a "model returned malformed output" message.
    """
    try:
        return _parse_json_loose(text)
    except Exception as exc:
        logger.warning(
            "resume_edit_assistant: failed to parse LLM output (%d chars): %s",
            len(text or ""),
            exc,
        )
        raise ValueError(
            "Resume editor returned malformed output. Try the instruction again."
        ) from exc
