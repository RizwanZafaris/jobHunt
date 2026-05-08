"""
agents/persona_synthesizer.py — Phase 1.6 — the loop that makes the system learn.

Runs weekly (Sunday 03:00 GST by default) via APScheduler in main.py.
Also triggerable on-demand via POST /personas/synthesize.

For each company with a persona row (or every target company on `force=True`):

  1. Pull recent resume_outcomes (last 90 days) for the company
  2. Pull recent resume_builds.agent_transcript for the company
  3. Pull existing company_knowledge (recruitment-intel sections)
  4. Skip if no NEW data since last_synthesized_at  (unless force=True)
  5. Call Gemini 2.5 Pro (long context, ~1M tokens) to synthesize:
        - Updated system_prompt_template
        - Structured ats_keyword_bank: {required, boost, banned}
        - success_patterns: bullet structures from resumes that GOT interviews
        - failure_patterns: bullet structures from resumes that DIDN'T
  6. UPSERT into company_personas with persona_version + 1
  7. Log to boss_audit_log so the cron's effect is visible

LLM choice: Gemini 2.5 Pro because the input can easily exceed 100k tokens
(13 sections × prose + 5 transcripts × 4 nodes × prose + 30+ outcomes).
Falls back to Claude when GOOGLE_API_KEY isn't configured.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from agents.base_agent import BaseAgent
from agents.llm_router import _parse_json_loose, get_router
from config.settings import get_settings

logger = logging.getLogger(__name__)


# How far back to pull outcomes + transcripts for synthesis
LOOKBACK_DAYS = 90


@dataclass
class SynthesisResult:
    """Per-company result emitted by run() — useful for the scheduler log."""
    company_name: str
    status: str    # 'synthesized' | 'skipped_no_new_data' | 'skipped_no_input' | 'failed'
    persona_version_after: Optional[int] = None
    n_outcomes_used: int = 0
    n_transcripts_used: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════
# Prompt
# ═════════════════════════════════════════════════════════════════════════
SYNTHESIZER_SYSTEM = """You are an elite resume strategist synthesizing an updated company persona for a multi-agent resume builder.

Given:
  - The existing company persona (system prompt + ATS keyword bank + past patterns)
  - Raw company recruitment-intel from web research
  - Recent outcomes (which resumes for this company got interviews / responses / offers)
  - Recent agent transcripts (debate logs from past resume builds)

Your job: produce a sharper persona that incorporates the OBSERVED reality of what
worked + what didn't, on top of the published recruitment-intel.

Be ruthless about pattern extraction:
  - If 3+ resumes that got interviews share a bullet structure → that's a success_pattern
  - If 3+ resumes that didn't get a response share a missing keyword → that's a failure_pattern
  - If recruitment-intel says X but outcomes contradict X → trust outcomes, note the override
  - If outcomes are sparse → keep recommendations conservative, don't over-fit on noise

Output STRICT JSON only. No prose, no markdown fences:

{
  "system_prompt_template": "<full updated system prompt — ~2-4k chars, used by G2's Insider Expert at runtime>",
  "ats_keyword_bank": {
    "required": ["keyword 1", "..."],
    "boost":    ["keyword that helps but isn't required", "..."],
    "banned":   ["keyword/phrase to avoid here", "..."]
  },
  "success_patterns": [
    {"pattern": "concise rule", "example": "actual bullet that worked", "evidence": "outcome IDs or N=count"}
  ],
  "failure_patterns": [
    {"pattern": "concise rule", "example": "actual bullet that failed", "evidence": "outcome IDs or N=count"}
  ],
  "synthesis_summary": "1-2 sentences: what changed in this version vs. the previous"
}"""


# ═════════════════════════════════════════════════════════════════════════
# Synthesizer
# ═════════════════════════════════════════════════════════════════════════
class PersonaSynthesizer(BaseAgent):
    """
    Refreshes company_personas rows from observed outcomes + transcripts.
    Inherits from BaseAgent so cost/latency telemetry flows through the
    LLMRouter into agent_call_log automatically.
    """

    def __init__(self):
        settings = get_settings()
        # We use Gemini 2.5 Pro for long context; agent's "default model" is
        # set to that, but specific calls can override via self.ask(model=...).
        super().__init__(
            name="PersonaSynthesizer",
            model=settings.g2_meta_critic_model,   # gemini-2.5-pro
            provider="google",
        )

    # ─── Top-level entry points ──────────────────────────────────────
    async def run(self, force: bool = False) -> list[SynthesisResult]:
        """
        Iterate over every company that has a persona row (or every target
        company if no personas exist yet). Run synthesize_one for each.
        """
        from db.client import get_supabase
        db = get_supabase()

        # Prefer existing personas (we know they're already seeded)
        personas = (
            db.table("company_personas")
            .select("company_name, persona_version, last_synthesized_at")
            .execute()
            .data
        ) or []

        if not personas:
            # Cold-start: no personas yet — fall back to all targets
            logger.info(
                "PersonaSynthesizer: no personas exist yet — synthesizing for all targets"
            )
            target_rows = (
                db.table("companies")
                .select("name")
                .eq("is_target", True)
                .execute()
                .data
            ) or []
            company_names = [r["name"] for r in target_rows]
        else:
            company_names = [p["company_name"] for p in personas]

        results: list[SynthesisResult] = []
        for name in company_names:
            try:
                r = await self.synthesize_one(name, force=force)
                results.append(r)
            except Exception as e:
                logger.exception(f"PersonaSynthesizer: failed for {name}")
                results.append(SynthesisResult(
                    company_name=name, status="failed", error=str(e)[:500]
                ))

        # Log a summary row to boss_audit_log so the cron's effect is visible
        try:
            self._log_summary(results)
        except Exception as e:
            logger.debug(f"persona synthesis summary log failed: {e}")

        return results

    async def synthesize_one(
        self,
        company_name: str,
        *,
        force: bool = False,
    ) -> SynthesisResult:
        """Synthesize an updated persona for one company."""
        from db.client import get_supabase
        db = get_supabase()

        # ── 1. Pull existing persona (if any) ─────────────────────────
        existing_rows = (
            db.table("company_personas")
            .select("*")
            .eq("company_name", company_name)
            .limit(1)
            .execute()
            .data
        ) or []
        existing = existing_rows[0] if existing_rows else None

        # ── 2. Pull outcomes + transcripts ────────────────────────────
        cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        outcomes = (
            db.table("resume_outcomes")
            .select("*")
            .eq("company_name", company_name)
            .gte("logged_at", cutoff)
            .order("logged_at", desc=True)
            .execute()
            .data
        ) or []

        transcripts = (
            db.table("resume_builds")
            .select("agent_transcript, polisher_score, finalized_at")
            .eq("company_name", company_name)
            .eq("status", "converged")
            .gte("created_at", cutoff)
            .order("finalized_at", desc=True)
            .limit(10)
            .execute()
            .data
        ) or []

        knowledge = (
            db.table("company_knowledge")
            .select("section, content")
            .eq("company_name", company_name)
            .execute()
            .data
        ) or []
        knowledge_text = "\n\n".join(
            f"[{r['section']}]\n{r['content']}" for r in knowledge
        )

        # ── 3. Skip if nothing has changed since last synthesis ───────
        if existing and not force:
            last_synth = existing.get("last_synthesized_at")
            if last_synth and not self._has_new_data(last_synth, outcomes, transcripts):
                logger.info(
                    f"PersonaSynthesizer: {company_name} — no new data since "
                    f"{last_synth}, skipping"
                )
                return SynthesisResult(
                    company_name=company_name,
                    status="skipped_no_new_data",
                    persona_version_after=existing.get("persona_version"),
                )

        # ── 4. If we have neither outcomes nor knowledge, bail ────────
        if not outcomes and not knowledge_text and not existing:
            logger.info(
                f"PersonaSynthesizer: {company_name} — no input data, skipping"
            )
            return SynthesisResult(
                company_name=company_name,
                status="skipped_no_input",
            )

        # ── 5. Synthesize via LLM ─────────────────────────────────────
        user = self._build_synthesis_prompt(
            company_name=company_name,
            existing=existing,
            knowledge_text=knowledge_text,
            outcomes=outcomes,
            transcripts=transcripts,
        )

        # Use the agent's default (Gemini 2.5 Pro). If the user hasn't
        # configured GOOGLE_API_KEY, ask() will raise — caller catches.
        try:
            result = await self.ask(
                system=SYNTHESIZER_SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=6000,
                temperature=0.2,
            )
        except RuntimeError as e:
            # No Google key — fall back to Claude (smaller context but still works)
            if "google" in str(e).lower() or "GOOGLE_API_KEY" in str(e):
                logger.warning(
                    f"PersonaSynthesizer: GOOGLE_API_KEY not configured — "
                    f"falling back to Claude for {company_name}"
                )
                result = await self.ask(
                    system=SYNTHESIZER_SYSTEM,
                    messages=[{"role": "user", "content": user}],
                    max_tokens=6000,
                    temperature=0.2,
                    provider="anthropic",
                    model=get_settings().g2_polisher_model,  # claude opus
                )
            else:
                raise

        try:
            parsed = _parse_json_loose(result.text)
        except Exception as e:
            logger.error(
                f"PersonaSynthesizer: JSON parse failed for {company_name}: {e}"
            )
            return SynthesisResult(
                company_name=company_name,
                status="failed",
                error=f"JSON parse: {str(e)[:200]}",
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )

        # ── 6. Upsert into company_personas ───────────────────────────
        company_id = None
        company_lookup = (
            db.table("companies")
            .select("id")
            .eq("name", company_name)
            .limit(1)
            .execute()
            .data
        ) or []
        if company_lookup:
            company_id = company_lookup[0]["id"]

        new_metadata = {
            **((existing or {}).get("metadata") or {}),
            "last_synthesizer_run": datetime.now(timezone.utc).isoformat(),
            "last_synthesizer_model": f"{result.provider}/{result.model}",
            "synthesis_summary": parsed.get("synthesis_summary", ""),
            "n_outcomes_used": len(outcomes),
            "n_transcripts_used": len(transcripts),
        }

        upsert_payload = {
            "company_id": company_id,
            "company_name": company_name,
            "system_prompt_template": parsed.get(
                "system_prompt_template", (existing or {}).get("system_prompt_template", "")
            ),
            "ats_keyword_bank": parsed.get("ats_keyword_bank", {}),
            "success_patterns": parsed.get("success_patterns", []),
            "failure_patterns": parsed.get("failure_patterns", []),
            "n_examples_used": len(outcomes),
            "last_synthesized_at": datetime.now(timezone.utc).isoformat(),
            "metadata": new_metadata,
        }
        # persona_version: increment if existing, else 2 (1 was the seed)
        if existing:
            upsert_payload["persona_version"] = (existing.get("persona_version") or 1) + 1
        else:
            upsert_payload["persona_version"] = 1

        db.table("company_personas").upsert(
            upsert_payload, on_conflict="company_name"
        ).execute()

        return SynthesisResult(
            company_name=company_name,
            status="synthesized",
            persona_version_after=upsert_payload["persona_version"],
            n_outcomes_used=len(outcomes),
            n_transcripts_used=len(transcripts),
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )

    # ─── Helpers ─────────────────────────────────────────────────────
    def _has_new_data(
        self,
        last_synthesized_at: str,
        outcomes: list[dict],
        transcripts: list[dict],
    ) -> bool:
        """True if any outcome or transcript is newer than the last synth run."""
        try:
            cutoff = datetime.fromisoformat(last_synthesized_at.replace("Z", "+00:00"))
        except Exception:
            return True   # malformed timestamp → safer to re-synth
        for o in outcomes:
            if not o.get("logged_at"):
                continue
            try:
                if datetime.fromisoformat(o["logged_at"].replace("Z", "+00:00")) > cutoff:
                    return True
            except Exception:
                continue
        for t in transcripts:
            if not t.get("finalized_at"):
                continue
            try:
                if datetime.fromisoformat(t["finalized_at"].replace("Z", "+00:00")) > cutoff:
                    return True
            except Exception:
                continue
        return False

    def _build_synthesis_prompt(
        self,
        company_name: str,
        existing: Optional[dict],
        knowledge_text: str,
        outcomes: list[dict],
        transcripts: list[dict],
    ) -> str:
        """Compose the user message for the synthesizer LLM call."""
        existing_block = "(no existing persona — this is a cold-start synthesis)"
        if existing:
            existing_block = json.dumps({
                "system_prompt_template": (existing.get("system_prompt_template") or "")[:3000],
                "ats_keyword_bank": existing.get("ats_keyword_bank", {}),
                "success_patterns": existing.get("success_patterns", []),
                "failure_patterns": existing.get("failure_patterns", []),
                "version": existing.get("persona_version"),
                "metadata": existing.get("metadata", {}),
            }, indent=2, default=str)[:6000]

        # Compress outcomes to the signal the synthesizer needs
        outcome_summaries = []
        for o in outcomes[:50]:
            outcome_summaries.append({
                "logged_at": o.get("logged_at"),
                "ats_passed": o.get("ats_passed"),
                "recruiter_responded": o.get("recruiter_responded"),
                "interview_received": o.get("interview_received"),
                "rounds_reached": o.get("rounds_reached"),
                "offer_received": o.get("offer_received"),
                "rejected_reason": o.get("rejected_reason"),
                "self_rated_quality": o.get("self_rated_quality"),
                "notes": (o.get("notes") or "")[:300],
            })

        return f"""COMPANY: {company_name}

═══ EXISTING PERSONA ═══
{existing_block}

═══ COMPANY RECRUITMENT INTEL (from web research, may be stale) ═══
{knowledge_text[:30000]}

═══ RECENT OUTCOMES (last {LOOKBACK_DAYS} days, n={len(outcomes)}) ═══
{json.dumps(outcome_summaries, indent=2, default=str)[:15000]}

═══ RECENT AGENT TRANSCRIPTS (converged G2 builds, n={len(transcripts)}) ═══
{json.dumps(transcripts, indent=2, default=str)[:50000]}

═══ TASK ═══
Synthesize an updated persona that incorporates observed reality on top of
the published intel. Output strict JSON only — see system prompt for schema.
"""

    def _log_summary(self, results: list[SynthesisResult]) -> None:
        """Write a row to boss_audit_log so the weekly cron's effect is visible."""
        from db.client import get_supabase
        n_synth = sum(1 for r in results if r.status == "synthesized")
        n_skipped = sum(1 for r in results if r.status.startswith("skipped"))
        n_failed = sum(1 for r in results if r.status == "failed")
        total_cost = sum(r.cost_usd for r in results)
        synthesized_companies = [r.company_name for r in results if r.status == "synthesized"]

        digest = (
            f"PersonaSynthesizer run: {n_synth} synthesized, "
            f"{n_skipped} skipped, {n_failed} failed. "
            f"Total cost: ${total_cost:.2f}. "
            f"Companies refreshed: {', '.join(synthesized_companies[:10])}"
            f"{' ...' if len(synthesized_companies) > 10 else ''}"
        )

        get_supabase().table("boss_audit_log").insert({
            "refreshed": synthesized_companies,
            "digest_content": digest,
            "digest_sent": False,
        }).execute()
