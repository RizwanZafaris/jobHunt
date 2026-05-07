"""
FastAPI server — deploys to Railway.
Provides REST API for the job hunt system.
Vercel dashboard can call these endpoints.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional
from datetime import date

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import os

from config.settings import get_settings

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


# ── Auth ──────────────────────────────────────────────────────────────────────
def verify_secret(x_secret_key: str = Header(None)):
    if x_secret_key != settings.secret_key:
        raise HTTPException(status_code=401, detail="Invalid secret key")
    return True


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
    _auth=Depends(verify_secret)
):
    """List all discovered jobs."""
    from db.client import get_supabase
    db = get_supabase()
    query = db.table("jobs").select(
        "id, title, company, location, match_score, status, url, discovered_at"
    ).gte("match_score", min_score).order("match_score", desc=True).limit(limit)

    if status:
        query = query.eq("status", status)

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
    """List all tracked companies."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("companies").select("*").order("name").execute()
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
