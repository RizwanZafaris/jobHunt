"""
interview_agents/g3_state.py — Shared state for the G3 Interview Prep graph.

A single InterviewPrepState TypedDict flows through every node. Each node
reads the fields it needs and returns a dict patch. LangGraph merges
patches into the running state.

Reducer notes (mirror G2's pattern):
  - `transcript`        uses operator.add so multiple nodes can append turns
                        without conflicting (every node MUST return its turn
                        wrapped in a single-element list).
  - `cost_usd_total`    uses operator.add for cumulative cost across nodes.
  - `latency_ms_total`  same.
  - All other fields are last-write-wins (the default).

See docs/G3_INTERVIEW_PREP_GRAPH.md §2 for the design context.
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


class TranscriptTurn(TypedDict, total=False):
    """One row appended to InterviewPrepState.transcript per node invocation."""
    node: str                       # "behavioral_predictor", "merge_questions", ...
    iteration: int                  # mock-loop iteration number (0 for non-loop nodes)
    provider: str                   # "anthropic" | "google" | "deepseek" | "moonshot" | "openai"
    model: str
    input_summary: str              # truncated user prompt or input description
    output: Any                     # text or parsed JSON dict
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class InterviewPrepState(TypedDict, total=False):
    # ─── Inputs (set at entry) ──────────────────────────────────────────
    application_id: str             # uuid, public.applications.id
    application: dict               # full row from public.applications
    job_id: int                     # public.jobs.id (INTEGER)
    job: dict                       # full row from public.jobs
    company_name: str               # canonicalised
    round_type: str                 # recruiter | hm | panel | exec | technical | take_home
    round_number: int               # 1-based interview round
    company_persona: dict | None    # row from public.company_personas (None on cold start)
    story_bank: list[dict]          # rows from public.story_bank for this candidate
    last_resume_build: dict | None  # most recent resume_builds row for this job (or None)
    interview_history: list[dict]   # prior interview_outcomes rows for this application
    interview_prep_id: str          # uuid, matches public.interview_prep.id

    # ─── Predictor outputs (parallel branch) ────────────────────────────
    behavioral_questions: list[dict]   # [{question, competency, importance}]
    technical_questions: list[dict]    # [{question, competency, importance}]
    domain_questions: list[dict]       # [{question, competency, importance}]
    # 2026-05-12 (G3-3): non-empty if a predictor's JSON parse failed. Each
    # entry is a short tag like "behavioral_predictor:json_parse_failed".
    # Surfaced in the compiled prep pack so partial preps (13/20 questions
    # instead of 20) are visible to the user, not silent.
    predictor_warnings: Annotated[list[str], add]

    # ─── Merged + matched ───────────────────────────────────────────────
    likely_questions: list[dict]       # union(3 predictors), deduped, sorted, capped at 20
    star_stories: list[dict]           # [{question, story_id|None, match_quality, needs_rizwan_input}]

    # ─── Tier 2 G3 — story_bank integration (Phase 2 §4.2) ──────────────
    # 2026-05-12: story_retriever_node + gap_analyzer_node consume the G9
    # story_bank via search_story_bank / agents.story_bank_agent.search_stories.
    # Keyed by question index (string of int) so the prep pack template can
    # zip them with likely_questions.
    retrieved_stories: dict             # {q_idx: {story: StoryBankRow.asdict(), similarity: float, source_text, source_experience_id}}
    # ─── Tier 4 G3 — proof_points sidecar (roadmap §6.4) ───────────────
    # 2026-05-12: story_retriever_node also pulls top-3 proof points per
    # question via agents.proof_point_agent.search_proof_points. The
    # prep_pack renderer surfaces them under each question alongside
    # stories so the candidate has a crisp "you can also mention X
    # (link)" cue at-a-glance.
    retrieved_proof_points: dict        # {q_idx: list[ProofPointMatch.asdict()]}
    # Per-question gap suggestions for low-similarity (< 0.5) hits.
    story_gaps: list[dict]              # [{question, missing_competency, reframe_suggestion}]
    # Identity-lock drops: competencies fabricated by gap_analyzer that
    # aren't in profile_master.core_competencies. Surfaced in transcript.
    persona_critic_drops: list[str]
    # Stories that were credited on the most recent outcome log (used by
    # interview_studio post_log_outcome).
    credited_story_ids: list[str]

    # ─── Mock loop ──────────────────────────────────────────────────────
    mock_target_question: dict | None  # the one question we're rehearsing
    mock_answer_md: str                # current strong-answer template
    mock_critic_score: int             # 0-100
    mock_critic_feedback: list[str]
    mock_iteration: int                # 0..g3_max_iterations

    # ─── Final pack outputs ─────────────────────────────────────────────
    company_hooks: list[str]           # extracted from persona — "we just shipped X"
    red_flag_questions: list[str]      # tough-but-fair gotchas to expect
    salary_negotiation_notes: str
    prep_pack_md: str                  # final rendered markdown
    prep_pack_url: str                 # Supabase Storage URL after upload

    # ─── Audit / control ────────────────────────────────────────────────
    transcript: Annotated[list[TranscriptTurn], add]   # append-only
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    iteration: int                                      # alias for mock_iteration after loop
    converged: bool                                     # mock_critic_score >= target
    error: Optional[str]                                # set if a node fails fatally

    # Phase 2: cost cap control (mirrors G2 Phase 1.11)
    cost_cap_usd: float                                 # per-prep hard cap (USD)
    cost_capped: bool                                   # True when cumulative cost forced
                                                        # mock loop to terminate. export_node
                                                        # uses this to mark
                                                        # interview_prep.status='cost_capped'


# ─── Helpers ──────────────────────────────────────────────────────────────
def make_turn(
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
) -> TranscriptTurn:
    """Convenience constructor so nodes don't accidentally drop fields."""
    return TranscriptTurn(
        node=node,
        iteration=iteration,
        provider=provider,
        model=model,
        input_summary=input_summary[:500] if isinstance(input_summary, str) else "",
        output=output,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        error=error,
    )


def truncate(text: str, max_chars: int = 500) -> str:
    """Used for input_summary in transcripts so the audit log stays small."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
