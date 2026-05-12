"""
agents/outcome_to_persona.py — Credit assignment + persona evolution.

Closes the audit's #1 unfilled wedge (docs/AUDIT_360_SYNTHESIS.md
§4 P1.3 outcome-conditioned RAG; §3 P1.5 persona evolution measured).

Two top-level entry points:

    credit_outcome(outcome_event_id, kind='interview' | 'resume') -> dict
        Reads one outcome row, traces back to the resume_build that produced
        the application, recovers the company_knowledge rows that were cited
        in the resume's agent_transcript, and writes one
        knowledge_outcome_credits row per (outcome × cited knowledge_id).
        Then recomputes company_knowledge.outcome_score as the weighted
        mean across all credits for that knowledge_id (clamped [0, 1]).

    evolve_persona(persona_id) -> dict
        Snapshots the current company_personas row to persona_versions,
        asks Claude Opus 4.5 to propose ATS-bank / success_pattern /
        failure_pattern updates conditioned on recent outcome credits,
        bumps persona_version, and returns a one-line diff_summary.

CLI:

    python -m agents.outcome_to_persona --outcome-id <uuid> --kind interview
    python -m agents.outcome_to_persona --persona-id <uuid>

Designed for Sunday cron (after enough outcomes accumulate) and for the
api/interview_studio.py log-outcome endpoint to fire-and-forget per
outcome event.

Credit-assignment scoring (the audit's table):

    interview_round_passed     +0.05 per cited knowledge row
    interview_round_failed     -0.02
    resume_recruiter_responded +0.04
    resume_rejected            -0.01
    offer_received             +0.10

Caps: per-event delta is bounded ±0.5 (the column CHECK).
Aggregate: outcome_score is clamped to [0, 1] before write.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Credit-assignment table (the audit's hard rules) ────────────────────
# Keep this table tiny + explicit. The api/INTERVIEW_STUDIO.md doc
# duplicates it verbatim — when you change a number here, change it there.

CREDIT_INTERVIEW_PASS = 0.05
CREDIT_INTERVIEW_FAIL = -0.02
CREDIT_RESUME_RESPONDED = 0.04
CREDIT_RESUME_REJECTED = -0.01
CREDIT_OFFER = 0.10

# Per-event delta hard cap (matches the column CHECK).
CREDIT_DELTA_MIN = -0.5
CREDIT_DELTA_MAX = 0.5

# Aggregate clamp.
SCORE_MIN = 0.0
SCORE_MAX = 1.0

# Persona evolution model.
# 2026-05-12 right-sizing: this is a "propose minimal JSON edits to a banked
# persona" task, not deep reasoning. Sonnet 4.6 matches Opus 4.5 at ~5× less.
# EVOLVE_SYSTEM_PROMPT explicitly cages the model to micro-edits, which is
# exactly what Sonnet is good at. See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §32.
EVOLVE_MODEL = "claude-sonnet-4-6"
EVOLVE_PROVIDER = "anthropic"
EVOLVE_MAX_COST_USD = 1.0

OutcomeKind = Literal["interview", "resume"]


# ─── Tiny utilities ───────────────────────────────────────────────────────
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Pattern used by G2/G3 transcripts when they want to leave a citation
# breadcrumb. Today the writer doesn't always emit these — we have a
# fallback path in _knowledge_ids_for_resume_build.
_CITE_RE = re.compile(r"cite\s*[:=]\s*knowledge_id\s*[:=]\s*([0-9a-fA-F\-]{36})")


def _supabase():
    """Lazy supabase client — avoids import-time DB connection."""
    from db.client import get_supabase
    return get_supabase()


# ─── Loading: outcome → resume_build → cited knowledge ────────────────────
def _load_interview_outcome(outcome_id: str) -> Optional[dict]:
    db = _supabase()
    rows = (
        db.table("interview_outcomes")
        .select("*")
        .eq("id", outcome_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _load_resume_outcome(outcome_id: str) -> Optional[dict]:
    db = _supabase()
    rows = (
        db.table("resume_outcomes")
        .select("*")
        .eq("id", outcome_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _resume_build_for_application(application_id: Optional[str]) -> Optional[dict]:
    """Most-recent converged (or any) resume_build for an application."""
    if not application_id:
        return None
    db = _supabase()
    rows = (
        db.table("resume_builds")
        .select("id, agent_transcript, company_name, status, finalized_at, created_at, user_id")
        .eq("application_id", application_id)
        .order("finalized_at", desc=True, nullsfirst=False)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _knowledge_ids_from_transcript(transcript: Any) -> list[str]:
    """Pull cite:knowledge_id=<uuid> tokens out of a G2 transcript JSON.

    Looks at every node turn's input_summary and output strings. Returns a
    deduped list. Falls back to empty if no citations were emitted.
    """
    if not transcript:
        return []
    text_blob_parts: list[str] = []
    if isinstance(transcript, list):
        for turn in transcript:
            if not isinstance(turn, dict):
                continue
            for k in ("input_summary", "output", "node"):
                v = turn.get(k)
                if isinstance(v, str):
                    text_blob_parts.append(v)
                elif isinstance(v, (dict, list)):
                    try:
                        text_blob_parts.append(json.dumps(v))
                    except Exception:
                        pass
    elif isinstance(transcript, dict):
        try:
            text_blob_parts.append(json.dumps(transcript))
        except Exception:
            pass
    elif isinstance(transcript, str):
        text_blob_parts.append(transcript)
    blob = "\n".join(text_blob_parts)
    found = _CITE_RE.findall(blob)
    # Dedupe but preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for fid in found:
        if fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


def _knowledge_ids_for_resume_build(resume_build: dict) -> tuple[list[str], str]:
    """Return (knowledge_ids, source) for a resume_build.

    Strategy:
      1. Try cite:knowledge_id=<uuid> tokens in agent_transcript.
      2. Fallback: top-k knowledge rows for the company by created_at.
         Tag the source so callers know it's a heuristic.
    """
    transcript = resume_build.get("agent_transcript")
    cited = _knowledge_ids_from_transcript(transcript)
    if cited:
        # Validate they exist + return only the ones we can actually credit.
        db = _supabase()
        rows = (
            db.table("company_knowledge")
            .select("id")
            .in_("id", cited[:32])
            .execute()
        ).data or []
        valid = [r["id"] for r in rows if r.get("id")]
        if valid:
            return valid, "transcript_citations"

    # Fallback: top 5 knowledge rows for the company (most recent).
    company = resume_build.get("company_name")
    if not company:
        return [], "no_citations_no_company"
    db = _supabase()
    rows = (
        db.table("company_knowledge")
        .select("id, scraped_at")
        .eq("company_name", company)
        .order("scraped_at", desc=True)
        .limit(5)
        .execute()
    ).data or []
    return [r["id"] for r in rows if r.get("id")], "fallback_top_k"


# ─── Credit math ──────────────────────────────────────────────────────────
def _compute_interview_delta(outcome: dict) -> float:
    """+0.05 per pass, -0.02 per fail. Capped to ±0.5."""
    passed = outcome.get("passed")
    if passed is True:
        return _clamp(CREDIT_INTERVIEW_PASS, CREDIT_DELTA_MIN, CREDIT_DELTA_MAX)
    if passed is False:
        return _clamp(CREDIT_INTERVIEW_FAIL, CREDIT_DELTA_MIN, CREDIT_DELTA_MAX)
    return 0.0


def _compute_resume_delta(outcome: dict) -> tuple[float, str]:
    """Compose the resume delta from flags.

    Order matters: offer dominates. Both 'recruiter_responded' and
    'rejected_reason' can be true on the same row when the resume got a
    callback that ultimately led to a no — net is positive (still
    correlated with making it past the ATS).
    """
    delta = 0.0
    reasons: list[str] = []
    if outcome.get("offer_received"):
        delta += CREDIT_OFFER
        reasons.append("offer")
    if outcome.get("recruiter_responded"):
        delta += CREDIT_RESUME_RESPONDED
        reasons.append("recruiter_responded")
    if outcome.get("rejected_reason"):
        delta += CREDIT_RESUME_REJECTED
        reasons.append("rejected")
    return _clamp(delta, CREDIT_DELTA_MIN, CREDIT_DELTA_MAX), ",".join(reasons) or "no_signal"


def _recompute_outcome_score(knowledge_id: str) -> tuple[float, int]:
    """Reload all credits for a knowledge_id, return (new_score, credit_count).

    new_score = clamp(0.5 + sum(delta) / max(1, count), 0, 1)
    The 0.5 prior is the audit's "no signal yet" anchor. Sum/count is the
    arithmetic mean of deltas; we add it to 0.5 so a +0.05 average drift
    yields outcome_score ≈ 0.55.
    """
    db = _supabase()
    rows = (
        db.table("knowledge_outcome_credits")
        .select("delta")
        .eq("knowledge_id", knowledge_id)
        .execute()
    ).data or []
    if not rows:
        return 0.5, 0
    deltas = [float(r.get("delta") or 0) for r in rows]
    n = len(deltas)
    avg = sum(deltas) / max(1, n)
    score = _clamp(0.5 + avg, SCORE_MIN, SCORE_MAX)
    return round(score, 4), n


def _write_credit_row(
    *,
    user_id: str,
    knowledge_id: str,
    interview_outcome_id: Optional[str],
    resume_outcome_id: Optional[str],
    resume_build_id: Optional[str],
    delta: float,
    reason: str,
) -> Optional[dict]:
    db = _supabase()
    payload: dict[str, Any] = {
        "user_id": user_id,
        "knowledge_id": knowledge_id,
        "interview_outcome_id": interview_outcome_id,
        "resume_outcome_id": resume_outcome_id,
        "resume_build_id": resume_build_id,
        "delta": _clamp(float(delta), CREDIT_DELTA_MIN, CREDIT_DELTA_MAX),
        "reason": reason or None,
    }
    try:
        result = db.table("knowledge_outcome_credits").insert(payload).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"credit insert failed for kid={knowledge_id}: {type(e).__name__}: {e}")
        return None


def _update_company_knowledge_score(knowledge_id: str) -> None:
    score, count = _recompute_outcome_score(knowledge_id)
    db = _supabase()
    try:
        db.table("company_knowledge").update({
            "outcome_score": score,
            "outcome_credit_count": count,
        }).eq("id", knowledge_id).execute()
    except Exception as e:
        logger.warning(f"outcome_score update failed for {knowledge_id}: {type(e).__name__}: {e}")


# ─── Top-level: credit_outcome ────────────────────────────────────────────
async def credit_outcome(outcome_event_id: str, kind: OutcomeKind) -> dict:
    """Credit one outcome event back to its cited knowledge rows.

    Idempotency note: we INSERT a new credit row each time. Callers should
    avoid calling twice for the same outcome event — but if it happens, the
    aggregate stays bounded thanks to the per-event ±0.5 cap and the
    aggregate [0, 1] clamp.

    Async since 2026-05-12: all callers are async (FastAPI handlers, RQ
    workers via _run_async, LangGraph nodes). Sync scripts must wrap the
    call in `asyncio.run(...)` at the entrypoint — never inside the
    library. See docs/AGENT_REVIEW_2026_05_11.md §31, §4.5.

    Returns a dict with what we did so the caller can log/surface it.
    """
    summary: dict[str, Any] = {
        "outcome_event_id": outcome_event_id,
        "kind": kind,
        "started_at": _now_iso(),
        "credited_knowledge_count": 0,
        "delta_per_row": 0.0,
        "source": None,
        "error": None,
    }

    # 1. Load outcome event.
    if kind == "interview":
        outcome = _load_interview_outcome(outcome_event_id)
    elif kind == "resume":
        outcome = _load_resume_outcome(outcome_event_id)
    else:
        summary["error"] = f"unknown outcome kind: {kind}"
        return summary
    if not outcome:
        summary["error"] = "outcome_not_found"
        return summary

    # 2. Resolve resume_build via application_id chain.
    application_id = outcome.get("application_id")
    resume_build = (
        outcome.get("resume_build_id")
        and _supabase().table("resume_builds")
            .select("id, agent_transcript, company_name, status, user_id")
            .eq("id", outcome["resume_build_id"]).limit(1).execute().data
    )
    if isinstance(resume_build, list) and resume_build:
        resume_build = resume_build[0]
    if not resume_build:
        resume_build = _resume_build_for_application(application_id)
    if not resume_build:
        summary["error"] = "no_resume_build_for_outcome"
        return summary

    user_id = outcome.get("user_id") or resume_build.get("user_id")
    if not user_id:
        summary["error"] = "missing_user_id"
        return summary

    # 3. Recover cited knowledge rows.
    knowledge_ids, source = _knowledge_ids_for_resume_build(resume_build)
    summary["source"] = source
    if not knowledge_ids:
        summary["error"] = "no_knowledge_to_credit"
        return summary

    # 4. Compute per-row delta.
    if kind == "interview":
        delta = _compute_interview_delta(outcome)
        reason = (
            f"interview_round_{outcome.get('round_number')}_"
            f"{'pass' if outcome.get('passed') else 'fail' if outcome.get('passed') is False else 'unknown'}"
        )
    else:
        delta, reason = _compute_resume_delta(outcome)
    if delta == 0.0:
        summary["error"] = "zero_delta_skipped"
        return summary

    summary["delta_per_row"] = delta

    # 5. Insert one credit row per knowledge_id; recompute score after each.
    credited = 0
    for kid in knowledge_ids:
        row = _write_credit_row(
            user_id=str(user_id),
            knowledge_id=str(kid),
            interview_outcome_id=str(outcome_event_id) if kind == "interview" else None,
            resume_outcome_id=str(outcome_event_id) if kind == "resume" else None,
            resume_build_id=str(resume_build["id"]) if resume_build.get("id") else None,
            delta=delta,
            reason=reason,
        )
        if row is not None:
            credited += 1
            _update_company_knowledge_score(str(kid))

    summary["credited_knowledge_count"] = credited
    summary["finished_at"] = _now_iso()
    return summary


# ─── Top-level: evolve_persona ────────────────────────────────────────────
EVOLVE_SYSTEM_PROMPT = """You are a persona-evolution analyst for a fintech / payments career-ops system.

You receive (a) the CURRENT persona for a target company — its system prompt, ATS keyword bank (required/boost/banned), success_patterns, failure_patterns — and (b) a window of RECENT OUTCOME CREDITS that propagated to this company's knowledge rows over the last N weeks.

Your job: propose the smallest set of changes to the persona that the evidence justifies. Do not invent. Do not rewrite the system prompt. Adjust only the bank + patterns, and explain in one line why.

Rules:
1. Move an ATS keyword from `required` to `boost` (or drop it entirely) only if its corresponding knowledge rows show a NEGATIVE outcome trend (more recruiter rejects or interview fails than callbacks).
2. Promote an ATS keyword from `boost` to `required` only if the corresponding knowledge rows show a POSITIVE outcome trend (recruiter callbacks and/or interview passes).
3. Add a new success_pattern only if you can point to specific evidence (a callback or pass on a knowledge row).
4. Move a success_pattern to failure_patterns only if its evidence has flipped negative.
5. Cap each list at 12 items. If you'd exceed that, drop the weakest one and say so in the diff.
6. Banned keywords are sacred — change at most one per evolution.

OUTPUT — strict JSON, no preamble:
{
  "ats_keyword_bank": {"required": [...], "boost": [...], "banned": [...]},
  "success_patterns": ["..."],
  "failure_patterns": ["..."],
  "diff_summary": "<one-line: 'Promoted X, demoted Y, dropped Z (reason); added pattern P'>"
}
"""


def _load_persona(persona_id: str) -> Optional[dict]:
    db = _supabase()
    rows = (
        db.table("company_personas")
        .select("*")
        .eq("id", persona_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _recent_credits_for_company(company_name: str, limit: int = 60) -> list[dict]:
    """Pull recent knowledge_outcome_credits joined with company_knowledge for one company.

    Supabase REST doesn't do real joins, so we do it in two hops.
    """
    db = _supabase()
    knowledge_rows = (
        db.table("company_knowledge")
        .select("id, section, content, outcome_score, outcome_credit_count")
        .eq("company_name", company_name)
        .gt("outcome_credit_count", 0)
        .order("outcome_credit_count", desc=True)
        .limit(limit)
        .execute()
    ).data or []
    if not knowledge_rows:
        return []
    ids = [r["id"] for r in knowledge_rows]
    creds = (
        db.table("knowledge_outcome_credits")
        .select("id, knowledge_id, delta, reason, created_at")
        .in_("knowledge_id", ids[:128])
        .order("created_at", desc=True)
        .limit(limit * 4)
        .execute()
    ).data or []
    by_kid: dict[str, list[dict]] = {}
    for c in creds:
        by_kid.setdefault(c["knowledge_id"], []).append(c)
    out: list[dict] = []
    for k in knowledge_rows:
        content = (k.get("content") or "")[:400]
        items = by_kid.get(k["id"], [])
        deltas = [float(c.get("delta") or 0) for c in items]
        out.append({
            "section": k.get("section"),
            "content_excerpt": content,
            "outcome_score": k.get("outcome_score"),
            "credit_count": k.get("outcome_credit_count"),
            "deltas_recent": deltas[:8],
            "delta_sum": round(sum(deltas), 3),
        })
    return out


def _snapshot_persona_to_versions(
    *,
    persona: dict,
    diff_summary: Optional[str],
    triggered_by: str,
    triggered_outcomes: Optional[Iterable[str]] = None,
    cost_usd: float = 0.0,
) -> Optional[dict]:
    """Append the CURRENT persona to persona_versions before mutating it."""
    db = _supabase()
    payload = {
        "user_id": persona.get("user_id"),
        "persona_id": persona["id"],
        "version": persona.get("persona_version") or 1,
        "snapshot_md": persona.get("system_prompt_template"),
        "system_prompt_template": persona.get("system_prompt_template"),
        "ats_keyword_bank": persona.get("ats_keyword_bank") or {},
        "success_patterns": persona.get("success_patterns") or [],
        "failure_patterns": persona.get("failure_patterns") or [],
        "triggered_by": triggered_by,
        "diff_summary": diff_summary,
        "triggered_by_outcomes": list(triggered_outcomes or []),
        "cost_usd": float(cost_usd or 0),
    }
    try:
        result = db.table("persona_versions").insert(payload).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"persona_versions insert failed: {type(e).__name__}: {e}")
        return None


def _update_persona(persona_id: str, fields: dict) -> Optional[dict]:
    db = _supabase()
    try:
        result = db.table("company_personas").update(fields).eq("id", persona_id).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"persona update failed: {type(e).__name__}: {e}")
        return None


async def evolve_persona(
    persona_id: str,
    *,
    triggered_by: str = "manual",
) -> dict:
    """Evolve one persona based on recent outcome credits.

    Async since 2026-05-12: every caller is already inside an event loop
    (FastAPI handlers, RQ worker's `_run_async`, LangGraph nodes, the
    Sunday cron). Previously this function used `asyncio.run(_ask())`
    with a `asyncio.new_event_loop()` fallback — a textbook footgun that
    detached the LLM call from the shared Anthropic client and httpx pool
    bound to the running loop. See docs/AGENT_REVIEW_2026_05_11.md §31
    and docs/SYSTEM_AUDIT_2026_05_12.md §4.5.

    Sync scripts (e.g. the CLI below) must wrap the call in
    `asyncio.run(...)` at the entrypoint — never inside the library.

    Returns:
      {
        "persona_id": str,
        "old_version": int,
        "new_version": int,
        "diff_summary": str,
        "cost_usd": float,
        "skipped": bool,
        "error": str | None,
      }
    """
    summary: dict[str, Any] = {
        "persona_id": persona_id,
        "old_version": None,
        "new_version": None,
        "diff_summary": None,
        "cost_usd": 0.0,
        "skipped": False,
        "error": None,
        "triggered_by": triggered_by,
    }
    persona = _load_persona(persona_id)
    if not persona:
        summary["error"] = "persona_not_found"
        return summary

    summary["old_version"] = persona.get("persona_version") or 1
    company = persona.get("company_name") or ""
    credits = _recent_credits_for_company(company)
    if not credits:
        # No credit signal yet — snapshot but do not mutate.
        _snapshot_persona_to_versions(
            persona=persona,
            diff_summary="no_outcome_signal_yet",
            triggered_by=triggered_by,
            cost_usd=0.0,
        )
        summary["skipped"] = True
        summary["diff_summary"] = "no_outcome_signal_yet"
        return summary

    # Build the prompt body.
    body = {
        "company_name": company,
        "current_version": summary["old_version"],
        "ats_keyword_bank": persona.get("ats_keyword_bank") or {},
        "success_patterns": persona.get("success_patterns") or [],
        "failure_patterns": persona.get("failure_patterns") or [],
        "recent_outcome_signals": credits[:30],
    }

    # Async LLM call. We're already in an event loop — just await the
    # router. (Previously used asyncio.run + new_event_loop fallback,
    # which detached this call from the shared Anthropic client and
    # httpx pool bound to the running loop. See docstring.)
    from agents.llm_router import _parse_json_loose, get_router

    try:
        router = get_router()
        result = await router.ask(
            provider=EVOLVE_PROVIDER,
            model=EVOLVE_MODEL,
            system=EVOLVE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(body, default=str)[:24000]}],
            max_tokens=3000,
            temperature=0.2,
            agent_name="g3.evolve_persona",
        )
        try:
            parsed = _parse_json_loose(result.text or "")
        except Exception:
            parsed = {}
        cost = float(result.cost_usd or 0.0)
    except Exception as e:
        logger.warning(f"evolve_persona LLM call failed: {type(e).__name__}: {e}")
        summary["error"] = "llm_call_failed"
        return summary

    summary["cost_usd"] = round(cost, 6)
    if cost > EVOLVE_MAX_COST_USD:
        logger.warning(f"evolve_persona cost ${cost:.4f} exceeded cap ${EVOLVE_MAX_COST_USD}")

    # Validate proposal.
    proposal_bank = parsed.get("ats_keyword_bank") if isinstance(parsed, dict) else None
    proposal_success = parsed.get("success_patterns") if isinstance(parsed, dict) else None
    proposal_failure = parsed.get("failure_patterns") if isinstance(parsed, dict) else None
    diff_summary = parsed.get("diff_summary") if isinstance(parsed, dict) else None

    if not isinstance(proposal_bank, dict) or not isinstance(proposal_success, list) or not isinstance(proposal_failure, list):
        summary["error"] = "llm_returned_invalid_shape"
        # Still snapshot so we have an audit trail of the failed call.
        _snapshot_persona_to_versions(
            persona=persona,
            diff_summary="evolution_failed_invalid_shape",
            triggered_by=triggered_by,
            cost_usd=cost,
        )
        return summary

    # Cap the lists per the system rules.
    for k in ("required", "boost", "banned"):
        v = proposal_bank.get(k) or []
        if isinstance(v, list):
            proposal_bank[k] = [str(x) for x in v if isinstance(x, (str, int, float))][:12]
        else:
            proposal_bank[k] = []
    proposal_success = [str(x) for x in proposal_success if isinstance(x, (str, int, float))][:12]
    proposal_failure = [str(x) for x in proposal_failure if isinstance(x, (str, int, float))][:12]

    # Snapshot OLD persona to persona_versions (with the diff_summary so we
    # can render it on the timeline).
    triggered_outcomes = [c.get("section") for c in credits[:8] if c.get("section")]
    _snapshot_persona_to_versions(
        persona=persona,
        diff_summary=str(diff_summary)[:600] if isinstance(diff_summary, str) else None,
        triggered_by=triggered_by,
        triggered_outcomes=triggered_outcomes,
        cost_usd=cost,
    )

    # Apply changes + bump version.
    new_version = summary["old_version"] + 1
    update_payload = {
        "ats_keyword_bank": proposal_bank,
        "success_patterns": proposal_success,
        "failure_patterns": proposal_failure,
        "persona_version": new_version,
        "last_synthesized_at": _now_iso(),
    }
    _update_persona(persona_id, update_payload)
    summary["new_version"] = new_version
    summary["diff_summary"] = diff_summary or "(no diff_summary returned by LLM)"
    return summary


# ─── CLI ──────────────────────────────────────────────────────────────────
# This is the only sync entrypoint. credit_outcome and evolve_persona are
# `async def`; we use `asyncio.run(...)` here at the top level — never
# inside the library functions themselves. See module docstring.
def _cli() -> int:
    parser = argparse.ArgumentParser(description="Outcome → persona credit + evolution.")
    parser.add_argument("--outcome-id", help="Run credit_outcome for this outcome event id.")
    parser.add_argument("--kind", choices=("interview", "resume"), help="Kind of outcome.")
    parser.add_argument("--persona-id", help="Run evolve_persona for this persona id.")
    parser.add_argument("--triggered-by", default="manual", help="Source of the evolve_persona call.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.outcome_id:
        if not args.kind:
            print("--kind is required with --outcome-id")
            return 2
        result = asyncio.run(credit_outcome(args.outcome_id, args.kind))
        print(json.dumps(result, indent=2, default=str))
        return 0 if not result.get("error") else 1

    if args.persona_id:
        result = asyncio.run(evolve_persona(args.persona_id, triggered_by=args.triggered_by))
        print(json.dumps(result, indent=2, default=str))
        return 0 if not result.get("error") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = [
    "CREDIT_INTERVIEW_PASS",
    "CREDIT_INTERVIEW_FAIL",
    "CREDIT_RESUME_RESPONDED",
    "CREDIT_RESUME_REJECTED",
    "CREDIT_OFFER",
    "credit_outcome",
    "evolve_persona",
]
