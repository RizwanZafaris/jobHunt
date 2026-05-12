"""
agents/g6_state.py — Shared LangGraph state for the G6 Follow-up Graph.

One TypedDict (FollowUpState) flows through every node. Each node reads
the fields it needs and returns a dict patch. LangGraph merges patches
into the running state.

LangGraph reducer notes (matches G2 convention):
  - `transcript`        uses operator.add  (append-only audit log)
  - `cost_usd_total`    uses operator.add
  - `latency_ms_total`  uses operator.add
  - All other fields are last-write-wins.

Lifecycle:
    entry from g6_io.run_g6_for_application(application_id)
      ↓
    cadence_checker   — sets urgency, next_action, days_since_event,
                        recommended_delay_days; SHORT-CIRCUITS when
                        next_action != 'follow_up' (waiting / cold / noop)
                        by leaving draft_email empty and persisting a
                        skip-row.
      ↓
    context_builder   — pgvector RAG hits (last 7d of company_knowledge)
                        + linkedin_drafts personal-win signals.
      ↓
    draft_generator   — Sonnet 4.6, the long-cached system prompt is the
                        career-ops follow-up framework. Output is the
                        draft email + cite breadcrumbs.
      ↓                       (parallel fan-out, matches G2 pattern)
    tone_calibrator + persona_critic  ← both read current_draft + persona
      ↓
    merge_critique    — pure code, applies the union of edits or bounces
                        back to draft_generator (max 2 retries).
      ↓
    urgency_scorer    — pure code, may downgrade URGENT→waiting if the
                        critique flagged the draft as too thin.
      ↓
    persist_and_notify — pure code, writes follow_up_cadence row + flips
                         applications.status to 'withdrawn' if COLD.
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


class FollowUpTranscriptTurn(TypedDict, total=False):
    node: str
    iteration: int
    provider: str
    model: str
    input_summary: str
    output: Any
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class FollowUpState(TypedDict, total=False):
    # ─── Inputs (hydrated by g6_io.run_g6_for_application) ──────────────
    application_id: int                         # public.applications.id
    user_id: str                                 # rls owner
    application_row: dict                        # full applications row
    company_persona: Optional[dict]              # row from company_personas (or None)
    last_cadence_row: Optional[dict]             # the most recent follow_up_cadence row

    # ─── Cadence decision (set by cadence_checker) ──────────────────────
    last_event: str                              # 'applied_date' | 'last_contact_date'
    days_since_event: int
    follow_up_count: int                         # CURRENT count before incrementing
    urgency: str                                  # URGENT | OVERDUE | waiting | COLD
    next_action: str                             # follow_up | wait | close | noop
    recommended_delay_days: int
    cadence_reason: str                          # for transcript + ops debugging
    short_circuit: bool                          # True when cadence_checker decided
                                                 # we don't need to draft today

    # ─── Context (set by context_builder) ───────────────────────────────
    # Each chunk: {"kind":"knowledge"|"linkedin","id":"uuid","content":"...",
    #              "section":"news"|"culture"|...,"similarity":0.0-1.0}
    rag_chunks: list[dict]

    # ─── Draft (set by draft_generator) ─────────────────────────────────
    draft_email: str
    # Citations: [{"kind":"knowledge"|"linkedin","id":"uuid"}], one per signal.
    draft_cites: list[dict]
    draft_attempts: int                          # # of times draft_generator ran

    # ─── Critique outputs (parallel fan-out) ────────────────────────────
    persona_critique: dict                       # banned_phrases_used etc.
    voice_critique: dict                         # tone match score etc.
    merge_outcome: dict                          # {accepted, edits, retry, reason}
    needs_retry: bool                            # set by merge to drive loop

    # ─── Telemetry ──────────────────────────────────────────────────────
    transcript: Annotated[list[FollowUpTranscriptTurn], add]
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    iteration: int                                # draft<->critique loop counter
    error: Optional[str]

    # ─── Persistence outcome (set by persist_and_notify) ────────────────
    cadence_row_id: Optional[str]                # uuid of the new follow_up_cadence row
    applications_closed_cold: bool               # True if we transitioned to withdrawn
    pushed_to_today_queue: bool                  # always True when URGENT/OVERDUE


def make_g6_turn(
    node: str,
    *,
    iteration: int = 0,
    provider: str = "",
    model: str = "",
    input_summary: str = "",
    output: Any = None,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> FollowUpTranscriptTurn:
    """Convenience constructor; matches make_turn in resume_agents.g2_state."""
    if isinstance(input_summary, str) and len(input_summary) > 500:
        input_summary = input_summary[:497] + "..."
    return FollowUpTranscriptTurn(
        node=node,
        iteration=iteration,
        provider=provider,
        model=model,
        input_summary=input_summary or "",
        output=output,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        error=error,
    )
