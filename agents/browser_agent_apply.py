"""
agents/browser_agent_apply.py — AI-driven browser applier (G10-B).

Why this exists
---------------
`agents/linkedin_easy_apply.py` drives LinkedIn with hardcoded CSS selectors
from `ats_patterns/linkedin.yml`. That breaks whenever LinkedIn ships a UI
change, and it only works on LinkedIn Easy Apply.

This module replaces the selector layer with an LLM browser agent
(browser-use). The agent reads the page semantically, so it:
  - survives DOM/layout changes (no selectors to rot)
  - works on ANY application form — LinkedIn Easy Apply, Greenhouse, Lever,
    Workday, Ashby, or a company's own career page
  - reuses the operator's real Chrome profile, so an already-logged-in
    LinkedIn session is used directly (no li_at cookie extraction, and far
    less bot-flagged than a fresh headless browser)

Everything around it is unchanged: G5 picks the roles, the geo/role filters
decide eligibility, G2 writes the tailored resume, and the queue table tracks
state. Only *how the browser is driven* differs.

Install (not bundled — this is an optional backend)
---------------------------------------------------
    pip install browser-use
    playwright install chromium

Usage
-----
    from agents.browser_agent_apply import apply_with_agent

    result = await apply_with_agent(
        job={"title": ..., "company": ..., "apply_url": ...},
        answers=[{"field_label": "Years of experience", "answer": "14"}],
        resume_path="/abs/path/resume.pdf",
        submit=False,       # False = fill and STOP at review (default)
    )

Safety
------
`submit` defaults to False. When False the task prompt instructs the agent to
stop at the review step and the submit verbs are explicitly forbidden. Passing
submit=True is the only way an application is sent, and callers must obtain
human approval first (api/linkedin_apply.py enforces `confirmed=true`).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Model used to drive the browser. Routed through OpenRouter so the whole
# system runs on one key. Vision-capable models do markedly better at reading
# application forms, so the default is a multimodal one.
_AGENT_MODEL = os.environ.get("BROWSER_AGENT_MODEL", "google/gemini-2.5-flash")
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Hard ceiling on agent steps. An application form is a handful of pages; if
# the agent is still going after this it is lost, and we stop rather than let
# it wander a logged-in LinkedIn session.
_MAX_STEPS = int(os.environ.get("BROWSER_AGENT_MAX_STEPS", "40"))


@dataclass
class AgentApplyResult:
    status: str                      # 'filled_paused' | 'submitted' | 'error'
    steps_taken: int = 0
    final_url: Optional[str] = None
    agent_summary: Optional[str] = None
    error: Optional[str] = None
    history: list[dict[str, Any]] = field(default_factory=list)


def _build_llm():
    """Construct the browser-use chat model, pointed at OpenRouter.

    Imported lazily so this module imports cleanly when browser-use is not
    installed — the rest of the codebase must not hard-depend on it.
    """
    try:
        from browser_use import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "browser-use is not installed. Run:\n"
            "    pip install browser-use\n"
            "    playwright install chromium"
        ) from exc

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required to drive the browser agent.")

    return ChatOpenAI(
        model=_AGENT_MODEL,
        api_key=api_key,
        base_url=_OPENROUTER_BASE,
    )


def _build_browser(use_system_chrome: bool = True):
    """Attach to the operator's real Chrome profile when possible.

    Reusing the live profile means the LinkedIn session is already
    authenticated, which removes the li_at cookie handling entirely and
    presents as an ordinary browser rather than a fresh automation profile.

    Falls back to a managed browser when the system profile can't be opened
    (Chrome not installed, or already running with the profile locked).
    """
    try:
        from browser_use import Browser
    except ImportError as exc:
        raise RuntimeError("browser-use is not installed.") from exc

    if use_system_chrome:
        try:
            return Browser.from_system_chrome()
        except Exception as exc:
            logger.warning(
                "browser_agent_apply: could not attach to system Chrome (%s). "
                "Falling back to a managed browser — you may need to log in.",
                exc,
            )
    return Browser()


def _build_task(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    resume_path: Optional[str],
    submit: bool,
) -> str:
    """Compose the natural-language task for the agent.

    The prompt is the safety boundary in this design: when submit=False it
    forbids the submit action outright rather than relying on the caller to
    stop in time.
    """
    answer_lines = "\n".join(
        f'  - When asked "{a.get("field_label")}": answer exactly "{a.get("answer")}"'
        for a in (answers or [])
        if a.get("field_label") and a.get("answer") not in (None, "")
    ) or "  (no pre-generated answers — use the resume and profile facts below)"

    resume_line = (
        f"- Upload this file wherever a resume/CV is requested: {resume_path}"
        if resume_path else
        "- If a resume upload is required and no file is provided, STOP and report that."
    )

    if submit:
        terminal = (
            "6. Submit the application. After submitting, confirm the success "
            "state (a confirmation message, or the button becoming 'Applied') "
            "and report exactly what you saw."
        )
        guard = (
            "You ARE authorized to submit this one application. The human has "
            "already reviewed and approved the answers above."
        )
    else:
        terminal = (
            "6. When you reach the final review step, STOP. Report every field "
            "and its filled value, and the exact label of the submit button."
        )
        guard = (
            "CRITICAL: You must NOT submit. Do not click 'Submit application', "
            "'Submit', or 'Send application' under any circumstance. Fill the "
            "form and stop at review. Submitting is a failure of this task."
        )

    return f"""Apply for this job on behalf of the candidate.

ROLE:    {job.get('title', '')}
COMPANY: {job.get('company', '')}
URL:     {job.get('apply_url', '')}

{guard}

STEPS:
1. Navigate to the URL above.
2. Find and start the application (an "Easy Apply" or "Apply" button). If the
   posting is closed, already applied to, or redirects to an external site you
   cannot complete, STOP and report that instead of improvising.
3. Work through every step of the form.
4. {resume_line.lstrip('- ')}
5. Answer questions using these values. Match on the MEANING of the question,
   not exact wording:
{answer_lines}
{terminal}

RULES:
- Never invent facts about the candidate. If a required question is not covered
  above and you cannot answer it from the resume, STOP and report the question.
- Never accept a different job, follow promotional links, or navigate away from
  this application.
- If you hit a CAPTCHA or identity check, STOP and report it. Do not attempt it.
"""


async def apply_with_agent(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    resume_path: Optional[str] = None,
    submit: bool = False,
    use_system_chrome: bool = True,
    max_steps: int = _MAX_STEPS,
) -> AgentApplyResult:
    """Drive one job application with an LLM browser agent.

    Works against any ATS, not just LinkedIn — the agent reads the page rather
    than matching selectors.

    `submit` defaults to False (fill and stop at review). Callers must have
    human approval before passing True.
    """
    try:
        from browser_use import Agent
    except ImportError as exc:
        return AgentApplyResult(
            status="error",
            error=(
                "browser-use not installed. Run: pip install browser-use && "
                "playwright install chromium"
            ),
        )

    try:
        llm = _build_llm()
        browser = _build_browser(use_system_chrome=use_system_chrome)
    except RuntimeError as exc:
        return AgentApplyResult(status="error", error=str(exc))

    task = _build_task(job=job, answers=answers, resume_path=resume_path, submit=submit)

    logger.info(
        "browser_agent_apply: starting %s for %s @ %s (max_steps=%d)",
        "SUBMIT" if submit else "fill-only",
        job.get("title"), job.get("company"), max_steps,
    )

    agent = Agent(task=task, llm=llm, browser=browser)
    try:
        history = await agent.run(max_steps=max_steps)
    except Exception as exc:
        logger.error("browser_agent_apply: agent run failed: %r", exc)
        return AgentApplyResult(status="error", error=f"{type(exc).__name__}: {exc}"[:400])

    # browser-use's history object exposes helpers across versions; probe
    # defensively rather than assuming one shape.
    summary = None
    final_url = None
    steps = 0
    try:
        summary = history.final_result()
    except Exception:
        pass
    try:
        urls = history.urls()
        final_url = urls[-1] if urls else None
    except Exception:
        pass
    try:
        steps = len(history.model_actions())
    except Exception:
        pass

    return AgentApplyResult(
        status="submitted" if submit else "filled_paused",
        steps_taken=steps,
        final_url=final_url,
        agent_summary=str(summary)[:2000] if summary else None,
    )
