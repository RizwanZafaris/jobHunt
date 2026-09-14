"""
api/linkedin_apply.py — LinkedIn Easy Apply pipeline endpoints.

Routes
------
GET  /linkedin-apply/eligible          list A-grade LinkedIn jobs ready to apply
POST /linkedin-apply/{job_id}/prepare  scan form + prefill answers (enqueues G2 if needed)
GET  /linkedin-apply/{job_id}/queue    check queue status for one job
POST /linkedin-apply/{job_id}/submit   HITL approve: drive Playwright to click Submit
POST /linkedin-apply/{job_id}/skip     skip this job (won't appear in eligible again)

Flow
----
1. Dashboard calls GET /eligible to show a list of A-grade LinkedIn jobs.
2. User clicks "Prepare" → POST /prepare scans the Easy Apply form and
   pre-generates answers via OpenRouter (Gemini Flash).
   If no G2 resume exists, G2 is auto-enqueued and the response says
   status='resume_building'.
3. Dashboard polls GET /{job_id}/queue until status='paused' (Playwright
   ran and stopped at the Review step).
4. User reviews the prefilled answers and screenshot in the dashboard, then
   clicks "Submit" → POST /submit drives Playwright to click the Submit
   button on the Review page and marks the application as applied.

All LLM calls in this pipeline use provider=openrouter (configurable via
LINKEDIN_APPLY_LLM_MODEL env). Resume generation (G2) uses the existing
model configuration in settings.py.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin-apply", tags=["linkedin-apply"])


# ─── Response models ─────────────────────────────────────────────────────────
class EligibleJobOut(BaseModel):
    job_id: int
    title: str
    company: str
    location: str
    apply_url: str
    composite: int
    letter_grade: str
    has_resume: bool
    queue_status: Optional[str] = None   # None = not in queue yet


class PrepareResponse(BaseModel):
    job_id: int
    status: str   # 'preparing' | 'resume_building' | 'paused' | 'error'
    message: str
    prefilled_answers: Optional[list[dict[str, Any]]] = None
    cost_usd: Optional[float] = None


class QueueStatusResponse(BaseModel):
    job_id: int
    status: str
    steps_completed: Optional[int] = None
    filled_fields: Optional[int] = None
    failed_fields: Optional[int] = None
    error: Optional[str] = None
    prefilled_answers: Optional[list[dict[str, Any]]] = None
    screenshot_path: Optional[str] = None


class SubmitResponse(BaseModel):
    job_id: int
    status: str    # 'submitted' | 'error'
    message: str


# ─── GET /eligible ───────────────────────────────────────────────────────────
@router.get("/eligible", response_model=list[EligibleJobOut])
async def list_eligible_jobs(
    current_user: User = Depends(get_current_user),
) -> list[EligibleJobOut]:
    """Return all A-grade LinkedIn jobs ready for Easy Apply.

    Excludes jobs already applied, already submitted through this pipeline,
    and jobs with posting_closed_at set.
    """
    from agents.linkedin_easy_apply import get_eligible_jobs

    try:
        jobs = get_eligible_jobs(user_id=UUID(current_user.id))
    except Exception as exc:
        logger.error("linkedin_apply.eligible: %r", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    db = get_supabase()
    # Fetch current queue statuses for these jobs.
    job_ids = [j.job_id for j in jobs]
    queue_rows = []
    if job_ids:
        queue_rows = (
            db.table("linkedin_apply_queue")
            .select("job_id, status")
            .eq("user_id", current_user.id)
            .in_("job_id", job_ids)
            .execute()
            .data
        ) or []
    q_status_map = {r["job_id"]: r["status"] for r in queue_rows}

    return [
        EligibleJobOut(
            job_id=j.job_id,
            title=j.title,
            company=j.company,
            location=j.location,
            apply_url=j.apply_url,
            composite=j.composite,
            letter_grade=j.letter_grade,
            has_resume=j.resume_build_id is not None,
            queue_status=q_status_map.get(j.job_id),
        )
        for j in jobs
    ]


# ─── POST /prepare ───────────────────────────────────────────────────────────
@router.post("/{job_id}/prepare", response_model=PrepareResponse)
async def prepare_application(
    job_id: int,
    current_user: User = Depends(get_current_user),
) -> PrepareResponse:
    """Scan the LinkedIn Easy Apply form and pre-generate answers.

    Background steps:
    1. Check that the job has letter_grade='A' and a LinkedIn apply_url.
    2. Ensure a G2 resume exists; if not, enqueue G2 and return
       status='resume_building'.
    3. Scan the form fields (Playwright, headless).
    4. Call OpenRouter to generate answers for each field.
    5. Run Easy Apply Playwright driver (fills all steps, stops at Review).
    6. Write status='paused' to linkedin_apply_queue.
    """
    from agents.linkedin_easy_apply import (
        ensure_resume_enqueued,
        get_eligible_jobs,
        load_candidate_profile,
        load_tailored_resume_md,
        prefill_application,
        run_easy_apply,
        scan_linkedin_form,
        upsert_queue_row,
        _extract_linkedin_job_id,
    )

    user_id = UUID(current_user.id)
    db = get_supabase()

    # 1. Load job from DB.
    job_rows = (
        db.table("jobs")
        .select("id, title, company, location, apply_url, letter_grade, fit_score_breakdown, match_score")
        .eq("id", job_id)
        .eq("user_id", current_user.id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not job_rows:
        raise HTTPException(status_code=404, detail="Job not found")

    job = job_rows[0]
    if job.get("letter_grade") != "A":
        raise HTTPException(
            status_code=422,
            detail=f"Job {job_id} has letter_grade='{job.get('letter_grade')}', not 'A'. "
                   "Only A-grade (composite ≥ 85) jobs are auto-applied.",
        )
    apply_url = job.get("apply_url", "")
    if "linkedin.com" not in apply_url:
        raise HTTPException(
            status_code=422,
            detail="Job apply_url is not a LinkedIn URL.",
        )

    # 2. Ensure resume.
    resume_build_id = ensure_resume_enqueued(job_id=job_id, user_id=user_id)
    if not resume_build_id:
        upsert_queue_row(
            user_id=user_id,
            job_id=job_id,
            status="resume_building",
            apply_url=apply_url,
            linkedin_job_id=_extract_linkedin_job_id(apply_url),
        )
        return PrepareResponse(
            job_id=job_id,
            status="resume_building",
            message="G2 resume build enqueued. Re-call /prepare once the resume is ready.",
        )

    # 3. Scan form fields.
    try:
        form_fields = await scan_linkedin_form(
            job_url=apply_url,
            headless=True,
            timeout_ms=40_000,
        )
    except Exception as exc:
        logger.error("linkedin_apply.prepare: scan failed for job_id=%s: %r", job_id, exc)
        upsert_queue_row(
            user_id=user_id, job_id=job_id,
            status="error", apply_url=apply_url,
            linkedin_job_id=_extract_linkedin_job_id(apply_url),
            error=f"scan_failed:{exc}",
        )
        return PrepareResponse(
            job_id=job_id, status="error",
            message=f"Form scan failed: {exc}",
        )

    # 4. Pre-fill answers via OpenRouter.
    profile = load_candidate_profile(user_id)
    resume_md = load_tailored_resume_md(job_id=job_id, user_id=user_id)
    prefill = await prefill_application(
        job=job,
        form_fields=form_fields,
        profile=profile,
        master_resume_md=resume_md,
        user_id=user_id,
    )

    # 5. Run Playwright Easy Apply driver (headless, stops before Submit).
    # Get resume DOCX path from Storage URL or skip (form will have a file input
    # the user can manually handle if needed).
    resume_rows = (
        db.table("resume_builds")
        .select("docx_url")
        .eq("id", resume_build_id)
        .limit(1)
        .execute()
        .data
    ) or []
    resume_path: Optional[str] = None
    if resume_rows and resume_rows[0].get("docx_url"):
        # Download the DOCX to a temp file so Playwright can upload it.
        import tempfile
        import httpx
        try:
            docx_url = resume_rows[0]["docx_url"]
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(docx_url)
                r.raise_for_status()

                # resume_agents/render.py degrades to b"" and only logs a
                # warning when reportlab/python-docx are missing or a render
                # throws mid-way. A 0-byte file still uploads to Storage and
                # yields a valid-looking docx_url, so without this guard an
                # empty attachment reaches a real employer. Refuse anything
                # too small to be a real document.
                if len(r.content) < 1024:
                    raise RuntimeError(
                        f"Resume artifact is {len(r.content)} bytes — almost "
                        "certainly an empty render. Refusing to attach. "
                        "Check that reportlab and python-docx are installed "
                        "and re-run the G2 build."
                    )

                suffix = ".pdf" if ".pdf" in docx_url.lower() else ".docx"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(r.content)
                tmp.close()
                resume_path = tmp.name
        except Exception as exc:
            logger.warning("linkedin_apply.prepare: resume download failed: %r", exc)

    fill = await run_easy_apply(
        job={**job, "job_id": job_id},
        answers=prefill.answers,
        resume_path=resume_path,
        headless=True,
        timeout_ms=40_000,
    )

    # Cleanup temp resume file.
    if resume_path:
        try:
            os.unlink(resume_path)
        except Exception:
            pass

    final_status = "paused" if fill.status == "paused_at_review" else "error"
    upsert_queue_row(
        user_id=user_id,
        job_id=job_id,
        status=final_status,
        resume_build_id=resume_build_id,
        apply_url=apply_url,
        linkedin_job_id=_extract_linkedin_job_id(apply_url),
        prefilled_answers=prefill.answers,
        fill_summary={
            "steps_completed": fill.steps_completed,
            "filled_fields": fill.filled_fields,
            "failed_fields": fill.failed_fields,
            "errors": fill.errors[:10],
            "screenshot_path": fill.screenshot_path,
        },
        error=fill.error,
    )

    if final_status == "error":
        return PrepareResponse(
            job_id=job_id, status="error",
            message=fill.error or "Unknown error during form fill",
        )

    return PrepareResponse(
        job_id=job_id,
        status="paused",
        message=(
            f"Ready for review. {fill.filled_fields} fields pre-filled across "
            f"{fill.steps_completed} steps. Approve via POST /{job_id}/submit."
        ),
        prefilled_answers=prefill.answers,
        cost_usd=round(prefill.cost_usd, 4),
    )


# ─── GET /queue status ───────────────────────────────────────────────────────
@router.get("/{job_id}/queue", response_model=QueueStatusResponse)
async def get_queue_status(
    job_id: int,
    current_user: User = Depends(get_current_user),
) -> QueueStatusResponse:
    """Return current status for one job in the apply queue."""
    from agents.linkedin_easy_apply import get_queue_row

    row = get_queue_row(user_id=UUID(current_user.id), job_id=job_id)
    if not row:
        return QueueStatusResponse(job_id=job_id, status="not_queued")

    fill = row.get("fill_summary") or {}
    return QueueStatusResponse(
        job_id=job_id,
        status=row["status"],
        steps_completed=fill.get("steps_completed"),
        filled_fields=fill.get("filled_fields"),
        failed_fields=fill.get("failed_fields"),
        error=row.get("error"),
        prefilled_answers=row.get("prefilled_answers"),
        screenshot_path=fill.get("screenshot_path"),
    )


# ─── POST /submit — HITL approval gate ──────────────────────────────────────
class SubmitRequest(BaseModel):
    confirmed: bool = False   # must be True to proceed


@router.post("/{job_id}/submit", response_model=SubmitResponse)
async def submit_application(
    job_id: int,
    body: SubmitRequest,
    current_user: User = Depends(get_current_user),
) -> SubmitResponse:
    """HITL gate: open the saved Easy Apply wizard and click Submit.

    Requires the queue row to be in status='paused' (from /prepare).
    `confirmed=true` in the body is the explicit user approval.

    This is the ONLY code path that clicks the LinkedIn Submit button.
    """
    from agents.linkedin_easy_apply import (
        get_queue_row,
        mark_submitted,
        run_easy_apply,
        upsert_queue_row,
    )
    from playwright.async_api import async_playwright

    if not body.confirmed:
        raise HTTPException(
            status_code=422,
            detail="Set confirmed=true to approve the LinkedIn application submission.",
        )

    user_id = UUID(current_user.id)
    db = get_supabase()
    row = get_queue_row(user_id=user_id, job_id=job_id)
    if not row:
        raise HTTPException(status_code=404, detail="No prepare step found for this job.")
    if row["status"] not in ("paused", "error"):
        raise HTTPException(
            status_code=409,
            detail=f"Application is in status='{row['status']}', expected 'paused'.",
        )

    # Mark approved so double-clicks don't double-submit.
    upsert_queue_row(user_id=user_id, job_id=job_id, status="approved")

    # Load job for apply_url.
    job_rows = (
        db.table("jobs")
        .select("id, title, company, location, apply_url")
        .eq("id", job_id)
        .eq("user_id", current_user.id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not job_rows:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_rows[0]

    # Re-run Easy Apply with headless=True and actually click Submit.
    answers = row.get("prefilled_answers") or []
    try:
        status = await _submit_via_playwright(
            job=job,
            answers=answers,
            row=row,
        )
    except Exception as exc:
        logger.error("linkedin_apply.submit: playwright submit failed for job_id=%s: %r", job_id, exc)
        upsert_queue_row(
            user_id=user_id, job_id=job_id,
            status="error", error=f"submit_failed:{exc}",
        )
        return SubmitResponse(
            job_id=job_id, status="error",
            message=f"Submit failed: {exc}",
        )

    mark_submitted(user_id=user_id, job_id=job_id)
    return SubmitResponse(
        job_id=job_id, status="submitted",
        message=f"Application submitted to LinkedIn for {job.get('title')} at {job.get('company')}.",
    )


async def _submit_via_playwright(
    *,
    job: dict[str, Any],
    answers: list[dict[str, Any]],
    row: dict[str, Any],
) -> str:
    """Re-run Easy Apply and click Submit on the Review page.

    This is a full replay — LinkedIn doesn't persist partial form state
    between sessions (the li_at cookie keeps auth only). We fill all steps
    again and then click Submit.
    """
    from playwright.async_api import async_playwright
    from pathlib import Path
    import yaml

    _PATTERN_DIR_LOCAL = Path(__file__).parent.parent / "agents" / "ats_patterns"
    pattern_path = _PATTERN_DIR_LOCAL / "linkedin.yml"
    pattern = yaml.safe_load(pattern_path.read_text()) if pattern_path.exists() else {}
    sel = pattern.get("selectors", {})
    safety = pattern.get("safety", {})

    from agents.linkedin_easy_apply import _get_li_at, _PATTERN_DIR

    apply_url = job.get("apply_url", "")
    answer_map = {
        a.get("field_label", "").lower().strip(): a.get("answer", "")
        for a in (answers or [])
    }

    import asyncio

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None,
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        try:
            li_at = _get_li_at()
            await context.add_cookies([{
                "name": "li_at", "value": li_at,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "None",
            }])
        except RuntimeError:
            raise RuntimeError("LinkedIn session cookie (LINKEDIN_SESSION_COOKIE) is required for Submit.")

        page = await context.new_page()
        try:
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=40_000)
            await asyncio.sleep(2)

            easy_apply_sel = sel.get("easy_apply_button", "button.jobs-apply-button")
            await page.click(easy_apply_sel, timeout=10_000)
            await asyncio.sleep(1.5)

            next_sel = sel.get("next_button", "button[aria-label='Continue to next step']")
            submit_sel = sel.get("submit_button", "button[aria-label='Submit application']")
            max_steps = safety.get("max_steps", 10)

            for step in range(1, max_steps + 1):
                await asyncio.sleep(0.8)

                # Fill fields on this step.
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
                        elif tag == "select":
                            try:
                                await page.select_option(f"#{for_id}", label=str(answer), timeout=3_000)
                            except Exception:
                                pass
                        elif tag == "input":
                            input_type = (await inp.get_attribute("type") or "text").lower()
                            if input_type not in ("file", "radio", "checkbox"):
                                await page.fill(f"#{for_id}", str(answer), timeout=3_000)
                    except Exception:
                        continue

                # At Review/Submit step — click Submit.
                try:
                    submit_btn = page.locator(submit_sel)
                    at_submit = await submit_btn.is_visible(timeout=1000)
                except Exception:
                    at_submit = False

                if at_submit:
                    await page.click(submit_sel, timeout=10_000)
                    await asyncio.sleep(2)
                    return "submitted"

                # Click Next.
                try:
                    await page.click(next_sel, timeout=5_000)
                except Exception:
                    break

        finally:
            await context.close()
            await browser.close()

    raise RuntimeError("Submit button not reached — wizard may have changed structure.")


# ─── POST /batch — apply to every eligible role in one call ─────────────────
class BatchApplyRequest(BaseModel):
    confirmed: bool = False       # must be True — this submits real applications
    max_jobs: int = 25            # safety ceiling per run
    dry_run: bool = False         # True = prepare only, never submit


class BatchApplyItem(BaseModel):
    job_id: int
    title: str
    company: str
    location: str
    composite: int
    outcome: str                  # 'submitted' | 'prepared' | 'resume_building' | 'failed' | 'skipped'
    detail: Optional[str] = None


class BatchApplyReport(BaseModel):
    total_eligible: int
    attempted: int
    submitted: int
    prepared_only: int
    resume_building: int
    failed: int
    total_cost_usd: float
    items: list[BatchApplyItem]


@router.post("/batch", response_model=BatchApplyReport)
async def batch_apply(
    body: BatchApplyRequest,
    current_user: User = Depends(get_current_user),
) -> BatchApplyReport:
    """Prepare and (optionally) submit every eligible A-grade LinkedIn role.

    Eligibility is the same filter set as GET /eligible:
      - letter_grade='A' (composite >= 85)
      - LinkedIn apply_url, posting open, not already applied
      - location in GCC / Singapore / Europe / UK / USA
        (Pakistan, Bangladesh, Nepal excluded)
      - title is a product- or program-management role

    `confirmed=true` is required to submit. With `dry_run=true` every job is
    prepared and left at the Review step so you can inspect before approving.

    Returns a per-job report of what actually happened.
    """
    from agents.linkedin_easy_apply import get_eligible_jobs, mark_submitted

    if not body.confirmed and not body.dry_run:
        raise HTTPException(
            status_code=422,
            detail="Set confirmed=true to submit applications, or dry_run=true to prepare only.",
        )

    user_id = UUID(current_user.id)
    try:
        eligible = get_eligible_jobs(user_id=user_id)
    except Exception as exc:
        logger.error("linkedin_apply.batch: eligibility query failed: %r", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    targets = eligible[: max(0, body.max_jobs)]
    items: list[BatchApplyItem] = []
    submitted = prepared = building = failed = 0
    total_cost = 0.0

    for job in targets:
        base = {
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "composite": job.composite,
        }
        try:
            prep = await prepare_application(job.job_id, current_user=current_user)
            total_cost += float(prep.cost_usd or 0.0)

            if prep.status == "resume_building":
                building += 1
                items.append(BatchApplyItem(
                    **base, outcome="resume_building",
                    detail="G2 resume enqueued; re-run batch once it completes.",
                ))
                continue

            if prep.status != "paused":
                failed += 1
                items.append(BatchApplyItem(
                    **base, outcome="failed", detail=prep.message,
                ))
                continue

            if body.dry_run:
                prepared += 1
                items.append(BatchApplyItem(
                    **base, outcome="prepared",
                    detail="Filled and paused at Review. Approve individually to submit.",
                ))
                continue

            sub = await submit_application(
                job.job_id,
                SubmitRequest(confirmed=True),
                current_user=current_user,
            )
            if sub.status == "submitted":
                submitted += 1
                items.append(BatchApplyItem(**base, outcome="submitted", detail=sub.message))
            else:
                failed += 1
                items.append(BatchApplyItem(**base, outcome="failed", detail=sub.message))

        except HTTPException as exc:
            failed += 1
            items.append(BatchApplyItem(**base, outcome="failed", detail=str(exc.detail)))
        except Exception as exc:
            logger.error("linkedin_apply.batch: job_id=%s failed: %r", job.job_id, exc)
            failed += 1
            items.append(BatchApplyItem(**base, outcome="failed", detail=str(exc)[:300]))

    return BatchApplyReport(
        total_eligible=len(eligible),
        attempted=len(targets),
        submitted=submitted,
        prepared_only=prepared,
        resume_building=building,
        failed=failed,
        total_cost_usd=round(total_cost, 4),
        items=items,
    )


# ─── POST /skip ──────────────────────────────────────────────────────────────
@router.post("/{job_id}/skip", response_model=dict)
async def skip_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Mark the job as skipped — it won't appear in /eligible again."""
    from agents.linkedin_easy_apply import upsert_queue_row

    upsert_queue_row(
        user_id=UUID(current_user.id),
        job_id=job_id,
        status="skipped",
    )
    return {"job_id": job_id, "status": "skipped"}
