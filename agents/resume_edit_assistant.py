"""
agents/resume_edit_assistant.py — In-browser resume editor's chat assistant.

The Workspace ships THREE editing modes:

    • quick_tweak()     — single Opus-4.7 call, ~$0.05, ~3s.
                          Surgical edit applied directly to current_md.
                          Default for "tighten this", "rephrase that bullet",
                          "swap MM/YYYY for YYYY", etc.
    • rebuild_section() — Synchronous mini-graph (writer→critic→polish) over
                          ONE H2 section. The rest of the resume is preserved
                          verbatim by markdown-splice. ~$0.40-0.60, ~30-60s.
                          Runs in-endpoint with a 60s timeout — if it overruns
                          we fail loud rather than silently regress to a worse
                          shape.
    • full_rebuild()    — Full G2 from scratch using the same job + persona.
                          ~$1, ~3-5 min. Wired through enqueue_g2_build with
                          force=True; this module exposes nothing for it
                          beyond the docstring (the workspace API calls the
                          queue directly).

`extract_section()` is the heuristic markdown splitter both rebuild_section
and the workspace UI use to find H2 boundaries.

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

import asyncio
import logging
import re
from typing import Any, Optional

from agents.llm_router import LLMResult, _parse_json_loose, get_router

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────
# Defensive ceiling. A normal quick tweak on a 2-page resume is ~$0.04-0.06.
QUICK_TWEAK_COST_CAP_USD = 0.10
QUICK_TWEAK_MODEL = "claude-opus-4-7"
QUICK_TWEAK_MAX_TOKENS = 4096
QUICK_TWEAK_TEMPERATURE = 0.2  # surgical, not creative

# rebuild_section: $0.60 ceiling across writer + critic + polish.
# Typical Opus pass on one H2 section runs ~$0.10-0.15; budget allows
# headroom for a critic retry without blowing the cap.
REBUILD_SECTION_COST_CAP_USD = 0.60
REBUILD_SECTION_TIMEOUT_S = 60
# 2026-05-12 right-sizing: writer still gets Opus (load-bearing rewrite work),
# but critic + polish are JSON-classification and surgical-edit tasks where
# Sonnet matches. Cuts rebuild_section cost from ~$0.60 ceiling to ~$0.25.
# See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §40.
REBUILD_SECTION_WRITER_MODEL = "claude-opus-4-7"
REBUILD_SECTION_CRITIC_MODEL = "claude-sonnet-4-6"
REBUILD_SECTION_POLISH_MODEL = "claude-sonnet-4-6"

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


# ─── Section heuristics ───────────────────────────────────────────────────
# Match an H2 heading line. Captures `#` count (we only fire on exactly 2
# hashes) and the heading text. Resume markdown convention:
#   ## Experience
#   ## Skills
#   ## Education
# Variants we tolerate: leading whitespace, trailing whitespace, a single
# inline emoji or pill character, leading bullet ":". We DON'T tolerate
# H1 (the candidate's name lives there) or deeper headings (those are
# subsections inside an H2 like "## Experience" → "### Daraz").
_H2_RE = re.compile(r"^\s{0,3}##\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _normalize_section_name(name: str) -> str:
    """Lowercase + strip + collapse whitespace. Used for fuzzy H2 match."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip().lower()


def extract_section(
    resume_md: str,
    section_name: str,
) -> tuple[str, str, str]:
    """Split a resume markdown into (before, section, after) on an H2 boundary.

    `section_name` is matched case-insensitively against H2 heading text.
    Match strategy, in order:
      1. exact (case-insensitive) heading text match
      2. heading text starts with section_name
      3. section_name is a substring of heading text
      4. raise ValueError — we'd rather fail loud than splice the wrong block

    The returned `section_md` includes the heading line itself, ending
    just before the next H2 (or the end of the document). `before_md`
    and `after_md` together with `section_md` recompose the original
    resume — concatenating them returns the input string verbatim.

    This is a pure heuristic; it doesn't understand markdown semantics
    beyond the H2 line pattern. That's fine for resumes where H2 is the
    canonical section boundary. For non-conforming markdown (single H1
    only, or all bullets) the caller should fall back to quick_tweak.
    """
    if not resume_md:
        raise ValueError("resume_md is empty")
    if not section_name or not section_name.strip():
        raise ValueError("section_name is empty")

    target = _normalize_section_name(section_name)
    matches: list[tuple[int, int, str]] = []  # (start, end_of_line, title)
    for m in _H2_RE.finditer(resume_md):
        title = (m.group("title") or "").strip()
        title_norm = _normalize_section_name(title)
        # Strip a few common decorations from the title before comparing.
        title_clean = re.sub(r"[^\w\s]+", " ", title_norm).strip()
        title_clean = re.sub(r"\s+", " ", title_clean)
        matches.append((m.start(), m.end(), title_clean or title_norm))

    if not matches:
        raise ValueError(
            f"no H2 sections found in resume_md "
            f"(needed to splice {section_name!r}; resume has {len(resume_md)} chars)"
        )

    # Match strategies, in priority order.
    target_clean = re.sub(r"[^\w\s]+", " ", target).strip()
    target_clean = re.sub(r"\s+", " ", target_clean)

    chosen_idx: Optional[int] = None
    for i, (_, _, title_clean) in enumerate(matches):
        if title_clean == target_clean:
            chosen_idx = i
            break
    if chosen_idx is None:
        for i, (_, _, title_clean) in enumerate(matches):
            if title_clean.startswith(target_clean):
                chosen_idx = i
                break
    if chosen_idx is None:
        for i, (_, _, title_clean) in enumerate(matches):
            if target_clean in title_clean:
                chosen_idx = i
                break
    if chosen_idx is None:
        titles = [t for _, _, t in matches]
        raise ValueError(
            f"section {section_name!r} not found in resume; "
            f"available H2 sections: {titles}"
        )

    section_start = matches[chosen_idx][0]
    if chosen_idx + 1 < len(matches):
        section_end = matches[chosen_idx + 1][0]
    else:
        section_end = len(resume_md)

    before_md = resume_md[:section_start]
    section_md = resume_md[section_start:section_end]
    after_md = resume_md[section_end:]
    return before_md, section_md, after_md


def list_h2_sections(resume_md: str) -> list[str]:
    """Return the visible H2 heading text for each section, in order.

    Used by the workspace UI's section-picker radio list. Empty list if
    the resume doesn't have H2 boundaries.
    """
    if not resume_md:
        return []
    out: list[str] = []
    for m in _H2_RE.finditer(resume_md):
        title = (m.group("title") or "").strip()
        if title:
            out.append(title)
    return out


# ─── rebuild_section ─────────────────────────────────────────────────────
REBUILD_SECTION_WRITER_SYSTEM = """\
You are a senior resume editor rebuilding ONE section of a candidate's resume.

You are given:
  • The full resume markdown for context
  • The CURRENT version of the section you're rebuilding (heading + body)
  • The candidate's edit instruction
  • Optional persona / job context

Your job: produce a REPLACEMENT for that single section. The heading line
itself MUST be preserved exactly (same level, same text — markdown
heading equality matters for downstream splicing). Below the heading you
may rewrite freely, but ONLY within these rules:

ABSOLUTE RULES
1. Output JUST the section — no preceding sections, no following sections, no preamble, no commentary. Start with the original `## Heading` line.
2. Preserve markdown shape: bullets stay bullets, role-line bolding stays bolded, dates stay in the same format. The user's downstream tooling assumes consistent formatting.
3. Never invent facts. If the candidate's instruction asks for a metric that isn't anywhere in the rest of the resume, you MUST NOT add it. Instead emit a single-line bullet at the top: `> needs_user_input: <what's missing>` and otherwise leave the section unchanged.
4. Keep the candidate's voice. Don't switch tense, don't Americanise British spelling, don't introduce buzzwords ("synergised", "leveraged", "spearheaded") unless the user asked for that register.
5. Respect persona banned-keyword list. Banned words must NOT appear.
6. Do NOT touch personal details — name, email, phone, links, location — unless the section literally IS the contact block.
7. Length discipline: aim for parity with the input section. A 6-bullet block stays 5-7 bullets, not 12. A 3-line summary stays 3-4 lines.

OUTPUT FORMAT
Return STRICT JSON, no markdown fences, no prose:
{
  "rebuilt_section_md":  "<the rewritten section, starting with `## Heading`, ending with the last line BEFORE the next section>",
  "response":            "<1-3 sentences in plain English: what changed, why, and any caveats>",
  "fixes_applied":       ["<short bullet>", "<short bullet>"]
}

If you cannot safely rebuild the section (missing facts, out-of-scope instruction, or the instruction contradicts the absolute rules above), return:
{
  "rebuilt_section_md":  "<the input section verbatim>",
  "response":            "<one sentence explaining why you didn't make the change>",
  "fixes_applied":       ["needs_user_input: <what's missing>"]
}
"""

REBUILD_SECTION_CRITIC_SYSTEM = """\
You are an ATS + recruiter-screening critic reviewing ONE rebuilt resume section.

You are given:
  • The candidate's full resume (the rest is fixed; only this section was rebuilt)
  • The section as it stood BEFORE the rebuild
  • The section as it stands AFTER the rebuild
  • The job description and edit intent

Score the rebuild and surface any issues the polish step should fix.

OUTPUT FORMAT (strict JSON, no fences, no prose):
{
  "score":          0-100,
  "improvements":   ["concrete improvement 1", "concrete improvement 2"],
  "regressions":    ["concrete regression 1 (if any)"],
  "polish_targets": ["<one-line instruction the polisher should apply>", ...]
}
"""

REBUILD_SECTION_POLISH_SYSTEM = """\
You are the final-pass polisher on a resume section that was just rebuilt and critiqued.

You will receive:
  • The candidate's current rebuilt section (heading included)
  • The critic's polish_targets (a short list of polish instructions)
  • The full resume for context

Apply the polish_targets and emit the FINAL section. The heading line
must be preserved exactly. Do not introduce new facts. Output STRICT
JSON, no markdown fences, no prose:
{
  "final_section_md":  "<the polished section, starting with `## Heading`>",
  "response":          "<1-3 sentences summarising the rebuild end-to-end>",
  "fixes_applied":     ["<short bullet>", "<short bullet>"]
}
"""


async def rebuild_section(
    *,
    current_md: str,
    section: str,
    edit_intent: str,
    persona: Optional[dict[str, Any]] = None,
    jd: Optional[dict[str, Any]] = None,
    cost_cap_usd: float = REBUILD_SECTION_COST_CAP_USD,
    timeout_s: int = REBUILD_SECTION_TIMEOUT_S,
) -> dict[str, Any]:
    """Rebuild ONE H2 section synchronously via writer → critic → polish.

    Strategy (kept simple to ship cleanly in V1):
      1. extract_section splits current_md into (before, section, after).
      2. Writer rewrites the section in isolation (sees the rest as
         context, but emits only the section).
      3. Critic scores the rewrite.
      4. Polisher applies the critic's polish_targets and emits final.
      5. We splice (before + final + after) back together.

    Total LLM calls: 3. Total wall-clock: typically 30-60s. Cost: $0.40-
    0.60 on Opus 4.7 across all three (writer is the long pole).

    Cost cap: if cumulative cost exceeds `cost_cap_usd` we raise
    CostCapExceeded — the API layer surfaces a 429.

    Timeout: the whole pipeline is wrapped in `asyncio.wait_for(timeout_s)`
    and a TimeoutError bubbles up. The endpoint turns that into a 504-ish
    error with a clear message.

    Output shape (matches quick_tweak so the UI doesn't branch):
      {
        "updated_md":     <full resume after splice>,
        "response":       <1-3 sentence chat-style explanation>,
        "fixes_applied":  list[str],
        "cost_usd":       float (cumulative across the 3 calls),
        "latency_ms":     int  (cumulative),
        "model":          str,
        "provider":       str,
        "section":        str  (echoed back; helps UI confirm),
      }
    """
    if not current_md or not current_md.strip():
        raise ValueError("current_md is empty — nothing to rebuild")
    if not section or not section.strip():
        raise ValueError("section is empty — pick a heading to rebuild")
    if not edit_intent or not edit_intent.strip():
        raise ValueError("edit_intent is empty — describe what to change")

    async def _run() -> dict[str, Any]:
        before_md, section_md, after_md = extract_section(current_md, section)

        # ── 1. WRITER ───────────────────────────────────────────────────
        writer_user = _build_rebuild_user_message(
            full_resume_md=current_md,
            section_md=section_md,
            edit_intent=edit_intent,
            persona=persona,
            jd=jd,
            mode="writer",
        )
        router = get_router()
        writer_result: LLMResult = await router.ask(
            provider="anthropic",
            model=REBUILD_SECTION_WRITER_MODEL,
            system=REBUILD_SECTION_WRITER_SYSTEM,
            messages=[{"role": "user", "content": writer_user}],
            max_tokens=4096,
            temperature=0.25,
            agent_name="resume_edit_assistant.rebuild_section.writer",
        )
        cumulative_cost = float(writer_result.cost_usd or 0.0)
        cumulative_latency = int(writer_result.latency_ms or 0)
        if cumulative_cost > cost_cap_usd:
            raise CostCapExceeded(
                f"rebuild_section writer cost ${cumulative_cost:.4f} "
                f"already exceeds cap ${cost_cap_usd:.2f}"
            )
        writer_parsed = _parse_or_repair(writer_result.text)
        rebuilt = writer_parsed.get("rebuilt_section_md")
        if not isinstance(rebuilt, str) or not rebuilt.strip():
            # Writer punted — return verbatim.
            rebuilt = section_md
        rebuilt_response = (
            writer_parsed.get("response")
            if isinstance(writer_parsed.get("response"), str)
            else ""
        )
        rebuilt_fixes_raw = writer_parsed.get("fixes_applied")
        rebuilt_fixes: list[str] = (
            [str(f) for f in rebuilt_fixes_raw if f]
            if isinstance(rebuilt_fixes_raw, list)
            else []
        )

        # ── 2. CRITIC ───────────────────────────────────────────────────
        critic_user = _build_rebuild_user_message(
            full_resume_md=current_md,
            section_md=section_md,
            edit_intent=edit_intent,
            persona=persona,
            jd=jd,
            mode="critic",
            rebuilt_section_md=rebuilt,
        )
        critic_result: LLMResult = await router.ask(
            provider="anthropic",
            model=REBUILD_SECTION_CRITIC_MODEL,
            system=REBUILD_SECTION_CRITIC_SYSTEM,
            messages=[{"role": "user", "content": critic_user}],
            max_tokens=1500,
            temperature=0.1,
            agent_name="resume_edit_assistant.rebuild_section.critic",
        )
        cumulative_cost += float(critic_result.cost_usd or 0.0)
        cumulative_latency += int(critic_result.latency_ms or 0)
        if cumulative_cost > cost_cap_usd:
            # Bail — we have a writer draft, no polish. Splice and return
            # rather than throwing; user sees the partial result with a
            # caveat in `response`.
            logger.warning(
                "rebuild_section: cost cap hit after critic (skipping polish)"
            )
            updated_md = before_md + rebuilt + after_md
            response = rebuilt_response or (
                "Rebuilt the section, but skipped the final polish pass to "
                "stay under the cost cap. Review carefully before saving."
            )
            return {
                "updated_md": updated_md,
                "response": response.strip(),
                "fixes_applied": rebuilt_fixes,
                "cost_usd": float(cumulative_cost),
                "latency_ms": int(cumulative_latency),
                "model": writer_result.model,
                "provider": writer_result.provider,
                "section": section,
            }

        try:
            critic_parsed = _parse_json_loose(critic_result.text)
        except Exception:
            logger.warning("rebuild_section critic JSON parse failed; skipping polish")
            critic_parsed = {}
        polish_targets_raw = critic_parsed.get("polish_targets") if isinstance(critic_parsed, dict) else None
        polish_targets: list[str] = (
            [str(t) for t in polish_targets_raw if t]
            if isinstance(polish_targets_raw, list)
            else []
        )

        # ── 3. POLISH ───────────────────────────────────────────────────
        # If the critic returned no targets, skip polish entirely — the
        # writer's output is the result. Saves $0.10-0.15 per call.
        final_section_md = rebuilt
        final_response = rebuilt_response
        final_fixes = list(rebuilt_fixes)
        polish_model = writer_result.model
        polish_provider = writer_result.provider

        if polish_targets:
            polish_user = _build_rebuild_user_message(
                full_resume_md=current_md,
                section_md=section_md,
                edit_intent=edit_intent,
                persona=persona,
                jd=jd,
                mode="polish",
                rebuilt_section_md=rebuilt,
                polish_targets=polish_targets,
            )
            polish_result: LLMResult = await router.ask(
                provider="anthropic",
                model=REBUILD_SECTION_POLISH_MODEL,
                system=REBUILD_SECTION_POLISH_SYSTEM,
                messages=[{"role": "user", "content": polish_user}],
                max_tokens=4096,
                temperature=0.2,
                agent_name="resume_edit_assistant.rebuild_section.polish",
            )
            cumulative_cost += float(polish_result.cost_usd or 0.0)
            cumulative_latency += int(polish_result.latency_ms or 0)
            if cumulative_cost > cost_cap_usd:
                raise CostCapExceeded(
                    f"rebuild_section cumulative cost ${cumulative_cost:.4f} "
                    f"exceeded cap ${cost_cap_usd:.2f}"
                )
            polish_parsed = _parse_or_repair(polish_result.text)
            polished = polish_parsed.get("final_section_md")
            if isinstance(polished, str) and polished.strip():
                final_section_md = polished
            polish_response = polish_parsed.get("response")
            if isinstance(polish_response, str) and polish_response.strip():
                final_response = polish_response
            polish_fixes = polish_parsed.get("fixes_applied")
            if isinstance(polish_fixes, list):
                # Merge with writer's fixes, dedup on lowercase prefix.
                seen = {f.strip().lower()[:80] for f in final_fixes}
                for f in polish_fixes:
                    if not f:
                        continue
                    key = str(f).strip().lower()[:80]
                    if key and key not in seen:
                        seen.add(key)
                        final_fixes.append(str(f))
            polish_model = polish_result.model
            polish_provider = polish_result.provider

        # ── 4. SPLICE ───────────────────────────────────────────────────
        # Defensive: if the polished output dropped the H2 heading (a
        # common LLM failure mode), reattach it. Splice integrity matters
        # more than the model's freedom here.
        if not final_section_md.lstrip().startswith("##"):
            # Pull the original heading line and prepend it.
            heading_match = _H2_RE.search(section_md)
            if heading_match is not None:
                heading_line = section_md[heading_match.start():heading_match.end()]
                final_section_md = heading_line + "\n\n" + final_section_md.lstrip()

        # Ensure section block ends with a newline so concatenation
        # doesn't fuse it to the next H2.
        if not final_section_md.endswith("\n"):
            final_section_md += "\n"
        updated_md = before_md + final_section_md + after_md

        return {
            "updated_md": updated_md,
            "response": (final_response or "Section rebuilt.").strip(),
            "fixes_applied": final_fixes,
            "cost_usd": float(cumulative_cost),
            "latency_ms": int(cumulative_latency),
            "model": polish_model,
            "provider": polish_provider,
            "section": section,
        }

    # Hard wall-clock timeout. If the pipeline overruns the budget the
    # endpoint surfaces a 504-ish error rather than tying up the worker.
    try:
        return await asyncio.wait_for(_run(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"rebuild_section exceeded {timeout_s}s timeout — "
            f"try a tighter section or fall back to Quick tweak"
        ) from exc


async def full_rebuild(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Full G2 rebuild from scratch.

    This module exposes nothing beyond this docstring. The workspace API
    routes /workspace/{id}/full-rebuild straight to enqueue_g2_build with
    `force=True` (changes the idempotency key), then the existing G2
    graph runs cold-start. Polling and result handling reuse the same
    machinery as the "Build resume" button.
    """
    raise NotImplementedError(
        "full_rebuild dispatches via api.queue.enqueue_g2_build(force=True) — "
        "call that directly from the workspace endpoint, no agent function needed."
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


def _build_rebuild_user_message(
    *,
    full_resume_md: str,
    section_md: str,
    edit_intent: str,
    persona: Optional[dict[str, Any]],
    jd: Optional[dict[str, Any]],
    mode: str,                                           # "writer" | "critic" | "polish"
    rebuilt_section_md: Optional[str] = None,
    polish_targets: Optional[list[str]] = None,
) -> str:
    """Assemble the user-turn message for one of the three rebuild stages.

    Same context skeleton across all three (full resume, current section,
    edit intent, persona, job) so the models all reason against the same
    facts; only the prompt-specific extras differ:

      • writer  — no extras
      • critic  — also gets `rebuilt_section_md` (the writer's draft)
      • polish  — also gets `rebuilt_section_md` AND `polish_targets`
    """
    parts: list[str] = []
    parts.append("EDIT INTENT (the human instruction to act on):")
    parts.append(edit_intent.strip())
    parts.append("")
    parts.append("FULL RESUME (markdown — the rest of the document is FIXED, do not modify it):")
    parts.append("```markdown")
    parts.append(full_resume_md)
    parts.append("```")
    parts.append("")
    parts.append("CURRENT SECTION (the block to rebuild — heading + body):")
    parts.append("```markdown")
    parts.append(section_md)
    parts.append("```")

    if mode in ("critic", "polish") and rebuilt_section_md:
        parts.append("")
        parts.append("REBUILT SECTION (the writer's first-pass output):")
        parts.append("```markdown")
        parts.append(rebuilt_section_md)
        parts.append("```")

    if mode == "polish" and polish_targets:
        parts.append("")
        parts.append("POLISH TARGETS (apply these one-line instructions, in order):")
        for t in polish_targets[:8]:
            parts.append(f"  • {t}")

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
                trimmed = description[:2000]
                parts.append(f"  JD (truncated): {trimmed}")

    if persona:
        bank = persona.get("ats_keyword_bank") or {}
        required = bank.get("required") or []
        boost = bank.get("boost") or []
        banned = bank.get("banned") or []
        if required or boost or banned:
            parts.append("")
            parts.append("ATS KEYWORD BANK:")
            if required:
                parts.append(f"  Required: {', '.join(map(str, required))[:1000]}")
            if boost:
                parts.append(f"  Boost: {', '.join(map(str, boost))[:1000]}")
            if banned:
                parts.append(f"  Banned (must NOT appear): {', '.join(map(str, banned))[:1000]}")

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
