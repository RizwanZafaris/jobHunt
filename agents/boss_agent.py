"""
BossAgent — nightly orchestrator and quality auditor.

Runs every evening at 21:00 and:
  1. Checks all company agents for data freshness (>24h old = stale)
  2. Re-scrapes stale companies
  3. Audits job application statuses
  4. Sends a daily digest email (via SendGrid)
  5. Logs everything to Supabase boss_audit_log
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import date, datetime
from typing import Optional
import yaml

from agents.base_agent import BaseAgent
from agents.company_agent import CompanyAgent
from config.settings import get_settings
from db.client import (
    get_stale_companies,
    log_boss_audit,
    get_supabase,
)

logger = logging.getLogger(__name__)


class BossAgent(BaseAgent):
    """
    Orchestrator agent. Runs nightly to audit and maintain the system.
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(name="BossAgent", model=settings.boss_agent_model)

    async def run(self) -> dict:
        """Run the nightly audit."""
        self.log("🌙 BossAgent starting nightly audit...", style="yellow")
        start = datetime.now()

        results = {
            "run_date": date.today().isoformat(),
            "total_companies": 0,
            "stale_companies": 0,
            "refreshed": [],
            "jobs_found_today": 0,
            "jobs_scored_high": 0,
            "applications_sent": 0,
            "digest_sent": False,
            "digest_content": "",
        }

        # 1. Check company freshness
        stale = await self._check_company_freshness()
        results["stale_companies"] = len(stale)
        results["total_companies"] = await self._count_total_companies()

        # 2. Refresh stale companies (max 5 per night to avoid rate limits)
        if stale:
            self.log(f"  Found {len(stale)} stale companies. Refreshing...", style="yellow")
            refreshed = await self._refresh_stale_companies(stale[:5])
            results["refreshed"] = refreshed

        # 3. Get today's job stats
        job_stats = await self._get_today_stats()
        results.update(job_stats)

        # 4. Generate and send digest
        digest = await self._generate_digest(results)
        results["digest_content"] = digest

        sent = await self._send_digest(digest)
        results["digest_sent"] = sent

        # 5. Log to Supabase
        log_boss_audit(results)

        elapsed = (datetime.now() - start).total_seconds()
        self.log(f"✅ Nightly audit complete in {elapsed:.1f}s", style="green")
        self.log(f"   Stale refreshed: {len(results['refreshed'])}", style="green")
        self.log(f"   Jobs today: {results['jobs_found_today']}", style="green")
        self.log(f"   High-score jobs: {results['jobs_scored_high']}", style="green")

        return results

    async def _check_company_freshness(self) -> list[dict]:
        """Find companies with stale knowledge (>24h)."""
        settings = get_settings()
        try:
            stale = get_stale_companies(hours=settings.company_freshness_hours)
            return stale
        except Exception as e:
            logger.error(f"Freshness check failed: {e}")
            return []

    async def _refresh_stale_companies(self, stale: list[dict]) -> list[str]:
        """Re-scrape stale companies."""
        refreshed = []
        for item in stale:
            company_name = item.get("company_name")
            if not company_name:
                continue
            try:
                agent = CompanyAgent(company_name)
                result = await agent.build_or_refresh(force=True)
                if result.get("status") == "built":
                    refreshed.append(company_name)
                    self.log(f"   ✅ Refreshed: {company_name}")
                await asyncio.sleep(2)  # Rate limit courtesy
            except Exception as e:
                logger.error(f"Failed to refresh {company_name}: {e}")
        return refreshed

    async def _count_total_companies(self) -> int:
        try:
            db = get_supabase()
            result = db.table("companies").select("id", count="exact").execute()
            return result.count or 0
        except Exception:
            return 0

    async def _get_today_stats(self) -> dict:
        """Get today's job activity stats from Supabase."""
        try:
            db = get_supabase()
            today = date.today().isoformat()

            # Jobs found today
            jobs_today = db.table("jobs") \
                .select("id", count="exact") \
                .gte("discovered_at", f"{today}T00:00:00") \
                .execute()

            # High-score jobs found today
            # 2026-05-12: filter out closed/failed jobs so the digest count
            # only reflects actionable opportunities. Otherwise stale-on-
            # arrival listings (the OKX archetype) inflate the headline.
            from api._job_guards import filter_open_jobs_query
            threshold = get_settings().fit_score_threshold
            high_score = filter_open_jobs_query(
                db.table("jobs")
                  .select("id", count="exact")
                  .gte("discovered_at", f"{today}T00:00:00")
                  .gte("match_score", threshold)
            ).execute()

            # Applications sent today
            applied = db.table("applications") \
                .select("id", count="exact") \
                .gte("created_at", f"{today}T00:00:00") \
                .execute()

            return {
                "jobs_found_today": jobs_today.count or 0,
                "jobs_scored_high": high_score.count or 0,
                "applications_sent": applied.count or 0,
            }
        except Exception as e:
            logger.error(f"Stats query failed: {e}")
            return {"jobs_found_today": 0, "jobs_scored_high": 0, "applications_sent": 0}

    async def _get_pipeline_summary(self) -> dict:
        """Get full pipeline status summary."""
        try:
            db = get_supabase()
            result = db.table("applications") \
                .select("status") \
                .execute()
            from collections import Counter
            counts = Counter(row["status"] for row in (result.data or []))
            return dict(counts)
        except Exception:
            return {}

    async def _generate_digest(self, stats: dict) -> str:
        """Use Claude to write a daily digest email."""
        pipeline = await self._get_pipeline_summary()

        # Get top new high-score jobs
        try:
            db = get_supabase()
            threshold = get_settings().fit_score_threshold
            today = date.today().isoformat()
            # 2026-05-12: same filter as the stats query above — the digest
            # email should never recommend a closed/failed listing.
            from api._job_guards import filter_open_jobs_query
            top_jobs = filter_open_jobs_query(
                db.table("jobs")
                  .select("title, company, location, match_score, url")
                  .gte("discovered_at", f"{today}T00:00:00")
                  .gte("match_score", threshold)
            ).order("match_score", desc=True) \
                .limit(10) \
                .execute()
            top_jobs_data = top_jobs.data or []
        except Exception:
            top_jobs_data = []

        system = "You are the BossAgent. Write a crisp daily digest for Rizwan's job hunt system."
        user = f"""Write a daily job hunt digest for {stats['run_date']}.

Stats:
- Jobs found today: {stats['jobs_found_today']}
- High-score jobs (>= {get_settings().fit_score_threshold}): {stats['jobs_scored_high']}
- Applications sent today: {stats['applications_sent']}
- Companies refreshed: {', '.join(stats.get('refreshed', [])) or 'None'}
- Stale companies found: {stats['stale_companies']}
- Total companies tracked: {stats['total_companies']}

Pipeline status:
{json.dumps(pipeline, indent=2)}

Top new jobs today:
{json.dumps(top_jobs_data, indent=2)}

Write a crisp, action-oriented digest:
1. Summary headline (2 lines)
2. Today's highlights (bullet points)
3. Top jobs to review
4. Pipeline health status (Green/Yellow/Red)
5. Recommended actions for tomorrow
6. Any system alerts (stale companies, etc.)

Tone: Direct, like a smart operations assistant."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1000,
            temperature=0.4,
        )

    async def _send_digest(self, digest: str) -> bool:
        """Send the digest via email (SendGrid) or save to file."""
        settings = get_settings()

        # Save to file always
        import os
        os.makedirs(settings.output_reports_dir, exist_ok=True)
        date_str = date.today().isoformat()
        path = f"{settings.output_reports_dir}/daily_digest_{date_str}.txt"
        with open(path, "w") as f:
            f.write(f"Daily Digest — {date_str}\n{'='*60}\n\n{digest}")
        self.log(f"  Digest saved: {path}")

        # Send email if SendGrid configured
        if settings.sendgrid_api_key:
            try:
                import sendgrid
                from sendgrid.helpers.mail import Mail
                sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
                message = Mail(
                    from_email=settings.digest_email_from,
                    to_emails=settings.digest_email_to,
                    subject=f"Job Hunt Digest — {date_str}",
                    plain_text_content=digest,
                )
                sg.send(message)
                self.log("  Email digest sent ✅")
                return True
            except Exception as e:
                logger.error(f"Email send failed: {e}")
        return False
