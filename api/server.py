"""
FastAPI server — deploys to Railway.
Provides REST API for the job hunt system.
Vercel dashboard can call these endpoints.
"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import Optional
from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel
import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from config.settings import get_settings
from api.rate_limits import RATE_LIMITS

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Job Hunt AI System v2",
    description="Rizwan's multi-agent AI job hunt system",
    version="2.0.0",
)

# CORS for Vercel dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your Vercel domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Rate limiting (P1-3) ─────────────────────────────────────────────────────
# slowapi keyed on remote IP, with a global default of 60/minute applied by
# SlowAPIMiddleware to every route automatically. High-risk routes (LLM
# generation, queue enqueue, data import) override the default via the
# @limiter.limit decorator — see RATE_LIMITS in api/rate_limits.py and the
# decorated routes below + in api/workspace.py, api/linkedin.py, api/network.py.
#
# /health and /healthz are exempted (Railway's healthcheck hits /health every
# few seconds and would otherwise trip the default 60/min on a slow restart).
#
# TODO(multi-tenant): switch key from get_remote_address to a user_id-aware
# key function once we leave single-user mode (per-IP throttles a whole
# household behind NAT). See docs/AUDIT_REVIEW_EXTERNAL_2026_05_12.md §3.6.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[RATE_LIMITS["default"]],
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a structured 429 with Retry-After in both header and body.

    The body shape `{"detail": "...", "retry_after": <seconds>}` is the
    contract the dashboard's fetch wrapper looks for when deciding whether
    to retry with backoff. Falls back to `60` when slowapi doesn't surface
    a window stat (e.g. memory backend race).
    """
    retry_after = 60
    try:
        # slowapi's exc.limit is a Limit wrapper whose .limit is a RateLimitItem
        # with a `get_expiry()` method returning the window length in seconds.
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        pass
    response = JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "retry_after": retry_after,
        },
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


app.add_middleware(SlowAPIMiddleware)


# ── Path C routers (P1.1 referral graph + P1.2 LinkedIn engine) ───────────────
# Wired here so the new differentiator endpoints (/network/*, /linkedin/*) are
# live the moment migrations 004/005 are applied. Endpoints internally use
# Depends(get_current_user) which short-circuits to user_001 (Rizwan) when
# RIZWAN_SINGLE_USER_MODE=1.
from api.network import router as network_router  # noqa: E402
from api.linkedin import router as linkedin_router  # noqa: E402
from api.actions import router as actions_router  # noqa: E402  /actions/today (Phase 1)
from api.workspace import router as workspace_router  # noqa: E402  /workspace/* (Phase 2)
from api.interview_studio import router as interview_studio_router  # noqa: E402  /interview-studio/* (Phase 3)
from api.perplexity import router as perplexity_router  # noqa: E402  Perplexity persona recency
from api.apollo import router as apollo_router  # noqa: E402  Apollo firmographic + hiring intel
from api.stories import router as stories_router  # noqa: E402  /workspace/stories/* (Phase 1.2 G9 + story_bank)
from api.follow_ups import router as follow_ups_router  # noqa: E402  /workspace/follow-ups/* (Phase 1.3, G6)
from api.g7 import router as g7_router  # noqa: E402  /workspace/{job_id}/apply + /workspace/applications/* (Tier 3, G7)
from api.proof_points import router as proof_points_router  # noqa: E402  /profile/proof-points/* (Tier 4 §6.4)
from api.profile import router as profile_router  # noqa: E402  /profile/* (Tier 4 G11 voice calibration)
from api.offers import router as offers_router  # noqa: E402  /offers/* (Tier 4 G8 offer evaluation)
app.include_router(network_router)
app.include_router(linkedin_router)
app.include_router(actions_router)
app.include_router(workspace_router)
app.include_router(interview_studio_router)
app.include_router(perplexity_router)
app.include_router(apollo_router)
app.include_router(stories_router)
app.include_router(follow_ups_router)
app.include_router(g7_router)
app.include_router(proof_points_router)
app.include_router(profile_router)
app.include_router(offers_router)


# ── Auth ──────────────────────────────────────────────────────────────────────
def verify_secret(x_secret_key: str = Header(None)):
    if x_secret_key != settings.secret_key:
        raise HTTPException(status_code=401, detail="Invalid secret key")
    return True


# ── Jobs-runs polling endpoint ────────────────────────────────────────────────
# Referenced by:
#   - dashboard ResumeEditor.tsx full-rebuild polling
#   - dashboard ResumeTab.tsx build-resume polling
#   - api/workspace.py (logs `poll_url: /jobs-runs/{id}` in responses)
#   - api/queue.py docstring + api/linkedin.py docstring
# but the GET handler itself was never implemented — frontend polling was
# returning 404. This single endpoint closes that gap.
#
# Defined AFTER `verify_secret` because Depends(verify_secret) is evaluated
# at decorator-time and needs the symbol resolved. Earlier placement caused
# Railway boot to crash with NameError (production logs 2026-05-10T12:37:52Z).
@app.get("/jobs-runs/{run_id}")
def get_jobs_run(
    run_id: str,
    _auth=Depends(verify_secret),
):
    """Return the current state of a jobs_runs row by id.

    Status lifecycle: queued → running → succeeded | failed | cancelled.
    Frontend polls every 8s after enqueueing G2/G3/G4 work.
    """
    from api.jobs_runs import get_run
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"jobs_run {run_id} not found")
    # Pydantic model.dump() handles UUIDs / datetimes for JSON.
    return run.model_dump(mode="json")


# ── Request/Response Models ───────────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    skip_scout: bool = False


class InterviewPrepRequest(BaseModel):
    job_id: int


class CompanyBuildRequest(BaseModel):
    company_name: str
    force_refresh: bool = False


class JobEvalRequest(BaseModel):
    jd_text: str
    company: str
    job_title: str
    job_url: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "system": "Job Hunt AI v2",
        "status": "running",
        "date": date.today().isoformat(),
        "endpoints": [
            "/pipeline/run", "/pipeline/evaluate", "/pipeline/stats",
            "/jobs", "/jobs/{id}",
            "/companies", "/companies/build",
            "/interview-prep",
            "/networking/strategy",
            "/salary/research",
            "/applications/review",
            "/digest/latest", "/boss/audit",
            "/resumes/{filename}"
        ]
    }


@app.get("/health")
async def health():
    """Railway health check endpoint."""
    return {"status": "healthy", "timestamp": date.today().isoformat()}


@app.get("/debug/apify-check")
async def debug_apify_check(_auth=Depends(verify_secret)):
    """Diagnostic: check whether APIFY_TOKEN reaches the container + works.

    Bypasses Pydantic settings cache by reading os.environ directly.
    If a token is present, fires one tiny rag-web-browser query and
    reports the actual HTTP status / response from Apify.

    Use this when /personas/deep-research keeps returning sources_count=0
    to figure out if the issue is (a) env var not set, (b) wrong name,
    (c) invalid token, (d) Apify quota exhausted, (e) rate-limited.
    """
    import os
    import httpx
    from agents.persona_deep_research import APIFY_ACTOR_RUN_SYNC_URL

    raw_token = os.environ.get("APIFY_TOKEN")
    settings_token = None
    try:
        settings_token = settings.apify_token
    except Exception:
        pass

    out = {
        "env_var_present_raw": bool(raw_token),
        "env_var_present_via_settings": bool(settings_token),
        "raw_token_len": len(raw_token) if raw_token else 0,
        "settings_token_len": len(settings_token) if settings_token else 0,
        "raw_token_prefix": (raw_token[:8] + "…") if raw_token else None,
        "settings_token_prefix": (settings_token[:8] + "…") if settings_token else None,
    }

    if not raw_token:
        out["verdict"] = "APIFY_TOKEN env var not visible to container. Set it on Railway and redeploy."
        return out

    # Fire one tiny call
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                APIFY_ACTOR_RUN_SYNC_URL,
                params={"token": raw_token},
                json={"query": "test", "maxResults": 1, "outputFormats": ["markdown"]},
            )
            out["http_status"] = resp.status_code
            # Apify's run-sync-get-dataset-items returns 201 (Created) on success,
            # not 200. Accept both.
            if resp.status_code in (200, 201):
                items = resp.json() or []
                out["items_returned"] = len(items)
                out["verdict"] = "OK — Apify auth works, query returned data"
            else:
                out["body_preview"] = resp.text[:400]
                out["verdict"] = f"Apify HTTP {resp.status_code} — token may be invalid, expired, or out of credit"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["verdict"] = "Network or client error reaching Apify"
    return out


@app.get("/debug/provider-ping")
async def debug_provider_ping(
    provider: str,
    model: str,
    _auth=Depends(verify_secret),
):
    """
    Diagnostic: send a 1-token "ping" to a provider+model and report the
    exact response or error. Used to triage why a provider's calls keep
    failing inside the G2 graph (where they're caught silently by the
    sentinel fallback in _run_ats_critic).

    Example:
        GET /debug/provider-ping?provider=moonshot&model=kimi-k2.5
        GET /debug/provider-ping?provider=deepseek&model=deepseek-reasoner

    Returns:
        {ok: true, text, latency_ms, input_tokens, output_tokens, cost_usd}
        OR
        {ok: false, error_type, error_message, traceback_tail}

    Never throws — every failure is captured into the JSON response so
    we can see the real error from a curl. Dropped behind /debug/* so
    it's never exposed without the X-Secret-Key header.
    """
    import os
    import traceback as tb_mod
    from agents.llm_router import get_router

    # Sanity: env-var presence (without leaking the key value)
    env_var_for_provider = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "google":    "GOOGLE_API_KEY",
        "deepseek":  "DEEPSEEK_API_KEY",
        "moonshot":  "KIMI_API_KEY",
    }.get(provider, "?")
    env_present = bool(os.environ.get(env_var_for_provider))

    try:
        result = await get_router().ask(
            provider=provider,
            model=model,
            system="You are a healthcheck endpoint. Respond with only the word 'pong'.",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=10,
            temperature=0.0,
            agent_name="debug.provider_ping",
        )
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "env_var": env_var_for_provider,
            "env_var_present": env_present,
            "response_text": result.text[:200],
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
        }
    except Exception as e:
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "env_var": env_var_for_provider,
            "env_var_present": env_present,
            "error_type": type(e).__name__,
            "error_message": str(e)[:600],
            "traceback_tail": tb_mod.format_exc()[-1500:],
        }


@app.post("/pipeline/run")
async def run_pipeline(
    request: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret)
):
    """Trigger a full pipeline run (runs in background)."""
    async def _run():
        from pipeline import JobHuntPipeline
        pipeline = JobHuntPipeline()
        await pipeline.run(
            target_company=request.company,
            target_role=request.role,
            skip_scout=request.skip_scout,
        )

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Pipeline running in background"}


@app.post("/pipeline/evaluate")
async def evaluate_job(
    request: JobEvalRequest,
    _auth=Depends(verify_secret)
):
    """
    Evaluate a single job description.
    Pass JD text + company → get full analysis + tailored resume.
    """
    from agents.company_agent import CompanyAgent
    from agents.rizwan_agent import RizwanAgent
    from db.client import upsert_job

    # Store job in DB
    job_record = upsert_job({
        "title": request.job_title,
        "company": request.company,
        "url": request.job_url or f"manual-{request.company}-{request.job_title}",
        "description": request.jd_text,
        "source": "manual",
        "match_score": 0,
    })
    job_id = job_record.get("id")

    # Quick evaluation
    company_agent = CompanyAgent(request.company)
    await company_agent.build_or_refresh()

    rizwan_agent = RizwanAgent()
    await rizwan_agent.seed_supabase_profile()

    from db.client import search_rizwan_profile
    profile_sections = await search_rizwan_profile(request.jd_text, match_count=4)
    profile_text = "\n".join([f"[{s['section']}]: {s['content']}" for s in profile_sections])

    gap_analysis = await company_agent.review_resume_against_jd(
        jd_text=request.jd_text,
        rizwan_profile_text=profile_text,
        job_title=request.job_title,
    )

    return {
        "job_id": job_id,
        "company": request.company,
        "role": request.job_title,
        "match_score": gap_analysis.get("overall_match_score"),
        "strengths": gap_analysis.get("strengths", []),
        "gaps": gap_analysis.get("gaps", []),
        "tailoring_priorities": gap_analysis.get("tailoring_priorities", []),
        "company_hooks": gap_analysis.get("company_hooks", []),
    }


@app.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    min_score: int = 0,
    limit: int = 50,
    include_closed: bool = False,
    letter_grade: Optional[str] = None,
    _auth=Depends(verify_secret)
):
    """List all discovered jobs.

    2026-05-12: filters out closed / failed-validation jobs by default.
    The OKX-1641 archetype (1-year-old LinkedIn listing scoring 95/100) was
    surfacing on this endpoint pre-fix. Pass include_closed=true to see
    everything (admin / debugging use).

    Phase 2 §4.1 (G5): exposes `letter_grade` on every row + accepts a
    `letter_grade` query filter (e.g. `?letter_grade=A` to surface only
    A-grade fits). Unscored rows have letter_grade IS NULL — they are
    NOT filtered out unless an explicit `letter_grade` filter is passed,
    so the dashboard can render a "Score now" state on those.
    """
    from db.client import get_supabase
    from api._job_guards import filter_open_jobs_query
    db = get_supabase()
    query = db.table("jobs").select(
        "id, title, company, location, match_score, status, url, discovered_at, "
        "archetype, legitimacy_tier, resume_generated_at, "
        "posting_closed_at, validation_failed, "
        # Phase 2 §4.1 — G5 surface columns. Dashboard reads letter_grade
        # for the A-F chip; fit_score_breakdown carries the per-dim
        # rationale + composite for the workspace detail view.
        "letter_grade, fit_score_breakdown"
    ).gte("match_score", min_score).order("match_score", desc=True).limit(limit)

    # Hide stale rows unless explicitly requested. Keeps the canonical list
    # endpoint (and anything that proxies it — dashboards, integrations)
    # safe from the OKX-1641 archetype.
    if not include_closed:
        query = filter_open_jobs_query(query)

    if status:
        query = query.eq("status", status)
    if letter_grade:
        # Accept single grade ("A") or comma-separated set ("A,B").
        grades = [g.strip().upper() for g in letter_grade.split(",") if g.strip()]
        if grades:
            query = query.in_("letter_grade", grades)

    result = query.execute()
    return {"jobs": result.data or [], "count": len(result.data or [])}


@app.get("/jobs/{job_id}")
async def get_job(job_id: int, _auth=Depends(verify_secret)):
    """Get full job details."""
    from db.client import get_job as _get_job
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/companies")
async def list_companies(_auth=Depends(verify_secret)):
    """List all tracked companies.

    BUG-013: filter out `is_phantom=true` rows (scraping artifacts).
    """
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("companies")
        .select("*")
        .eq("is_phantom", False)
        .order("name")
        .execute()
    )
    return {"companies": result.data or []}


@app.post("/companies/build")
async def build_company(
    request: CompanyBuildRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret)
):
    """Build or refresh a company agent."""
    async def _build():
        from agents.company_agent import CompanyAgent
        agent = CompanyAgent(request.company_name)
        await agent.build_or_refresh(force=request.force_refresh)

    background_tasks.add_task(_build)
    return {"status": "started", "company": request.company_name}


@app.post("/interview-prep")
async def generate_interview_prep(
    request: InterviewPrepRequest,
    _auth=Depends(verify_secret)
):
    """Generate interview prep for a job."""
    from agents.interview_agent import InterviewAgent
    from db.client import get_job as _get_job

    job = _get_job(request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    agent = InterviewAgent()
    result = await agent.run(
        company=job["company"],
        job_title=job["title"],
        jd_text=job.get("description", ""),
        job_id=request.job_id,
    )
    return {
        "job_id": request.job_id,
        "company": job["company"],
        "file_path": result.get("file_path"),
        "process_intel": result.get("process_intel"),
        "questions_count": {
            "behavioral": len(result.get("likely_questions", {}).get("behavioral", [])),
            "technical": len(result.get("likely_questions", {}).get("technical", [])),
        }
    }


@app.get("/digest/latest")
async def get_latest_digest(_auth=Depends(verify_secret)):
    """Get the latest daily digest."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("boss_audit_log") \
        .select("*") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if result.data:
        return result.data[0]
    return {"message": "No digest available yet"}


@app.post("/boss/audit")
async def run_boss_audit(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret)
):
    """Trigger a boss agent audit immediately."""
    async def _audit():
        from agents.boss_agent import BossAgent
        boss = BossAgent()
        await boss.run()

    background_tasks.add_task(_audit)
    return {"status": "started", "message": "Boss audit running in background"}


# ─── Boss Chat (interactive Q&A) ──────────────────────────────────────────
class BossChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class BossChatRequest(BaseModel):
    messages: list[BossChatMessage]


BOSS_CHAT_SYSTEM = """You are the Boss — Rizwan's nightly orchestrator agent
who knows the entire job-hunt pipeline. You speak in the voice of a senior
operations partner: direct, strategic, no fluff. You answer questions about
pipeline state, recommend next actions, and push back when Rizwan is
chasing the wrong thing.

You have access to live pipeline context (recent jobs, applications, costs)
in the system prompt below. Use it. Cite specific job IDs, companies, or
numbers when answering — never wave at "the data" abstractly.

Style:
- 2-5 sentences for typical questions; bullet lists for multi-part answers
- Concrete recommendations, not "you might consider..."
- Treat this as a working conversation, not a chatbot demo
- Never fabricate. If pipeline context doesn't show what was asked, say so
  and suggest the closest signal that does exist.
"""


def _build_boss_context() -> str:
    """Pull a tight snapshot of pipeline state to ground the boss in reality.

    Includes:
      - Top 10 unscored / unbuilt jobs (score >= 85)
      - Last 5 resume_builds (status, cost, target)
      - Last 24h cost rollup
      - Active applications by status
    Never raises — best-effort; returns 'no live data' on partial failure.
    """
    from db.client import get_supabase
    from datetime import datetime, timezone, timedelta
    db = get_supabase()
    parts: list[str] = []

    try:
        # 2026-05-12: filter stale rows out of the daily digest. Pre-fix,
        # this endpoint surfaced the OKX-1641 (1-year-old) listing because
        # it had match_score 95 and no closed_at.
        from api._job_guards import filter_open_jobs_query
        _q = (
            db.table("jobs")
            .select("id, title, company, match_score, status, archetype, legitimacy_tier, resume_generated_at")
            .gte("match_score", 85)
        )
        _q = filter_open_jobs_query(_q)
        jobs = (
            _q.order("match_score", desc=True)
            .limit(10)
            .execute()
            .data or []
        )
        if jobs:
            lines = [
                f"  - [{j['id']}] {j.get('title','?')[:60]} @ {j.get('company','?')[:40]} "
                f"score={j.get('match_score')} status={j.get('status')} "
                f"resume={'yes' if j.get('resume_generated_at') else 'no'}"
                for j in jobs
            ]
            parts.append("TOP 10 HIGH-SCORE JOBS:\n" + "\n".join(lines))
    except Exception:
        parts.append("TOP JOBS: (unavailable)")

    try:
        builds = (
            db.table("resume_builds")
            .select("id, job_id, company_name, status, iterations, cost_usd_total, created_at")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
            .data or []
        )
        if builds:
            lines = [
                f"  - {b.get('company_name','?')[:30]} job_id={b.get('job_id')} "
                f"status={b.get('status')} iters={b.get('iterations')} "
                f"cost=${float(b.get('cost_usd_total') or 0):.2f}"
                for b in builds
            ]
            parts.append("LAST 5 RESUME BUILDS:\n" + "\n".join(lines))
    except Exception:
        parts.append("RESUME BUILDS: (unavailable)")

    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = (
            db.table("agent_call_log")
            .select("provider, cost_usd")
            .gte("called_at", since)
            .execute()
            .data or []
        )
        total = sum(float(r.get("cost_usd") or 0) for r in rows)
        by_p: dict = {}
        for r in rows:
            by_p[r.get("provider", "?")] = by_p.get(r.get("provider", "?"), 0.0) + float(r.get("cost_usd") or 0)
        breakdown = ", ".join(f"{k}=${v:.2f}" for k, v in sorted(by_p.items(), key=lambda x: -x[1]))
        parts.append(f"24H COST: ${total:.2f} total ({breakdown or 'no calls'}) across {len(rows)} LLM calls")
    except Exception:
        parts.append("24H COST: (unavailable)")

    try:
        apps = (
            db.table("applications")
            .select("status")
            .execute()
            .data or []
        )
        if apps:
            counts: dict = {}
            for a in apps:
                counts[a.get("status", "?")] = counts.get(a.get("status", "?"), 0) + 1
            parts.append("APPLICATIONS BY STATUS: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "(no live pipeline data available right now)"


@app.post("/boss/chat")
async def boss_chat(
    request: BossChatRequest,
    _auth=Depends(verify_secret),
):
    """Interactive chat with the BossAgent persona, grounded in live pipeline state.

    Frontend posts {messages: [{role, content}, ...]} where role is 'user' or
    'assistant'. The latest message must be from 'user'. Returns
    {role: 'assistant', content, cost_usd, latency_ms}.

    Cheap by design — Claude Opus, max_tokens=1500. Each turn ~$0.05-$0.15.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages must be non-empty")
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from 'user'")

    from agents.llm_router import get_router
    from config.settings import get_settings

    context = _build_boss_context()
    system = BOSS_CHAT_SYSTEM + "\n\n--- LIVE PIPELINE STATE ---\n" + context

    router = get_router()
    result = await router.ask(
        provider="anthropic",
        model=get_settings().boss_agent_model,
        system=system,
        messages=[{"role": m.role, "content": m.content} for m in request.messages],
        max_tokens=1500,
        temperature=0.4,
        agent_name="boss.chat",
    )
    return {
        "role": "assistant",
        "content": result.text,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "model": result.model,
    }


class NetworkingRequest(BaseModel):
    company: str
    job_title: str
    job_url: Optional[str] = None
    hiring_manager_name: Optional[str] = None
    company_context: Optional[str] = None


class SalaryRequest(BaseModel):
    company: str
    job_title: str
    location: str
    company_stage: Optional[str] = None  # startup, scaleup, enterprise


class OfferEvalRequest(BaseModel):
    company: str
    job_title: str
    location: str
    offered_base_monthly: float
    offered_currency: str = "AED"
    bonus_pct: float = 0
    equity_details: str = ""
    other_benefits: str = ""


@app.post("/networking/strategy")
async def get_networking_strategy(
    request: NetworkingRequest,
    _auth=Depends(verify_secret)
):
    """Generate networking strategy and outreach messages for a target company."""
    from agents.networking_agent import NetworkingAgent
    agent = NetworkingAgent()
    result = await agent.run(
        company=request.company,
        job_title=request.job_title,
        job_url=request.job_url,
        hiring_manager_name=request.hiring_manager_name,
        company_context=request.company_context,
    )
    return result


@app.post("/salary/research")
async def research_salary(
    request: SalaryRequest,
    _auth=Depends(verify_secret)
):
    """Research market compensation for a role."""
    from agents.salary_research_agent import SalaryResearchAgent
    agent = SalaryResearchAgent()
    result = await agent.research_compensation(
        company=request.company,
        job_title=request.job_title,
        location=request.location,
        company_stage=request.company_stage,
    )
    return result


@app.post("/salary/evaluate-offer")
async def evaluate_offer(
    request: OfferEvalRequest,
    _auth=Depends(verify_secret)
):
    """Evaluate a job offer against market and Rizwan's targets."""
    from agents.salary_research_agent import SalaryResearchAgent
    agent = SalaryResearchAgent()
    result = await agent.evaluate_offer(
        company=request.company,
        job_title=request.job_title,
        location=request.location,
        offered_base_monthly=request.offered_base_monthly,
        offered_currency=request.offered_currency,
        bonus_pct=request.bonus_pct,
        equity_details=request.equity_details,
        other_benefits=request.other_benefits,
    )
    return result


@app.post("/applications/review")
async def review_applications(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret)
):
    """Run application tracker — surfaces follow-ups and drafts emails."""
    from agents.application_tracker_agent import ApplicationTrackerAgent
    agent = ApplicationTrackerAgent()
    result = await agent.run()
    return result


@app.get("/applications/pipeline")
async def get_pipeline_report(_auth=Depends(verify_secret)):
    """Get the application pipeline report."""
    from agents.application_tracker_agent import ApplicationTrackerAgent
    agent = ApplicationTrackerAgent()
    return agent.get_pipeline_report()


@app.get("/resumes/{filename}")
async def download_resume(filename: str, _auth=Depends(verify_secret)):
    """Download a generated resume."""
    path = os.path.join(settings.output_resumes_dir, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename)


# ─── Resume Builds — view / edit / download / feedback (Phase 2.1) ────────
#
# Closes the loop after a G2 graph build: the user couldn't see the
# resume, edit it, download it, or give feedback. These five endpoints
# (plus the migration in db/resume_builds_user_feedback.sql) wire that up.

class ResumeBuildEdit(BaseModel):
    user_edited_md: str


class ResumeBuildFeedback(BaseModel):
    user_rating: Optional[int] = None  # 1-5; None = clear rating
    user_feedback: Optional[str] = None


def _safe_resume_build_row(row: dict) -> dict:
    """Strip noisy fields; return a UI-shaped dict."""
    return {
        "id":                row.get("id"),
        "job_id":            row.get("job_id"),
        "company_name":      row.get("company_name"),
        "status":            row.get("status"),
        "iterations":        row.get("iterations"),
        "cost_usd_total":    float(row.get("cost_usd_total") or 0),
        "latency_ms_total":  row.get("latency_ms_total"),
        "resume_pdf_url":    row.get("resume_pdf_url"),
        "resume_docx_url":   row.get("resume_docx_url"),
        "cover_email_md":    row.get("cover_email_md"),
        "user_rating":       row.get("user_rating"),
        "user_feedback":     row.get("user_feedback"),
        "user_edited_md":    row.get("user_edited_md"),
        "user_edited_at":    row.get("user_edited_at"),
        "created_at":        row.get("created_at"),
        "finalized_at":      row.get("finalized_at"),
        "error":             row.get("error"),
    }


@app.get("/resume-builds/by-job/{job_id}")
async def list_resume_builds_for_job(job_id: int, _auth=Depends(verify_secret)):
    """All builds for a job, latest first. Used by the Resume tab to
    show 'previous attempts' and let the user switch between them."""
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("resume_builds")
        .select("*")
        .eq("job_id", job_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = [_safe_resume_build_row(r) for r in (result.data or [])]
    return {"builds": rows, "total": len(rows)}


@app.get("/resume-builds/{build_id}")
async def get_resume_build(build_id: str, _auth=Depends(verify_secret)):
    """Single build record with all fields (without the heavy
    agent_transcript blob — fetch that via /resume-builds/{id}/transcript
    if you need it)."""
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("resume_builds")
        .select("*")
        .eq("id", build_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Resume build not found")
    return _safe_resume_build_row(rows[0])


@app.get("/resume-builds/{build_id}/markdown")
async def get_resume_build_markdown(build_id: str, _auth=Depends(verify_secret)):
    """Return the resume markdown content for the dashboard's preview/edit
    surface. Prefers user_edited_md if the user has saved an override; else
    fetches the canonical Storage object referenced from jobs.resume_path.

    Returns: {markdown: str, source: 'user_edit'|'storage'|'missing', byte_size: int}
    """
    from db.client import get_supabase
    import httpx
    db = get_supabase()
    rb = (
        db.table("resume_builds")
        .select("id, job_id, user_edited_md, user_edited_at")
        .eq("id", build_id)
        .limit(1)
        .execute()
        .data or []
    )
    if not rb:
        raise HTTPException(status_code=404, detail="Resume build not found")
    rb = rb[0]

    # 1. User edit takes precedence
    if rb.get("user_edited_md"):
        md = rb["user_edited_md"]
        return {"markdown": md, "source": "user_edit", "byte_size": len(md.encode("utf-8"))}

    # 2. Fall back to canonical Storage URL on the jobs row
    job = (
        db.table("jobs")
        .select("resume_path")
        .eq("id", rb["job_id"])
        .limit(1)
        .execute()
        .data or []
    )
    url = (job[0] if job else {}).get("resume_path")
    if not url or not url.startswith("http"):
        return {"markdown": "", "source": "missing", "byte_size": 0}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"markdown": "", "source": "missing",
                        "byte_size": 0,
                        "fetch_status": resp.status_code}
            md = resp.text
            return {"markdown": md, "source": "storage", "byte_size": len(md.encode("utf-8"))}
    except Exception as e:
        logger.warning(f"resume markdown fetch failed for {build_id}: {e}")
        return {"markdown": "", "source": "missing", "byte_size": 0,
                "error": str(e)[:200]}


@app.patch("/resume-builds/{build_id}/markdown")
async def save_resume_build_edit(
    build_id: str,
    payload: ResumeBuildEdit,
    _auth=Depends(verify_secret),
):
    """Save user-edited markdown over the agent draft. Stored in
    resume_builds.user_edited_md so the original Storage object is kept
    intact (audit trail). Display/download endpoints prefer this over
    the canonical version."""
    from db.client import get_supabase
    db = get_supabase()
    if not payload.user_edited_md or not payload.user_edited_md.strip():
        raise HTTPException(status_code=400, detail="user_edited_md cannot be empty")
    if len(payload.user_edited_md) > 200_000:
        raise HTTPException(status_code=400, detail="resume too large (>200KB)")

    result = (
        db.table("resume_builds")
        .update({
            "user_edited_md": payload.user_edited_md,
            "user_edited_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", build_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Resume build not found")
    return {"ok": True, "user_edited_at": result.data[0].get("user_edited_at")}


@app.post("/resume-builds/{build_id}/feedback")
async def save_resume_build_feedback(
    build_id: str,
    payload: ResumeBuildFeedback,
    _auth=Depends(verify_secret),
):
    """Save user rating + free-text feedback on a build. Either field
    is optional but at least one must be set. Used by the Sunday persona
    synth cron to learn from negative ratings."""
    from db.client import get_supabase
    db = get_supabase()
    if payload.user_rating is None and not payload.user_feedback:
        raise HTTPException(
            status_code=400,
            detail="Provide user_rating (1-5) and/or user_feedback (text)",
        )
    if payload.user_rating is not None and not (1 <= payload.user_rating <= 5):
        raise HTTPException(status_code=400, detail="user_rating must be 1-5")

    update: dict = {}
    if payload.user_rating is not None:
        update["user_rating"] = payload.user_rating
    if payload.user_feedback is not None:
        update["user_feedback"] = payload.user_feedback[:5000]  # cap

    result = (
        db.table("resume_builds")
        .update(update)
        .eq("id", build_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Resume build not found")
    return _safe_resume_build_row(result.data[0])


@app.get("/resume-builds/{build_id}/download")
async def download_resume_build(
    build_id: str,
    fmt: str = "md",
    _auth=Depends(verify_secret),
):
    """Download a resume in the chosen format.

    Currently supports:
      - md (markdown, the canonical format)

    docx/pdf are deliberately not implemented yet — they need a
    pandoc/python-docx pipeline. Track at GH issue when you need them.
    For now copy-paste the markdown into Pages/Word; the Phase 2.0
    typography is intentionally clean enough to render verbatim.
    """
    from db.client import get_supabase
    import httpx
    fmt = (fmt or "md").lower()
    if fmt != "md":
        raise HTTPException(
            status_code=400,
            detail=f"format '{fmt}' not yet supported — only 'md' for now",
        )

    db = get_supabase()
    rb = (
        db.table("resume_builds")
        .select("id, job_id, company_name, user_edited_md")
        .eq("id", build_id)
        .limit(1)
        .execute()
        .data or []
    )
    if not rb:
        raise HTTPException(status_code=404, detail="Resume build not found")
    rb = rb[0]

    # Source: user edit if present, else canonical Storage URL
    md = rb.get("user_edited_md")
    if not md:
        job = (
            db.table("jobs")
            .select("resume_path")
            .eq("id", rb["job_id"])
            .limit(1)
            .execute()
            .data or []
        )
        url = (job[0] if job else {}).get("resume_path")
        if url and url.startswith("http"):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        md = r.text
            except Exception:
                pass

    if not md:
        raise HTTPException(status_code=404, detail="No resume content to download")

    company = (rb.get("company_name") or "company").lower().replace(" ", "-")
    filename = f"resume_{company}_{rb['id'][:8]}.md"
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/pipeline/stats")
async def get_pipeline_stats(_auth=Depends(verify_secret)):
    """Get overall pipeline statistics."""
    from db.client import get_supabase
    from collections import Counter
    db = get_supabase()

    jobs_result = db.table("jobs").select("status, match_score").execute()
    apps_result = db.table("applications").select("status").execute()

    jobs_data = jobs_result.data or []
    apps_data = apps_result.data or []

    status_counts = Counter(j["status"] for j in jobs_data)
    score_buckets = {
        "0-39": sum(1 for j in jobs_data if j.get("match_score", 0) < 40),
        "40-59": sum(1 for j in jobs_data if 40 <= j.get("match_score", 0) < 60),
        "60-79": sum(1 for j in jobs_data if 60 <= j.get("match_score", 0) < 80),
        "80+": sum(1 for j in jobs_data if j.get("match_score", 0) >= 80),
    }

    return {
        "total_jobs": len(jobs_data),
        "by_status": dict(status_counts),
        "by_score": score_buckets,
        "applications": Counter(a["status"] for a in apps_data),
    }


# ── Profile endpoints ──────────────────────────────────────────────────────

@app.get("/profile")
async def get_profile(_auth=Depends(verify_secret)):
    """Return master profile + experience + certs + education."""
    from db.client import get_supabase
    db = get_supabase()

    master = db.table("profile_master").select("*").eq("id", 1).limit(1).execute()
    experience = db.table("profile_experience").select("*").order("sort_order").execute()
    certs = db.table("profile_certification").select("*").order("sort_order").execute()
    edu = db.table("profile_education").select("*").order("sort_order").execute()

    return {
        "master": (master.data or [None])[0],
        "experience": experience.data or [],
        "certifications": certs.data or [],
        "education": edu.data or [],
    }


@app.get("/profile/keywords")
async def get_profile_keywords(_auth=Depends(verify_secret), category: Optional[str] = None, limit: int = 500):
    """Return keyword bank, optionally filtered by category."""
    from db.client import get_supabase
    db = get_supabase()

    q = db.table("profile_keyword").select("*").order("ats_strength", desc=True).limit(limit)
    if category:
        q = q.eq("category", category)
    result = q.execute()

    cats = db.table("profile_keyword_category").select("*").order("total_occurrences", desc=True).execute()
    return {
        "keywords": result.data or [],
        "categories": cats.data or [],
    }


@app.get("/profile/sources")
async def get_profile_sources(_auth=Depends(verify_secret)):
    """Return parsed source-document registry."""
    from db.client import get_supabase
    from collections import Counter
    db = get_supabase()
    result = db.table("profile_source_document").select(
        "id, file_hash, file_name, document_class, char_count, file_size, parsed_at"
    ).order("parsed_at", desc=True).execute()
    docs = result.data or []
    by_class = Counter(d["document_class"] for d in docs)
    return {
        "documents": docs,
        "total": len(docs),
        "by_class": dict(by_class),
    }


# ── Profile edit endpoints (Phase B) ──────────────────────────────────────

class ProfileMasterUpdate(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    phones: Optional[list[str]] = None
    linkedin_url: Optional[str] = None
    core_competencies: Optional[list[str]] = None
    technical_knowledge: Optional[list[str]] = None
    languages: Optional[list[dict]] = None
    ai_solutions: Optional[list[dict]] = None


class ProfileExperienceUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    scope: Optional[str] = None
    dates: Optional[str] = None
    summary: Optional[str] = None
    highlights: Optional[list[str]] = None
    groups: Optional[list[dict]] = None


@app.put("/profile")
async def update_profile_master(
    payload: ProfileMasterUpdate,
    _auth=Depends(verify_secret),
):
    """Update master profile fields. Only provided fields are written."""
    from db.client import get_supabase
    db = get_supabase()
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("profile_master").update(updates).eq("id", 1).execute()
    # ─── Phase 1.2: re-extract STAR+R stories after a master-CV change ──
    # G9 is idempotent on (user_id, cv_hash); if nothing relevant changed
    # the enqueue collapses to a no-op via the queue's dedup gate. We
    # intentionally swallow failures here — story_bank lag is acceptable,
    # but failing the profile update because of a queue hiccup is not.
    g9_run_id = _maybe_enqueue_g9_after_profile_change(reason="profile_master")
    return {"updated": True, "row": (result.data or [None])[0], "g9_run_id": g9_run_id}


@app.put("/profile/experience/{exp_id}")
async def update_profile_experience(
    exp_id: int,
    payload: ProfileExperienceUpdate,
    _auth=Depends(verify_secret),
):
    """Update one experience entry."""
    from db.client import get_supabase
    db = get_supabase()
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("profile_experience").update(updates).eq("id", exp_id).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"Experience {exp_id} not found")
    # Phase 1.2: re-extract STAR+R stories after an experience change.
    g9_run_id = _maybe_enqueue_g9_after_profile_change(reason="profile_experience")
    return {"updated": True, "row": rows[0], "g9_run_id": g9_run_id}


def _maybe_enqueue_g9_after_profile_change(*, reason: str) -> Optional[str]:
    """Enqueue a G9 STAR+R re-extraction after a master-CV/experience change.

    Best-effort. Returns the jobs_runs.id of the enqueued run, or None on
    any failure (we never fail the profile update because of this).

    The queue's idempotency-key dedup means redundant calls are free: if
    the CV markdown hash hasn't changed since the last G9 run, this is a
    no-op. The cv_hash is recomputed inside enqueue_g9_story_extract via
    the same render pipeline G9 uses, so we don't recompute here.
    """
    try:
        import os
        from agents.g9_io import hash_cv, render_master_cv_md
        from api.queue import enqueue_g9_story_extract
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
        md = render_master_cv_md()
        cv_hash = hash_cv(md)
        run_id = enqueue_g9_story_extract(user_id, cv_hash=cv_hash)
        logger.info(
            f"G9 enqueue after {reason}: user_id={user_id} cv_hash={cv_hash[:12]} "
            f"run_id={run_id}"
        )
        return run_id
    except Exception as e:
        logger.warning(f"G9 enqueue after {reason} failed (non-fatal): {e}")
        return None


# ── Profile recommendations (Phase C) ─────────────────────────────────────

@app.get("/profile/recommendations")
async def get_profile_recommendations(
    _auth=Depends(verify_secret),
    include_dismissed: bool = False,
):
    """Return AI-generated profile improvement recommendations."""
    from db.client import get_supabase
    from collections import Counter
    db = get_supabase()
    q = db.table("profile_recommendation").select("*").order("severity", desc=True).order("created_at", desc=True)
    if not include_dismissed:
        q = q.eq("dismissed", False)
    result = q.execute()
    recs = result.data or []
    by_kind = Counter(r["kind"] for r in recs)
    by_severity = Counter(r["severity"] for r in recs)
    return {
        "recommendations": recs,
        "total": len(recs),
        "by_kind": dict(by_kind),
        "by_severity": dict(by_severity),
    }


class RecommendationDismiss(BaseModel):
    dismissed: bool = True


@app.put("/profile/recommendations/{rec_id}")
async def update_recommendation(
    rec_id: int,
    payload: RecommendationDismiss,
    _auth=Depends(verify_secret),
):
    """Dismiss/restore a recommendation."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("profile_recommendation").update(
        {"dismissed": payload.dismissed}
    ).eq("id", rec_id).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"Recommendation {rec_id} not found")
    return {"updated": True, "row": rows[0]}


@app.post("/profile/recommendations/regenerate")
async def regenerate_recommendations(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """Re-run the analyzer to refresh recommendations."""
    async def _run():
        from agents.profile_analyzer import ProfileAnalyzer
        analyzer = ProfileAnalyzer()
        await analyzer.run()
    background_tasks.add_task(_run)
    return {"status": "started", "message": "Recommendations refresh running in background"}


# ── Target Companies (Phase D) ────────────────────────────────────────────

class TargetCompanyCreate(BaseModel):
    name: str
    category: Optional[str] = None
    priority: str = "medium"
    careers_url: Optional[str] = None
    notes: Optional[str] = None


class TargetCompanyUpdate(BaseModel):
    is_target: Optional[bool] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    careers_url: Optional[str] = None
    notes: Optional[str] = None


@app.get("/companies/targets")
async def list_target_companies(_auth=Depends(verify_secret)):
    """List all target companies grouped by category.

    BUG-013: exclude phantom rows.
    """
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("companies")
        .select("*")
        .eq("is_target", True)
        .eq("is_phantom", False)
        .order("priority", desc=False)
        .order("name")
        .execute()
    )
    companies = result.data or []
    by_cat: dict[str, list] = {}
    for c in companies:
        by_cat.setdefault(c.get("category") or "Uncategorized", []).append(c)
    return {
        "companies": companies,
        "total": len(companies),
        "by_category": by_cat,
    }


@app.post("/companies/targets")
async def add_target_company(
    payload: TargetCompanyCreate,
    _auth=Depends(verify_secret),
):
    """Add a new target company."""
    from db.client import get_supabase
    from datetime import datetime, timezone
    db = get_supabase()
    row = {
        "name": payload.name,
        "category": payload.category,
        "priority": payload.priority,
        "careers_url": payload.careers_url,
        "notes": payload.notes,
        "is_target": True,
        "target_added_at": datetime.now(timezone.utc).isoformat(),
    }
    result = db.table("companies").upsert(row, on_conflict="name").execute()
    return {"created": True, "row": (result.data or [None])[0]}


@app.put("/companies/{company_id}")
async def update_company(
    company_id: str,
    payload: TargetCompanyUpdate,
    _auth=Depends(verify_secret),
):
    """Update a company's target/priority/etc."""
    from db.client import get_supabase
    db = get_supabase()
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("companies").update(updates).eq("id", company_id).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"updated": True, "row": rows[0]}


@app.delete("/companies/{company_id}")
async def remove_target_company(
    company_id: str,
    _auth=Depends(verify_secret),
):
    """Remove from targets (soft: just sets is_target=false)."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("companies").update({"is_target": False}).eq("id", company_id).execute()
    return {"removed": True, "row": (result.data or [None])[0]}


# BUG-011: LLM disclaimer patterns to strip from rendered knowledge cards.
# These appear when the synthesizer falls back to "general knowledge" — the
# user has no way of telling the data is stale, so the disclaimer reads as
# part of the actual research. Strip on read; the canonical content stays in
# the DB so a future re-synthesis fixes it once.
_LLM_DISCLAIMER_PATTERNS = [
    re.compile(
        r"\bAs (?:of|this analysis)[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bspecific (?:news|data|information) (?:from|for) the last \d+ months?"
        r" (?:is|are) not available\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bbased on general knowledge[^.]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:without|with no) recent web research[^.]*\.",
        re.IGNORECASE,
    ),
]


def _strip_llm_disclaimers(text: str | None) -> str | None:
    """BUG-011: Strip hard-coded LLM disclaimers so cards don't render
    self-undermining text like "As this analysis is based on general
    knowledge..." Leaves structural sentences around them intact."""
    if not text:
        return text
    cleaned = text
    for pat in _LLM_DISCLAIMER_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # Collapse the whitespace gaps the substitutions left behind.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned)
    return cleaned.strip()


@app.get("/companies/{company_name}/knowledge")
async def get_company_knowledge(
    company_name: str,
    _auth=Depends(verify_secret),
):
    """Return full research intel for a company (overview, news, culture, recruitment process, etc.).

    BUG-011: also exposes ``last_synthesized_at`` from ``company_personas`` so
    the dashboard can render a freshness pill + a "Refresh" CTA when the
    research is older than 30 days. Disclaimer-y LLM filler is stripped from
    each section's content on read.
    """
    from db.client import get_supabase
    db = get_supabase()
    # Look up company record
    company = db.table("companies").select("*").eq("name", company_name).limit(1).execute()
    company_row = (company.data or [None])[0]
    # Pull all knowledge sections
    knowledge = db.table("company_knowledge").select(
        "section, content, source_url, scraped_at"
    ).eq("company_name", company_name).execute()
    knowledge_rows = knowledge.data or []
    # BUG-011: strip hard-coded LLM disclaimers from every section's content.
    for row in knowledge_rows:
        row["content"] = _strip_llm_disclaimers(row.get("content")) or ""
    # BUG-011: surface the persona-row freshness timestamp so the UI can
    # render a relative-time pill ("3 days ago" / "stale — 47 days old").
    last_synthesized_at: str | None = None
    try:
        persona = (
            db.table("company_personas")
            .select("last_synthesized_at")
            .eq("company_name", company_name)
            .limit(1)
            .execute()
        )
        persona_row = (persona.data or [None])[0]
        if persona_row:
            last_synthesized_at = persona_row.get("last_synthesized_at")
    except Exception as e:
        logger.warning(
            f"get_company_knowledge[{company_name}]: persona freshness lookup failed: {e}"
        )
    return {
        "company": company_row,
        "knowledge": knowledge_rows,
        "section_count": len(knowledge_rows),
        "last_synthesized_at": last_synthesized_at,
    }


class CompanyResearchRequest(BaseModel):
    company_name: Optional[str] = None
    priority: Optional[str] = None  # high | medium | low — research only this tier
    force: bool = False


@app.post("/companies/research")
async def trigger_company_research(
    request: CompanyResearchRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """
    Trigger CompanyAgent research on one company or a tier of targets.
    Runs in background; results stored in company_knowledge.
    """
    async def _run():
        from agents.company_agent import CompanyAgent
        from db.client import get_supabase as _sb
        names: list[str] = []
        if request.company_name:
            names = [request.company_name]
        else:
            db = _sb()
            q = db.table("companies").select("name").eq("is_target", True)
            if request.priority:
                q = q.eq("priority", request.priority)
            names = [c["name"] for c in (q.execute().data or [])]

        # Run with concurrency limit
        sem = asyncio.Semaphore(3)
        async def _one(name: str):
            async with sem:
                try:
                    agent = CompanyAgent(name)
                    await agent.build_or_refresh(force=request.force)
                except Exception as e:
                    logger.warning(f"Research failed for {name}: {e}")

        await asyncio.gather(*[_one(n) for n in names])

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "scope": (
            request.company_name
            or (f"all {request.priority}-priority targets" if request.priority else "all targets")
        ),
        "message": "Research running in background. Check /companies/{name}/knowledge for results.",
    }


@app.post("/pipeline/run-targets")
async def run_pipeline_targets(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """
    Workflow v2: scout-only mode. Scans all target companies, scores + classifies
    archetype + assesses posting legitimacy, stores in DB. Does NOT auto-build
    resumes — user clicks "Generate Resume" on each high-scoring job.
    """
    async def _run():
        from pipeline import JobHuntPipeline
        from db.client import get_supabase
        db = get_supabase()
        targets = db.table("companies").select("name").eq("is_target", True).execute()
        target_names = [t["name"] for t in (targets.data or [])]
        pipeline = JobHuntPipeline()
        await pipeline.scout_only(target_names=target_names)
    background_tasks.add_task(_run)
    return {"status": "started", "message": "Scout-only pipeline running. No auto-resume — manual gate at score >= 85."}


@app.post("/jobs/reclassify")
async def reclassify_existing_jobs(
    background_tasks: BackgroundTasks,
    only_missing: bool = True,
    _auth=Depends(verify_secret),
):
    """
    Re-run archetype + legitimacy classification on jobs that were scored
    BEFORE workflow v2. Default: only jobs where archetype IS NULL.
    Set only_missing=false to reclassify ALL jobs.
    """
    async def _run():
        from db.client import get_supabase, upsert_job
        from agents.job_scout_agent import JobScoutAgent

        db = get_supabase()
        q = db.table("jobs").select("id, title, company, location, description, match_score, archetype")
        if only_missing:
            q = q.is_("archetype", "null")
        rows = (q.execute().data) or []
        if not rows:
            logger.info("No jobs to reclassify")
            return

        logger.info(f"Reclassifying {len(rows)} jobs...")
        scout = JobScoutAgent()

        # Re-run scoring + classifier in batches of 25
        batch_size = 25
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                # Pass through the existing _score_jobs_batch which now also classifies
                rescored = await scout._score_jobs_batch(batch)
                for j in rescored:
                    if j.get("id") and j.get("archetype"):
                        # Don't overwrite match_score — only add archetype + legitimacy
                        db.table("jobs").update({
                            "archetype": j.get("archetype"),
                            "legitimacy_tier": j.get("legitimacy_tier"),
                            "legitimacy_signals": j.get("legitimacy_signals", []),
                        }).eq("id", j["id"]).execute()
            except Exception as e:
                logger.error(f"Reclassify batch failed: {e}")

        logger.info(f"Reclassified {len(rows)} jobs")

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "scope": "jobs missing archetype" if only_missing else "all jobs",
        "message": "Reclassification running in background",
    }


@app.post("/jobs/{job_id}/generate-resume")
async def generate_resume_for_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    max_cost_usd: Optional[float] = None,
    force: bool = False,
    _auth=Depends(verify_secret),
):
    """
    Workflow v2: manual trigger to build a tailored resume for ONE job.
    Only allowed for jobs scoring >= 85 (configurable threshold).
    Runs the recruitment-expert flow in background.

    Phase 1.11: pass max_cost_usd as a query param to override the default
    per-build cost cap (settings.g2_max_cost_usd, default $5). Useful for
    top-tier targets where you want to allow more iterations.
        POST /jobs/123/generate-resume?max_cost_usd=10
    Refused if max_cost_usd < 0.50 (would always cost-cap before any work).

    Phase 1.12: persona quality gate. Refuses builds for companies whose
    persona quality is below g2_min_persona_quality (default 'medium')
    unless force=true. This prevents wasting ~$5 on a build for Visa,
    Thunes, etc. where 3+ of 5 recruitment-intel sections are missing.
        POST /jobs/123/generate-resume?force=true
    Returns HTTP 400 with a structured detail if blocked, so the
    dashboard can show a confirm dialog and retry with force=true.
    """
    from db.client import get_supabase
    from api._job_guards import load_open_job
    db = get_supabase()
    # 2026-05-12: route through load_open_job. This was the highest-impact
    # bypass found by Agent B's code audit — a legacy twin of
    # `/workspace/{id}/build-resume` that lacked the OKX-1641 wallet guard.
    # `_auth=verify_secret` (no per-user scope) → pass user_id=None and rely
    # on the staleness 409. If posting_closed_at or validation_failed is
    # set, 409 with a structured `code` so the dashboard can show a confirm
    # dialog and offer a `force=true` retry.
    job = load_open_job(
        db,
        job_id=job_id,
        user_id=None,
        force=force,
        cost_label="G2 resume build (~$1-5)",
    )
    score = job.get("match_score", 0)
    if score < 85:
        raise HTTPException(
            status_code=400,
            detail=f"Job scored {score}/100. Resume generation gated at 85+. Update threshold via config or override.",
        )

    if max_cost_usd is not None:
        if max_cost_usd < 0.50:
            raise HTTPException(
                status_code=400,
                detail=f"max_cost_usd={max_cost_usd} too low — minimum is $0.50 to avoid no-op builds.",
            )
        # Stash on the job dict so _process_single_job picks it up below.
        # job is a fresh dict from Supabase; mutating it locally is fine.
        job["_g2_max_cost_usd"] = max_cost_usd

    # ── Phase 1.12: persona quality gate ────────────────────────────────
    from resume_agents.g2_run import check_persona_quality_gate
    company_name = job.get("company") or ""
    gate = check_persona_quality_gate(company_name, force=force)
    if gate.verdict == "blocked":
        # 400 with structured payload so the dashboard can show a confirm
        # dialog and offer a "force anyway" retry.
        raise HTTPException(
            status_code=400,
            detail={
                "code": "persona_quality_too_low",
                "message": gate.message,
                "company_name": company_name,
                "persona_quality": gate.quality,
                "persona_version": gate.persona_version,
                "unknown_sections": gate.unknown_sections,
                "min_quality": (
                    "medium"
                    if not hasattr(check_persona_quality_gate, "_settings_cache")
                    else check_persona_quality_gate._settings_cache
                ),
                "retry_with_force": True,
            },
        )

    async def _run():
        from pipeline import JobHuntPipeline
        from datetime import datetime, timezone
        pipeline = JobHuntPipeline()
        try:
            await pipeline._process_single_job(job)
            db.table("jobs").update({
                "resume_generated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).execute()
        except Exception as e:
            logger.error(f"Resume generation failed for job {job_id}: {e}")

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": f"Generating tailored resume for {job.get('title')} @ {job.get('company')}. Refresh in ~60s.",
        "job_id": job_id,
        "score": score,
        "archetype": job.get("archetype"),
        "max_cost_usd": max_cost_usd,
        "force": force,
        "persona_gate": {
            "verdict": gate.verdict,
            "quality": gate.quality,
            "persona_version": gate.persona_version,
            "message": gate.message,
        },
    }


# ── G3 Interview Prep Graph (Phase 2) ─────────────────────────────────────

@app.post("/jobs/{job_id}/prep-interview")
async def prep_interview_for_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    application_id: Optional[str] = None,
    round_type: str = "hm",
    round_number: int = 1,
    max_cost_usd: Optional[float] = None,
    force: bool = False,
    _auth=Depends(verify_secret),
):
    """
    Phase 2: manual trigger to build a persona-aware interview prep pack
    for ONE application/round. Runs the G3 LangGraph in background.

    Required: an `applications` row must exist for this job (the kanban
    transition to 'interview' creates it). Pass `application_id` explicitly
    OR rely on auto-resolution: we look up applications by job_id and
    pick the most recent.

    `round_type` is one of: recruiter | hm | panel | exec | technical | take_home
    `round_number` is 1-based.

    Phase 2 cost cap: pass max_cost_usd to override settings.g3_max_cost_usd
    (default $3). Refused if max_cost_usd < $0.30 (would always cost-cap
    before any work). Worst-case cost is capped at the value + ~$0 fixed
    overhead since the only unbounded loop is the mock interview loop.

    Phase 2 persona quality gate: reuses G2's check_persona_quality_gate.
    Refuses preps for companies whose persona quality is below
    g3_min_persona_quality (default 'medium') unless force=true. Returns
    HTTP 400 with structured detail so the dashboard can show a confirm
    dialog and retry with force=true.
    """
    from db.client import get_supabase
    from api._job_guards import load_open_job
    db = get_supabase()
    # 2026-05-12: wallet guard. G3 prep packs cost ~$0.50 each; preparing
    # for an interview on a closed posting is pure waste. Same 409 pattern
    # as generate-resume above — pass force=true to override (e.g. user
    # had an interview scheduled BEFORE the posting got marked closed).
    job = load_open_job(
        db,
        job_id=job_id,
        user_id=None,
        force=force,
        cost_label="G3 interview prep (~$0.50)",
    )

    # Resolve application_id if not provided
    resolved_application_id = application_id
    if not resolved_application_id:
        apps = (
            db.table("applications")
            .select("id, status, created_at")
            .eq("job_id", job_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not apps.data:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No application row exists for job {job_id}. "
                    f"POST /applications with job_id first, or pass "
                    f"application_id= explicitly."
                ),
            )
        resolved_application_id = apps.data[0]["id"]

    if max_cost_usd is not None and max_cost_usd < 0.30:
        raise HTTPException(
            status_code=400,
            detail=f"max_cost_usd={max_cost_usd} too low — minimum is $0.30 to avoid no-op preps.",
        )

    # ── Persona quality gate (reuses G2's check_persona_quality_gate) ─────
    from resume_agents.g2_run import check_persona_quality_gate
    company_name = job.get("company") or ""
    gate = check_persona_quality_gate(
        company_name,
        force=force,
        min_quality=settings.g3_min_persona_quality,
    )
    if gate.verdict == "blocked":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "persona_quality_too_low",
                "message": gate.message,
                "company_name": company_name,
                "persona_quality": gate.quality,
                "persona_version": gate.persona_version,
                "unknown_sections": gate.unknown_sections,
                "min_quality": settings.g3_min_persona_quality,
                "retry_with_force": True,
            },
        )

    async def _run():
        from interview_agents.g3_run import run_g3_graph
        try:
            await run_g3_graph(
                application_id=resolved_application_id,
                round_type=round_type,
                round_number=round_number,
                company_name=company_name,
                max_cost_usd=max_cost_usd,
            )
        except Exception as e:
            logger.error(
                f"G3 prep-interview failed for application {resolved_application_id}: {e}"
            )

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": (
            f"Generating interview prep pack for {job.get('title')} @ "
            f"{company_name} (round {round_type}#{round_number}). Refresh in ~60s."
        ),
        "job_id": job_id,
        "application_id": resolved_application_id,
        "round_type": round_type,
        "round_number": round_number,
        "max_cost_usd": max_cost_usd,
        "force": force,
        "persona_gate": {
            "verdict": gate.verdict,
            "quality": gate.quality,
            "persona_version": gate.persona_version,
            "message": gate.message,
        },
    }


# ── Job Detail (Phase D) ──────────────────────────────────────────────────

@app.get("/jobs/{job_id}/detail")
async def get_job_detail(job_id: int, _auth=Depends(verify_secret)):
    """Full job detail including artifacts paths + fit details."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("jobs").select("*").eq("id", job_id).limit(1).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Job not found")
    job = rows[0]

    # Workflow v2: artifacts can be either a remote URL (Supabase Storage,
    # persistent) OR a local container path (Railway, ephemeral — lost on
    # redeploy). Detect the kind so the dashboard knows whether to download
    # via URL or fall back.
    artifacts: dict = {}
    import os
    # BUG-030 (2026-05-12): `report_path` removed from this loop — column is
    # never written by any pipeline. See db/client.py `_JOBS_COLUMNS` note and
    # the dead-column-gate archetype in docs/GAP_CLOSURE_ROADMAP §17.
    for k in ("resume_path", "email_path", "interview_path"):
        p = job.get(k)
        if not p:
            artifacts[k] = {"exists": False}
            continue
        if p.startswith("http"):
            # Remote URL — assume exists (signed URL)
            artifacts[k] = {"exists": True, "url": p, "kind": "remote"}
            continue
        # Local path — only readable if container hasn't redeployed since
        if os.path.exists(p):
            artifacts[k] = {"exists": True, "kind": "local", "size": os.path.getsize(p)}
            if p.endswith((".txt", ".md")):
                try:
                    with open(p, "r") as f:
                        artifacts[k]["content"] = f.read()
                except Exception:
                    pass
        else:
            artifacts[k] = {"exists": False, "path": p, "kind": "local-missing"}

    # BUG-031 / BUG-032 (2026-05-12): synthesize cover_email and
    # interview-prep artifacts from their REAL sources.
    #
    # `jobs.email_path` and `jobs.interview_path` are dead-column gates —
    # G2 writes `resume_builds.cover_email_md` (cover email lives inline
    # in the latest converged build) and G3 writes
    # `interview_prep.prep_pack_url` + `interview_prep.prep_pack_md`.
    # Without this synthesis the Cover-email + Interview-prep ArtifactCards
    # always render "missing" after a successful build.
    try:
        rb_rows = (
            db.table("resume_builds")
            .select("status, cover_email_md, created_at")
            .eq("job_id", job_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        ).data or []
        # Prefer converged build's cover_email_md; fall back to latest.
        cover_md = None
        converged = next(
            (r for r in rb_rows
             if r.get("status") == "converged" and r.get("cover_email_md")),
            None,
        )
        if converged:
            cover_md = converged.get("cover_email_md")
        elif rb_rows:
            cover_md = rb_rows[0].get("cover_email_md")
        if cover_md:
            artifacts["email_path"] = {
                "exists": True,
                "kind": "inline",
                "content": cover_md,
                "size": len((cover_md or "").encode("utf-8")),
            }
    except Exception as exc:
        logger.warning(
            "resume_builds.cover_email_md lookup failed for job %s: %s",
            job_id, exc,
        )

    try:
        ip_rows = (
            db.table("interview_prep")
            .select("status, prep_pack_url, prep_pack_md, created_at")
            .eq("job_id", job_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        ).data or []
        prep = next(
            (r for r in ip_rows
             if r.get("status") == "converged"
             and (r.get("prep_pack_url") or r.get("prep_pack_md"))),
            None,
        )
        if prep is None and ip_rows:
            # Fall back to latest row with any pack content (e.g. storage
            # upload failed but markdown was generated — see G3 io.py).
            prep = next(
                (r for r in ip_rows
                 if r.get("prep_pack_url") or r.get("prep_pack_md")),
                None,
            )
        if prep:
            if prep.get("prep_pack_url"):
                artifacts["interview_path"] = {
                    "exists": True,
                    "kind": "remote",
                    "url": prep["prep_pack_url"],
                }
                if prep.get("prep_pack_md"):
                    artifacts["interview_path"]["content"] = prep["prep_pack_md"]
            elif prep.get("prep_pack_md"):
                # Storage upload failed but pack content exists — render inline.
                md = prep["prep_pack_md"]
                artifacts["interview_path"] = {
                    "exists": True,
                    "kind": "inline",
                    "content": md,
                    "size": len((md or "").encode("utf-8")),
                }
    except Exception as exc:
        logger.warning(
            "interview_prep lookup failed for job %s: %s",
            job_id, exc,
        )

    # Application status (if any)
    apps = db.table("applications").select("*").eq("job_id", job_id).limit(1).execute()
    application = (apps.data or [None])[0]
    return {"job": job, "artifacts": artifacts, "application": application}


# ── Applications (Phase D) ────────────────────────────────────────────────

class ApplicationCreate(BaseModel):
    job_id: int
    status: str = "evaluated"
    notes: Optional[str] = None


class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    follow_up_due: Optional[str] = None
    applied_date: Optional[str] = None


@app.get("/applications")
async def list_applications(_auth=Depends(verify_secret)):
    """List all applications grouped by status (kanban columns).

    BUG-012: also annotates each row with ``threshold_violated`` so the
    dashboard can pill rows whose underlying ``jobs.match_score`` came in
    below ``settings.apply_threshold`` (default 85). This explains *why*
    a rejection happened without forcing the user to recompute the
    fit-score arithmetic in their head.
    """
    from db.client import get_supabase
    from collections import defaultdict
    settings = get_settings()
    db = get_supabase()
    apps = db.table("applications").select("*").order("created_at", desc=True).execute()
    apps_data = apps.data or []
    apply_threshold = int(getattr(settings, "apply_threshold", 85) or 85)
    # Also enrich with job info
    job_map: dict = {}
    if apps_data:
        job_ids = list({a["job_id"] for a in apps_data if a.get("job_id")})
        if job_ids:
            jobs = db.table("jobs").select("id, title, company, location, match_score, url").in_("id", job_ids).execute()
            job_map = {j["id"]: j for j in (jobs.data or [])}
            for a in apps_data:
                a["job"] = job_map.get(a.get("job_id"))
    # Apply the threshold annotation after the join so the value is always
    # consistent with the canonical jobs.match_score the user sees elsewhere.
    for a in apps_data:
        job = a.get("job") or {}
        match_score = job.get("match_score")
        a["threshold_violated"] = (
            isinstance(match_score, (int, float)) and match_score < apply_threshold
        )
    by_status: dict[str, list] = defaultdict(list)
    for a in apps_data:
        by_status[a.get("status", "evaluated")].append(a)
    return {
        "applications": apps_data,
        "by_status": dict(by_status),
        "total": len(apps_data),
        "apply_threshold": apply_threshold,
    }


@app.post("/applications")
async def create_application(
    payload: ApplicationCreate,
    _auth=Depends(verify_secret),
):
    """Create application from a job (used when user clicks 'Apply')."""
    from db.client import get_supabase
    db = get_supabase()
    job_result = db.table("jobs").select("*").eq("id", payload.job_id).limit(1).execute()
    job_rows = job_result.data or []
    if not job_rows:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_rows[0]
    row = {
        "job_id": payload.job_id,
        "company": job.get("company", ""),
        "role": job.get("title", ""),
        "status": payload.status,
        "score": (job.get("match_score") or 0) / 20.0,  # 0-100 → 0-5
        "resume_path": job.get("resume_path"),
        "email_path": job.get("email_path"),
        "interview_path": job.get("interview_path"),
        "notes": payload.notes,
        "company_id": job.get("company_id"),
    }
    result = db.table("applications").insert(row).execute()
    return {"created": True, "row": (result.data or [None])[0]}


@app.put("/applications/{app_id}")
async def update_application(
    app_id: str,
    payload: ApplicationUpdate,
    _auth=Depends(verify_secret),
):
    """Update application status/notes/dates."""
    from db.client import get_supabase
    db = get_supabase()
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    # If status is moving to 'applied' and applied_date not set, set it
    if updates.get("status") == "applied" and "applied_date" not in updates:
        from datetime import date
        updates["applied_date"] = date.today().isoformat()
    result = db.table("applications").update(updates).eq("id", app_id).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"updated": True, "row": rows[0]}


# ── Resume Outcomes (Phase 1.5 — the learning loop) ───────────────────────
# resume_outcomes is the "did the resume actually work?" tracking table.
# User logs from /jobs/[id] page after applying. The persona_synthesizer
# reads this weekly to update success_patterns / failure_patterns on
# company_personas.

class ResumeOutcomeUpsert(BaseModel):
    """All fields nullable — the user fills these in over time as outcomes
    become known (applied → response → interview → offer)."""
    job_id: Optional[int] = None
    resume_build_id: Optional[str] = None
    application_id: Optional[str] = None
    company_name: Optional[str] = None
    ats_passed: Optional[bool] = None
    submitted_at: Optional[str] = None
    recruiter_responded: Optional[bool] = None
    recruiter_response_at: Optional[str] = None
    interview_received: Optional[bool] = None
    rounds_reached: Optional[int] = None
    offer_received: Optional[bool] = None
    rejected_reason: Optional[str] = None
    self_rated_quality: Optional[int] = None  # 1-5
    notes: Optional[str] = None


@app.get("/resumes/outcomes/by-job/{job_id}")
async def get_outcome_by_job(job_id: int, _auth=Depends(verify_secret)):
    """
    Return the most-recent outcome row for this job (or null if none).
    There can be multiple if multiple resume_builds exist for the same
    job; we return the most recently logged.
    """
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("resume_outcomes")
        .select("*")
        .eq("job_id", job_id)
        .order("logged_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return {"outcome": rows[0] if rows else None}


@app.post("/resumes/outcomes")
async def upsert_outcome(
    payload: ResumeOutcomeUpsert,
    _auth=Depends(verify_secret),
):
    """
    Create OR update an outcome row.

    Resolution order for the target row:
      1. If `resume_build_id` provided and a row exists for it → update
      2. Else if `job_id` provided + a row already exists for this job
         → update the most recent one
      3. Else → INSERT a new row
    """
    from db.client import get_supabase
    db = get_supabase()

    payload_dict: dict = {k: v for k, v in payload.dict().items() if v is not None}
    if not payload_dict:
        raise HTTPException(status_code=400, detail="No fields to write")

    target_id: Optional[str] = None

    # Case 1: resume_build_id provided — find by it
    if payload_dict.get("resume_build_id"):
        existing = (
            db.table("resume_outcomes")
            .select("id")
            .eq("resume_build_id", payload_dict["resume_build_id"])
            .limit(1)
            .execute()
        )
        if existing.data:
            target_id = existing.data[0]["id"]

    # Case 2: fall back to job_id-based update
    elif payload_dict.get("job_id"):
        existing = (
            db.table("resume_outcomes")
            .select("id")
            .eq("job_id", payload_dict["job_id"])
            .order("logged_at", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            target_id = existing.data[0]["id"]

    if target_id:
        result = (
            db.table("resume_outcomes")
            .update(payload_dict)
            .eq("id", target_id)
            .execute()
        )
        return {"updated": True, "row": (result.data or [None])[0]}

    # Case 3: INSERT new row. If company_name not passed but job_id is,
    # auto-fill company_name from the job for downstream persona aggregation.
    if not payload_dict.get("company_name") and payload_dict.get("job_id"):
        job_lookup = (
            db.table("jobs")
            .select("company")
            .eq("id", payload_dict["job_id"])
            .limit(1)
            .execute()
        )
        if job_lookup.data:
            payload_dict["company_name"] = job_lookup.data[0]["company"]

    result = db.table("resume_outcomes").insert(payload_dict).execute()
    return {"created": True, "row": (result.data or [None])[0]}


@app.patch("/resumes/outcomes/{outcome_id}")
async def patch_outcome(
    outcome_id: str,
    payload: ResumeOutcomeUpsert,
    _auth=Depends(verify_secret),
):
    """Direct PATCH by outcome id. Used when the client already has the row id."""
    from db.client import get_supabase
    updates: dict = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = (
        get_supabase()
        .table("resume_outcomes")
        .update(updates)
        .eq("id", outcome_id)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Outcome not found")
    return {"updated": True, "row": rows[0]}


@app.get("/resumes/outcomes/conversion")
async def get_conversion_funnel(_auth=Depends(verify_secret)):
    """
    Per-company conversion funnel from the v_company_conversion_funnel view.
    Used by the dashboard to show which company personas are converting best.
    """
    from db.client import get_supabase
    try:
        result = get_supabase().table("v_company_conversion_funnel").select("*").execute()
        return {"funnel": result.data or []}
    except Exception as e:
        # View may not exist yet on dev DBs that haven't run multi_llm_schema
        logger.warning(f"conversion funnel view query failed: {e}")
        return {"funnel": [], "warning": "v_company_conversion_funnel view not present"}


# ── Persona Synthesis (Phase 1.6 — manual trigger; weekly cron in main.py) ──

@app.post("/personas/synthesize")
async def trigger_persona_synthesis(
    background_tasks: BackgroundTasks,
    company_name: Optional[str] = None,
    force: bool = False,
    quality_filter: Optional[str] = None,
    _auth=Depends(verify_secret),
):
    """
    Manually trigger PersonaSynthesizer. By default, runs against all
    companies with personas. Pass `company_name` to limit to one.
    `force=true` re-synthesizes even if no new data since last run.

    Phase 1.13: pass `quality_filter` (one of low/medium/high/unknown)
    to bulk-regenerate only personas at that quality tier. Useful for
    fixing all 5 'low' personas in one click instead of clicking
    'Regenerate' five times. Mutually exclusive with `company_name`
    (company_name wins if both are set, but the dashboard never sends
    both).
    """
    allowed_quality = {"low", "medium", "high", "unknown"}
    if quality_filter is not None and quality_filter not in allowed_quality:
        raise HTTPException(
            status_code=400,
            detail=f"quality_filter must be one of {sorted(allowed_quality)}, got {quality_filter!r}",
        )

    # Pre-compute the filtered company list synchronously so we can echo
    # the count in the response. The actual synthesis still runs in the
    # background so the request returns immediately.
    filtered_names: list[str] = []
    if quality_filter and not company_name:
        from db.client import get_supabase
        rows = (
            get_supabase()
            .table("company_personas")
            .select("company_name, metadata")
            .execute()
            .data
        ) or []
        filtered_names = [
            r["company_name"]
            for r in rows
            if (r.get("metadata") or {}).get("persona_quality", "unknown")
            == quality_filter
        ]

    async def _run():
        from agents.persona_synthesizer import PersonaSynthesizer
        synth = PersonaSynthesizer()
        if company_name:
            await synth.synthesize_one(company_name, force=force)
        elif quality_filter:
            # Bulk-regenerate only personas matching the quality tier.
            # We iterate manually instead of calling .run() so we don't
            # touch personas at other quality tiers.
            for name in filtered_names:
                try:
                    await synth.synthesize_one(name, force=force)
                except Exception as e:
                    logger.warning(
                        f"persona synth failed for {name} (quality={quality_filter}): {e}"
                    )
        else:
            await synth.run(force=force)

    background_tasks.add_task(_run)

    if company_name:
        scope = company_name
    elif quality_filter:
        scope = f"quality={quality_filter} ({len(filtered_names)} personas)"
    else:
        scope = "all_personas"

    return {
        "status": "started",
        "scope": scope,
        "force": force,
        "quality_filter": quality_filter,
        "filtered_count": (
            len(filtered_names) if quality_filter and not company_name else None
        ),
        "message": "Persona synthesis running in background.",
    }


# ─── Phase 2.2 — Deep research persona builder ────────────────────────────
@app.post("/personas/backfill-embeddings")
async def trigger_backfill_embeddings(
    company: Optional[str] = None,
    _auth=Depends(verify_secret),
):
    """One-shot: re-embed any company_knowledge rows whose embedding IS NULL.

    Used after the deep-research migration to embed the 30 v1-persona
    companies whose seed knowledge was inserted without embeddings.
    Pass `company` to scope to one company; omit for all.

    Synchronous (typical run is <30s for 70 cos × 13 sec).
    """
    from agents.persona_deep_research import backfill_embeddings
    result = await backfill_embeddings(company)
    return {"status": "complete", **result}


@app.post("/personas/deep-research")
async def trigger_deep_research(
    company: str,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    _auth=Depends(verify_secret),
):
    """
    Build (or refresh) a company persona using live web research.

    Pipeline:
      1. 8 parallel Apify rag-web-browser queries (overview, news, careers,
         culture, tech_stack, leadership, competitors, interview process)
      2. Gemini 2.5 Pro long-context synthesis into 13 knowledge sections
         + system_prompt_template + ats_keyword_bank + persona_quality
      3. Upsert company_knowledge (per section) and company_personas
         (bumps version on refresh)

    Cost: ~$0.20/company (Apify ~$0.10 + Gemini ~$0.10).

    Requires APIFY_TOKEN env var. Without it, the call still runs but
    returns persona_quality='low' since synthesis falls back to LLM
    prior-knowledge only.

    Pass `sync=true` to wait for the result inline (90-180s). Default
    is background — returns immediately, poll /personas/{company} to
    see the new version.
    """
    if not company or not company.strip():
        raise HTTPException(status_code=400, detail="company is required")
    company = company.strip()

    if sync:
        from agents.persona_deep_research import deep_research_persona
        result = await deep_research_persona(company)
        return {
            "status": "complete",
            "company": result.company_name,
            "persona_quality": result.persona_quality,
            "sources_count": result.sources_count,
            "knowledge_sections": list(result.sections.keys()),
            "ats_keyword_bank": result.ats_keyword_bank,
            "success_patterns_count": len(result.success_patterns),
            "failure_patterns_count": len(result.failure_patterns),
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "note": result.note,
        }

    async def _run():
        try:
            from agents.persona_deep_research import deep_research_persona
            r = await deep_research_persona(company)
            logger.info(
                f"deep_research_persona[{company}]: quality={r.persona_quality} "
                f"sources={r.sources_count} sections={len(r.sections)} "
                f"cost=${r.cost_usd:.4f} latency={r.latency_ms}ms"
            )
        except Exception as e:
            logger.exception(f"deep_research_persona[{company}] failed")

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "company": company,
        "message": (
            f"Deep-research persona build for {company} running in background. "
            f"Poll /personas/{company} to see the new version (~2 min)."
        ),
    }


@app.post("/personas/refresh-news")
async def trigger_refresh_news(
    background_tasks: BackgroundTasks,
    company: Optional[str] = None,
    sync: bool = False,
    _auth=Depends(verify_secret),
):
    """Daily news-only refresh. Cheap variant of /personas/deep-research.

    PR C: meant to be called by the scheduled-tasks MCP cron once per day
    for every target company. Updates ONLY the `news` section + bumps
    last_synthesized_at. Leaves the other 12 sections + success/failure
    patterns + ATS bank untouched.

    Cost: ~$0.005 Apify per company × 70 × 30 days = ~$10/month.

    `company`: pass to refresh ONE company. Omit to refresh ALL targets.
    `sync`: pass true to wait inline; default is background.
    """
    from agents.persona_deep_research import refresh_news_only
    from db.client import get_supabase

    if company:
        if sync:
            r = await refresh_news_only(company)
            return {"status": "complete", **r}
        async def _run_one():
            try:
                r = await refresh_news_only(company)
                logger.info(
                    f"refresh_news_only[{company}]: chars={r['news_chars']} "
                    f"sources={r['sources_count']} lat={r['latency_ms']}ms"
                )
            except Exception:
                logger.exception(f"refresh_news_only[{company}] failed")
        background_tasks.add_task(_run_one)
        return {
            "status": "started",
            "company": company,
            "message": f"News refresh for {company} running in background.",
        }

    # All targets — BUG-013: exclude phantoms from the news-refresh roster.
    rows = (
        get_supabase()
        .table("companies")
        .select("name")
        .eq("is_target", True)
        .eq("is_phantom", False)
        .execute()
        .data or []
    )
    company_names = [r["name"] for r in rows]

    async def _run_all():
        from agents.persona_deep_research import refresh_news_only
        for name in company_names:
            try:
                r = await refresh_news_only(name)
                logger.info(
                    f"[news-cron] {name}: sources={r['sources_count']} chars={r['news_chars']}"
                )
            except Exception as e:
                logger.warning(f"[news-cron] {name} failed: {e}")

    background_tasks.add_task(_run_all)
    return {
        "status": "started",
        "queued_count": len(company_names),
        "message": (
            f"Daily news refresh started for {len(company_names)} companies. "
            f"Estimated runtime: ~{len(company_names)} minutes (~$0.005/company)."
        ),
    }


@app.post("/personas/deep-research-batch")
async def trigger_deep_research_batch(
    background_tasks: BackgroundTasks,
    priority: Optional[str] = None,
    only_missing: bool = True,
    _auth=Depends(verify_secret),
):
    """
    Run deep research across all target companies, sequentially.

    Filters:
      - priority: 'high' | 'medium' | 'low' (defaults to all priorities)
      - only_missing: when True (default), skips companies that already
        have a persona. Set to False to refresh existing personas too.

    Uses background task — returns immediately with the planned roster.
    Per-company latency ~2 min, so a full 70-company run is ~2.5 hours.
    """
    from db.client import get_supabase
    db = get_supabase()
    # BUG-013: never run deep research on phantom rows.
    q = (
        db.table("companies")
        .select("name, priority")
        .eq("is_target", True)
        .eq("is_phantom", False)
    )
    if priority:
        q = q.eq("priority", priority)
    rows = q.execute().data or []
    company_names = [r["name"] for r in rows]

    if only_missing:
        existing = (
            db.table("company_personas")
            .select("company_name")
            .execute()
            .data or []
        )
        have = {r["company_name"] for r in existing}
        company_names = [n for n in company_names if n not in have]

    async def _run():
        from agents.persona_deep_research import deep_research_persona
        for name in company_names:
            try:
                r = await deep_research_persona(name)
                logger.info(
                    f"[batch] {name}: quality={r.persona_quality} "
                    f"sources={r.sources_count} cost=${r.cost_usd:.4f}"
                )
            except Exception as e:
                logger.warning(f"[batch] {name} failed: {e}")

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "queued": company_names,
        "queued_count": len(company_names),
        "priority_filter": priority,
        "only_missing": only_missing,
        "message": (
            f"Batch deep-research started for {len(company_names)} companies. "
            f"Estimated runtime: {len(company_names) * 2} minutes."
        ),
    }


@app.get("/personas")
async def list_personas(_auth=Depends(verify_secret)):
    """List all company_personas with quality + version info.

    BUG-013: drop personas whose `company_name` matches a phantom row in
    `companies` (Adyen Careers, SuperApp, Merchant Acquiring ...). The
    persona row still exists for audit, but the /insights table no longer
    shows it.
    """
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("company_personas")
        .select(
            "company_name, persona_version, n_examples_used, "
            "last_synthesized_at, metadata, ats_keyword_bank"
        )
        .order("last_synthesized_at", desc=True)
        .execute()
    )
    rows = result.data or []
    phantom_names = {
        r["name"]
        for r in (
            db.table("companies")
            .select("name")
            .eq("is_phantom", True)
            .execute()
            .data
            or []
        )
    }
    rows = [r for r in rows if r.get("company_name") not in phantom_names]
    by_quality: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for r in rows:
        q = (r.get("metadata") or {}).get("persona_quality", "unknown")
        by_quality[q] = by_quality.get(q, 0) + 1
    return {"personas": rows, "total": len(rows), "by_quality": by_quality}


@app.get("/personas/{company_name}")
async def get_persona(company_name: str, _auth=Depends(verify_secret)):
    """Full persona row for a single company (incl. system_prompt_template)."""
    from db.client import get_supabase
    result = (
        get_supabase()
        .table("company_personas")
        .select("*")
        .eq("company_name", company_name)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Persona not found")
    return rows[0]


# ── Cost Observability (Phase 1.8) ────────────────────────────────────
# All endpoints query public.agent_call_log (written by agents/llm_router.py
# on every LLM call). Rollups happen in Python — table is small enough
# (a few thousand rows after months of use) that this is faster than
# adding more views.

def _cost_window_query(days: int):
    """Return the supabase query builder filtered to last `days` days."""
    from db.client import get_supabase
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return (
        get_supabase()
        .table("agent_call_log")
        .select(
            "called_at, agent_name, graph, node_name, provider, model, "
            "input_tokens, output_tokens, cost_usd, latency_ms, "
            "job_id, application_id, resume_build_id, error"
        )
        .gte("called_at", cutoff)
    )


@app.get("/costs/summary")
async def costs_summary(_auth=Depends(verify_secret)):
    """
    Top-line cost stats: today / 7d / 30d totals, plus all-time + per-resume_build avg.
    Empty-state safe — returns zeros when agent_call_log is empty.
    """
    from db.client import get_supabase
    from datetime import datetime, timezone, timedelta
    db = get_supabase()
    now = datetime.now(timezone.utc)

    def _bucket(rows: list[dict]) -> dict:
        return {
            "calls": len(rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
            "avg_latency_ms": (
                round(sum(int(r.get("latency_ms") or 0) for r in rows) / len(rows))
                if rows else 0
            ),
        }

    try:
        rows_30d = (
            db.table("agent_call_log")
            .select("called_at, cost_usd, input_tokens, output_tokens, latency_ms, resume_build_id")
            .gte("called_at", (now - timedelta(days=30)).isoformat())
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.warning(f"costs_summary query failed: {e}")
        return {
            "warning": "agent_call_log not present yet",
            "today": _bucket([]),
            "last_7d": _bucket([]),
            "last_30d": _bucket([]),
            "avg_per_resume_build": 0,
            "n_resume_builds": 0,
        }

    today_iso = now.date().isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    rows_today = [r for r in rows_30d if (r.get("called_at") or "")[:10] == today_iso]
    rows_7d = [r for r in rows_30d if (r.get("called_at") or "") >= cutoff_7d]

    # Per-resume-build cost avg (only rows with a build id)
    by_build: dict[str, float] = {}
    for r in rows_30d:
        rb = r.get("resume_build_id")
        if rb:
            by_build[rb] = by_build.get(rb, 0.0) + float(r.get("cost_usd") or 0)
    avg_per_build = round(sum(by_build.values()) / len(by_build), 4) if by_build else 0

    return {
        "today": _bucket(rows_today),
        "last_7d": _bucket(rows_7d),
        "last_30d": _bucket(rows_30d),
        "avg_per_resume_build": avg_per_build,
        "n_resume_builds": len(by_build),
    }


@app.get("/costs/daily")
async def costs_daily(days: int = 30, _auth=Depends(verify_secret)):
    """
    Per-day rollup, fronted by the v_daily_llm_cost view when present.
    Returns one row per (day, provider, model) — frontend re-aggregates
    for line chart vs. provider stack, etc.
    """
    from db.client import get_supabase
    from datetime import datetime, timezone, timedelta
    db = get_supabase()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()

    try:
        result = (
            db.table("v_daily_llm_cost")
            .select("*")
            .gte("day", cutoff)
            .order("day", desc=False)
            .execute()
        )
        return {"days": days, "rows": result.data or []}
    except Exception as e:
        logger.warning(f"v_daily_llm_cost query failed: {e}")
        return {
            "days": days, "rows": [],
            "warning": "v_daily_llm_cost view not present (apply db/multi_llm_schema.sql)",
        }


@app.get("/costs/by-provider")
async def costs_by_provider(days: int = 7, _auth=Depends(verify_secret)):
    """
    Aggregate cost + calls + tokens + latency by provider over the window.
    Phase 1.9: uses cost_by_provider_window() RPC for DB-side aggregation
    (was Python-side; matters at >10k rows). Falls back gracefully if the
    function isn't installed yet.
    """
    from db.client import get_supabase
    try:
        result = get_supabase().rpc(
            "cost_by_provider_window", {"days_back": days}
        ).execute()
        rows = result.data or []
        # cost_usd comes back as numeric → JSON string in some configs;
        # coerce to float for predictable shape
        for r in rows:
            r["cost_usd"] = float(r.get("cost_usd") or 0)
            r["avg_latency_ms"] = int(float(r.get("avg_latency_ms") or 0))
        return {"days": days, "providers": rows}
    except Exception as e:
        logger.warning(
            f"cost_by_provider_window RPC failed ({e}); "
            f"falling back to Python aggregation"
        )

    # Fallback: legacy Python aggregation
    rows = _cost_window_query(days).execute().data or []
    agg: dict[str, dict] = {}
    for r in rows:
        p = r.get("provider") or "(unknown)"
        a = agg.setdefault(p, {
            "provider": p, "calls": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "latency_ms_total": 0,
        })
        a["calls"] += 1
        a["cost_usd"] += float(r.get("cost_usd") or 0)
        a["input_tokens"] += int(r.get("input_tokens") or 0)
        a["output_tokens"] += int(r.get("output_tokens") or 0)
        a["latency_ms_total"] += int(r.get("latency_ms") or 0)
    out = []
    for a in agg.values():
        a["cost_usd"] = round(a["cost_usd"], 4)
        a["avg_latency_ms"] = round(a["latency_ms_total"] / a["calls"]) if a["calls"] else 0
        a.pop("latency_ms_total")
        out.append(a)
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return {"days": days, "providers": out}


@app.get("/costs/by-agent")
async def costs_by_agent(days: int = 7, _auth=Depends(verify_secret)):
    """
    Aggregate by agent_name (e.g. 'g2.writer', 'CompanyAgent[Stripe]').
    Phase 1.9: uses cost_by_agent_window() RPC for DB-side aggregation.
    """
    from db.client import get_supabase
    try:
        result = get_supabase().rpc(
            "cost_by_agent_window", {"days_back": days}
        ).execute()
        rows = result.data or []
        for r in rows:
            r["cost_usd"] = float(r.get("cost_usd") or 0)
            r["avg_latency_ms"] = int(float(r.get("avg_latency_ms") or 0))
            # providers/models are already TEXT[] from the RPC
            r["providers"] = r.get("providers") or []
            r["models"] = r.get("models") or []
        return {"days": days, "agents": rows}
    except Exception as e:
        logger.warning(
            f"cost_by_agent_window RPC failed ({e}); "
            f"falling back to Python aggregation"
        )

    # Fallback: legacy Python aggregation
    rows = _cost_window_query(days).execute().data or []
    agg: dict[str, dict] = {}
    for r in rows:
        a_name = r.get("agent_name") or "(unknown)"
        a = agg.setdefault(a_name, {
            "agent_name": a_name, "calls": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "latency_ms_total": 0,
            "providers": set(), "models": set(),
        })
        a["calls"] += 1
        a["cost_usd"] += float(r.get("cost_usd") or 0)
        a["input_tokens"] += int(r.get("input_tokens") or 0)
        a["output_tokens"] += int(r.get("output_tokens") or 0)
        a["latency_ms_total"] += int(r.get("latency_ms") or 0)
        if r.get("provider"):
            a["providers"].add(r["provider"])
        if r.get("model"):
            a["models"].add(r["model"])
    out = []
    for a in agg.values():
        a["cost_usd"] = round(a["cost_usd"], 4)
        a["avg_latency_ms"] = round(a["latency_ms_total"] / a["calls"]) if a["calls"] else 0
        a["providers"] = sorted(a["providers"])
        a["models"] = sorted(a["models"])
        a.pop("latency_ms_total")
        out.append(a)
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return {"days": days, "agents": out}


@app.get("/costs/health")
async def costs_health(_auth=Depends(verify_secret)):
    """
    Per-provider health summary: error rate, p50/p95/p99 latency, last 7d
    cost, last call timestamp. Reads v_agent_call_health (added in
    db/agent_call_log_perf.sql).
    """
    from db.client import get_supabase
    try:
        result = (
            get_supabase().table("v_agent_call_health").select("*").execute()
        )
        rows = result.data or []
        # Coerce numerics for predictable JSON shape
        for r in rows:
            for k in (
                "calls_7d", "errors_7d",
                "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
            ):
                if r.get(k) is not None:
                    r[k] = int(r[k])
            for k in ("error_rate_pct", "total_cost_usd_7d"):
                if r.get(k) is not None:
                    r[k] = float(r[k])
        return {"providers": rows}
    except Exception as e:
        logger.warning(f"v_agent_call_health query failed: {e}")
        return {
            "providers": [],
            "warning": "v_agent_call_health view not present "
                       "(apply db/agent_call_log_perf.sql)",
        }


@app.get("/costs/log-stats")
async def costs_log_stats(_auth=Depends(verify_secret)):
    """
    Stats on the agent_call_log table itself: row count, size, oldest +
    newest entries. Used by docs/PERF.md guidance + dashboard footer.
    """
    from db.client import get_supabase
    try:
        result = (
            get_supabase().table("v_agent_call_log_stats").select("*").execute()
        )
        rows = result.data or []
        if not rows:
            return {"total_rows": 0}
        return rows[0]
    except Exception as e:
        logger.warning(f"v_agent_call_log_stats query failed: {e}")
        return {
            "total_rows": 0,
            "warning": "v_agent_call_log_stats view not present "
                       "(apply db/agent_call_log_perf.sql)",
        }


class CleanupRequest(BaseModel):
    days_to_keep: int = 365


@app.post("/costs/cleanup")
async def costs_cleanup(
    request: CleanupRequest,
    _auth=Depends(verify_secret),
):
    """
    Delete agent_call_log rows older than `days_to_keep` (default 365).
    The DB function refuses anything < 7 days as a safety guard.
    Returns the number of rows deleted.
    """
    from db.client import get_supabase
    try:
        result = get_supabase().rpc(
            "cleanup_agent_call_log", {"days_to_keep": request.days_to_keep}
        ).execute()
        deleted = result.data
        if isinstance(deleted, list):
            deleted = deleted[0] if deleted else 0
        return {"deleted": int(deleted or 0), "days_to_keep": request.days_to_keep}
    except Exception as e:
        # If the function refused (< 7 days), surface the message
        msg = str(e)
        if "refusing" in msg:
            raise HTTPException(status_code=400, detail=msg)
        logger.error(f"cleanup_agent_call_log failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {msg}")


# ── Cost Alerts (Phase 1.10) ──────────────────────────────────────────
# Manual triggers for the daily threshold check + weekly digest. The
# scheduler in main.py also fires these on cron (22:00 daily / Sunday 09:00).

@app.post("/alerts/check")
async def trigger_daily_alert_check(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """
    Manually trigger the daily-spend alert check. Idempotent — won't
    double-fire if the cron already ran today (boss_audit_log dedup).
    Use to test alerting wiring or re-run after fixing config.
    """
    async def _run():
        from agents.cost_alerter import CostAlerter
        await CostAlerter().check_daily_spend()
    background_tasks.add_task(_run)
    return {"status": "started", "kind": "daily"}


@app.post("/alerts/weekly-digest")
async def trigger_weekly_digest(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """
    Manually trigger the weekly cost digest. Useful for previewing the
    digest format before the Sunday cron fires it for real.
    """
    async def _run():
        from agents.cost_alerter import CostAlerter
        await CostAlerter().send_weekly_digest()
    background_tasks.add_task(_run)
    return {"status": "started", "kind": "weekly_digest"}


@app.get("/alerts/last")
async def get_last_alerts(_auth=Depends(verify_secret)):
    """
    Return the last 10 cost-alerter audit log entries — useful for the
    dashboard's audit-trail view to confirm alerts are firing as expected.
    """
    from db.client import get_supabase
    try:
        result = (
            get_supabase()
            .table("boss_audit_log")
            .select("id, run_date, digest_content, digest_sent, created_at")
            .ilike("digest_content", "%cost-alerter:%")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return {"alerts": result.data or []}
    except Exception as e:
        logger.warning(f"get_last_alerts failed: {e}")
        return {"alerts": [], "warning": str(e)[:200]}


@app.get("/costs/by-resume-build")
async def costs_by_resume_build(limit: int = 20, _auth=Depends(verify_secret)):
    """
    Top resume_builds by total cost. Joins to resume_builds for context
    (company_name, polisher_score, status).
    """
    from db.client import get_supabase
    db = get_supabase()
    # Pull rows that have a resume_build_id in the last 90 days, aggregate in Python
    rows = _cost_window_query(90).execute().data or []
    agg: dict[str, dict] = {}
    for r in rows:
        rb = r.get("resume_build_id")
        if not rb:
            continue
        a = agg.setdefault(rb, {
            "resume_build_id": rb, "calls": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0,
        })
        a["calls"] += 1
        a["cost_usd"] += float(r.get("cost_usd") or 0)
        a["input_tokens"] += int(r.get("input_tokens") or 0)
        a["output_tokens"] += int(r.get("output_tokens") or 0)

    # Hydrate with resume_builds context
    if agg:
        try:
            build_rows = (
                db.table("resume_builds")
                .select("id, company_name, polisher_score, status, ats_score_a, ats_score_b, iterations, created_at")
                .in_("id", list(agg.keys()))
                .execute()
                .data
            ) or []
            build_map = {b["id"]: b for b in build_rows}
            for rb_id, a in agg.items():
                b = build_map.get(rb_id, {})
                a["company_name"] = b.get("company_name")
                a["polisher_score"] = b.get("polisher_score")
                a["status"] = b.get("status")
                a["iterations"] = b.get("iterations")
                a["created_at"] = b.get("created_at")
        except Exception as e:
            logger.debug(f"by-resume-build hydration failed: {e}")

    out = []
    for a in agg.values():
        a["cost_usd"] = round(a["cost_usd"], 4)
        out.append(a)
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return {"builds": out[:limit]}


@app.get("/costs/recent-calls")
async def costs_recent_calls(
    limit: int = 100,
    provider: Optional[str] = None,
    agent_name: Optional[str] = None,
    has_error: Optional[bool] = None,
    _auth=Depends(verify_secret),
):
    """
    Last N rows from agent_call_log with optional filters.
    Used by the dashboard's recent-calls table.
    """
    from db.client import get_supabase
    db = get_supabase()
    q = (
        db.table("agent_call_log")
        .select(
            "id, called_at, agent_name, graph, node_name, provider, model, "
            "input_tokens, output_tokens, cost_usd, latency_ms, "
            "job_id, resume_build_id, error"
        )
        .order("called_at", desc=True)
        .limit(min(max(limit, 1), 500))
    )
    if provider:
        q = q.eq("provider", provider)
    if agent_name:
        q = q.eq("agent_name", agent_name)
    if has_error is True:
        q = q.not_.is_("error", "null")
    elif has_error is False:
        q = q.is_("error", "null")

    try:
        result = q.execute()
        return {"calls": result.data or []}
    except Exception as e:
        logger.warning(f"recent-calls query failed: {e}")
        return {"calls": [], "warning": "agent_call_log not present yet"}
