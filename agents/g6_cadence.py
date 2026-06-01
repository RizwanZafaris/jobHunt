"""
agents/g6_cadence.py — Pure rules engine for the G6 Follow-up Graph.

NO LLM, NO DB. Given an application row + its last cadence state, returns
the urgency tag, the next recommended action, and how long to wait before
the next follow-up. This is the deterministic core that the LangGraph
cadence_checker node calls — kept separate so it is unit-testable in
isolation and behaves identically in dry-run / replay scenarios.

Cadence rules (encoded from career-ops best practices):

    | Application status   | Follow-up after | Max follow-ups | Auto-close after |
    |----------------------|-----------------|-----------------|------------------|
    | applied              | 7 days          | 2               | 30 days ghosted  |
    | interviewing         | 3 days          | 3               | 14 days          |
    | scheduled (intvw)    | 1 day pre       | 1 (thank-you)   | (manual)         |
    | offered              | 2 days          | 2 (negotiation) | (manual)         |
    | rejected / withdrawn | n/a             | n/a             | terminal         |

Note: jobHunt's `application_status` enum (db/migrations/002_status_enum)
does NOT include a distinct `responded` value — `interviewing` is the
canonical post-response state. The spec's "responded" row in the table
above maps to `interviewing` here.

Urgency taxonomy (used by /today ranking and the /workspace/follow-ups
queue):
    URGENT   — next_follow_up_date is in the past, follow_up_count < max,
               AND we have not exceeded the auto-close window.
    OVERDUE  — next_follow_up_date is in the past, but auto-close window
               has elapsed too. The user must explicitly skip or close.
    waiting  — within cadence window; nothing to do today.
    COLD     — follow_up_count >= max OR auto-close window elapsed.
               Returns a `next_action='close'` recommendation; the API
               surfaces it to the user and transitions the application
               to `withdrawn` once confirmed.

next_action values:
    'follow_up' — draft a follow-up today
    'wait'      — no follow-up needed yet
    'close'     — recommend closure (max follow-ups OR ghosted)
    'noop'      — terminal status (rejected/withdrawn)

The function returns (urgency, next_action, recommended_delay_days). The
caller persists urgency to follow_up_cadence and uses recommended_delay_days
to compute next_follow_up_date when a follow-up is drafted now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

Urgency = Literal["URGENT", "OVERDUE", "waiting", "COLD"]
NextAction = Literal["follow_up", "wait", "close", "noop"]


# ──────────────────────────────────────────────────────────────────────────
# Rules tables — single source of truth. Edit here, not in nodes.
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CadenceRule:
    """One row of the cadence-rules table."""
    follow_up_after_days: Optional[int]    # None means n/a (terminal)
    max_follow_ups: int                     # 0 means n/a
    auto_close_after_days: Optional[int]    # None means manual / never


# Keyed by application_status enum value (db/migrations/002_status_enum).
# Statuses NOT in this dict (e.g. 'new', 'researched', 'resume_ready') are
# pre-apply and the rules engine returns ('waiting', 'noop', 0) — there's
# nothing to follow up on if the application hasn't been sent.
CADENCE_RULES: dict[str, CadenceRule] = {
    "applied":      CadenceRule(follow_up_after_days=7, max_follow_ups=2, auto_close_after_days=30),
    "interviewing": CadenceRule(follow_up_after_days=3, max_follow_ups=3, auto_close_after_days=14),
    "offered":      CadenceRule(follow_up_after_days=2, max_follow_ups=2, auto_close_after_days=None),
    "rejected":     CadenceRule(follow_up_after_days=None, max_follow_ups=0, auto_close_after_days=None),
    "withdrawn":    CadenceRule(follow_up_after_days=None, max_follow_ups=0, auto_close_after_days=None),
}

# Pre-apply statuses: nothing to follow up on yet.
_PRE_APPLY_STATUSES = frozenset({"new", "researched", "resume_ready"})

# Terminal statuses: noop. (Same set as the "rules say n/a" rows above, but
# kept as an explicit constant so the cadence_checker can early-return on
# `rejected` and `withdrawn` without inspecting the rules table.)
_TERMINAL_STATUSES = frozenset({"rejected", "withdrawn"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value) -> Optional[datetime]:
    """Convert a Postgres-shaped value to an aware UTC datetime.

    Accepts datetime instances (assumed UTC if naive), ISO-8601 strings,
    and date instances. Returns None for None/empty inputs.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        # Supabase returns 'YYYY-MM-DD' for DATE and ISO-8601 with offset
        # for TIMESTAMPTZ. fromisoformat handles both in 3.11+.
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # date instance (sub-class check after datetime, since datetime IS date).
    try:
        from datetime import date as _date
        if isinstance(value, _date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    except Exception:
        pass
    return None


@dataclass
class CadenceDecision:
    """Output of compute_cadence — what the rest of G6 should do for this app."""
    urgency: Urgency
    next_action: NextAction
    recommended_delay_days: int        # Days from NOW until the next follow-up.
                                       # 0 means "draft today".
    days_since_last_event: int         # For telemetry / explainer.
    rule_applied: Optional[CadenceRule]
    reason: str                        # Human-readable, for transcript audit.

    def as_dict(self) -> dict:
        return {
            "urgency": self.urgency,
            "next_action": self.next_action,
            "recommended_delay_days": self.recommended_delay_days,
            "days_since_last_event": self.days_since_last_event,
            "reason": self.reason,
        }


def compute_cadence(
    *,
    status: str,
    applied_date,                        # datetime | date | str | None
    last_contact_date=None,              # datetime | str | None
    follow_up_count: int = 0,
    now: Optional[datetime] = None,
) -> CadenceDecision:
    """
    The rules engine. Given an application's status + relevant timestamps,
    decide what G6 should do today.

    Args:
        status: application_status enum value.
        applied_date: when the user submitted the application. Required for
                      post-apply statuses (the migration 014 CHECK enforces).
                      For pre-apply statuses, may be None.
        last_contact_date: when the user last contacted the recruiter (i.e.
                           when they last approved a follow-up). On the first
                           follow-up this is None — the cadence anchors on
                           applied_date.
        follow_up_count: how many follow-ups have already been sent for this
                         application.
        now: override for testing. Defaults to UTC now.

    Returns:
        CadenceDecision — urgency tag, next action, and recommended delay.

    Pure function. No DB, no LLM, no I/O. Testable in isolation.
    """
    now = now or _utcnow()

    # ── Terminal statuses ────────────────────────────────────────────────
    if status in _TERMINAL_STATUSES:
        return CadenceDecision(
            urgency="COLD",
            next_action="noop",
            recommended_delay_days=0,
            days_since_last_event=0,
            rule_applied=None,
            reason=f"terminal_status:{status}",
        )

    # ── Pre-apply statuses ───────────────────────────────────────────────
    if status in _PRE_APPLY_STATUSES:
        return CadenceDecision(
            urgency="waiting",
            next_action="noop",
            recommended_delay_days=0,
            days_since_last_event=0,
            rule_applied=None,
            reason=f"pre_apply_status:{status}",
        )

    # ── Lookup rule for this status ─────────────────────────────────────
    rule = CADENCE_RULES.get(status)
    if rule is None or rule.follow_up_after_days is None:
        # Status known to enum but not on our follow-up table. Be safe: wait.
        return CadenceDecision(
            urgency="waiting",
            next_action="noop",
            recommended_delay_days=0,
            days_since_last_event=0,
            rule_applied=rule,
            reason=f"no_cadence_rule_for_status:{status}",
        )

    # The cadence anchor is whichever happened LATEST:
    #   - applied_date (first cycle)
    #   - last_contact_date (subsequent cycles, set when user approves)
    anchor_applied = _coerce_dt(applied_date)
    anchor_contact = _coerce_dt(last_contact_date)
    if anchor_contact and (anchor_applied is None or anchor_contact > anchor_applied):
        anchor = anchor_contact
        anchor_source = "last_contact_date"
    elif anchor_applied:
        anchor = anchor_applied
        anchor_source = "applied_date"
    else:
        # Post-apply status WITHOUT applied_date. This is a data-integrity bug
        # the 014 CHECK constraint is supposed to prevent. Defensive fallback:
        # treat the application as just-applied so we don't either crash the
        # cron or spam the user with an URGENT card.
        return CadenceDecision(
            urgency="waiting",
            next_action="noop",
            recommended_delay_days=rule.follow_up_after_days,
            days_since_last_event=0,
            rule_applied=rule,
            reason="post_apply_status_missing_applied_date",
        )

    days_since = max(0, int((now - anchor).total_seconds() // 86400))

    # ── Auto-close window check (COLD: ghosted past auto-close window) ───
    if (
        rule.auto_close_after_days is not None
        and days_since >= rule.auto_close_after_days
    ):
        return CadenceDecision(
            urgency="COLD",
            next_action="close",
            recommended_delay_days=0,
            days_since_last_event=days_since,
            rule_applied=rule,
            reason=(
                f"ghosted_past_auto_close:{days_since}d>={rule.auto_close_after_days}d"
                f" (anchor={anchor_source})"
            ),
        )

    # ── Max follow-ups exhausted ─────────────────────────────────────────
    if follow_up_count >= rule.max_follow_ups:
        return CadenceDecision(
            urgency="COLD",
            next_action="close",
            recommended_delay_days=0,
            days_since_last_event=days_since,
            rule_applied=rule,
            reason=f"max_follow_ups_reached:{follow_up_count}>={rule.max_follow_ups}",
        )

    # ── Inside the wait window ───────────────────────────────────────────
    if days_since < rule.follow_up_after_days:
        delay = rule.follow_up_after_days - days_since
        return CadenceDecision(
            urgency="waiting",
            next_action="wait",
            recommended_delay_days=delay,
            days_since_last_event=days_since,
            rule_applied=rule,
            reason=(
                f"within_window:{days_since}d<{rule.follow_up_after_days}d"
                f" (anchor={anchor_source})"
            ),
        )

    # ── Past the wait window: URGENT (or OVERDUE if very late) ───────────
    # We classify as OVERDUE when the user is significantly past the cadence
    # window. The threshold is 2x the cadence interval — by that point the
    # callback probability has decayed substantially.
    overdue_threshold = rule.follow_up_after_days * 2
    if days_since >= overdue_threshold:
        urgency: Urgency = "OVERDUE"
    else:
        urgency = "URGENT"
    return CadenceDecision(
        urgency=urgency,
        next_action="follow_up",
        recommended_delay_days=0,    # draft today
        days_since_last_event=days_since,
        rule_applied=rule,
        reason=(
            f"past_window:{days_since}d>={rule.follow_up_after_days}d"
            f",follow_up_count={follow_up_count}/{rule.max_follow_ups}"
            f" (anchor={anchor_source})"
        ),
    )


def compute_next_follow_up_date(
    *,
    status: str,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """When the user approves a follow-up, this returns the next scheduled
    follow-up date so we can stash it on the new cadence row.

    None for terminal / pre-apply / unknown statuses.
    """
    now = now or _utcnow()
    rule = CADENCE_RULES.get(status)
    if rule is None or rule.follow_up_after_days is None:
        return None
    return now + timedelta(days=rule.follow_up_after_days)
