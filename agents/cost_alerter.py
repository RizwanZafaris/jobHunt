"""
agents/cost_alerter.py — Phase 1.10 — cost-budget alerts + weekly digest.

Two operations:

  check_daily_spend()
    Runs after the boss audit (22:00 GST cron). Reads today's spend from
    agent_call_log, compares to settings.daily_cost_alert_usd, fires an
    alert if exceeded. Idempotent — won't double-fire if already alerted
    today.

  send_weekly_digest()
    Runs Sundays 09:00 GST. Aggregates last 7 days:
      - per-provider cost + error rate from v_agent_call_health
      - top 5 most expensive resume_builds with company + status
      - count of cost_capped builds (signal that g2_max_cost_usd may be
        too tight)
      - persona-conversion funnel (resumes → responses → interviews)
        from v_company_conversion_funnel

Dispatch order (first match wins):
  1. Slack webhook (settings.slack_webhook_url) — preferred
  2. SendGrid email (settings.sendgrid_api_key + alert_email_to or
     digest_email_to)
  3. Stdout log only (last resort, also useful for testing)

Both ops write a row to boss_audit_log on completion so /personas
dashboard's audit history surfaces them.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from agents.base_agent import BaseAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AlertResult:
    """Returned from each entry point — useful for the scheduler log."""
    kind: str                  # 'daily' | 'weekly_digest'
    fired: bool                # did we actually dispatch?
    channel: Optional[str]     # 'slack' | 'sendgrid' | 'stdout' | None
    message: str               # the body that was sent
    metrics: dict[str, Any]    # raw numbers (for boss_audit_log)
    error: Optional[str] = None


class CostAlerter(BaseAgent):
    """
    Cost-budget alerts. Inherits from BaseAgent purely for telemetry —
    doesn't actually call LLMs. Set provider/model to a no-op default.
    """

    def __init__(self):
        # Stub provider/model — we never call .ask(). Inheritance is for
        # consistency with the rest of the agent layer.
        super().__init__(
            name="CostAlerter",
            model="claude-haiku-4-5-20251001",
            provider="anthropic",
        )

    # ─── Public entry points ─────────────────────────────────────────
    async def check_daily_spend(self) -> AlertResult:
        """
        Fire an alert if today's cumulative LLM spend exceeds
        settings.daily_cost_alert_usd. No-op if under threshold or if
        we've already fired today (idempotency).
        """
        settings = self.settings
        today = datetime.now(timezone.utc).date()
        today_iso = today.isoformat()

        # Idempotency — check boss_audit_log for today's daily alert
        if self._already_alerted_today():
            return AlertResult(
                kind="daily",
                fired=False,
                channel=None,
                message="(skipped — alert already fired today)",
                metrics={"today_iso": today_iso},
            )

        # Gather today's spend
        today_spend, today_calls, by_provider = self._aggregate_today()

        if today_spend < settings.daily_cost_alert_usd:
            self.log(
                f"  ✓ Daily spend ${today_spend:.2f} under threshold "
                f"${settings.daily_cost_alert_usd:.2f} — no alert"
            )
            return AlertResult(
                kind="daily",
                fired=False,
                channel=None,
                message=f"Under threshold (${today_spend:.2f} / ${settings.daily_cost_alert_usd:.2f})",
                metrics={
                    "today_spend_usd": today_spend,
                    "today_calls": today_calls,
                    "threshold_usd": settings.daily_cost_alert_usd,
                    "by_provider": by_provider,
                },
            )

        # Threshold exceeded — build message + dispatch
        message = self._format_daily_alert(
            today_spend=today_spend,
            today_calls=today_calls,
            by_provider=by_provider,
            threshold=settings.daily_cost_alert_usd,
        )
        channel, error = await self._dispatch(
            subject=f"[jobHunt] Daily cost alert — ${today_spend:.2f}",
            message=message,
        )

        result = AlertResult(
            kind="daily",
            fired=True,
            channel=channel,
            message=message,
            metrics={
                "today_spend_usd": today_spend,
                "today_calls": today_calls,
                "threshold_usd": settings.daily_cost_alert_usd,
                "by_provider": by_provider,
            },
            error=error,
        )
        self._log_to_audit(result)
        return result

    async def send_weekly_digest(self) -> AlertResult:
        """Aggregate last 7 days into a digest message and dispatch."""
        settings = self.settings
        if not settings.weekly_cost_digest:
            return AlertResult(
                kind="weekly_digest",
                fired=False,
                channel=None,
                message="(weekly digest disabled in settings)",
                metrics={},
            )

        metrics = self._gather_weekly_metrics()
        message = self._format_weekly_digest(metrics)
        channel, error = await self._dispatch(
            subject=f"[jobHunt] Weekly cost digest — {datetime.now(timezone.utc).date().isoformat()}",
            message=message,
        )

        result = AlertResult(
            kind="weekly_digest",
            fired=True,
            channel=channel,
            message=message,
            metrics=metrics,
            error=error,
        )
        self._log_to_audit(result)
        return result

    # ─── Data gathering ──────────────────────────────────────────────
    def _aggregate_today(self) -> tuple[float, int, dict[str, dict]]:
        """Returns (total_usd, total_calls, by_provider_dict)."""
        from db.client import get_supabase
        db = get_supabase()
        today_iso = datetime.now(timezone.utc).date().isoformat()
        try:
            rows = (
                db.table("agent_call_log")
                .select("provider, cost_usd, latency_ms")
                .gte("called_at", today_iso)
                .execute()
                .data
            ) or []
        except Exception as e:
            logger.warning(f"_aggregate_today query failed: {e}")
            return 0.0, 0, {}

        total = 0.0
        by_provider: dict[str, dict] = {}
        for r in rows:
            cost = float(r.get("cost_usd") or 0)
            total += cost
            p = r.get("provider") or "(unknown)"
            entry = by_provider.setdefault(p, {"calls": 0, "cost_usd": 0.0})
            entry["calls"] += 1
            entry["cost_usd"] += cost
        for p, e in by_provider.items():
            e["cost_usd"] = round(e["cost_usd"], 4)
        return round(total, 4), len(rows), by_provider

    def _gather_weekly_metrics(self) -> dict[str, Any]:
        """
        Pulls everything the weekly digest needs in 4 queries.
        Each is wrapped in try/except so partial failures don't break the
        whole digest — the metric gets defaulted to an empty list/zero.
        """
        from db.client import get_supabase
        db = get_supabase()
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # 1. Health by provider (uses Phase 1.9 view)
        health: list[dict] = []
        try:
            health = db.table("v_agent_call_health").select("*").execute().data or []
        except Exception as e:
            logger.debug(f"weekly digest health pull failed: {e}")

        # 2. Top 5 expensive resume_builds
        top_builds: list[dict] = []
        try:
            top_builds = (
                db.table("resume_builds")
                .select(
                    "id, company_name, polisher_score, status, "
                    "iterations, cost_usd_total, finalized_at"
                )
                .gte("created_at", cutoff_7d)
                .order("cost_usd_total", desc=True)
                .limit(5)
                .execute()
                .data
            ) or []
        except Exception as e:
            logger.debug(f"weekly digest top_builds pull failed: {e}")

        # 3. cost_capped count
        cost_capped_count = 0
        try:
            r = (
                db.table("resume_builds")
                .select("id", count="exact")
                .eq("status", "cost_capped")
                .gte("created_at", cutoff_7d)
                .execute()
            )
            cost_capped_count = r.count or 0
        except Exception as e:
            logger.debug(f"weekly digest cost_capped count failed: {e}")

        # 4. Conversion funnel (Phase 0 view)
        funnel: list[dict] = []
        try:
            funnel = (
                db.table("v_company_conversion_funnel")
                .select("*")
                .execute()
                .data
            ) or []
        except Exception as e:
            logger.debug(f"weekly digest funnel pull failed: {e}")

        total_cost_7d = round(
            sum(float(h.get("total_cost_usd_7d") or 0) for h in health), 4
        )
        total_calls_7d = sum(int(h.get("calls_7d") or 0) for h in health)
        total_errors_7d = sum(int(h.get("errors_7d") or 0) for h in health)

        return {
            "window_start_iso": cutoff_7d,
            "total_cost_usd_7d": total_cost_7d,
            "total_calls_7d": total_calls_7d,
            "total_errors_7d": total_errors_7d,
            "health_by_provider": health,
            "top_builds": top_builds,
            "cost_capped_count_7d": cost_capped_count,
            "conversion_funnel": funnel,
        }

    # ─── Message formatting ──────────────────────────────────────────
    def _format_daily_alert(
        self,
        *,
        today_spend: float,
        today_calls: int,
        by_provider: dict[str, dict],
        threshold: float,
    ) -> str:
        lines = [
            f"⚠ *Daily LLM cost alert* — today's spend exceeded threshold",
            "",
            f"   • Today: *${today_spend:.2f}* across {today_calls} calls",
            f"   • Threshold: ${threshold:.2f}",
            f"   • Overage: *${today_spend - threshold:.2f}*",
            "",
            "*By provider:*",
        ]
        for provider, e in sorted(
            by_provider.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
        ):
            lines.append(f"   • `{provider}` — ${e['cost_usd']:.2f} ({e['calls']} calls)")
        lines.append("")
        lines.append("Investigate at /costs · check Recent Calls for spikes or errors.")
        return "\n".join(lines)

    def _format_weekly_digest(self, m: dict[str, Any]) -> str:
        date_str = datetime.now(timezone.utc).date().isoformat()
        lines = [
            f"*jobHunt — Weekly cost digest, {date_str}*",
            f"Window: last 7 days",
            "",
            "*Spend:*",
            f"   • Total: *${m['total_cost_usd_7d']:.2f}* across {m['total_calls_7d']} calls",
            f"   • Errors: {m['total_errors_7d']} ({_pct(m['total_errors_7d'], m['total_calls_7d'])}%)",
            f"   • Cost-capped builds: {m['cost_capped_count_7d']}",
            "",
            "*By provider:*",
        ]
        if not m["health_by_provider"]:
            lines.append("   _(no calls — agent_call_log is empty)_")
        for h in m["health_by_provider"]:
            err_rate = float(h.get("error_rate_pct") or 0)
            err_marker = " ⚠" if err_rate > 5 else ""
            p95_ms = h.get("p95_latency_ms") or 0
            slow_marker = " 🐢" if p95_ms > 20000 else ""
            lines.append(
                f"   • `{h['provider']}` — ${float(h['total_cost_usd_7d']):.2f}, "
                f"{h['calls_7d']} calls, "
                f"err {err_rate:.1f}%{err_marker}, "
                f"p95 {p95_ms / 1000:.1f}s{slow_marker}"
            )
        lines.append("")
        lines.append("*Top 5 most expensive resume_builds:*")
        if not m["top_builds"]:
            lines.append("   _(no builds yet — flip USE_G2_GRAPH=true to start)_")
        for i, b in enumerate(m["top_builds"], 1):
            cost = float(b.get("cost_usd_total") or 0)
            score = b.get("polisher_score")
            score_str = f"{score}/100" if score is not None else "—"
            iters = b.get("iterations") or 0
            lines.append(
                f"   {i}. {b.get('company_name', '?')} — "
                f"${cost:.2f}, score {score_str}, "
                f"{iters} iter, status `{b.get('status', '?')}`"
            )
        lines.append("")
        lines.append("*Conversion funnel (companies with ≥1 build):*")
        funnel = [r for r in m["conversion_funnel"] if r.get("resumes_built", 0) > 0]
        if not funnel:
            lines.append("   _(no converged builds yet — log outcomes from /jobs/[id])_")
        for r in funnel[:8]:
            built = r.get("resumes_built", 0)
            responses = r.get("responses") or 0
            interviews = r.get("interviews") or 0
            offers = r.get("offers") or 0
            lines.append(
                f"   • {r.get('company_name', '?')}: "
                f"{built} built → {responses} resp → {interviews} interview → {offers} offer"
            )
        lines.append("")
        lines.append("Full dashboard: /costs · /personas")
        return "\n".join(lines)

    # ─── Dispatch ────────────────────────────────────────────────────
    async def _dispatch(
        self,
        *,
        subject: str,
        message: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Try Slack first, then SendGrid, then stdout. Returns
        (channel_used, error_or_None). Channel is None on full failure.
        """
        s = self.settings

        # Slack first — fastest, most reliable
        if s.slack_webhook_url:
            err = await self._send_slack(s.slack_webhook_url, message)
            if err is None:
                self.log(f"  Alert dispatched via Slack ✅", style="green")
                return "slack", None
            logger.warning(f"Slack dispatch failed: {err}, trying email")

        # SendGrid fallback
        recipient = s.alert_email_to or s.digest_email_to
        if s.sendgrid_api_key and recipient:
            err = self._send_sendgrid(
                s.sendgrid_api_key,
                from_addr=s.digest_email_from,
                to_addr=recipient,
                subject=subject,
                body=message,
            )
            if err is None:
                self.log(f"  Alert dispatched via SendGrid ✅", style="green")
                return "sendgrid", None
            logger.warning(f"SendGrid dispatch failed: {err}, falling back to stdout")

        # Stdout — visible in Railway logs
        logger.warning("=" * 70)
        logger.warning(f"ALERT (no channel configured): {subject}")
        logger.warning("=" * 70)
        for line in message.split("\n"):
            logger.warning(line)
        logger.warning("=" * 70)
        return "stdout", None

    async def _send_slack(self, webhook_url: str, text: str) -> Optional[str]:
        """Returns None on success, error string on failure."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    webhook_url,
                    json={"text": text},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code != 200:
                    return f"HTTP {resp.status_code}: {resp.text[:200]}"
                return None
        except Exception as e:
            return str(e)[:200]

    def _send_sendgrid(
        self,
        api_key: str,
        *,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
    ) -> Optional[str]:
        try:
            import sendgrid
            from sendgrid.helpers.mail import Mail
            sg = sendgrid.SendGridAPIClient(api_key=api_key)
            msg = Mail(
                from_email=from_addr,
                to_emails=to_addr,
                subject=subject,
                plain_text_content=body,
            )
            sg.send(msg)
            return None
        except Exception as e:
            return str(e)[:200]

    # ─── Idempotency + audit logging ────────────────────────────────
    def _already_alerted_today(self) -> bool:
        from db.client import get_supabase
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            r = (
                get_supabase()
                .table("boss_audit_log")
                .select("id, digest_content")
                .eq("run_date", today)
                .ilike("digest_content", "%cost-alerter:daily%")
                .limit(1)
                .execute()
            )
            return bool(r.data)
        except Exception:
            return False

    def _log_to_audit(self, result: AlertResult) -> None:
        from db.client import get_supabase
        try:
            tag = f"cost-alerter:{result.kind}"
            get_supabase().table("boss_audit_log").insert({
                "digest_content": (
                    f"[{tag}] fired={result.fired} channel={result.channel} "
                    f"error={result.error}\n\n{result.message[:4000]}"
                ),
                "digest_sent": result.fired and result.channel != "stdout",
            }).execute()
        except Exception as e:
            logger.debug(f"Audit log write failed: {e}")

    # ─── BaseAgent abstract ──────────────────────────────────────────
    async def run(self, *args, **kwargs):
        """Convenience: call check_daily_spend by default."""
        return await self.check_daily_spend()


def _pct(num: int, denom: int) -> float:
    if not denom:
        return 0.0
    return round(100.0 * num / denom, 1)
