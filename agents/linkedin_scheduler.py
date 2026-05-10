"""
agents/linkedin_scheduler.py — LinkedIn scheduler / sweeper.

Runs every 30 minutes (via APScheduler in api/orphan_reaper.py-style cron
or an external scheduler). Three responsibilities:

  1. **Notify**: For every approved draft whose `scheduled_for` is now ±15min
     and whose status is still 'approved' or 'scheduled', send a desktop
     notification (or email) saying:
       "Your LinkedIn draft for today is ready. Open the dashboard,
        copy, and post."
     The user clicks "Copy to clipboard" in the dashboard which records
     `manual_copy_at` and flips status='posted'.

  2. **Expire**: Approved/scheduled drafts whose `scheduled_for` is more
     than 24h in the past AND were never copied flip to status='expired'.
     Keeps the dashboard tidy without losing analytics.

  3. **Bookkeeping**: Future Buffer / Hypefury integration would post here.
     V1 does NOT auto-post; we stub the integration with a clearly-marked
     FUTURE_WORK comment for when the Career-tier ships.

CLI:
    python -m agents.linkedin_scheduler --once   # single sweep, exit
    python -m agents.linkedin_scheduler          # loop forever, sleep 30min

Wiring:
- Production: invoke from a Railway cron or APScheduler instance once
  every 30 minutes. The `_sweep_once` function is idempotent — safe to
  re-run if the cron double-fires.
- The notify step is a **placeholder** today (logs + writes to a
  `notifications` queue table if it exists). Wire to email/SMS/push in
  a follow-up.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time as _time_mod
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from db.client import get_supabase

logger = logging.getLogger(__name__)


SWEEP_WINDOW_MINUTES = 15        # ±15min around scheduled_for triggers a notify
EXPIRY_HOURS = 24                # past scheduled_for + this → status='expired'
DEFAULT_INTERVAL_SECS = 1800     # 30 min loop default


# ═════════════════════════════════════════════════════════════════════════
# Notification stub — V1 logs only. Replace with email/push when ready.
# ═════════════════════════════════════════════════════════════════════════
def _notify_user(user_id: str, draft: dict[str, Any]) -> None:
    """Send the user a "your LinkedIn draft is ready" notification.

    V1: logs + best-effort writes a row to a `notifications` table if one
    exists. The dashboard already pings the API when the user opens
    /linkedin so this is belt-and-braces.

    Replace this body with email (Resend / SendGrid) or push (OneSignal)
    once those are wired. Don't change the call signature.
    """
    msg = (
        f"LinkedIn draft ready (id={draft.get('id')}, angle={draft.get('angle')}). "
        f"Scheduled for {draft.get('scheduled_for')}. Copy and post manually."
    )
    logger.info(f"[notify] user_id={user_id} :: {msg}")

    # Best-effort: write a notification row if the table exists.
    try:
        db = get_supabase()
        db.table("notifications").insert({
            "user_id": user_id,
            "kind":    "linkedin_draft_ready",
            "payload": {"draft_id": draft.get("id"), "angle": draft.get("angle")},
            "message": msg,
            "delivered_at": None,
        }).execute()
    except Exception:
        # Notifications table doesn't exist yet — silent.
        pass


# ═════════════════════════════════════════════════════════════════════════
# FUTURE_WORK — Career-tier auto-post via Buffer / Hypefury.
# DO NOT enable until the user has explicitly opted in AND we've validated
# the integration's TOS. See api/LINKEDIN.md §"Future integrations".
# ═════════════════════════════════════════════════════════════════════════
def _post_via_buffer(_user_id: str, _draft: dict[str, Any]) -> Optional[str]:
    """Buffer/Hypefury auto-post. Returns the posted URL on success.

    FUTURE_WORK: not implemented. The V1 engine NEVER auto-posts.
    Returning None signals the caller to fall back to the manual path
    (notify + user-copy + flip status='posted').

    When this lands:
      1. Read the user's Buffer/Hypefury API key from a secrets table
         (encrypted at rest).
      2. POST to Buffer Updates API with the body + hashtags.
      3. Return the resulting public LinkedIn URL.
      4. Caller flips status='posted' + posted_url + posted_at.

    Risk gates that must pass first:
      - User explicitly opted in (career_tier_settings.allow_auto_post = TRUE).
      - LinkedIn TOS reviewed for the integration's posting pattern.
      - Rate limit: max 5 auto-posts/week per user (LinkedIn flags burst posting).
    """
    return None


# ═════════════════════════════════════════════════════════════════════════
# Sweepers
# ═════════════════════════════════════════════════════════════════════════
def _sweep_notify(now: Optional[datetime] = None) -> int:
    """Find approved/scheduled drafts within the notify window.

    Returns the count notified. Each notify is idempotent at the row
    level — we only notify once per draft (we look at a `notified_at`
    field if present in `engagement_metrics` or fall back to "we already
    fired today").
    """
    now = now or datetime.now(timezone.utc)
    lo = (now - timedelta(minutes=SWEEP_WINDOW_MINUTES)).isoformat()
    hi = (now + timedelta(minutes=SWEEP_WINDOW_MINUTES)).isoformat()

    db = get_supabase()
    result = (
        db.table("linkedin_drafts")
        .select("*")
        .in_("status", ["approved", "scheduled"])
        .gte("scheduled_for", lo)
        .lte("scheduled_for", hi)
        .execute()
    )
    drafts = result.data or []
    notified = 0
    for d in drafts:
        eng = d.get("engagement_metrics") or {}
        if eng.get("notified_at"):
            # Already notified — skip.
            continue
        _notify_user(str(d["user_id"]), d)
        # Stamp `notified_at` so we don't re-notify on the next sweep.
        try:
            new_eng = {**eng, "notified_at": now.isoformat()}
            db.table("linkedin_drafts").update(
                {"engagement_metrics": new_eng}
            ).eq("id", d["id"]).execute()
            notified += 1
        except Exception as e:
            logger.warning(f"sweep_notify: failed to stamp draft {d['id']}: {e}")
    return notified


def _sweep_expire(now: Optional[datetime] = None) -> int:
    """Flip drafts past their scheduled_for (+ EXPIRY_HOURS) to 'expired'.

    Only touches drafts that are still 'approved' or 'scheduled' AND
    were never manually copied. Returns the count expired.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=EXPIRY_HOURS)).isoformat()
    db = get_supabase()
    result = (
        db.table("linkedin_drafts")
        .select("id")
        .in_("status", ["approved", "scheduled"])
        .lt("scheduled_for", cutoff)
        .is_("manual_copy_at", "null")
        .execute()
    )
    rows = result.data or []
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    db.table("linkedin_drafts").update({"status": "expired"}).in_("id", ids).execute()
    return len(ids)


def _sweep_once() -> dict[str, int]:
    """Run a single sweep. Idempotent. Used by both the loop and the cron
    invocation in production.
    """
    now = datetime.now(timezone.utc)
    notified = _sweep_notify(now)
    expired = _sweep_expire(now)
    logger.info(f"linkedin_scheduler: notified={notified} expired={expired}")
    return {"notified": notified, "expired": expired}


def _loop(interval_secs: int) -> None:
    """Sleep loop. Replaces a cron when we want to run inline (dev or
    a single-process worker)."""
    logger.info(f"linkedin_scheduler: looping every {interval_secs}s")
    while True:
        try:
            _sweep_once()
        except Exception:
            logger.exception("linkedin_scheduler: sweep failed; will retry")
        _time_mod.sleep(interval_secs)


# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents.linkedin_scheduler",
        description="LinkedIn draft scheduler / notifier sweep.",
    )
    p.add_argument("--once", action="store_true",
                   help="Run a single sweep and exit (for cron).")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECS,
                   help="Loop interval in seconds (default 1800 = 30min).")
    return p


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _build_argparser().parse_args()
    if args.once:
        result = _sweep_once()
        print(result)
        return 0
    _loop(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
