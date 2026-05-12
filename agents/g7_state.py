"""
agents/g7_state.py — Shared LangGraph state for the G7 Application Graph.

One TypedDict (`ApplicationFormState`) flows through every node. Each
node reads the fields it needs and returns a dict patch. LangGraph
merges patches into the running state.

LangGraph reducer notes (matches G2/G6 convention):
  - `transcript`        uses operator.add  (append-only audit log)
  - `cost_usd_total`    uses operator.add
  - `latency_ms_total`  uses operator.add
  - All other fields are last-write-wins.

Lifecycle:
    entry from g7_io.run_g7_for_application(application_id, form_url)
      ↓
    form_scanner     — Playwright reads the live form. Writes
                       extracted_fields (list of {field_id, label, type,
                       required, options, raw_html_snippet, idx}).
      ↓
    question_classifier — Sonnet 4.6 maps each field to one of 10
                          categories. Writes
                          questions[i].classification.
      ↓
    answer_retriever — code + RAG. For each question pulls the relevant
                       context (story_bank / company_knowledge / comp_cache /
                       profile.yml) and stores it in
                       questions[i].retrieved_context.
      ↓
    answer_generator (Sonnet 4.6, batch)  + persona_critic  (parallel sibling)
      ↘                                     ↙
    merge_critique   — code; sets needs_retry if persona_critic flagged
                       fabricated metrics OR banned keywords AND attempts
                       left. Up to _MAX_GENERATOR_RETRIES retries.
      ↓
    human_review     — LangGraph interrupt(). Persists state via the
                       checkpointer + flips application_answers.status to
                       'awaiting_review'. Dashboard fetches the row,
                       user approves/edits/regenerates per-field, POSTs
                       /approve which calls graph.invoke with the
                       approvals patched into state.
      ↓ (resume)
    form_filler      — Playwright. Fills fields, STOPS before submit.
                       Writes per-question ats_filled_ok + fill_error.
      ↓
    persist          — flips status to 'filled', INSERTs a
                       follow_up_cadence row (auto-enrolment in G6), and
                       runs the structural identity-lock scan to drop any
                       fabricated $ metrics that snuck through.
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


# ──────────────────────────────────────────────────────────────────────────
# The 10 classification categories from roadmap §5. Stored as a frozenset
# so the classifier prompt and the retriever can both ground on the same
# vocabulary without drift.
# ──────────────────────────────────────────────────────────────────────────
QUESTION_CATEGORIES: frozenset[str] = frozenset({
    "basic_info",         # name, email, phone, location, work auth
    "cover_letter",       # full cover-letter text-area
    "why_this_company",   # "why are you interested in {company}?"
    "why_this_role",      # "what excites you about this role?"
    "salary",             # expected comp / desired range
    "behavioral",         # "tell us about a time..." STAR-shaped
    "technical",          # "describe your experience with X technology"
    "dei_values",         # "what does inclusion mean to you?"
    "portfolio_url",      # LinkedIn URL, website, GitHub
    "custom",             # anything that doesn't fit above
})


class G7TranscriptTurn(TypedDict, total=False):
    """Append-only audit row — same shape as G6 / G2 turns so the cost
    alerter aggregator can pick it up without special casing."""
    node: str
    iteration: int
    provider: str
    model: str
    input_summary: str
    output: Any
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class G7Question(TypedDict, total=False):
    """One question from the form — assembled progressively by the graph.

    form_scanner populates the field_* + raw_* fields. question_classifier
    sets classification. answer_retriever fills retrieved_context + cites.
    answer_generator writes generated_answer. persona_critic writes
    persona_critique. form_filler writes ats_filled_ok + fill_error.
    """
    idx: int
    field_id: str
    field_label: str
    field_type: str          # text | textarea | select | radio | url | tel | email
    required: bool
    options: Optional[list[str]]
    raw_html_snippet: Optional[str]

    classification: str       # one of QUESTION_CATEGORIES
    retrieved_context: list[dict]  # see g7_state header for shape
    cites: list[dict]              # [{kind, id}]
    generated_answer: str
    persona_critique: dict

    user_edited: Optional[str]
    approved: bool

    ats_filled_ok: Optional[bool]
    fill_error: Optional[str]


class ApplicationFormState(TypedDict, total=False):
    # ─── Inputs (hydrated by g7_io.run_g7_for_application) ──────────────
    application_answer_id: str              # uuid; thread_id for checkpointer
    application_id: int                     # public.applications.id (BIGINT)
    user_id: str                            # rls owner; profiles(id)
    form_url: str
    ats_type: str                           # 'greenhouse' (v1)
    application_row: dict                   # full applications row
    job_row: Optional[dict]                 # parent jobs row (JD + comp band)
    company_persona: Optional[dict]
    cv_md: Optional[str]                    # rendered profile text
    profile: Optional[dict]                 # parsed profile.yml-ish dict

    # ─── form_scanner output ────────────────────────────────────────────
    questions: list[G7Question]
    scanner_meta: dict                      # {field_count, page_title, raw_html_size}

    # ─── question_classifier output (cumulative on questions) ───────────
    classification_summary: dict            # {category: count}

    # ─── answer_retriever output (cumulative on questions) ──────────────
    retrieval_summary: dict                 # {category: hits_per_question_avg}

    # ─── generator / critic loop ────────────────────────────────────────
    generation_iteration: int
    generation_attempts: int
    persona_critique_summary: dict          # {flagged_count, fabricated_metric_count}
    merge_outcome: dict
    needs_generator_retry: bool

    # ─── HITL ───────────────────────────────────────────────────────────
    # When the graph hits the human_review interrupt, LangGraph saves the
    # current state to the checkpointer and pauses. On resume, the API
    # passes user approvals back through the graph as the resume value;
    # the human_review_node merges them into questions[i].approved /
    # questions[i].user_edited.
    user_approvals: Optional[list[dict]]    # [{idx, approved, user_edited?}]
    review_started_at: Optional[str]        # ISO
    review_resumed_at: Optional[str]        # ISO

    # ─── form_filler output ─────────────────────────────────────────────
    filler_summary: dict                    # {filled_ok, failed, total}
    filler_screenshot_path: Optional[str]   # local path, opt-in
    stopped_before_submit: bool             # always True post-fill

    # ─── persist output ─────────────────────────────────────────────────
    cadence_row_id: Optional[str]           # follow_up_cadence row created
    identity_lock_redactions: list[dict]    # [{idx, metric, reason}]

    # ─── Telemetry (reducers) ───────────────────────────────────────────
    transcript: Annotated[list[G7TranscriptTurn], add]
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    error: Optional[str]

    # ─── Test override (skip Playwright) ────────────────────────────────
    # Declared in the TypedDict so LangGraph carries it through state
    # merging. Tests set this to a string of HTML; form_scanner_node uses
    # the value when present and never launches a browser.
    _test_html: Optional[str]


def make_g7_turn(
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
) -> G7TranscriptTurn:
    """Convenience constructor (matches make_g6_turn / G2 make_turn)."""
    if isinstance(input_summary, str) and len(input_summary) > 500:
        input_summary = input_summary[:497] + "..."
    return G7TranscriptTurn(
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
