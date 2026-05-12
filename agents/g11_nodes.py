"""
agents/g11_nodes.py — Three LangGraph node functions for G11 voice calibration.

Layout (matches G9's "one path, three nodes" shape — no critic loop):

    style_extractor   (Sonnet 4.6, prompt-cached system)
          ↓
    voice_profiler    (Sonnet 4.6, prompt-cached system)
          ↓
    persist           (pure code)
          ↓
        END

Cost model (one calibration run):
    style_extractor    ~$0.05  (Sonnet 4.6, ~6k in / 1.5k out)
    voice_profiler     ~$0.03  (Sonnet 4.6, ~2k in / 0.5k out)
    persist             $0.00  (pure code)
    ──────────────────────────────
    total              ~$0.08
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from agents.g11_state import (
    StyleAttributes,
    VoiceCalibrationState,
    WritingSample,
    make_g11_turn,
)
from agents.llm_router import _parse_json_loose, get_router

logger = logging.getLogger(__name__)


# Sonnet 4.6 is the workhorse — same model G6/G9 use. Cached system
# prompts auto-wrap when >1024 tokens (see llm_router._call_anthropic).
_SONNET = "claude-sonnet-4-6"

# Hard cap on samples per run. The LLM can lose signal above ~15 samples
# and we want the cost to stay bounded. If a user has more, the
# style_extractor picks the most recent + diverse via the SQL ordering
# in g11_io.load_writing_samples_for_calibration.
_MIN_SAMPLES = 1
_MAX_SAMPLES = 15

# Hard cap on individual sample body length sent to the LLM (chars). One
# 20K-word essay would otherwise dominate the prompt. We truncate at
# ~3000 chars per sample (≈600 tokens), with a clear "[…truncated]" tail.
_MAX_BODY_CHARS_PER_SAMPLE = 3000


# ══════════════════════════════════════════════════════════════════════════
# Node 1 — style_extractor (Sonnet 4.6, prompt-cached system)
# ══════════════════════════════════════════════════════════════════════════
STYLE_EXTRACTOR_SYSTEM = """You are a forensic writing-style analyst. You read
between 1 and 15 short pieces of writing produced by ONE person and extract
the structural fingerprint of their voice.

You will be given a list of samples (each titled, with a `kind` tag — e.g.
cover_letter, linkedin_post, email). Read them all, then output a single
JSON object with these fields.

────────────────────────────────────────────────────────────────────────
Output schema (STRICT JSON, no prose, no markdown fences):
────────────────────────────────────────────────────────────────────────
{
  "tone": "<one short phrase, e.g. 'conversational-confident, no hedging qualifiers'>",
  "sentence_length_avg": <number, e.g. 12.4>,
  "sentence_length_distribution": {
    "short":  <int, count of sentences ≤ 8 words>,
    "medium": <int, count of sentences 9-18 words>,
    "long":   <int, count of sentences ≥ 19 words>
  },
  "openings_preferred": ["<verb>", "<verb>", ...],
  "punctuation_habits": ["<observation>", "<observation>", ...],
  "vocabulary_preferred": ["<word>", "<word>", ...],
  "vocabulary_avoid":    ["<word>", "<word>", ...],
  "notes": "<≤400 chars of free-text observations that don't fit above>"
}

────────────────────────────────────────────────────────────────────────
RULES (the calibration breaks if you violate these):
────────────────────────────────────────────────────────────────────────
1. EVERY entry in vocabulary_preferred and openings_preferred MUST literally
   appear (case-insensitive) in at least one sample. If you cannot find
   a real word in the corpus, do NOT add an invented one. Better to
   return a SHORTER list than to fabricate.
2. vocabulary_avoid is the INVERSE — words that DO NOT appear in the
   samples but are AI/corporate clichés the user has clearly chosen not
   to use (e.g. "leveraged", "synergies", "passionate about"). These
   are pattern-detected from the absence.
3. sentence_length_avg is the arithmetic mean across all sentences in
   the corpus, to one decimal place. Count by whitespace-separated
   tokens (rough proxy is fine).
4. punctuation_habits should describe RECURRING patterns (e.g.
   "uses em-dashes for asides", "Oxford commas consistently", "no
   ellipses", "two-line paragraphs"). NOT a punctuation tally.
5. openings_preferred captures the verbs/structures at the START of
   sentences (especially in bulleted achievements). Cap at 8 entries.
6. Cap vocabulary_preferred at 12 entries. Cap vocabulary_avoid at 10.
   Cap punctuation_habits at 6 entries.
7. tone is ONE phrase, ≤80 chars. Pick the most distinctive trait.
8. Output ONLY the JSON object. No preamble, no commentary, no fences.
"""


async def style_extractor_node(state: VoiceCalibrationState) -> dict:
    """Analyse the user's writing samples and extract a structured style profile.

    Reads:   samples, user_id
    Writes:  style_attributes
    Cost:    ~$0.05 (Sonnet 4.6, prompt-cached system)
    """
    samples: list[WritingSample] = state.get("samples") or []
    if not samples:
        # Hard guard — voice_profiler can't run without something to profile.
        # The orchestrator (g11_io.run_g11) should never call us with an
        # empty list, but we belt-and-brace.
        return {
            "style_attributes": {},
            "transcript": [
                make_g11_turn(
                    node="style_extractor",
                    input_summary="no samples in state — skipping LLM call",
                    output={"reason": "empty_samples"},
                )
            ],
        }

    # Cap and serialize the samples block. We use a stable per-sample
    # delimiter so the LLM can clearly enumerate them in its analysis.
    blocks: list[str] = []
    for idx, s in enumerate(samples[:_MAX_SAMPLES], start=1):
        body = (s.get("body") or "").strip()
        if len(body) > _MAX_BODY_CHARS_PER_SAMPLE:
            body = body[: _MAX_BODY_CHARS_PER_SAMPLE - 18] + "\n[…truncated]"
        blocks.append(
            f"────── SAMPLE {idx} ──────\n"
            f"title: {(s.get('title') or '(untitled)')[:120]}\n"
            f"kind:  {s.get('kind') or 'other'}\n\n"
            f"{body}"
        )
    corpus = "\n\n".join(blocks)

    user = (
        f"Analyse the following {len(blocks)} writing samples from ONE "
        f"person and emit the structured JSON described in the system "
        f"prompt. Return ONLY the JSON object.\n\n"
        f"{corpus}\n\n"
        f"Now emit the JSON."
    )

    result = await get_router().ask(
        provider="anthropic",
        model=_SONNET,
        system=STYLE_EXTRACTOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=2000,
        temperature=0.2,
        agent_name="g11.style_extractor",
    )

    try:
        parsed = _parse_json_loose(result.text)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception as e:
        logger.warning(f"G11 style_extractor JSON parse failed: {e}")
        parsed = {}

    # Pure-code identity-lock: drop any vocabulary_preferred / openings
    # entries that don't actually appear in the corpus. The system prompt
    # forbids fabrication but Sonnet has been observed to slip a synonym
    # in occasionally; we enforce structurally.
    cleaned = _identity_lock_style(parsed, samples)

    return {
        "style_attributes": cleaned,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_g11_turn(
                node="style_extractor",
                provider=result.provider,
                model=result.model,
                input_summary=(
                    f"{len(samples)} samples, "
                    f"{sum(len((s.get('body') or '')) for s in samples)} total chars"
                ),
                output={
                    "tone": cleaned.get("tone"),
                    "sentence_length_avg": cleaned.get("sentence_length_avg"),
                    "vocab_pref_count": len(cleaned.get("vocabulary_preferred") or []),
                    "vocab_avoid_count": len(cleaned.get("vocabulary_avoid") or []),
                    "openings_count": len(cleaned.get("openings_preferred") or []),
                },
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


def _identity_lock_style(
    parsed: dict,
    samples: list[WritingSample],
) -> StyleAttributes:
    """Drop fabricated entries — vocabulary_preferred and openings_preferred
    items MUST appear (case-insensitive) somewhere in the corpus.

    This is the structural enforcement of engineering principle 3:
    'voice attributes can't reference content the samples don't contain.'
    """
    if not isinstance(parsed, dict):
        return StyleAttributes()  # type: ignore[return-value]

    corpus_lower = " ".join(
        (s.get("body") or "").lower() for s in samples
    )

    def _kept(words: Any) -> list[str]:
        if not isinstance(words, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for w in words:
            if not isinstance(w, str):
                continue
            w_clean = w.strip()
            if not w_clean:
                continue
            # case-insensitive substring match — token boundaries would
            # be safer but markdown bullets etc. make that brittle, and
            # the cap (12 / 8) bounds the search space already.
            if w_clean.lower() in corpus_lower and w_clean.lower() not in seen:
                out.append(w_clean)
                seen.add(w_clean.lower())
        return out

    def _strs(raw: Any, cap: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for x in raw:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
                if len(out) >= cap:
                    break
        return out

    cleaned: StyleAttributes = {
        "tone": str(parsed.get("tone") or "")[:120].strip(),
        "sentence_length_avg": float(parsed.get("sentence_length_avg") or 0.0),
        "sentence_length_distribution": parsed.get("sentence_length_distribution") or {},
        "openings_preferred": _kept(parsed.get("openings_preferred"))[:8],
        # punctuation_habits is a free-text description list — no source check
        "punctuation_habits": _strs(parsed.get("punctuation_habits"), 6),
        "vocabulary_preferred": _kept(parsed.get("vocabulary_preferred"))[:12],
        # vocabulary_avoid is by design things NOT in the corpus
        "vocabulary_avoid": _strs(parsed.get("vocabulary_avoid"), 10),
        "notes": str(parsed.get("notes") or "")[:400],
    }
    if not isinstance(cleaned["sentence_length_distribution"], dict):
        cleaned["sentence_length_distribution"] = {}
    return cleaned


# ══════════════════════════════════════════════════════════════════════════
# Node 2 — voice_profiler (Sonnet 4.6, prompt-cached system)
# ══════════════════════════════════════════════════════════════════════════
VOICE_PROFILER_SYSTEM = """You synthesise a structured style profile into a
SHORT injectable prompt fragment. This fragment will be prepended to every
downstream writer's user message so the generated output sounds like the
user — not a generic Sonnet response.

You will receive the structured style profile (tone, sentence length,
openings, vocabulary, punctuation) and you will produce ONE plain-text
block matching the template below. No JSON. No commentary. No fences.

────────────────────────────────────────────────────────────────────────
Template (use literally — keep the headings, fill in the values):
────────────────────────────────────────────────────────────────────────
WRITING STYLE PROFILE (write in THIS voice — it's the user's):
- Tone: <one phrase>
- Sentence length: <descriptor>, avg <N> words
- Openings: <action-first | hook-first | other>, examples: "<verb>", "<verb>", "<verb>"
- Punctuation: <habit>, <habit>
- Prefer: "<word>", "<word>", "<word>" (over "<corp-synonym>", "<corp-synonym>")
- Avoid: "<cliche>", "<cliche>", "<cliche>"
────────────────────────────────────────────────────────────────────────

Hard rules:
1. Output is ONLY the block above — start with "WRITING STYLE PROFILE", end
   with the "Avoid:" line. No leading or trailing prose.
2. Keep the entire block ≤ 800 chars. Writers prepend it to their user
   message and the tokens count against the writer's budget every call.
3. Fill in actual values from the style profile you receive. Never copy
   the placeholder angle-bracket text verbatim.
4. Use double-quotes around every literal word. This makes the writer
   model less likely to confuse the user's vocabulary with normal text.
5. The "Avoid:" line is critical — it tells the writer what NOT to do.
   Include the strongest 3-6 entries from vocabulary_avoid."""


async def voice_profiler_node(state: VoiceCalibrationState) -> dict:
    """Synthesise the structured style into a single injectable prompt block.

    Reads:   style_attributes
    Writes:  voice_profile_block
    Cost:    ~$0.03 (Sonnet 4.6, prompt-cached system)
    """
    style = state.get("style_attributes") or {}
    if not style:
        # No style → no profile. The persist node will see an empty block
        # and write voice_calibration={} so writers fall back to generic.
        return {
            "voice_profile_block": "",
            "transcript": [
                make_g11_turn(
                    node="voice_profiler",
                    input_summary="no style_attributes — skipping LLM call",
                    output={"reason": "empty_style"},
                )
            ],
        }

    user = (
        "Synthesise this structured style profile into the injectable prompt "
        "block described in the system prompt. Output ONLY the block — no "
        "JSON, no commentary, no fences.\n\n"
        "STRUCTURED STYLE PROFILE:\n"
        f"{json.dumps(style, indent=2)}\n\n"
        "Now write the injectable block."
    )

    result = await get_router().ask(
        provider="anthropic",
        model=_SONNET,
        system=VOICE_PROFILER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=600,
        temperature=0.3,
        agent_name="g11.voice_profiler",
    )

    block = (result.text or "").strip()
    # Defensive trim: if Sonnet added stray prose before the heading, snip
    # back to it so writers don't get junk prepended.
    heading_idx = block.find("WRITING STYLE PROFILE")
    if heading_idx > 0:
        block = block[heading_idx:].strip()
    # Cap the block at 1200 chars (safety margin over the 800-char target;
    # we accept a little overshoot but not unbounded output).
    if len(block) > 1200:
        block = block[:1200].rstrip() + "…"

    return {
        "voice_profile_block": block,
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
        "transcript": [
            make_g11_turn(
                node="voice_profiler",
                provider=result.provider,
                model=result.model,
                input_summary=(
                    f"style.tone={style.get('tone')!r} "
                    f"vocab_pref={len(style.get('vocabulary_preferred') or [])}"
                ),
                output={"block_chars": len(block)},
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# Node 3 — persist (pure code)
# ══════════════════════════════════════════════════════════════════════════
async def persist_node(state: VoiceCalibrationState) -> dict:
    """UPDATE profile_master.voice_calibration and mark writing_samples used.

    Reads:   user_id, samples, style_attributes, voice_profile_block,
             cost_usd_total
    Writes:  persisted, voice_calibration_json, samples_marked_used
    Cost:    $0.00 — Postgres writes only.

    Identity-lock: refuses to persist if source_sample_ids would be empty
    (i.e. no samples were actually consumed). Writers fall back to
    generic style when voice_calibration={}.
    """
    user_id = state.get("user_id")
    if not user_id:
        # Wallet guard — every writer in the system filters by user_id
        # and an empty user_id would write Rizwan's voice into the seed row.
        return {
            "persisted": False,
            "error": "missing_user_id",
            "transcript": [
                make_g11_turn(
                    node="persist",
                    input_summary="no user_id in state — refusing to persist",
                    output={"refused": True},
                    error="missing_user_id",
                )
            ],
        }

    samples = state.get("samples") or []
    style = state.get("style_attributes") or {}
    block = (state.get("voice_profile_block") or "").strip()
    sample_ids = [str(s.get("id")) for s in samples if s.get("id")]

    if not sample_ids:
        return {
            "persisted": False,
            "error": "no_sample_ids",
            "transcript": [
                make_g11_turn(
                    node="persist",
                    input_summary=f"user_id={user_id} samples=0",
                    output={"refused": True, "reason": "no_sample_ids"},
                    error="no_sample_ids",
                )
            ],
        }

    voice_json: dict[str, Any] = {
        "tone": style.get("tone") or "",
        "sentence_length_avg": style.get("sentence_length_avg") or 0.0,
        "sentence_length_distribution": style.get("sentence_length_distribution") or {},
        "openings_preferred": style.get("openings_preferred") or [],
        "punctuation_habits": style.get("punctuation_habits") or [],
        "vocabulary_preferred": style.get("vocabulary_preferred") or [],
        "vocabulary_avoid": style.get("vocabulary_avoid") or [],
        "voice_profile_block": block,
        "sample_count": len(sample_ids),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source_sample_ids": sample_ids,
    }

    # Delegate the actual writes to g11_io so the node stays pure.
    from agents.g11_io import (
        mark_samples_used,
        update_profile_voice_calibration,
    )

    try:
        update_profile_voice_calibration(user_id=user_id, voice_calibration=voice_json)
        marked = mark_samples_used(user_id=user_id, sample_ids=sample_ids)
    except Exception as e:
        logger.exception(f"G11 persist failed for user_id={user_id}")
        return {
            "persisted": False,
            "error": f"persist_failed:{type(e).__name__}:{str(e)[:200]}",
            "transcript": [
                make_g11_turn(
                    node="persist",
                    input_summary=f"user_id={user_id} samples={len(sample_ids)}",
                    output={"error": str(e)[:200]},
                    error=str(e)[:500],
                )
            ],
        }

    return {
        "persisted": True,
        "voice_calibration_json": voice_json,
        "samples_marked_used": marked,
        "transcript": [
            make_g11_turn(
                node="persist",
                input_summary=(
                    f"user_id={user_id} samples={len(sample_ids)} "
                    f"block_chars={len(block)}"
                ),
                output={
                    "persisted": True,
                    "samples_marked_used": marked,
                    "block_chars": len(block),
                },
            )
        ],
    }
