"""
resume_agents/g2_state.py — Shared state for the G2 Resume Builder graph.

A single ResumeState TypedDict flows through every node. Each node reads
the fields it needs and returns a dict patch. LangGraph merges patches
into the running state.

Two LangGraph reducer notes:
  - `transcript`     uses operator.add so multiple nodes can append turns
                     without conflicting (every node MUST return its turn
                     wrapped in a single-element list).
  - `cost_usd_total` uses operator.add for cumulative cost across nodes.
  - `latency_ms_total` same.
  - All other fields are last-write-wins (the default).

See docs/G2_RESUME_BUILDER_GRAPH.md §2 for the design context.
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


class TranscriptTurn(TypedDict, total=False):
    """One row appended to ResumeState.transcript per node invocation."""
    node: str                       # "insider_expert", "writer", "ats_critic_a", ...
    iteration: int                  # writer-critic loop number (0 for non-loop nodes)
    provider: str                   # "anthropic" | "google" | "deepseek" | "moonshot" | "openai"
    model: str
    input_summary: str              # truncated user prompt or input description
    output: Any                     # text or parsed JSON dict
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class ResumeState(TypedDict, total=False):
    # ─── Inputs (set at entry) ──────────────────────────────────────────
    job: dict                       # full row from public.jobs
    job_id: int                     # public.jobs.id is INTEGER
    company_name: str               # canonicalized
    company_persona: dict | None    # row from public.company_personas (None on cold start)
    master_resume_md: str           # rendered from profile_master + profile_experience +
                                    # profile_certification + profile_education
    past_transcripts: list[dict]    # last N resume_builds.agent_transcript for this company,
                                    # OR fallback rows from agent_conversations on cold start
    resume_build_id: str            # uuid, matches public.resume_builds.id

    # ─── Agent outputs (accumulated) ────────────────────────────────────
    expert_notes: str               # Insider Expert (Gemini 2.5 Pro + grounding)
    advocate_notes: str             # Advocate (Claude Opus 4.5)
    meta_critic_warnings: list[str] # Meta-Critic (Gemini 2.5 Pro)
    current_draft: str              # Writer (Claude Opus 4.5)
    critic_a: dict                  # ATS Critic A (DeepSeek-R1)
    critic_b: dict                  # ATS Critic B (Kimi K2)
    merged_critique: dict           # union(A, B), strictest score, deduped fixes

    # ─── Final output ───────────────────────────────────────────────────
    final_resume_md: str
    final_score: int
    final_breakdown: dict
    cover_email_md: str
    resume_docx_url: str
    resume_pdf_url: str

    # ─── Audit / control ────────────────────────────────────────────────
    transcript: Annotated[list[TranscriptTurn], add]   # append-only
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    iteration: int                                      # writer-critic loop counter
    converged: bool                                     # orchestrator decision
    error: Optional[str]                                # set if a node fails fatally

    # Phase 1.11: cost cap control
    cost_cap_usd: float                                 # per-build hard cap (USD)
    cost_capped: bool                                   # True if orchestrator forced
                                                        # converge because cost >= cap.
                                                        # export_node uses this to mark
                                                        # resume_builds.status='cost_capped'

    # Phase 2.1: workspace rebuild flow
    warm_start_md: Optional[str]                        # if set, the writer seeds from
                                                        # this markdown instead of
                                                        # generating from scratch. Used
                                                        # by rebuild-section / full-rebuild
                                                        # in the workspace editor.
    edit_intent: Optional[str]                          # human instruction that drove the
                                                        # rebuild ("make this more
                                                        # product-led", "tighten Daraz
                                                        # bullets"). The writer node
                                                        # surfaces this in its brief so
                                                        # the iteration is responsive to
                                                        # the user's actual intent.


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
