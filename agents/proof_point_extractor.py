"""
agents/proof_point_extractor.py — Tier 4 §6.4 auto-extraction of proof
points from profile_experience.

Behaviour
---------
1. Read every profile_experience row for the user (highlights + summary).
2. Candidate filter: only highlights / summary sentences that contain a
   quantified signal — $X / N% / N× / Nk / Nm / "from … to …" / etc.
   The vast majority of "soft" highlights (mentorship, leadership,
   adjectives) don't survive this filter — proof points are
   number-anchored.
3. For each candidate, ask Sonnet (g3_gap_analyzer_model — the same
   Sonnet alias the rest of the surface uses) to classify kind + emit
   tags + quantified_metrics. ONE LLM call per CV with all candidates
   batched into a single user message — cheap (~$0.05 typical).
4. Identity-lock: every quantified_metric must appear in the source
   text. Anything that doesn't gets pruned (same pattern as
   story_bank.quantified_metrics).
5. Insert via proof_point_agent.add_proof_point with source=
   'extracted_from_cv'. Idempotent via the (user_id, content_hash)
   unique partial index — re-runs collapse to no-ops.

Cost target: ~$0.05 one-time for a 20-experience CV with 50-100
highlights. Subsequent re-runs (after CV edits) re-extract only the
delta because the content_hash dedup short-circuits inserts.

Telemetry: writes one agent_call_log row with graph=NULL,
agent_name='proof_point.extractor' so the cost panel surfaces it
without bucketing into a graph.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Quantified-signal regex (cheap pre-filter) ────────────────────────────
# Any of these in a highlight → it's a proof-point candidate.
_QUANT_PATTERNS = [
    r"\$\s?\d",                  # $50M, $1.2bn
    r"\d+\s?%",                  # 18%, 200%
    r"\d+\s?[x×]",               # 3x, 10×
    r"\d+\s?[kKmMbB](?!\w)",     # 12k, 50M
    r"\d{1,3}(,\d{3})+",         # 12,000
    r"\b\d+\s?(months?|weeks?|days?|years?)\b",  # 18 months
    r"\b(grew|reduced|increased|cut|raised|launched|scaled|drove|generated)\s+[^.]*\d",  # action verbs + numbers
]
_QUANT_RE = re.compile("|".join(_QUANT_PATTERNS), re.IGNORECASE)


def _split_into_candidates(experience_row: dict) -> list[str]:
    """Yield candidate proof statements from one profile_experience row.

    Sources:
      - each highlights[i] verbatim
      - each sentence of summary (split on .?! followed by space/end)
    Filter:
      - dropped if no quantified signal matches
      - dropped if very short (< 20 chars) or huge (> 600 chars)
    """
    out: list[str] = []
    highlights = experience_row.get("highlights") or []
    for h in highlights:
        s = (h or "").strip()
        if 20 <= len(s) <= 600 and _QUANT_RE.search(s):
            out.append(s)
    summary = (experience_row.get("summary") or "").strip()
    if summary:
        for sent in re.split(r"(?<=[.!?])\s+", summary):
            sent = sent.strip()
            if 20 <= len(sent) <= 600 and _QUANT_RE.search(sent):
                out.append(sent)
    return out


# ─── LLM prompt ────────────────────────────────────────────────────────────
EXTRACTOR_SYSTEM = """You are a proof-point classifier for a career-ops AI.

You receive a JSON array of CANDIDATE proof statements pulled from a
candidate's CV. Each candidate is a short sentence with at least one
quantified metric ($X, N%, N×, etc.).

For EACH candidate, output:
  - kind: one of [launch, metric, thought_leadership, milestone, press, other]
      * launch         — shipped/launched a product or feature
      * metric         — achieved a measurable business outcome
      * thought_leadership — wrote/spoke publicly (NOT typical for CV bullets)
      * milestone      — career or org milestone (promo, raise, market entry)
      * press          — public press / award / external recognition
      * other          — none of the above
  - tags: 2-5 lowercase short tags ([fintech, product-launch, pricing])
  - quantified_metrics: every concrete number, currency, percentage, multiplier,
    or time-period in the candidate text. Verbatim from the source. Each entry
    a self-contained string ("$50M TPV", "18 months", "200% growth").

Hard rules:
  - Do NOT add metrics that don't appear in the source text (identity lock).
  - Do NOT invent context. Tags should describe the candidate's domain
    based on words present.
  - Do NOT classify generic responsibility statements as "thought_leadership"
    unless the source explicitly references publishing/speaking.
  - If a candidate is too generic to classify usefully, return kind='other'
    and empty tags.

Output STRICT JSON only — no preamble, no commentary, no markdown fences:
{
  "items": [
    {"idx": 0, "kind": "launch", "tags": ["fintech","product-launch"],
     "quantified_metrics": ["$50M TPV","18 months"]},
    ...
  ]
}
"""


# ─── Public entry point ────────────────────────────────────────────────────
async def extract_proof_points_from_experience(
    user_id: UUID | str,
    *,
    experiences: Optional[list[dict]] = None,
) -> int:
    """Run the extractor once. Returns the count of NEW proof points added.

    Idempotent — re-runs against unchanged experience rows produce zero new
    rows (the (user_id, content_hash) unique partial index collapses
    duplicates).

    `experiences` override is for testing; production callers pass None
    and we load from profile_experience.
    """
    from agents.g9_io import load_experiences

    rows = experiences if experiences is not None else load_experiences()
    if not rows:
        logger.info("proof_point_extractor: no experience rows")
        return 0

    # Build the flat candidate list with provenance (which experience row
    # produced which candidate text).
    candidates: list[tuple[int, dict]] = []
    for exp in rows:
        exp_id = exp.get("id")
        for text in _split_into_candidates(exp):
            candidates.append((exp_id, {"text": text}))
    if not candidates:
        logger.info("proof_point_extractor: zero quantified candidates found")
        return 0

    # ── LLM call ───────────────────────────────────────────────────────
    from agents.llm_router import _parse_json_loose, get_router
    from config.settings import get_settings

    settings = get_settings()
    user_msg = json.dumps(
        [{"idx": i, "text": c[1]["text"]} for i, c in enumerate(candidates)],
        indent=2,
    )

    try:
        result = await get_router().ask(
            provider="anthropic",
            model=settings.g3_gap_analyzer_model,
            system=EXTRACTOR_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=4000,
            temperature=0.15,
            agent_name="proof_point.extractor",
            # graph=NULL — extractor isn't a graph, it's a one-shot.
            cache_system=True,
        )
        classifications = _parse_json_loose(result.text)
        items = classifications.get("items", []) if isinstance(classifications, dict) else []
    except Exception as e:
        logger.warning(f"proof_point_extractor: LLM call failed: {e}")
        return 0

    # ── Identity-lock + insert ─────────────────────────────────────────
    from agents.proof_point_agent import (
        KIND_VALUES,
        _content_hash,
        add_proof_point,
    )

    inserted = 0
    by_idx: dict[int, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = it.get("idx")
        if isinstance(idx, int):
            by_idx[idx] = it

    for i, (exp_id, cand) in enumerate(candidates):
        cls = by_idx.get(i)
        if not cls:
            continue
        kind = (cls.get("kind") or "other").strip().lower()
        if kind not in KIND_VALUES:
            kind = "other"
        raw_tags = cls.get("tags") or []
        tags = [str(t).strip().lower() for t in raw_tags if t][:8]
        raw_metrics = cls.get("quantified_metrics") or []
        # Identity-lock: only keep metrics that actually appear in the
        # source text (case-insensitive substring). Same pattern as
        # story_bank.quantified_metrics.
        src_lower = cand["text"].lower()
        metrics = [
            str(m).strip()
            for m in raw_metrics
            if m and str(m).strip().lower() in src_lower
        ][:10]

        ch = _content_hash(cand["text"])
        try:
            new_id = await add_proof_point(
                user_id=user_id,
                content=cand["text"],
                kind=kind,
                tags=tags,
                quantified_metrics=metrics,
                source="extracted_from_cv",
                source_experience_id=exp_id,
                content_hash=ch,
            )
            if new_id is not None:
                inserted += 1
        except Exception as e:
            logger.warning(f"proof_point_extractor: insert failed for idx={i}: {e}")

    logger.info(
        "proof_point_extractor: candidates=%d classified=%d upserted=%d",
        len(candidates),
        len(by_idx),
        inserted,
    )
    return inserted
