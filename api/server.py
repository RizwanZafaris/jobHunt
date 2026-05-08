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
        "id, title, company, location, match_score, status, url, discovered_at, "
        "archetype, legitimacy_tier, resume_generated_at"
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


@app.get("/companies/{company_name}/knowledge")
async def get_company_knowledge(
    company_name: str,
    _auth=Depends(verify_secret),
):
    """Return full research intel for a company (overview, news, culture, recruitment process, etc.)."""
    from db.client import get_supabase
    db = get_supabase()
    # Look up company record
    company = db.table("companies").select("*").eq("name", company_name).limit(1).execute()
    company_row = (company.data or [None])[0]
    # Pull all knowledge sections
    knowledge = db.table("company_knowledge").select(
        "section, content, source_url, scraped_at"
    ).eq("company_name", company_name).execute()
    return {
        "company": company_row,
        "knowledge": knowledge.data or [],
        "section_count": len(knowledge.data or []),
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
    db = get_supabase()
    job_result = db.table("jobs").select("*").eq("id", job_id).limit(1).execute()
    job_rows = job_result.data or []
    if not job_rows:
        raise HTTPException(status_code=404, detail="Job not found")
    job = job_rows[0]
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
    for k in ("resume_path", "email_path", "interview_path", "report_path"):
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
    _auth=Depends(verify_secret),
):
    """
    Manually trigger PersonaSynthesizer. By default, runs against all
    companies with personas. Pass `company_name` to limit to one.
    `force=true` re-synthesizes even if no new data since last run.
    """
    async def _run():
        from agents.persona_synthesizer import PersonaSynthesizer
        synth = PersonaSynthesizer()
        if company_name:
            await synth.synthesize_one(company_name, force=force)
        else:
            await synth.run(force=force)

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "scope": company_name or "all_personas",
        "force": force,
        "message": "Persona synthesis running in background.",
    }


@app.get("/personas")
async def list_personas(_auth=Depends(verify_secret)):
    """List all company_personas with quality + version info."""
    from db.client import get_supabase
    result = (
        get_supabase()
        .table("company_personas")
        .select(
            "company_name, persona_version, n_examples_used, "
            "last_synthesized_at, metadata, ats_keyword_bank"
        )
        .order("last_synthesized_at", desc=True)
        .execute()
    )
    rows = result.data or []
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
