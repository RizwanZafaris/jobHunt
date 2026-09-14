"""
agents/linkedin_easy_apply.py — LinkedIn Easy Apply automation pipeline.

Pipeline stages
---------------
1. `get_eligible_jobs(user_id)`  — query jobs with letter_grade='A' (composite ≥ 85)
                                    and a LinkedIn apply_url, no existing application.
2. `ensure_resume(job_id, user_id)` — check for a completed G2 resume_build; enqueue
                                       G2 if not found. Returns the DOCX bytes path.
3. `prefill_application(job, resume_path, user_id)` — use OpenRouter (Gemini Flash)
                                                        to generate answers for every
                                                        Easy Apply question found on
                                                        the LinkedIn job page.
4. `run_easy_apply(job, answers, resume_path)` — Playwright driver that:
   a. Injects the LinkedIn `li_at` session cookie.
   b. Navigates to the job posting.
   c. Opens the Easy Apply modal.
   d. Uploads the DOCX/PDF resume.
   e. Fills each wizard step.
   f. STOPS at the Review/Submit step — never clicks Submit.
   Returns a `FillResult` with the pre-filled state.

Human-in-the-loop invariant (mirrors G7)
-----------------------------------------
`run_easy_apply` NEVER calls page.click(submit_button). The pipeline
always pauses with status='paused' in linkedin_apply_queue waiting for
`POST /linkedin-apply/{job_id}/submit` from the user. A human approving
through the API is what calls `submit_approved_application()`.

OpenRouter usage
-----------------
All LLM calls in this module use provider="openrouter" with
OPENROUTER_API_KEY. The answer generation node defaults to
`google/gemini-2.5-flash` (cheap, fast, quality sufficient for
short-form application answers). Override via LINKEDIN_APPLY_LLM_MODEL env.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

_PATTERN_DIR = Path(__file__).parent / "ats_patterns"

from agents.llm_router import get_router
from db.client import get_supabase

logger = logging.getLogger(__name__)

# ─── Model selection ─────────────────────────────────────────────────────────
# All LLM calls in this module go through OpenRouter as requested.
_OPENROUTER_MODEL = os.environ.get(
    "LINKEDIN_APPLY_LLM_MODEL",
    "google/gemini-2.5-flash",  # cost-efficient for short application answers
)
_PROVIDER = "openrouter"

# ─── LinkedIn session ────────────────────────────────────────────────────────
_LINKEDIN_HOME = "https://www.linkedin.com"


def _get_li_at() -> str:
    """Return the li_at session cookie. Raises if not configured."""
    val = os.environ.get("LINKEDIN_SESSION_COOKIE") or os.environ.get("LINKEDIN_LI_AT")
    if not val:
        raise RuntimeError(
            "LinkedIn session cookie not configured. "
            "Set LINKEDIN_SESSION_COOKIE=<your li_at cookie value> in .env."
        )
    return val.strip()


# ─── Result types ─────────────────────────────────────────────────────────────
@dataclass
class EligibleJob:
    job_id: int
    title: str
    company: str
    location: str
    apply_url: str
    composite: int
    letter_grade: str
    linkedin_job_id: Optional[str]
    resume_build_id: Optional[str]
    resume_path: Optional[str]   # local file path to the DOCX/PDF, if available


@dataclass
class PrefillResult:
    answers: list[dict[str, Any]]   # [{field_label, field_type, answer, source}]
    cost_usd: float
    model: str
    error: Optional[str] = None


@dataclass
class FillResult:
    status: str                      # 'paused_at_review' | 'error'
    steps_completed: int
    filled_fields: int
    failed_fields: int
    errors: list[dict[str, Any]] = field(default_factory=list)
    screenshot_path: Optional[str] = None
    error: Optional[str] = None


# ─── 1. Eligible job discovery ───────────────────────────────────────────────
def get_eligible_jobs(user_id: UUID) -> list[EligibleJob]:
    """Query jobs that are ready for LinkedIn Easy Apply:
    - letter_grade = 'A'  (composite ≥ 85 from G5 scoring)
    - apply_url contains 'linkedin.com'
    - no existing application row (status != 'applied')
    - posting still open (posting_closed_at IS NULL)
    - not in linkedin_apply_queue with status IN ('paused','approved','submitted')
    """
    db = get_supabase()
    uid = str(user_id)

    # 1. Jobs that qualify by score + URL.
    job_rows = (
        db.table("jobs")
        .select(
            "id, title, company, location, apply_url, "
            "match_score, fit_score_breakdown, letter_grade"
        )
        .eq("user_id", uid)
        .eq("letter_grade", "A")
        .is_("posting_closed_at", "null")
        .is_("validation_failed", "null")
        .like("apply_url", "%linkedin.com%")
        .order("id", desc=True)
        .limit(50)
        .execute()
        .data
    ) or []

    if not job_rows:
        return []

    job_ids = [r["id"] for r in job_rows]

    # 2. Already-applied job IDs.
    applied_rows = (
        db.table("applications")
        .select("job_id")
        .eq("user_id", uid)
        .in_("job_id", job_ids)
        .execute()
        .data
    ) or []
    applied_ids = {r["job_id"] for r in applied_rows}

    # 3. Jobs already in the queue in a terminal/in-flight state.
    queued_rows = (
        db.table("linkedin_apply_queue")
        .select("job_id, status, resume_build_id")
        .eq("user_id", uid)
        .in_("job_id", job_ids)
        .in_("status", ["paused", "approved", "submitted"])
        .execute()
        .data
    ) or []
    queued_map: dict[int, dict] = {r["job_id"]: r for r in queued_rows}

    # 4. Completed resume builds for these jobs.
    resume_rows = (
        db.table("resume_builds")
        .select("id, job_id, status, docx_url, resume_md")
        .eq("user_id", uid)
        .in_("job_id", job_ids)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []
    resume_map: dict[int, dict] = {}
    for r in resume_rows:
        jid = r["job_id"]
        if jid not in resume_map:
            resume_map[jid] = r

    results: list[EligibleJob] = []
    for row in job_rows:
        jid = row["id"]
        if jid in applied_ids:
            continue

        q = queued_map.get(jid)
        # Skip if already paused (waiting user action), approved, or submitted.
        if q and q["status"] in ("paused", "approved", "submitted"):
            continue

        breakdown = row.get("fit_score_breakdown") or {}
        composite = int(breakdown.get("composite") or row.get("match_score") or 0)

        resume = resume_map.get(jid)
        results.append(EligibleJob(
            job_id=jid,
            title=row.get("title", ""),
            company=row.get("company", ""),
            location=row.get("location", ""),
            apply_url=row.get("apply_url", ""),
            composite=composite,
            letter_grade=row.get("letter_grade", "A"),
            linkedin_job_id=_extract_linkedin_job_id(row.get("apply_url", "")),
            resume_build_id=resume["id"] if resume else None,
            resume_path=None,  # populated later by ensure_resume
        ))

    return results


def _extract_linkedin_job_id(apply_url: str) -> Optional[str]:
    """Pull the numeric LinkedIn job ID from the apply_url.

    Handles both:
      https://www.linkedin.com/jobs/view/1234567890/
      https://www.linkedin.com/jobs/view/1234567890?trackingId=...
    """
    m = re.search(r"/jobs/view/(\d+)", apply_url or "")
    return m.group(1) if m else None


# ─── 2. Resume readiness ─────────────────────────────────────────────────────
def ensure_resume_enqueued(job_id: int, user_id: UUID) -> Optional[str]:
    """Return the resume_build_id if a completed G2 resume exists.
    If not, enqueue G2 and return None (caller should poll or retry later).

    Does NOT block waiting for G2 to finish — the caller (API endpoint)
    should surface the 'building resume' status to the user and they can
    trigger apply again once the resume is ready.
    """
    db = get_supabase()
    uid = str(user_id)

    existing = (
        db.table("resume_builds")
        .select("id, status")
        .eq("user_id", uid)
        .eq("job_id", job_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    if existing:
        return existing[0]["id"]

    # Check for in-flight build; don't double-enqueue.
    in_flight = (
        db.table("resume_builds")
        .select("id, status")
        .eq("user_id", uid)
        .eq("job_id", job_id)
        .in_("status", ["pending", "running"])
        .limit(1)
        .execute()
        .data
    ) or []
    if in_flight:
        logger.info("linkedin_easy_apply: G2 already in flight for job_id=%s", job_id)
        return None

    # Enqueue via RQ.
    try:
        from api.queue import enqueue_g2
        enqueue_g2(job_id=job_id, user_id=uid)
        logger.info("linkedin_easy_apply: G2 enqueued for job_id=%s", job_id)
    except Exception as exc:
        logger.warning("linkedin_easy_apply: failed to enqueue G2 for job_id=%s: %r", job_id, exc)
    return None


# ─── 3. Answer pre-generation (OpenRouter) ───────────────────────────────────
_ANSWER_SYSTEM = """\
You are a job-application assistant. You read LinkedIn Easy Apply form fields
and generate concise, honest answers on behalf of the candidate.

Rules:
- Base every answer on the provided candidate profile. NEVER fabricate metrics,
  companies, or claims not found in the profile.
- For basic info (name, email, phone, location) return the exact profile value.
- For "years of experience" return the numeric value that best fits the field
  label, drawn from the profile. Do NOT round up; do NOT exceed real tenure.
- For salary: return a number or range appropriate for the role + location from
  the profile's target_total_comp when available. If unavailable, return "Open
  to discussion".
- For cover-letter / why-this-company / why-this-role: write 2-4 tight sentences
  linking the candidate's most relevant achievement (from profile highlights) to
  the role. No fluff. No AI-tell phrases ("I am excited to...").
- For work authorization: return the profile value directly.
- For yes/no: return "Yes" or "No" only.
- For select / radio options: return EXACTLY one of the provided option strings.

Return strict JSON:
{"answers": [
  {"field_label": "...", "field_type": "...", "answer": "...", "source": "profile|generated"},
  ...
]}
"""


async def prefill_application(
    *,
    job: dict[str, Any],
    form_fields: list[dict[str, Any]],
    profile: dict[str, Any],
    master_resume_md: str,
    user_id: UUID,
) -> PrefillResult:
    """Generate answers for LinkedIn Easy Apply fields using OpenRouter.

    `form_fields` is the list produced by `scan_linkedin_form()`.
    `profile` is the profile_master row + contact details.
    `master_resume_md` is the tailored resume markdown from G2 (or the master
    resume text as fallback).
    """
    if not form_fields:
        return PrefillResult(answers=[], cost_usd=0.0, model=_OPENROUTER_MODEL)

    fields_block = "\n".join(
        f"  {i+1}. label={f.get('field_label')} type={f.get('field_type')}"
        + (f" options={f.get('options')}" if f.get("options") else "")
        for i, f in enumerate(form_fields[:30])
    )

    profile_block = f"""
Name: {profile.get('full_name', '')}
Email: {profile.get('email', '')}
Phone: {profile.get('phone', '')}
Location: {profile.get('location', '')}
LinkedIn: {profile.get('linkedin_url', '')}
Work Authorization: {profile.get('work_authorization', 'UAE resident — no sponsorship required')}
Target Compensation: {profile.get('target_total_comp', 'Open to discussion')}
""".strip()

    # Trim resume to first 4000 chars (the most recent / relevant section).
    resume_excerpt = (master_resume_md or "")[:4000]

    user_msg = f"""ROLE: {job.get('title', '')} at {job.get('company', '')} ({job.get('location', '')})

CANDIDATE PROFILE:
{profile_block}

TAILORED RESUME EXCERPT:
{resume_excerpt}

FORM FIELDS TO ANSWER:
{fields_block}

Generate accurate, profile-grounded answers for every field listed above.
Return strict JSON only."""

    try:
        parsed, result = await get_router().ask_json(
            provider=_PROVIDER,
            model=_OPENROUTER_MODEL,
            system=_ANSWER_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            temperature=0.1,
            agent_name="g10.prefill",
            user_id=str(user_id),
        )
        answers = parsed.get("answers") or []
        if not isinstance(answers, list):
            answers = []
        return PrefillResult(
            answers=answers,
            cost_usd=float(result.cost_usd or 0.0),
            model=_OPENROUTER_MODEL,
        )
    except Exception as exc:
        logger.error("linkedin_easy_apply: prefill failed: %r", exc)
        return PrefillResult(
            answers=[],
            cost_usd=0.0,
            model=_OPENROUTER_MODEL,
            error=str(exc),
        )


# ─── 4. Playwright Easy Apply driver ─────────────────────────────────────────
async def scan_linkedin_form(
    *,
    job_url: str,
    headless: bool = True,
    timeout_ms: int = 30_000,
) -> list[dict[str, Any]]:
    """Open the LinkedIn job page, click Easy Apply, and harvest all form fields
    across all wizard steps WITHOUT filling them.

    Returns a list of field dicts:
        [{field_label, field_type, options, required, step, selector}]

    The browser is closed after scanning. Caller uses the returned fields to
    call `prefill_application()` then `run_easy_apply()`.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError as exc:
        raise RuntimeError("Playwright not installed. See requirements.txt.") from exc

    import yaml

    pattern_path = _PATTERN_DIR / "linkedin.yml"
    pattern = yaml.safe_load(pattern_path.read_text()) if pattern_path.exists() else {}
    sel = pattern.get("selectors", {})

    fields: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None,
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        # Inject the li_at session cookie so we're authenticated.
        try:
            li_at = _get_li_at()
            await context.add_cookies([{
                "name": "li_at",
                "value": li_at,
                "domain": ".linkedin.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }])
        except RuntimeError:
            logger.warning("linkedin_easy_apply: no li_at cookie — scan may fail on auth")

        page = await context.new_page()
        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await asyncio.sleep(2)

            # Click Easy Apply button.
            easy_apply_sel = sel.get("easy_apply_button", "button.jobs-apply-button")
            try:
                await page.click(easy_apply_sel, timeout=10_000)
                await asyncio.sleep(1)
            except Exception:
                logger.warning("linkedin_easy_apply: Easy Apply button not found at %s", job_url)
                return fields

            # Walk through all wizard steps, collecting fields.
            step = 0
            max_steps = pattern.get("safety", {}).get("max_steps", 10)
            next_sel = sel.get("next_button", "button[aria-label='Continue to next step']")
            submit_sel = sel.get("submit_button", "button[aria-label='Submit application']")

            while step < max_steps:
                step += 1
                await asyncio.sleep(0.8)

                # Harvest fields on this step.
                page_fields = await _harvest_step_fields(page, sel, step)
                fields.extend(page_fields)

                # Check if we've reached the Review/Submit step.
                submit_visible = False
                try:
                    btn = page.locator(submit_sel)
                    submit_visible = await btn.is_visible(timeout=1000)
                except Exception:
                    pass
                if submit_visible:
                    logger.info("linkedin_easy_apply: reached review step (step %d) — stopping scan", step)
                    break

                # Click Next to advance.
                try:
                    await page.click(next_sel, timeout=5_000)
                except Exception:
                    break  # No more next button — done.

        finally:
            await context.close()
            await browser.close()

    return fields


async def _harvest_step_fields(
    page: Any,
    sel: dict[str, str],
    step: int,
) -> list[dict[str, Any]]:
    """Scrape visible interactive fields on the current wizard step."""
    from playwright.async_api import Error as PWError

    fields: list[dict[str, Any]] = []

    # Gather all visible label elements.
    try:
        label_els = await page.query_selector_all("label")
    except Exception:
        return fields

    for label_el in label_els[:40]:
        try:
            label_text = (await label_el.inner_text()).strip()
            if not label_text:
                continue

            # Find the associated input via the `for` attribute.
            for_id = await label_el.get_attribute("for") or ""
            ftype = "text"
            options: list[str] = []
            required = False

            if for_id:
                inp = await page.query_selector(f"#{for_id}")
                if inp:
                    tag = await inp.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "textarea":
                        ftype = "textarea"
                    elif tag == "select":
                        ftype = "select"
                        opts = await inp.query_selector_all("option")
                        for o in opts:
                            t = (await o.inner_text()).strip()
                            if t:
                                options.append(t)
                    else:
                        input_type = (await inp.get_attribute("type") or "text").lower()
                        ftype = input_type if input_type in (
                            "text", "email", "tel", "url", "number",
                            "radio", "checkbox", "file"
                        ) else "text"
                    required = bool(
                        await inp.get_attribute("required")
                        or await inp.get_attribute("aria-required") == "true"
                    )

            fields.append({
                "step": step,
                "field_id": for_id,
                "field_label": label_text,
                "field_type": ftype,
                "required": required,
                "options": options or None,
            })
        except Exception:
            continue

    return fields


async def run_easy_apply(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    resume_path: Optional[str] = None,
    headless: bool = False,  # False so user can see it running (same as G7)
    timeout_ms: int = 30_000,
    screenshot_dir: Optional[str] = None,
) -> FillResult:
    """Drive LinkedIn Easy Apply: fill all steps, stop before Submit.

    Leaves the browser open at the Review step when headless=False so the user
    can inspect everything before approving via the API (which then calls
    `submit_approved_application`).

    When headless=True the browser is closed after pausing; the HITL is
    entirely API-driven in that mode.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright not installed.") from exc

    import yaml

    pattern_path = _PATTERN_DIR / "linkedin.yml"
    pattern = yaml.safe_load(pattern_path.read_text()) if pattern_path.exists() else {}
    sel = pattern.get("selectors", {})
    safety = pattern.get("safety", {})

    job_url = job.get("apply_url", "")
    filled = 0
    failed = 0
    errors: list[dict[str, Any]] = []
    steps_done = 0
    screenshot_path: Optional[str] = None

    # Build an answer lookup keyed by field_label (lowercase).
    answer_map = {
        a.get("field_label", "").lower().strip(): a.get("answer", "")
        for a in (answers or [])
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None,
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )

        # Auth cookie.
        try:
            li_at = _get_li_at()
            await context.add_cookies([{
                "name": "li_at", "value": li_at,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "None",
            }])
        except RuntimeError:
            pass

        page = await context.new_page()
        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await asyncio.sleep(2)

            # Open Easy Apply.
            easy_apply_sel = sel.get("easy_apply_button", "button.jobs-apply-button")
            try:
                await page.click(easy_apply_sel, timeout=10_000)
                await asyncio.sleep(1.5)
            except Exception as exc:
                return FillResult(
                    status="error", steps_completed=0,
                    filled_fields=0, failed_fields=0,
                    error=f"Easy Apply button not found: {exc}",
                )

            # Walk wizard steps.
            next_sel = sel.get("next_button", "button[aria-label='Continue to next step']")
            submit_sel = sel.get("submit_button", "button[aria-label='Submit application']")
            resume_sel = sel.get("resume_upload", "input[type='file']")
            max_steps = safety.get("max_steps", 10)
            resume_uploaded = False

            for step in range(1, max_steps + 1):
                steps_done = step
                await asyncio.sleep(0.8)

                # Upload resume if a file input is present and we haven't uploaded yet.
                if resume_path and not resume_uploaded:
                    try:
                        file_inp = page.locator(resume_sel).first
                        if await file_inp.is_visible(timeout=1000):
                            await file_inp.set_input_files(resume_path)
                            resume_uploaded = True
                            filled += 1
                            logger.info("linkedin_easy_apply: uploaded resume at step %d", step)
                    except Exception:
                        pass

                # Fill visible text / textarea / select fields.
                label_els = await page.query_selector_all("label")
                for label_el in label_els[:40]:
                    try:
                        label_text = (await label_el.inner_text()).strip().lower()
                        answer = answer_map.get(label_text) or answer_map.get(label_text.rstrip("*").strip())
                        if not answer:
                            continue
                        for_id = await label_el.get_attribute("for") or ""
                        if not for_id:
                            continue
                        inp = await page.query_selector(f"#{for_id}")
                        if not inp:
                            continue
                        tag = await inp.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "textarea":
                            await page.fill(f"#{for_id}", str(answer), timeout=3_000)
                            filled += 1
                        elif tag == "select":
                            try:
                                await page.select_option(f"#{for_id}", label=str(answer), timeout=3_000)
                                filled += 1
                            except Exception:
                                # Try value match.
                                try:
                                    await page.select_option(f"#{for_id}", value=str(answer), timeout=2_000)
                                    filled += 1
                                except Exception as exc2:
                                    failed += 1
                                    errors.append({"label": label_text, "error": str(exc2)[:200]})
                        else:
                            input_type = (await inp.get_attribute("type") or "text").lower()
                            if input_type == "file":
                                pass  # handled above
                            elif input_type in ("radio", "checkbox"):
                                pass  # skip — radio/checkbox handled separately
                            else:
                                await page.fill(f"#{for_id}", str(answer), timeout=3_000)
                                filled += 1
                    except Exception as exc:
                        failed += 1
                        errors.append({
                            "label": label_text if "label_text" in dir() else "?",
                            "error": str(exc)[:200],
                        })

                # Check for Review/Submit button — STOP here.
                try:
                    submit_btn = page.locator(submit_sel)
                    at_review = await submit_btn.is_visible(timeout=1000)
                except Exception:
                    at_review = False

                if at_review:
                    logger.info("linkedin_easy_apply: reached review step — PAUSING (HITL)")
                    if screenshot_dir:
                        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        screenshot_path = os.path.join(
                            screenshot_dir,
                            f"linkedin_apply_{job.get('job_id','?')}_{ts}.png",
                        )
                        try:
                            await page.screenshot(path=screenshot_path, full_page=False)
                        except Exception:
                            screenshot_path = None
                    break  # pause — never click submit

                # Advance to next step.
                try:
                    await page.click(next_sel, timeout=5_000)
                except Exception:
                    break

        finally:
            if headless:
                await context.close()
                await browser.close()
            # When headless=False: leave browser open for user to inspect.
            # The process will keep it alive until the context is GC'd.

    return FillResult(
        status="paused_at_review",
        steps_completed=steps_done,
        filled_fields=filled,
        failed_fields=failed,
        errors=errors,
        screenshot_path=screenshot_path,
    )


# ─── 5. DB queue helpers ──────────────────────────────────────────────────────
def upsert_queue_row(
    *,
    user_id: UUID,
    job_id: int,
    status: str,
    resume_build_id: Optional[str] = None,
    linkedin_job_id: Optional[str] = None,
    apply_url: Optional[str] = None,
    prefilled_answers: Optional[list] = None,
    fill_summary: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Insert or update a row in linkedin_apply_queue. Returns the upserted row."""
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "user_id": str(user_id),
        "job_id": job_id,
        "status": status,
        "updated_at": now,
    }
    if resume_build_id is not None:
        row["resume_build_id"] = resume_build_id
    if linkedin_job_id is not None:
        row["linkedin_job_id"] = linkedin_job_id
    if apply_url is not None:
        row["apply_url"] = apply_url
    if prefilled_answers is not None:
        row["prefilled_answers"] = prefilled_answers
    if fill_summary is not None:
        row["fill_summary"] = fill_summary
    if error is not None:
        row["error"] = error

    result = (
        db.table("linkedin_apply_queue")
        .upsert(row, on_conflict="user_id,job_id")
        .execute()
        .data
    )
    return result[0] if result else row


def get_queue_row(*, user_id: UUID, job_id: int) -> Optional[dict[str, Any]]:
    """Fetch the current queue row for (user_id, job_id)."""
    db = get_supabase()
    rows = (
        db.table("linkedin_apply_queue")
        .select("*")
        .eq("user_id", str(user_id))
        .eq("job_id", job_id)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def mark_submitted(*, user_id: UUID, job_id: int) -> None:
    """Update queue row to submitted and log the application."""
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    db.table("linkedin_apply_queue").update({
        "status": "submitted",
        "submitted_at": now,
        "updated_at": now,
    }).eq("user_id", str(user_id)).eq("job_id", job_id).execute()

    # Write to applications table so the /today dashboard reflects the apply.
    try:
        db.table("applications").insert({
            "user_id": str(user_id),
            "job_id": job_id,
            "status": "applied",
            "applied_at": now,
            "channel": "linkedin_easy_apply",
        }).execute()
    except Exception as exc:
        logger.warning(
            "linkedin_easy_apply: applications insert failed for job_id=%s: %r",
            job_id, exc,
        )


# ─── 6. Profile loader ────────────────────────────────────────────────────────
def load_candidate_profile(user_id: UUID) -> dict[str, Any]:
    """Load name, email, phone, location from profile_master and profiles table."""
    db = get_supabase()
    uid = str(user_id)

    # profile_master has core_competencies etc. but not contact details.
    pm_rows = (
        db.table("profile_master")
        .select("name, headline, summary")
        .limit(1)
        .execute()
        .data
    ) or []
    pm = pm_rows[0] if pm_rows else {}

    # The `profiles` table (from auth) or a `rizwan_profile` section.
    profile_rows = (
        db.table("rizwan_profile")
        .select("section, content")
        .in_("section", ["contact", "personal", "header"])
        .execute()
        .data
    ) or []

    # Build a simple dict by parsing known contact-section content.
    contact: dict[str, Any] = {
        "full_name": pm.get("name", "Rizwan Zafar"),
        "email": os.environ.get("RIZWAN_EMAIL", "rizwanzaffar.pk@gmail.com"),
        "phone": "",
        "location": "Dubai, UAE",
        "linkedin_url": "https://linkedin.com/in/rizwanzaffar",
        "work_authorization": "UAE resident — no visa sponsorship required",
        "target_total_comp": os.environ.get("TARGET_TOTAL_COMP", ""),
    }
    for row in profile_rows:
        content = row.get("content") or ""
        # Crude extraction: look for phone / location in the section text.
        phone_m = re.search(r"\+?\d[\d\s\-().]{8,}", content)
        if phone_m and not contact["phone"]:
            contact["phone"] = phone_m.group(0).strip()
        loc_m = re.search(r"(?:Dubai|Abu Dhabi|Karachi|Singapore|London)[^,\n]*", content, re.I)
        if loc_m and contact["location"] == "Dubai, UAE":
            contact["location"] = loc_m.group(0).strip()

    return contact


def load_tailored_resume_md(*, job_id: int, user_id: UUID) -> str:
    """Return the most recent completed G2 resume_md for this job, or empty str."""
    db = get_supabase()
    rows = (
        db.table("resume_builds")
        .select("resume_md, user_edited_md")
        .eq("user_id", str(user_id))
        .eq("job_id", job_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        return ""
    # user_edited_md takes priority (the user may have polished it).
    return rows[0].get("user_edited_md") or rows[0].get("resume_md") or ""
