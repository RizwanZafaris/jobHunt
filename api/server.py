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
    return {"updated": True, "row": (result.data or [None])[0]}


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
    return {"updated": True, "row": rows[0]}


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
    """List all target companies grouped by category."""
    from db.client import get_supabase
    db = get_supabase()
    result = db.table("companies").select("*").eq("is_target", True).order("priority", desc=False).order("name").execute()
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


@app.post("/pipeline/run-targets")
async def run_pipeline_targets(
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_secret),
):
    """Run the pipeline ONLY for target companies — no random web search."""
    async def _run():
        from pipeline import JobHuntPipeline
        from db.client import get_supabase
        db = get_supabase()
        targets = db.table("companies").select("name").eq("is_target", True).execute()
        target_names = [t["name"] for t in (targets.data or [])]
        pipeline = JobHuntPipeline()
        # Run scout filtered to targets only — pipeline will handle each
        await pipeline.run_for_targets(target_names=target_names)
    background_tasks.add_task(_run)
    return {"status": "started", "message": "Targets-only pipeline running in background"}


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
    # Read text artifacts if they exist
    artifacts: dict = {}
    import os
    for k in ("resume_path", "email_path", "interview_path", "report_path"):
        p = job.get(k)
        if p and os.path.exists(p):
            artifacts[k] = {"exists": True, "size": os.path.getsize(p)}
            # For text-based, return content
            if p.endswith((".txt", ".md")):
                try:
                    with open(p, "r") as f:
                        artifacts[k]["content"] = f.read()
                except Exception:
                    pass
        else:
            artifacts[k] = {"exists": False, "path": p}
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
    """List all applications grouped by status (kanban columns)."""
    from db.client import get_supabase
    from collections import defaultdict
    db = get_supabase()
    apps = db.table("applications").select("*").order("created_at", desc=True).execute()
    apps_data = apps.data or []
    by_status: dict[str, list] = defaultdict(list)
    for a in apps_data:
        by_status[a.get("status", "evaluated")].append(a)
    # Also enrich with job info
    if apps_data:
        job_ids = list({a["job_id"] for a in apps_data if a.get("job_id")})
        if job_ids:
            jobs = db.table("jobs").select("id, title, company, location, match_score, url").in_("id", job_ids).execute()
            job_map = {j["id"]: j for j in (jobs.data or [])}
            for a in apps_data:
                a["job"] = job_map.get(a.get("job_id"))
    return {"applications": apps_data, "by_status": dict(by_status), "total": len(apps_data)}


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
