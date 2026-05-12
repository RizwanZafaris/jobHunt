"""
agents/g11_state.py — Shared LangGraph state for the G11 Voice Calibration Graph.

A single VoiceCalibrationState TypedDict flows through every node.

Reducer notes (mirror G9 / G6 patterns):
  - `transcript`        uses operator.add  (append-only audit log)
  - `cost_usd_total`    uses operator.add
  - `latency_ms_total`  uses operator.add
  - All other fields are last-write-wins.

Lifecycle (per roadmap §6.3):
    run_g11(user_id)
        ↓
    style_extractor    — Sonnet 4.6, prompt-cached system. Reads
                         writing_samples for the user (fresh + previously-
                         used), returns structured style_attributes dict.
        ↓
    voice_profiler     — Sonnet 4.6, synthesises a single injectable
                         PROMPT BLOCK (~200-400 tokens) string.
        ↓
    persist            — Pure code. Writes profile_master.voice_calibration
                         and flips writing_samples.used_in_calibration=TRUE
                         for the samples consumed. Wires identity-lock:
                         source_sample_ids must be non-empty.
        ↓
    END

Cost model (one-time per upload):
    style_extractor    ~$0.05  (Sonnet, ~6k in / 1.5k out, cached system)
    voice_profiler     ~$0.03  (Sonnet, ~2k in / 0.5k out, cached system)
    persist             $0.00  (pure code)
    ────────────────────────────────
    total              ~$0.08  (well under the ~$0.10 target)
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Optional, TypedDict


class VoiceCalibrationTranscriptTurn(TypedDict, total=False):
    """One row appended to VoiceCalibrationState.transcript per node invocation."""
    node: str
    iteration: int
    provider: str
    model: str
    input_summary: str
    output: Any
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class WritingSample(TypedDict, total=False):
    """One row from writing_samples, surfaced into state for the LLM."""
    id: str
    user_id: str
    title: str
    body: str
    kind: str
    source: Optional[str]
    used_in_calibration: bool


class StyleAttributes(TypedDict, total=False):
    """Output of the style_extractor node. Mirrors the persisted
    voice_calibration JSONB shape minus voice_profile_block (which is
    set by voice_profiler) and the persistence-side fields (extracted_at,
    source_sample_ids).
    """
    tone: str                               # "conversational-confident, no hedging"
    sentence_length_avg: float              # e.g. 12.4
    sentence_length_distribution: dict      # {"short": 12, "medium": 8, "long": 2}
    openings_preferred: list[str]           # ["Led", "Built", "Cut"]
    punctuation_habits: list[str]           # ["em-dashes for asides", "Oxford commas"]
    vocabulary_preferred: list[str]         # ["built", "ran", "cut"]
    vocabulary_avoid: list[str]             # ["developed", "leveraged", "synergies"]
    notes: str                              # free-text observations (≤400 chars)


class VoiceCalibrationState(TypedDict, total=False):
    # ─── Inputs (hydrated by run_g11) ───────────────────────────────────
    user_id: str
    samples: list[WritingSample]            # writing_samples rows
    sample_ids: list[str]                   # ids of samples we'll consume

    # ─── style_extractor outputs ────────────────────────────────────────
    style_attributes: StyleAttributes

    # ─── voice_profiler outputs ─────────────────────────────────────────
    voice_profile_block: str                # the injectable prompt fragment

    # ─── persist outputs ────────────────────────────────────────────────
    persisted: bool
    voice_calibration_json: dict            # the exact JSONB written
    samples_marked_used: int                # count of writing_samples updated

    # ─── Telemetry ──────────────────────────────────────────────────────
    transcript: Annotated[list[VoiceCalibrationTranscriptTurn], add]
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    error: Optional[str]


def make_g11_turn(
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
) -> VoiceCalibrationTranscriptTurn:
    """Convenience constructor; matches make_turn in resume_agents.g2_state."""
    if isinstance(input_summary, str) and len(input_summary) > 500:
        input_summary = input_summary[:497] + "..."
    return VoiceCalibrationTranscriptTurn(
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
