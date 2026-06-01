"""
agents/proof_point_agent.py — Tier 4 (§6.4) Proof Point Agent.

The proof_points table holds short, tag-able facts (launches, metrics,
posts, milestones) sourced from articles / links / personal milestones.
They're shorter than STAR stories (which live in story_bank) and meant
to be pulled by topical match in:

    G3 Interview Prep   — surface alongside STAR stories per question
    G7 Cover Letter     — drop into "why I'm a fit" sentences
    outcome_to_persona  — credit a proof_point when an outcome cites it

Design choices mirror story_bank_agent so callers don't have to learn a
new retrieval idiom:

  1. Pure async functions, not a class. Lighter, easier to mock in tests.
  2. Embedding done HERE on insert/update, hidden behind the agent so
     future model swaps don't ripple to call sites.
  3. RLS-friendly: every call passes user_id explicitly.
  4. search_proof_points uses the SECURITY DEFINER RPC
     `search_proof_points_v2` (migration 024) for cosine ordering at the
     DB layer. On RPC failure or cold-start (no embedded rows yet) it
     falls back to chronological list — same posture as
     story_bank_agent.search_stories.

cite:knowledge_id breadcrumbs
-----------------------------
Every proof point carries `source` (`user_manual_add` |
`extracted_from_cv` | `g4_linkedin_post`) plus the source row id
(`source_experience_id` for CV-extracted, `source_draft_id` for LinkedIn
posts). When outcome_to_persona credits a successful outcome, the
attribution path walks back to (a) the proof point id, (b) the
originating profile_experience or linkedin_drafts row — keeping the
end-to-end audit trail intact.

Identity-lock guarantee
-----------------------
Auto-extracted proof points (source != 'user_manual_add') MUST trace
back to a real profile_experience row or linkedin_draft. The extractor
verifies each quantified metric appears in the source text before
inserting — fabricated metrics get pruned, same pattern as
story_bank.quantified_metrics identity-lock.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Dataclasses ───────────────────────────────────────────────────────────


KIND_VALUES = ("launch", "metric", "thought_leadership", "milestone", "press", "other")
SOURCE_VALUES = ("user_manual_add", "extracted_from_cv", "g4_linkedin_post")


@dataclass
class ProofPointRow:
    """One persisted proof point."""
    id: str
    user_id: str
    content: str
    kind: str
    context: Optional[str] = None
    url: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    quantified_metrics: list[str] = field(default_factory=list)
    outcome_score: int = 0
    source: Optional[str] = None
    source_experience_id: Optional[int] = None
    source_draft_id: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ProofPointMatch:
    """A search hit — proof point body plus similarity score."""
    proof_point: ProofPointRow
    similarity: float


def _default_user_id() -> str:
    return os.environ.get(
        "RIZWAN_USER_ID",
        "00000000-0000-0000-0000-000000000001",
    )


def _row_to_proof_point(row: dict) -> ProofPointRow:
    """Map a Supabase row to ProofPointRow.

    Defensive about missing columns (legacy / partial rows). Anything
    that can't coerce silently defaults.
    """
    sdid = row.get("source_draft_id")
    return ProofPointRow(
        id=str(row.get("id") or ""),
        user_id=str(row.get("user_id") or ""),
        content=str(row.get("content") or ""),
        kind=str(row.get("kind") or "other"),
        context=row.get("context"),
        url=row.get("url"),
        tags=list(row.get("tags") or []),
        quantified_metrics=list(row.get("quantified_metrics") or []),
        outcome_score=int(row.get("outcome_score") or 0),
        source=row.get("source"),
        source_experience_id=row.get("source_experience_id"),
        source_draft_id=str(sdid) if sdid else None,
        content_hash=row.get("content_hash"),
        created_at=str(row.get("created_at") or "") or None,
        updated_at=str(row.get("updated_at") or "") or None,
    )


def proof_point_to_dict(p: ProofPointRow) -> dict:
    """Stable JSON-ready serialization for API responses."""
    return asdict(p)


def match_to_dict(m: ProofPointMatch) -> dict:
    """Stable JSON-ready serialization for search responses."""
    return {
        "proof_point": proof_point_to_dict(m.proof_point),
        "similarity": float(m.similarity),
    }


def _normalise_content(content: str) -> str:
    """Canonicalise a content string for hash dedup.

    Lowercase + collapse whitespace. We intentionally don't strip
    punctuation — "$50M" and "50M" should stay distinct.
    """
    return " ".join((content or "").lower().split())


def _content_hash(content: str) -> str:
    """sha256 of normalised content — used by the extractor for dedup."""
    return hashlib.sha256(_normalise_content(content).encode("utf-8")).hexdigest()


def _embed_payload(
    content: str,
    *,
    context: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Build the embedding input.

    Deterministic concatenation so re-embeds on edit are stable. Tags
    are flattened into the text body so queries like "fintech proof
    points about pricing" can match without tag-pre-filter.
    """
    parts = [content or ""]
    if context:
        parts.append(context)
    if tags:
        parts.append(" ".join(tags))
    return "\n".join(p for p in parts if p).strip()


# ═════════════════════════════════════════════════════════════════════════
# CRUD
# ═════════════════════════════════════════════════════════════════════════
async def list_proof_points(
    user_id: UUID | str,
    *,
    kind: Optional[str] = None,
    limit: int = 50,
) -> list[ProofPointRow]:
    """List a user's proof points, ordered by outcome_score DESC then created_at DESC.

    The most-credit-attributed proof points surface first. Falls back
    to chronological when outcome_score is 0 across the board (cold
    start). We never return embeddings (large vectors waste payload).
    """
    from db.client import get_supabase
    q = (
        get_supabase()
        .table("proof_points")
        .select(
            "id, user_id, content, context, url, kind, tags, quantified_metrics, "
            "outcome_score, source, source_experience_id, source_draft_id, "
            "content_hash, created_at, updated_at"
        )
        .eq("user_id", str(user_id))
        .order("outcome_score", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if kind:
        q = q.eq("kind", kind)
    rows = (q.execute().data) or []
    return [_row_to_proof_point(r) for r in rows]


async def get_proof_point(
    user_id: UUID | str,
    pid: UUID | str,
) -> Optional[ProofPointRow]:
    """Fetch one proof point by id, scoped to user."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("proof_points")
        .select(
            "id, user_id, content, context, url, kind, tags, quantified_metrics, "
            "outcome_score, source, source_experience_id, source_draft_id, "
            "content_hash, created_at, updated_at"
        )
        .eq("id", str(pid))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    ) or []
    return _row_to_proof_point(rows[0]) if rows else None


async def add_proof_point(
    user_id: UUID | str,
    content: str,
    *,
    kind: str,
    context: Optional[str] = None,
    url: Optional[str] = None,
    tags: Optional[list[str]] = None,
    quantified_metrics: Optional[list[str]] = None,
    source: str = "user_manual_add",
    source_experience_id: Optional[int] = None,
    source_draft_id: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> Optional[UUID]:
    """Insert a new proof point. Returns the new id (UUID).

    Embedding is computed here from content + context + tags. We let
    embedding errors fall through (best-effort) — the row still inserts
    with NULL embedding and just won't surface in vector search until
    it's re-embedded by an edit.

    The unique partial index on (user_id, content_hash) means callers
    can pass a pre-computed hash for extractor dedup. If the row would
    collide, the upsert path returns the existing id (no error).
    """
    if kind not in KIND_VALUES:
        raise ValueError(f"invalid kind {kind!r} (must be one of {KIND_VALUES})")
    if source not in SOURCE_VALUES:
        raise ValueError(f"invalid source {source!r} (must be one of {SOURCE_VALUES})")
    if not (content or "").strip():
        raise ValueError("content cannot be empty")

    from db.client import embed, get_supabase

    tags = tags or []
    quantified_metrics = quantified_metrics or []
    ch = content_hash or _content_hash(content)

    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "content": content.strip(),
        "context": (context or None),
        "url": (url or None),
        "kind": kind,
        "tags": tags,
        "quantified_metrics": quantified_metrics,
        "source": source,
        "source_experience_id": source_experience_id,
        "source_draft_id": str(source_draft_id) if source_draft_id else None,
        "content_hash": ch,
    }

    # Best-effort embedding. Failure → NULL embedding (still indexable).
    try:
        payload["embedding"] = await embed(
            _embed_payload(content, context=context, tags=tags)
        )
    except Exception as e:
        logger.warning(f"add_proof_point: embed failed for content={content[:60]!r}: {e}")

    db = get_supabase()
    # Use upsert on (user_id, content_hash) so the extractor is idempotent.
    rows = (
        db.table("proof_points")
        .upsert(payload, on_conflict="user_id,content_hash")
        .execute()
        .data
    ) or []
    if not rows:
        return None
    return UUID(rows[0]["id"]) if rows[0].get("id") else None


async def update_proof_point(
    user_id: UUID | str,
    pid: UUID | str,
    patch: dict[str, Any],
) -> Optional[ProofPointRow]:
    """Patch a proof point. Whitelisted mutable fields only.

    Allowed: content, context, url, kind, tags, quantified_metrics.
    Off-limits: user_id, source, source_*, outcome_score, content_hash
    (the latter is recomputed if content changes).

    Re-embeds when content / context / tags change.
    """
    from db.client import embed, get_supabase

    allowed = {"content", "context", "url", "kind", "tags", "quantified_metrics"}
    safe_patch = {k: v for k, v in (patch or {}).items() if k in allowed}
    if not safe_patch:
        return await get_proof_point(user_id, pid)

    if "kind" in safe_patch and safe_patch["kind"] not in KIND_VALUES:
        raise ValueError(f"invalid kind {safe_patch['kind']!r}")

    # Re-embed if the embedding input changed.
    if any(k in safe_patch for k in ("content", "context", "tags")):
        existing = await get_proof_point(user_id, pid)
        if existing is None:
            return None
        merged = {**proof_point_to_dict(existing), **safe_patch}
        try:
            safe_patch["embedding"] = await embed(
                _embed_payload(
                    merged.get("content") or "",
                    context=merged.get("context"),
                    tags=merged.get("tags") or [],
                )
            )
        except Exception as e:
            logger.warning(f"update_proof_point: re-embed failed for {pid}: {e}")
        # If content itself changed, recompute the dedup hash.
        if "content" in safe_patch:
            safe_patch["content_hash"] = _content_hash(safe_patch["content"])

    rows = (
        get_supabase()
        .table("proof_points")
        .update(safe_patch)
        .eq("id", str(pid))
        .eq("user_id", str(user_id))
        .execute()
        .data
    ) or []
    return _row_to_proof_point(rows[0]) if rows else None


async def delete_proof_point(user_id: UUID | str, pid: UUID | str) -> bool:
    """Delete a proof point. Returns True if a row was deleted."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("proof_points")
        .delete()
        .eq("id", str(pid))
        .eq("user_id", str(user_id))
        .execute()
        .data
    ) or []
    return bool(rows)


# ═════════════════════════════════════════════════════════════════════════
# Outcome attribution
# ═════════════════════════════════════════════════════════════════════════
async def update_outcome_score(pid: UUID | str, delta: int) -> None:
    """Bump outcome_score by `delta`. Clamped at >= 0.

    Called by agents/outcome_to_persona.credit_outcome when a G3 prep
    pack or G7 cover letter that cited THIS proof point produces a
    callback / offer.
    """
    from db.client import get_supabase
    db = get_supabase()
    rows = (
        db.table("proof_points")
        .select("outcome_score")
        .eq("id", str(pid))
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        logger.warning(f"update_outcome_score: proof_point {pid} not found")
        return
    current = int(rows[0].get("outcome_score") or 0)
    new_score = max(0, current + int(delta))
    db.table("proof_points").update({"outcome_score": new_score}).eq("id", str(pid)).execute()


# ═════════════════════════════════════════════════════════════════════════
# Semantic search
# ═════════════════════════════════════════════════════════════════════════
async def search_proof_points(
    user_id: UUID | str,
    query: str,
    *,
    k: int = 5,
    kind: Optional[str | list[str]] = None,
    tags: Optional[list[str]] = None,
) -> list[ProofPointMatch]:
    """Top-k semantic matches for `query`.

    Tries the SECURITY DEFINER RPC `search_proof_points_v2` first
    (cosine ordering in the DB). Falls back to client-side cosine over
    a SELECT pull when the RPC fails (e.g. migration not yet applied
    against a fork). Final fallback is a chronological list when no
    rows have embeddings (cold start).

    `kind` accepts either a single string ('launch') or a list
    (['launch','press']) — the RPC takes TEXT[].

    `tags` is an OR-set pre-filter (GIN-indexed `&&` overlap).

    Cost: one embedding call per query (~$0.00002). No LLM call.
    """
    if not query or not query.strip():
        return []

    from db.client import embed, get_supabase

    db = get_supabase()
    try:
        query_embedding = await embed(query)
    except Exception as e:
        logger.warning(f"search_proof_points: embed failed: {e} — falling back")
        rows = await list_proof_points(
            user_id,
            kind=kind if isinstance(kind, str) else None,
            limit=k,
        )
        return [ProofPointMatch(proof_point=r, similarity=0.0) for r in rows]

    kind_filter: Optional[list[str]] = None
    if isinstance(kind, str):
        kind_filter = [kind]
    elif isinstance(kind, list):
        kind_filter = kind or None

    # Try the RPC.
    try:
        rpc_args = {
            "p_user_id": str(user_id),
            "p_query_embedding": query_embedding,
            "p_k": k,
            "p_kind_filter": kind_filter,
            "p_tag_filter": tags,
        }
        result = db.rpc("search_proof_points_v2", rpc_args).execute()
        rows = result.data or []
        if rows:
            return [
                ProofPointMatch(
                    proof_point=_row_to_proof_point(r),
                    similarity=float(r.get("similarity") or 0.0),
                )
                for r in rows
            ]
        # Empty result from RPC — could be (a) no matching rows or
        # (b) cold-start (no embedded rows yet). Distinguish via the
        # fallback path below.
    except Exception as e:
        logger.warning(f"search_proof_points: RPC v2 failed: {e} — client-side fallback")

    # Client-side cosine fallback.
    q = (
        db.table("proof_points")
        .select(
            "id, user_id, content, context, url, kind, tags, quantified_metrics, "
            "outcome_score, source, source_experience_id, source_draft_id, "
            "content_hash, created_at, updated_at, embedding"
        )
        .eq("user_id", str(user_id))
        .not_.is_("embedding", "null")
    )
    if kind_filter:
        q = q.in_("kind", kind_filter)
    if tags:
        q = q.overlaps("tags", tags)
    rows = (q.execute().data) or []
    if not rows:
        cold = await list_proof_points(
            user_id,
            kind=kind if isinstance(kind, str) else None,
            limit=k,
        )
        return [ProofPointMatch(proof_point=r, similarity=0.0) for r in cold]

    matches: list[ProofPointMatch] = []
    for row in rows:
        emb = row.get("embedding")
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                continue
        if not isinstance(emb, list) or len(emb) != len(query_embedding):
            continue
        sim = _cosine(emb, query_embedding)
        pp = _row_to_proof_point(row)
        # Bayesian tiebreaker — same pattern as story_bank_agent.
        boost = min(0.05, 0.005 * pp.outcome_score)
        matches.append(ProofPointMatch(proof_point=pp, similarity=sim + boost))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-python cosine similarity. a · b / (|a| |b|).

    Inputs are already 1536-dim text-embedding-3-small vectors. OpenAI
    returns unit vectors so the dot product IS the cosine similarity
    (modulo float drift, fine for ranking).
    """
    if not a or not b:
        return 0.0
    dot = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
    return dot
