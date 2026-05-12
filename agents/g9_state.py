"""
agents/g9_state.py — Shared state for the G9 STAR+R Story Extractor graph.

A single StoryExtractionState TypedDict flows through every node. Each node
reads the fields it needs and returns a dict patch. LangGraph merges
patches into the running state.

Reducer notes (mirror G2 / G3 patterns):
  - `transcript`        uses operator.add so multiple nodes can append turns
                        without conflicting (every node MUST return its turn
                        wrapped in a single-element list).
  - `total_cost_usd`    uses operator.add for cumulative cost across nodes.
  - `total_latency_ms`  same.
  - `persona_warnings`  uses operator.add (persona_critic can append per-story
                        warnings without overwriting parser/formatter output).
  - All other fields are last-write-wins (the default).

Design rationale (docs/GAP_CLOSURE_ROADMAP_2026_05_12.md §3.2)
-------------------------------------------------------------
G9 runs ONCE per master-CV update and emits 10-15 STAR+R stories. The graph
shape is intentionally simple — four nodes, no critic loop:

    entry → experience_parser → star_formatter → persona_critic → persist → END

The persona_critic is the "persona-as-critic" pass (engineering principle 1
in §11): it cross-references generated stories against
profile_master.core_competencies and drops fabrications. It's a sibling to
star_formatter in spirit but runs after, because it needs the structured
STAR JSON to critique. No iterative loop — a fabrication-flagged story is
silently dropped, not regenerated. This keeps cost bounded (~$0.20/run).
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


class TranscriptTurn(TypedDict, total=False):
    """One row appended to StoryExtractionState.transcript per node invocation."""
    node: str                       # "experience_parser", "star_formatter", ...
    iteration: int                  # 0 for non-loop nodes
    provider: str                   # "anthropic" | "openai" | ""
    model: str
    input_summary: str              # truncated user prompt or input description
    output: Any                     # text or parsed JSON dict
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class CandidatePassage(TypedDict, total=False):
    """One achievement candidate emitted by experience_parser_node."""
    passage_id: str                 # local id within this run (e.g. "exp-1-h-2")
    source_experience_id: Optional[int]  # FK to profile_experience.id, when known
    role_title: str                 # "CPO", "Head of Product", etc.
    company: str
    dates: str                      # "2020 - Present", etc.
    source_text: str                # the exact CV passage this was lifted from
    rough_title: str                # parser's first-pass guess at a story title


class StoryJSON(TypedDict, total=False):
    """One STAR+R story emitted by star_formatter_node."""
    passage_id: str                 # back-pointer to the candidate passage
    title: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    competencies: list[str]         # tags
    archetypes: list[str]           # role archetypes
    quantified_metrics: list[str]   # MUST appear in source_text after lowercase
    source_text: str                # carried forward for identity-lock + audit


class StoryExtractionState(TypedDict, total=False):
    # ─── Inputs (set by run_g9) ─────────────────────────────────────────
    user_id: str                    # uuid of the candidate
    master_cv_md: str               # rendered master profile markdown
    source_cv_hash: str             # sha256 of master_cv_md
    profile_competencies: list[str] # profile_master.core_competencies (for critic)

    # ─── Node outputs ───────────────────────────────────────────────────
    candidate_passages: list[CandidatePassage]    # experience_parser
    stories: list[StoryJSON]                       # star_formatter
    persona_warnings: Annotated[list[str], add]    # persona_critic per-story
    stories_persisted: list[dict]                  # persist_node — {id, title}
    stories_dropped: list[dict]                    # persist_node — {title, reason}

    # ─── Final outputs ──────────────────────────────────────────────────
    embeddings_count: int                          # how many embeddings issued
    persisted_count: int
    dropped_count: int

    # ─── Audit / control ────────────────────────────────────────────────
    transcript: Annotated[list[TranscriptTurn], add]    # append-only
    total_cost_usd: Annotated[float, add]
    total_latency_ms: Annotated[int, add]
    error: Optional[str]


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
