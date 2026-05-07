"""
ApplicationTrackerAgent — Tracks application statuses and follow-ups.

Monitors all active applications, surfaces ones needing follow-up,
generates follow-up emails, and maintains the application pipeline.
Integrates with Supabase applications table.
"""

from __future__ import annotations
import logging
from typing import Optional
from datetime import date, datetime, timedelta

from agents.base_agent import BaseAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Status progression
STATUS_FLOW = [
    "draft",           # Resume/email drafted, not yet sent
    "applied",         # Application submitted
    "acknowledged",    # Auto-acknowledgement received
    "screening",       # HR phone screen scheduled/completed
    "interviewing",    # In interview process
    "final_round",     # Final round interviews
    "offer",           # Offer received
    "negotiating",     # Offer negotiation in progress
    "accepted",        # Offer accepted
    "rejected",        # Application rejected
    "withdrawn",       # Withdrew application
    "ghosted",         # No response after 2+ weeks
]

# Follow-up timing rules
FOLLOW_UP_RULES = {
    "applied": 7,       # Follow up 7 days after applying if no response
    "screening": 3,     # Follow up 3 days after screen if no next steps
    "interviewing": 5,  # Follow up 5 days after interview
    "final_round": 3,   # Follow up 3 days after final round
}


class ApplicationTrackerAgent(BaseAgent):
    """
    Manages application pipeline and follow-up cadence.

    Features:
    - Identifies applications needing follow-up
    - Drafts follow-up emails (not pushy, adds value)
    - Updates application statuses
    - Generates pipeline health reports
    - Flags stale applications for re-engagement or closure
    """

    RIZWAN_FOLLOW_UP_TONE = """
Rizwan Zafar writing a professional follow-up.
Tone: Confident, not desperate. Adds value in follow-up (mentions new achievement or relevant news).
Never says "just checking in" — always has a reason to reconnect.
Maximum 3 follow-ups per application.
"""

    def __init__(self):
        settings = get_settings()
        super().__init__(name="ApplicationTracker", model=settings.rizwan_agent_model)

    async def run(self) -> dict:
        """
        Daily application pipeline review.
        Identifies follow-ups needed, drafts messages, flags stale applications.
        """
        self.log("Running application pipeline review...")

        # Get all active applications
        applications = self._get_active_applications()

        if not applications:
            self.log("No active applications to review.")
            return {"applications_reviewed": 0, "follow_ups_needed": []}

        # Categorize by action needed
        follow_ups_needed = []
        stale_applications = []
        pipeline_health = {"total": len(applications), "by_status": {}}

        today = date.today()

        for app in applications:
            status = app.get("status", "applied")
            last_action = app.get("last_action_date")
            company = app.get("company", "Unknown")
            role = app.get("role", "Unknown")

            # Count by status
            pipeline_health["by_status"][status] = \
                pipeline_health["by_status"].get(status, 0) + 1

            # Check follow-up rules
            if last_action and status in FOLLOW_UP_RULES:
                try:
                    last_date = datetime.fromisoformat(last_action).date()
                    days_since = (today - last_date).days
                    threshold = FOLLOW_UP_RULES[status]

                    if days_since >= threshold:
                        follow_ups_needed.append({
                            "app_id": app.get("id"),
                            "company": company,
                            "role": role,
                            "status": status,
                            "days_since_last_action": days_since,
                            "follow_up_type": f"post_{status}_follow_up",
                        })
                except Exception:
                    pass

            # Mark as ghosted if applied > 21 days ago with no progress
            if status == "applied" and last_action:
                try:
                    last_date = datetime.fromisoformat(last_action).date()
                    if (today - last_date).days > 21:
                        stale_applications.append({
                            "app_id": app.get("id"),
                            "company": company,
                            "role": role,
                            "days_stale": (today - last_date).days,
                        })
                except Exception:
                    pass

        # Draft follow-up emails for top priority follow-ups
        follow_up_emails = []
        for fu in follow_ups_needed[:5]:  # Max 5 follow-ups per run
            email = await self._draft_follow_up(
                company=fu["company"],
                role=fu["role"],
                status=fu["status"],
                days_since=fu["days_since_last_action"],
            )
            follow_up_emails.append({**fu, "email_draft": email})

        # Generate pipeline health summary
        summary = await self._generate_pipeline_summary(
            pipeline_health=pipeline_health,
            follow_ups=follow_ups_needed,
            stale=stale_applications,
        )

        return {
            "date": today.isoformat(),
            "applications_reviewed": len(applications),
            "follow_ups_needed": len(follow_ups_needed),
            "stale_applications": len(stale_applications),
            "follow_up_emails": follow_up_emails,
            "pipeline_health": pipeline_health,
            "summary": summary,
        }

    def _get_active_applications(self) -> list[dict]:
        """Load active applications from Supabase."""
        try:
            from supabase.client import get_supabase
            db = get_supabase()
            result = db.table("applications") \
                .select("*") \
                .not_.in_("status", ["rejected", "withdrawn", "accepted", "ghosted"]) \
                .order("created_at", desc=False) \
                .execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to load applications: {e}")
            return []

    async def _draft_follow_up(
        self,
        company: str,
        role: str,
        status: str,
        days_since: int,
    ) -> str:
        """Draft a follow-up email appropriate to the application stage."""
        stage_context = {
            "applied": f"Applied {days_since} days ago, no response yet",
            "screening": f"Had HR screen {days_since} days ago, waiting for next steps",
            "interviewing": f"Completed interview {days_since} days ago, waiting for feedback",
            "final_round": f"Completed final round {days_since} days ago, awaiting decision",
        }

        system = f"You are Rizwan Zafar writing a professional follow-up email. {self.RIZWAN_FOLLOW_UP_TONE}"
        user = f"""Draft a follow-up email for:
Company: {company}
Role: {role}
Situation: {stage_context.get(status, 'Waiting for next steps')}

Requirements:
- Under 100 words
- Has a reason to follow up (don't say "just checking in")
- Mentions something specific about {company} (recent news, product launch, etc. — infer from context)
- Reiterates fit in one sentence maximum
- Clear call to action (specific ask)
- Subject line included

Format:
Subject: [subject line]

[email body]"""

        try:
            return await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=300,
                temperature=0.5,
            )
        except Exception as e:
            logger.warning(f"Follow-up draft failed: {e}")
            return ""

    async def _generate_pipeline_summary(
        self,
        pipeline_health: dict,
        follow_ups: list[dict],
        stale: list[dict],
    ) -> str:
        """Generate a brief pipeline health summary."""
        system = "You are a career operations assistant giving a crisp daily briefing."
        user = f"""Generate a 5-line pipeline health briefing.

Pipeline: {pipeline_health}
Follow-ups needed: {len(follow_ups)} ({[f['company'] for f in follow_ups[:5]]})
Stale applications: {len(stale)} ({[s['company'] for s in stale[:3]]})
Date: {date.today().isoformat()}

Be direct. Green/Yellow/Red status. Key actions only."""

        try:
            return await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=300,
                temperature=0.3,
            )
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return f"Pipeline: {pipeline_health['total']} active applications. {len(follow_ups)} follow-ups needed."

    async def log_application(
        self,
        company: str,
        role: str,
        job_url: str,
        applied_via: str = "direct",
        notes: str = "",
    ) -> dict:
        """Log a new application to Supabase."""
        try:
            from supabase.client import get_supabase
            db = get_supabase()
            record = {
                "company": company,
                "role": role,
                "job_url": job_url,
                "status": "applied",
                "applied_via": applied_via,
                "notes": notes,
                "last_action_date": datetime.now().isoformat(),
                "follow_up_count": 0,
            }
            result = db.table("applications").insert(record).execute()
            self.log(f"✅ Logged application: {role} @ {company}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to log application: {e}")
            return {}

    async def update_status(
        self,
        app_id: int,
        new_status: str,
        notes: str = "",
    ) -> bool:
        """Update an application status."""
        if new_status not in STATUS_FLOW:
            logger.warning(f"Invalid status: {new_status}")
            return False

        try:
            from supabase.client import get_supabase
            db = get_supabase()
            db.table("applications").update({
                "status": new_status,
                "last_action_date": datetime.now().isoformat(),
                "notes": notes,
            }).eq("id", app_id).execute()
            self.log(f"Updated app {app_id} → {new_status}")
            return True
        except Exception as e:
            logger.error(f"Status update failed: {e}")
            return False

    def get_pipeline_report(self) -> dict:
        """Get a formatted pipeline report for the Vercel dashboard."""
        try:
            from supabase.client import get_supabase
            from collections import Counter
            db = get_supabase()
            result = db.table("applications").select("*").execute()
            apps = result.data or []

            by_status = Counter(a["status"] for a in apps)
            active = [a for a in apps if a["status"] not in ["rejected", "withdrawn", "accepted", "ghosted"]]
            recent = sorted(apps, key=lambda x: x.get("created_at", ""), reverse=True)[:5]

            return {
                "total": len(apps),
                "active": len(active),
                "by_status": dict(by_status),
                "recent_applications": recent,
                "success_rate": (
                    round(by_status.get("offer", 0) / len(apps) * 100, 1)
                    if apps else 0
                ),
            }
        except Exception as e:
            logger.error(f"Pipeline report failed: {e}")
            return {"total": 0, "active": 0, "by_status": {}}
