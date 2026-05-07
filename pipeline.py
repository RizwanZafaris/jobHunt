"""
Pipeline — orchestrates all agents for a full job application run.

Flow:
  JobScoutAgent → discovers jobs
  CompanyAgent  → researches each company (once, cached forever)
  CompanyAgent ↔ RizwanAgent → dialogue to fill resume gaps
  ResumeBuilderAgent → builds tailored DOCX
  RizwanAgent → writes cover email
  InterviewAgent → generates interview prep (for high-score jobs)
  All results → Supabase

This is the brain that coordinates the agents.
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from agents.job_scout_agent import JobScoutAgent
from agents.company_agent import CompanyAgent
from agents.rizwan_agent import RizwanAgent
from agents.resume_builder_agent import ResumeBuilderAgent
from agents.interview_agent import InterviewAgent
from config.settings import get_settings
from db.client import update_job, get_supabase

logger = logging.getLogger(__name__)
console = Console()


class JobHuntPipeline:
    """Full end-to-end pipeline coordinating all agents."""

    def __init__(self):
        self.settings = get_settings()
        self.job_scout = JobScoutAgent()
        self.rizwan_agent = RizwanAgent()
        self.resume_builder = ResumeBuilderAgent()
        self.interview_agent = InterviewAgent()
        # Company agents are created on-demand (one per company)
        self._company_agents: dict[str, CompanyAgent] = {}

    def get_company_agent(self, company_name: str) -> CompanyAgent:
        """Get or create a company agent (cached in memory per run)."""
        key = company_name.lower().strip()
        if key not in self._company_agents:
            self._company_agents[key] = CompanyAgent(company_name)
        return self._company_agents[key]

    async def run(
        self,
        target_company: Optional[str] = None,
        target_role: Optional[str] = None,
        job_ids: Optional[list[int]] = None,
        skip_scout: bool = False,
    ) -> dict:
        """
        Full pipeline run.

        Args:
            target_company: Focus on a specific company
            target_role: Focus on a specific role type
            job_ids: Process specific job IDs (skip scout)
            skip_scout: Skip job discovery (use existing new jobs in DB)
        """
        console.print("\n" + "="*70)
        console.print("[bold cyan]🚀 JOB HUNT PIPELINE STARTING[/bold cyan]")
        console.print(f"   Target company: {target_company or 'All'}")
        console.print(f"   Target role: {target_role or 'All PM/PMO roles'}")
        console.print("="*70 + "\n")

        results = {
            "jobs_discovered": 0,
            "jobs_processed": 0,
            "resumes_built": [],
            "emails_drafted": [],
            "interview_packs": [],
            "errors": [],
        }

        # Step 0: Seed Rizwan's profile to Supabase (idempotent)
        await self.rizwan_agent.seed_supabase_profile()

        # Step 1: Discover jobs
        if job_ids:
            jobs = self._load_jobs_by_ids(job_ids)
        elif skip_scout:
            jobs = self._load_new_jobs_from_db()
        else:
            console.print("[bold]Phase 1: Job Discovery[/bold]")
            jobs = await self.job_scout.run(
                target_company=target_company,
                target_role=target_role
            )
            results["jobs_discovered"] = len(jobs)
            console.print(f"  ✅ {len(jobs)} qualifying jobs found\n")

        if not jobs:
            console.print("[yellow]No qualifying jobs found. Exiting.[/yellow]")
            return results

        # Step 2: Process each job
        console.print(f"[bold]Phase 2: Processing {len(jobs)} jobs[/bold]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Processing jobs...", total=len(jobs))

            for job in jobs[:self.settings.max_jobs_per_run]:
                try:
                    job_results = await self._process_single_job(job, progress)
                    if job_results.get("resume_path"):
                        results["resumes_built"].append(job_results["resume_path"])
                    if job_results.get("email_path"):
                        results["emails_drafted"].append(job_results["email_path"])
                    if job_results.get("interview_path"):
                        results["interview_packs"].append(job_results["interview_path"])
                    results["jobs_processed"] += 1
                except Exception as e:
                    error_msg = f"Failed: {job.get('title')} @ {job.get('company')}: {e}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)

                progress.advance(task)
                await asyncio.sleep(1)  # Courtesy rate limiting

        # Print summary
        self._print_summary(results)
        return results

    async def _process_single_job(self, job: dict, progress=None) -> dict:
        """
        Process a single job through the full pipeline:
        Company research → Gap analysis dialogue → Resume → Email → Interview prep
        """
        title = job.get("title", "Unknown Role")
        company = job.get("company", "Unknown Company")
        jd_text = job.get("description", "")
        job_id = job.get("db_id") or job.get("id")
        match_score = job.get("match_score", 0)

        if progress:
            progress.print(f"  Processing: [cyan]{title}[/cyan] @ [bold]{company}[/bold] (score: {match_score})")

        result = {"job_id": job_id, "company": company, "title": title}

        # ── Phase 2a: Build/refresh company knowledge ──────────────────────
        company_agent = self.get_company_agent(company)
        await company_agent.build_or_refresh()

        # ── Phase 2b: Gap analysis ─────────────────────────────────────────
        from db.client import search_rizwan_profile
        profile_sections = await search_rizwan_profile(jd_text, match_count=5)
        rizwan_profile_text = "\n".join([
            f"[{s['section']}]: {s['content']}" for s in profile_sections
        ])

        gap_analysis = await company_agent.review_resume_against_jd(
            jd_text=jd_text,
            rizwan_profile_text=rizwan_profile_text,
            job_title=title,
        )

        score_from_analysis = gap_analysis.get("overall_match_score", match_score)
        if progress:
            progress.print(f"    Match score: {score_from_analysis}/100")

        # If score too low after analysis, skip
        if score_from_analysis < self.settings.fit_score_threshold:
            if progress:
                progress.print(f"    [yellow]Score below threshold ({score_from_analysis}). Skipping.[/yellow]")
            update_job(job_id, {"status": "pass", "match_score": score_from_analysis})
            return result

        # ── Phase 2c: Gap-filling dialogue ────────────────────────────────
        filled_gaps = []
        gaps = gap_analysis.get("gaps", [])
        critical_gaps = [g for g in gaps if g.get("gap_severity") == "critical"]

        if progress:
            progress.print(f"    Conducting gap dialogue ({len(critical_gaps)} critical gaps)...")

        for turn, gap in enumerate(critical_gaps[:self.settings.max_company_dialogue_turns]):
            try:
                # Rizwan responds to gap question
                rizwan_response = await self.rizwan_agent.respond_to_gap(
                    gap=gap,
                    company_name=company,
                    jd_context=jd_text[:500],
                    turn=turn * 2 + 1,
                    job_id=job_id or 0,
                )

                # Company agent evaluates the evidence
                evaluation = await company_agent.respond_to_rizwan_evidence(
                    gap=gap,
                    rizwan_evidence=rizwan_response,
                    turn=turn * 2 + 2,
                    job_id=job_id or 0,
                )

                if evaluation.get("verdict") in ("filled", "partial"):
                    gap["resume_bullet"] = evaluation.get("resume_bullet", "")
                    gap["verdict"] = evaluation.get("verdict")
                    filled_gaps.append(gap)

            except Exception as e:
                logger.warning(f"Gap dialogue failed for turn {turn}: {e}")

        if progress:
            progress.print(f"    Gaps filled: {len(filled_gaps)}/{len(critical_gaps)}")

        # ── Phase 2d: Build tailoring brief ───────────────────────────────
        tailoring_brief = await company_agent.build_final_resume_brief(
            job_title=title,
            jd_text=jd_text,
            original_profile=rizwan_profile_text,
            filled_gaps=filled_gaps,
        )

        # ── Phase 2e: Build resume ─────────────────────────────────────────
        resume_path = await self.resume_builder.run(
            job_title=title,
            company=company,
            tailoring_brief=tailoring_brief,
            job_id=job_id,
        )
        result["resume_path"] = resume_path

        # ── Phase 2f: Write cover email ────────────────────────────────────
        # BUG-06 fix: wrap entire email generation + write in try/except so a
        # cover email failure doesn't abort the whole job (resume already done).
        email_path = None
        try:
            hooks = gap_analysis.get("company_hooks", [])
            priorities = gap_analysis.get("tailoring_priorities", [])
            cover_email = await self.rizwan_agent.generate_cover_email(
                job_title=title,
                company_name=company,
                company_hooks=hooks,
                tailoring_priorities=priorities,
                jd_summary=jd_text[:500],
            )

            import os
            from datetime import date
            os.makedirs(self.settings.output_reports_dir, exist_ok=True)
            safe_co = company.lower().replace(" ", "-")[:20]
            safe_title = title.lower().replace(" ", "-")[:20]
            email_path = (
                f"{self.settings.output_reports_dir}/"
                f"email_{safe_co}_{safe_title}_{date.today().isoformat()}.txt"
            )
            with open(email_path, "w") as f:
                f.write(f"# Cover Email: {title} @ {company}\n\n{cover_email}")
            result["email_path"] = email_path
        except Exception as e:
            logger.warning(f"Cover email generation failed for {title} @ {company}: {e}")

        # ── Phase 2g: Interview prep (if high score) ───────────────────────
        if score_from_analysis >= 65:
            try:
                interview_result = await self.interview_agent.run(
                    company=company,
                    job_title=title,
                    jd_text=jd_text,
                    job_id=job_id,
                )
                result["interview_path"] = interview_result.get("file_path")
            except Exception as e:
                logger.warning(f"Interview prep failed: {e}")

        # ── Update DB ──────────────────────────────────────────────────────
        if job_id:
            update_job(job_id, {
                "status": "evaluated",
                "match_score": score_from_analysis,
                "resume_path": resume_path,
                "email_path": email_path,
                "fit_details": {
                    "gaps_found": len(gaps),
                    "gaps_filled": len(filled_gaps),
                    "strengths": [s["area"] for s in gap_analysis.get("strengths", [])],
                    "tailoring_priorities": priorities,
                }
            })

        if progress:
            progress.print(f"    ✅ Complete: resume + email + {'interview prep' if score_from_analysis >= 65 else 'no interview prep'}")

        return result

    def _load_jobs_by_ids(self, job_ids: list[int]) -> list[dict]:
        db = get_supabase()
        result = db.table("jobs").select("*").in_("id", job_ids).execute()
        return result.data or []

    def _load_new_jobs_from_db(self) -> list[dict]:
        """Load unprocessed qualifying jobs from Supabase."""
        db = get_supabase()
        threshold = self.settings.fit_score_threshold
        result = db.table("jobs") \
            .select("*") \
            .eq("status", "new") \
            .gte("match_score", threshold) \
            .order("match_score", desc=True) \
            .limit(self.settings.max_jobs_per_run) \
            .execute()
        return result.data or []

    def _print_summary(self, results: dict):
        console.print("\n" + "="*70)
        console.print("[bold green]✅ PIPELINE COMPLETE[/bold green]")
        console.print(f"   Jobs discovered:  {results['jobs_discovered']}")
        console.print(f"   Jobs processed:   {results['jobs_processed']}")
        console.print(f"   Resumes built:    {len(results['resumes_built'])}")
        console.print(f"   Emails drafted:   {len(results['emails_drafted'])}")
        console.print(f"   Interview packs:  {len(results['interview_packs'])}")
        if results["errors"]:
            console.print(f"   [red]Errors: {len(results['errors'])}[/red]")
            for e in results["errors"][:3]:
                console.print(f"     - {e}")

        if results["resumes_built"]:
            console.print("\n[bold]📄 Resumes:[/bold]")
            for r in results["resumes_built"]:
                console.print(f"   {r}")
        if results["emails_drafted"]:
            console.print("\n[bold]✉️  Emails:[/bold]")
            for e in results["emails_drafted"]:
                console.print(f"   {e}")
        if results["interview_packs"]:
            console.print("\n[bold]🎯 Interview Packs:[/bold]")
            for i in results["interview_packs"]:
                console.print(f"   {i}")
        console.print("="*70 + "\n")
