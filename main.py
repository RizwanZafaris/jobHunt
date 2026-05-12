"""
main.py — Entry point for the AI Job Hunt System v2.

Usage:
    python main.py --now                          # Run full pipeline immediately
    python main.py --scheduler                    # Start daily scheduler
    python main.py --company "Tabby" --now        # Target specific company
    python main.py --role "Head of Product" --now # Target specific role
    python main.py --interview --job-id 42        # Interview prep for a job
    python main.py --boss                         # Run boss agent audit now
    python main.py --onboard                      # First-time setup
    python main.py --api                          # Start FastAPI server

Railway / Production:
    Set START_MODE=api in Railway env to run as API server.
    Set START_MODE=scheduler to run as daily scheduler daemon.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from rich.console import Console
from dotenv import load_dotenv

load_dotenv()

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/reports/system.log", mode="a"),
    ]
)
logger = logging.getLogger(__name__)


async def run_pipeline(company=None, role=None, job_ids=None, skip_scout=False):
    """Run the full job hunt pipeline."""
    from pipeline import JobHuntPipeline
    pipeline = JobHuntPipeline()
    return await pipeline.run(
        target_company=company,
        target_role=role,
        job_ids=job_ids,
        skip_scout=skip_scout,
    )


async def run_boss_agent():
    """Run the BossAgent nightly audit."""
    from agents.boss_agent import BossAgent
    boss = BossAgent()
    return await boss.run()


async def run_persona_synthesis(force: bool = False):
    """Run the PersonaSynthesizer (Phase 1.6 weekly cron)."""
    from agents.persona_synthesizer import PersonaSynthesizer
    synth = PersonaSynthesizer()
    results = await synth.run(force=force)
    n_synth = sum(1 for r in results if r.status == "synthesized")
    n_skipped = sum(1 for r in results if r.status.startswith("skipped"))
    n_failed = sum(1 for r in results if r.status == "failed")
    total_cost = sum(r.cost_usd for r in results)
    console.print(
        f"\n[bold green]✅ Persona synthesis done[/bold green]\n"
        f"   Synthesized: {n_synth}\n"
        f"   Skipped:     {n_skipped}\n"
        f"   Failed:      {n_failed}\n"
        f"   Total cost:  ${total_cost:.2f}\n"
    )
    return results


async def run_daily_cost_alert():
    """Phase 1.10 daily-spend threshold check (cron 22:00 GST)."""
    from agents.cost_alerter import CostAlerter
    return await CostAlerter().check_daily_spend()


async def run_weekly_cost_digest():
    """Phase 1.10 weekly cost digest (cron Sunday 09:00 GST)."""
    from agents.cost_alerter import CostAlerter
    return await CostAlerter().send_weekly_digest()


async def run_interview_prep(job_id: int):
    """Generate interview prep for a specific job."""
    from agents.interview_agent import InterviewAgent
    from db.client import get_job
    job = get_job(job_id)
    if not job:
        console.print(f"[red]Job ID {job_id} not found.[/red]")
        return
    agent = InterviewAgent()
    result = await agent.run(
        company=job["company"],
        job_title=job["title"],
        jd_text=job.get("description", ""),
        job_id=job_id,
    )
    console.print(f"✅ Interview prep saved: {result.get('file_path')}")


async def onboard():
    """First-time setup: seed profile, set up Supabase."""
    console.print("[bold cyan]🚀 Job Hunt System v2 — First-Time Setup[/bold cyan]\n")

    # Create directories
    for d in ["output/resumes", "output/reports", "output/interview_prep", "data"]:
        os.makedirs(d, exist_ok=True)

    console.print("1. Seeding Rizwan's profile to Supabase...")
    from agents.rizwan_agent import RizwanAgent
    agent = RizwanAgent()
    await agent.seed_supabase_profile()
    console.print("   ✅ Profile seeded\n")

    console.print("2. Checking Supabase connection...")
    try:
        from db.client import get_supabase
        db = get_supabase()
        result = db.table("companies").select("id").limit(1).execute()
        console.print("   ✅ Supabase connected\n")
    except Exception as e:
        console.print(f"   [red]❌ Supabase error: {e}[/red]")
        console.print("   → Run: psql your-supabase-url < db/schema.sql\n")

    console.print("3. Checking API keys...")
    from config.settings import get_settings
    s = get_settings()
    console.print(f"   Anthropic: {'✅' if s.anthropic_api_key else '❌ Missing'}")
    console.print(f"   OpenAI: {'✅' if s.openai_api_key else '❌ Missing'}")
    console.print(f"   Serper: {'✅' if s.serper_api_key else '❌ Missing'}")
    console.print(f"   Supabase URL: {'✅' if s.supabase_url else '❌ Missing'}\n")

    console.print("[bold green]✅ Setup complete! Run: python main.py --now[/bold green]")


def start_scheduler():
    """Start the daily scheduler: 09:00 pipeline + 21:00 boss agent."""
    from config.settings import get_settings
    s = get_settings()
    tz = pytz.timezone(s.timezone)

    console.print(f"[bold cyan]📅 Starting scheduler (timezone: {s.timezone})[/bold cyan]")
    console.print(f"   Job Scout: daily at {s.job_scout_time}")
    console.print(f"   Boss Agent: daily at {s.boss_agent_time}")
    console.print("   Press Ctrl+C to stop\n")

    scheduler = AsyncIOScheduler(timezone=tz)

    # P1-2 (external audit) / §6.5 (internal): harden scheduler defaults so a
    # backlog of missed fires (e.g. after a Railway restart) coalesces into a
    # single run instead of a thundering herd. Per-job `misfire_grace_time`
    # overrides below still win — these defaults are the floor.
    JOB_DEFAULTS = {
        "coalesce": True,           # collapse missed fires into one
        "max_instances": 1,         # never run more than one instance at once
        "misfire_grace_time": 300,  # floor; per-job values still override
    }
    scheduler.configure(job_defaults=JOB_DEFAULTS)

    def on_job_misfire(event):
        """Log scheduler misfires so we can see them in Railway logs."""
        logger.warning(
            f"scheduler_misfire: job_id={event.job_id} "
            f"scheduled_run_time={event.scheduled_run_time}"
        )

    scheduler.add_listener(on_job_misfire, EVENT_JOB_MISSED)

    # Parse times
    scout_h, scout_m = map(int, s.job_scout_time.split(":"))
    boss_h, boss_m = map(int, s.boss_agent_time.split(":"))

    scheduler.add_job(
        lambda: asyncio.create_task(run_pipeline()),
        CronTrigger(hour=scout_h, minute=scout_m, timezone=tz),
        id="job_scout",
        name="Daily Job Scout",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        lambda: asyncio.create_task(run_boss_agent()),
        CronTrigger(hour=boss_h, minute=boss_m, timezone=tz),
        id="boss_agent",
        name="Nightly Boss Agent",
        misfire_grace_time=300,
    )

    # Phase 1.6: weekly persona-synthesis cron
    # Sundays at 03:00 GST (or whatever timezone) — quiet time, after a full week
    # of outcome data accumulation. The synthesizer skips companies with no new
    # data, so this is cheap on weeks where you haven't logged outcomes.
    scheduler.add_job(
        lambda: asyncio.create_task(run_persona_synthesis()),
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=tz),
        id="persona_synthesis",
        name="Weekly Persona Synthesis",
        misfire_grace_time=3600,    # 1h grace — non-urgent
    )
    console.print(f"   Persona Synthesis: Sundays 03:00 (auto-skips when no new data)")

    # Phase 1.10: daily cost-spend alert (after boss audit) +
    # weekly cost digest (Sunday morning, before user wakes up).
    daily_alert_h, daily_alert_m = map(int, s.daily_alert_time.split(":"))
    weekly_digest_h, weekly_digest_m = map(int, s.weekly_digest_time.split(":"))
    scheduler.add_job(
        lambda: asyncio.create_task(run_daily_cost_alert()),
        CronTrigger(hour=daily_alert_h, minute=daily_alert_m, timezone=tz),
        id="daily_cost_alert",
        name="Daily Cost Alert",
        misfire_grace_time=600,    # 10m grace
    )
    scheduler.add_job(
        lambda: asyncio.create_task(run_weekly_cost_digest()),
        CronTrigger(day_of_week="sun", hour=weekly_digest_h, minute=weekly_digest_m, timezone=tz),
        id="weekly_cost_digest",
        name="Weekly Cost Digest",
        misfire_grace_time=3600,    # 1h grace
    )
    console.print(f"   Daily Cost Alert:  {s.daily_alert_time} (>= ${s.daily_cost_alert_usd:.0f} threshold)")
    console.print(f"   Weekly Digest:     Sundays {s.weekly_digest_time}")

    scheduler.start()
    console.print("[green]Scheduler running. Waiting for next trigger...[/green]")

    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        console.print("\n[yellow]Scheduler stopped.[/yellow]")


def start_api():
    """Start the FastAPI server for Railway/Vercel deployment."""
    import uvicorn
    from config.settings import get_settings
    s = get_settings()
    console.print(f"[bold cyan]🌐 Starting API server on port {s.port}[/bold cyan]")

    # Route Uvicorn lifecycle logs to stdout instead of stderr.
    # Railway's log ingester tags any stderr line as severity=error, which
    # was causing "Started server process", "Application startup complete",
    # etc. to appear as red ERROR entries even though the app is healthy.
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error":  {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"],  "level": "INFO", "propagate": False},
        },
    }

    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=s.port,
        reload=s.environment == "development",
        log_config=log_config,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Job Hunt System v2")
    parser.add_argument("--now", action="store_true", help="Run pipeline immediately")
    parser.add_argument("--scheduler", action="store_true", help="Start daily scheduler")
    parser.add_argument("--boss", action="store_true", help="Run boss agent audit now")
    parser.add_argument("--persona-synth", action="store_true",
                        help="Run persona synthesizer now (Phase 1.6)")
    parser.add_argument("--force", action="store_true",
                        help="With --persona-synth: re-synthesize even when no new data")
    parser.add_argument("--alert-check", action="store_true",
                        help="Run daily cost-alert threshold check now (Phase 1.10)")
    parser.add_argument("--weekly-digest", action="store_true",
                        help="Send weekly cost digest now (Phase 1.10)")
    parser.add_argument("--interview", action="store_true", help="Generate interview prep")
    parser.add_argument("--job-id", type=int, help="Job ID for interview prep")
    parser.add_argument("--company", type=str, help="Target a specific company")
    parser.add_argument("--role", type=str, help="Target a specific role type")
    parser.add_argument("--skip-scout", action="store_true", help="Skip job discovery")
    parser.add_argument("--onboard", action="store_true", help="First-time setup")
    parser.add_argument("--api", action="store_true", help="Start API server")
    args = parser.parse_args()

    # Railway auto-detection
    start_mode = os.environ.get("START_MODE", "")
    if start_mode == "scheduler":
        args.scheduler = True
    elif start_mode == "api":
        args.api = True

    # Ensure output dirs exist
    os.makedirs("output/reports", exist_ok=True)

    if args.onboard:
        asyncio.run(onboard())
    elif args.api:
        start_api()
    elif args.scheduler:
        start_scheduler()
    elif args.boss:
        asyncio.run(run_boss_agent())
    elif args.persona_synth:
        asyncio.run(run_persona_synthesis(force=args.force))
    elif args.alert_check:
        asyncio.run(run_daily_cost_alert())
    elif args.weekly_digest:
        asyncio.run(run_weekly_cost_digest())
    elif args.interview and args.job_id:
        asyncio.run(run_interview_prep(args.job_id))
    elif args.now or args.company or args.role:
        asyncio.run(run_pipeline(
            company=args.company,
            role=args.role,
            skip_scout=args.skip_scout,
        ))
    else:
        parser.print_help()
