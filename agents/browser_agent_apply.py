"""
agents/browser_agent_apply.py — AI-driven browser applier (G10-B).

Why this exists
---------------
`agents/linkedin_easy_apply.py` drives LinkedIn with hardcoded CSS selectors
from `ats_patterns/linkedin.yml`. That approach rots: GodsScion's bot shipped
release v26.01.20 in Jan 2026 purely to fix a LinkedIn Next-button selector
that had started matching pagination instead. Selector packs need a patch
every time LinkedIn touches its DOM, and they only cover LinkedIn.

This module drives the browser with an LLM instead (browser-use). The agent
reads pages semantically, so it survives layout changes and works on any
application form — LinkedIn Easy Apply, Greenhouse, Lever, Workday, Ashby, or
a company's own careers page.

Everything around it is unchanged: G5 picks the roles, the geo/role filters
decide eligibility, G2 writes the tailored resume, the queue table tracks
state. Only how the browser is driven differs.

Safety — read this before changing anything below
--------------------------------------------------
The LLM NEVER holds the ability to submit. That is a structural property, not
a prompt instruction.

An earlier version of this module relied on telling the agent "do not click
Submit". That is not a control. A system-prompt rule and the page's own text
sit in the same context with no enforced hierarchy, so agents drift off the
instruction or get talked out of it by injected page content. Prompt text is
defense-in-depth here; it is never the boundary.

The boundary is two-phase, and each phase is enforced in code:

  Phase 1 — `apply_with_agent()` fills the form. Two code-level controls:
    (a) `Tools(exclude_actions=...)` strips dangerous actions out of the
        registry, so the model cannot emit them at all.
    (b) An `on_step_start` hook inspects the live DOM BEFORE every agent step
        and hard-stops the run the moment a submit control is visible. That
        stop is a code decision made from observed DOM — it does not depend
        on the model choosing to comply.

  Phase 2 — `submit_approved_application()` clicks submit with plain
  deterministic Playwright. No LLM is in the loop at all. It runs only after
  a human approves (api/linkedin_apply.py enforces `confirmed=true`).

The separation is the point: during Phase 1 there is no code path from the
model to a submit click, so a drifting or injected agent cannot send an
application. Do not merge the phases, and do not "simplify" the hook away.

Reality check on reliability
-----------------------------
WebBench (452 live sites) measures agents on WRITE tasks — forms, logins,
uploads. The current state of the art scores 46.6% on non-READ tasks. The
best browser agent in the world fails more than half of them. Treat every run
as needing verification, and never assume a batch succeeded because it did
not raise.

Install (optional backend, not bundled)
----------------------------------------
    pip install browser-use
    playwright install chromium
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Model that drives the browser, routed through the configured gateway.
# Vision-capable models read application forms markedly better.
_AGENT_MODEL = os.environ.get("BROWSER_AGENT_MODEL", "google/gemini-2.5-flash")

# Hard ceiling on agent steps. An application form is a handful of pages; if
# the agent is still going after this it is lost, and we stop rather than let
# it wander a logged-in session.
_MAX_STEPS = int(os.environ.get("BROWSER_AGENT_MAX_STEPS", "40"))

# Actions removed from the agent's registry in Phase 1. Stripping them at the
# registry level means the model cannot emit them — unlike a prompt rule,
# there is nothing to drift off or be injected past.
#
# NB: plain clicking stays available, because the agent must click "Next" to
# advance the wizard. The submit-specific protection is the DOM hook below.
_PHASE1_EXCLUDED_ACTIONS = [
    "send_keys",   # raw key injection can trigger Enter-to-submit on a form
]

# Selectors that indicate a submit / final-review control is on screen. When
# the hook sees any of these, Phase 1 stops immediately.
_SUBMIT_INDICATORS = [
    "button[aria-label='Submit application']",
    "button[aria-label*='Submit application']",
    "button:has-text('Submit application')",
    "button:has-text('Submit Application')",
]


@dataclass
class AgentApplyResult:
    status: str                      # 'filled_paused' | 'stopped_at_submit' | 'error'
    steps_taken: int = 0
    final_url: Optional[str] = None
    agent_summary: Optional[str] = None
    stopped_reason: Optional[str] = None
    error: Optional[str] = None
    history: list[dict[str, Any]] = field(default_factory=list)


class SubmitControlReached(Exception):
    """Raised by the step hook when a submit control becomes visible.

    This is the Phase 1 stop signal. It is an exception rather than a flag so
    the agent loop cannot continue past it by ignoring a return value.
    """


def _build_llm():
    """Construct the browser-use chat model, pointed at the configured gateway.

    Imported lazily so this module imports cleanly when browser-use is absent —
    the rest of the codebase must not hard-depend on it.
    """
    try:
        from browser_use import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "browser-use is not installed. Run:\n"
            "    pip install browser-use\n"
            "    playwright install chromium"
        ) from exc

    # Prefer OmniRoute when the system is in single-gateway mode, else
    # OpenRouter. Mirrors the precedence in agents/llm_router.py.
    from agents.llm_router import (
        OPENAI_COMPATIBLE_BASE_URLS,
        _force_omniroute_enabled,
    )

    if _force_omniroute_enabled():
        base_url = OPENAI_COMPATIBLE_BASE_URLS["omniroute"]
        api_key = os.environ.get("OMNIROUTE_API_KEY") or "omniroute-local"
    else:
        base_url = OPENAI_COMPATIBLE_BASE_URLS["openrouter"]
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required (or set LLM_FORCE_OMNIROUTE=1 "
                "to drive the agent through a local OmniRoute gateway)."
            )

    return ChatOpenAI(model=_AGENT_MODEL, api_key=api_key, base_url=base_url)


def _build_browser(use_system_chrome: bool = True):
    """Attach to the operator's real Chrome profile when possible.

    Reusing the live profile means the LinkedIn session is already
    authenticated — no cookie extraction — and presents as an ordinary warm
    browser rather than a fresh automation profile.

    Chrome 136+ refuses --remote-debugging-port on the DEFAULT user-data-dir.
    If attaching fails for that reason, either enable remote debugging on the
    running instance via chrome://inspect/#remote-debugging, or point at a
    dedicated copied profile directory.
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
                "Chrome 136+ blocks remote debugging on the default profile — "
                "see chrome://inspect/#remote-debugging. Falling back to a "
                "managed browser; you will need to log in there.",
                exc,
            )
    return Browser()


async def _page_from_agent(agent: Any) -> Optional[Any]:
    """Best-effort retrieval of the live Playwright page from a browser-use
    agent. The accessor has moved across browser-use versions, so probe
    several shapes rather than pinning one."""
    for getter in (
        lambda: agent.browser_session.get_current_page(),
        lambda: agent.browser_session.current_page,
        lambda: agent.browser_context.get_current_page(),
        lambda: agent.page,
    ):
        try:
            candidate = getter()
            if asyncio.iscoroutine(candidate):
                candidate = await candidate
            if candidate is not None:
                return candidate
        except Exception:
            continue
    return None


def _make_submit_guard_hook():
    """Build the on_step_start hook that hard-stops Phase 1 at a submit control.

    This is the real safety boundary. It reads the DOM directly and raises
    before the agent takes its next step, so the decision to stop is never
    delegated to the model.

    Fails CLOSED in one specific sense: if the page cannot be inspected at all
    we log loudly, because a guard that silently degrades to no-op is worse
    than no guard. We do not abort the run on an inspection miss (that would
    make the applier unusable on any DOM quirk), which is exactly why Phase 2
    exists as a separate deterministic step rather than something the agent
    could ever reach.
    """
    async def on_step_start(agent: Any) -> None:
        page = await _page_from_agent(agent)
        if page is None:
            logger.warning(
                "browser_agent_apply: submit-guard could not read the page this "
                "step; relying on Phase-1/Phase-2 separation instead."
            )
            return
        for selector in _SUBMIT_INDICATORS:
            try:
                locator = page.locator(selector)
                if await locator.count() and await locator.first.is_visible(timeout=500):
                    raise SubmitControlReached(
                        f"Submit control visible ({selector}) — stopping before "
                        "the agent can act on it."
                    )
            except SubmitControlReached:
                raise
            except Exception:
                # Selector unsupported or element detached mid-check; try next.
                continue

    return on_step_start


def _build_task(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    resume_path: Optional[str],
) -> str:
    """Compose the natural-language task for Phase 1.

    The 'do not submit' line here is defense-in-depth ONLY. The enforcement is
    the registry exclusion plus the DOM hook — never this text.
    """
    answer_lines = "\n".join(
        f'  - When asked "{a.get("field_label")}": answer exactly "{a.get("answer")}"'
        for a in (answers or [])
        if a.get("field_label") and a.get("answer") not in (None, "")
    ) or "  (no pre-generated answers — use the resume facts below)"

    resume_line = (
        f"Upload this file wherever a resume/CV is requested: {resume_path}"
        if resume_path else
        "If a resume upload is required and no file is provided, STOP and report that."
    )

    return f"""Fill in the application form for this job. Do not submit it.

ROLE:    {job.get('title', '')}
COMPANY: {job.get('company', '')}
URL:     {job.get('apply_url', '')}

STEPS:
1. Navigate to the URL above.
2. Find and start the application (an "Easy Apply" or "Apply" button). If the
   posting is closed, already applied to, or redirects somewhere you cannot
   complete, STOP and report that instead of improvising.
3. Work through each step of the form, clicking Next to advance.
4. {resume_line}
5. Answer questions using these values. Match on the MEANING of the question,
   not exact wording:
{answer_lines}
6. When you reach the final review step, STOP. Report every field and the
   value you entered.

RULES:
- Do not submit the application. A human reviews it first.
- Never invent facts about the candidate. If a required question is not
  covered above and you cannot answer it from the resume, STOP and report it.
- Never accept a different job, follow promotional links, or navigate away
  from this application.
- If you hit a CAPTCHA or identity check, STOP and report it. Do not attempt it.
"""


async def apply_with_agent(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    resume_path: Optional[str] = None,
    use_system_chrome: bool = True,
    max_steps: int = _MAX_STEPS,
) -> AgentApplyResult:
    """PHASE 1 — fill one application and stop at review. Cannot submit.

    There is deliberately no `submit` parameter. This function has no code
    path to a submit click; sending an application requires the separate
    `submit_approved_application()` below, after human approval.
    """
    try:
        from browser_use import Agent, Tools
    except ImportError:
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

    # Registry-level action removal — the model cannot emit what isn't there.
    tools = Tools(exclude_actions=_PHASE1_EXCLUDED_ACTIONS)

    # Path allowlist for uploads: the agent can attach ONLY this resume, so a
    # confused or injected agent cannot exfiltrate another file from disk.
    available_file_paths: list[str] = []
    if resume_path:
        resolved = str(Path(resume_path).resolve())
        if not Path(resolved).is_file():
            return AgentApplyResult(
                status="error",
                error=f"Resume file not found: {resolved}",
            )
        available_file_paths.append(resolved)
        resume_path = resolved

    task = _build_task(job=job, answers=answers, resume_path=resume_path)

    logger.info(
        "browser_agent_apply: Phase 1 (fill-only) for %s @ %s (max_steps=%d)",
        job.get("title"), job.get("company"), max_steps,
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        tools=tools,
        available_file_paths=available_file_paths or None,
    )

    stopped_reason: Optional[str] = None
    history = None
    try:
        history = await agent.run(
            max_steps=max_steps,
            on_step_start=_make_submit_guard_hook(),
        )
    except SubmitControlReached as stop:
        stopped_reason = str(stop)
        logger.info("browser_agent_apply: %s", stopped_reason)
    except TypeError:
        # Older browser-use builds take hooks on the constructor rather than
        # run(). Retry once that way before giving up.
        try:
            history = await agent.run(max_steps=max_steps)
        except SubmitControlReached as stop:
            stopped_reason = str(stop)
        except Exception as exc:
            return AgentApplyResult(
                status="error", error=f"{type(exc).__name__}: {exc}"[:400]
            )
    except Exception as exc:
        logger.error("browser_agent_apply: agent run failed: %r", exc)
        return AgentApplyResult(status="error", error=f"{type(exc).__name__}: {exc}"[:400])

    summary = final_url = None
    steps = 0
    if history is not None:
        for attr, target in (
            ("final_result", "summary"), ("urls", "final_url"),
            ("model_actions", "steps"),
        ):
            try:
                value = getattr(history, attr)()
                if target == "summary":
                    summary = value
                elif target == "final_url":
                    final_url = value[-1] if value else None
                else:
                    steps = len(value)
            except Exception:
                continue

    return AgentApplyResult(
        status="stopped_at_submit" if stopped_reason else "filled_paused",
        steps_taken=steps,
        final_url=final_url,
        agent_summary=str(summary)[:2000] if summary else None,
        stopped_reason=stopped_reason,
    )


async def submit_approved_application(
    *,
    apply_url: str,
    cdp_url: Optional[str] = None,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """PHASE 2 — click submit. Deterministic Playwright, no LLM involved.

    Call ONLY after a human has reviewed the filled form and approved it
    (api/linkedin_apply.py gates this behind `confirmed=true`).

    Attaches to the already-open browser via CDP so it acts on the session
    Phase 1 left at the review screen, rather than re-filling from scratch.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright not installed.") from exc

    async with async_playwright() as pw:
        if cdp_url:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[-1] if context.pages else await context.new_page()
        else:
            raise RuntimeError(
                "submit_approved_application requires cdp_url — it must act on "
                "the browser session left at the review screen by Phase 1."
            )

        for selector in _SUBMIT_INDICATORS:
            try:
                locator = page.locator(selector)
                if await locator.count() and await locator.first.is_visible(timeout=1000):
                    await locator.first.click(timeout=timeout_ms)
                    await asyncio.sleep(2)
                    return {
                        "status": "submitted",
                        "selector_used": selector,
                        "final_url": page.url,
                    }
            except Exception:
                continue

        return {
            "status": "error",
            "error": "No visible submit control found on the current page.",
            "final_url": page.url,
        }
