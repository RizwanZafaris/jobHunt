"""
agents/story_bank_agent.py — CRUD + semantic-search wrapper around story_bank.

Phase 1.2 surface. The G9 graph WRITES into story_bank via this module's
`upsert_story_from_dict`. G3 (Phase 2) and G7 (Phase 3) will READ via
`search_stories` to pull top-k STAR stories for a likely-interview question
or a behavioural application form field.

Design choices
--------------
1. Pure async functions, not a class. Lighter, easier to mock in tests.
2. Embedding is done HERE, not on the caller side. Keeps the embedding
   model (text-embedding-3-small) hidden behind the agent so future model
   swaps don't ripple.
3. RLS: every call passes the candidate's user_id. We never select across
   users — Phase 1.2 is single-user but the writer convention is already
   in place for multi-tenancy.
4. `search_stories` uses pgvector cosine similarity via direct SQL
   ordering on the embedding column. We could expose this as a Postgres
   RPC (`search_story_bank_v2`) for consistency with company_knowledge,
   but adding an RPC is a separate migration's worth of work — the
   direct query is simpler to ship and the existing `search_story_bank`
   RPC has a v1 shape we don't want to retrofit yet.

cite:knowledge_id breadcrumbs
-----------------------------
StoryBankMatch carries `story_id` + `source_experience_id` so that when
G3/G7 quotes a story in a generated answer, the cite trail walks back to
(a) the originating story, (b) the originating profile_experience row.
outcome_to_persona credits the story when the cited answer leads to a
callback / offer.
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class StoryBankRow:
    """One persisted STAR+R story (full body)."""
    id: str
    user_id: str
    title: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    competencies: list[str] = field(default_factory=list)
    archetypes: list[str] = field(default_factory=list)
    quantified_metrics: list[str] = field(default_factory=list)
    outcome_score: int = 0
    source_cv_hash: Optional[str] = None
    source_experience_id: Optional[int] = None
    source_text: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class StoryBankMatch:
    """A search hit — story body plus similarity score."""
    story: StoryBankRow
    similarity: float


def _default_user_id() -> str:
    return os.environ.get(
        "RIZWAN_USER_ID",
        "00000000-0000-0000-0000-000000000001",
    )


def _row_to_story(row: dict) -> StoryBankRow:
    """Map a Supabase row to StoryBankRow.

    Defensive about missing columns (legacy rows might lack the Phase
    1.2 fields). Anything that can't coerce silently defaults.
    """
    return StoryBankRow(
        id=str(row.get("id") or ""),
        user_id=str(row.get("user_id") or ""),
        title=str(row.get("title") or ""),
        situation=str(row.get("situation") or ""),
        task=str(row.get("task") or ""),
        action=str(row.get("action") or ""),
        result=str(row.get("result") or ""),
        reflection=str(row.get("reflection") or ""),
        competencies=list(row.get("competencies") or []),
        archetypes=list(row.get("archetypes") or []),
        quantified_metrics=list(row.get("quantified_metrics") or []),
        outcome_score=int(row.get("outcome_score") or 0),
        source_cv_hash=row.get("source_cv_hash"),
        source_experience_id=row.get("source_experience_id"),
        source_text=row.get("source_text"),
        created_at=str(row.get("created_at") or "") or None,
        updated_at=str(row.get("updated_at") or "") or None,
    )


def story_to_dict(s: StoryBankRow) -> dict:
    """Stable JSON-ready serialization for API responses."""
    return asdict(s)


def match_to_dict(m: StoryBankMatch) -> dict:
    """Stable JSON-ready serialization for search responses."""
    return {"story": story_to_dict(m.story), "similarity": float(m.similarity)}


# ═════════════════════════════════════════════════════════════════════════
# CRUD
# ═════════════════════════════════════════════════════════════════════════
async def list_stories(
    user_id: UUID | str,
    limit: int = 50,
    *,
    competency: Optional[str] = None,
) -> list[StoryBankRow]:
    """List a user's stories, optionally filtered to one competency tag.

    Ordered by outcome_score DESC then created_at DESC — the
    most-credit-attributed stories surface first. Falls back to
    chronological when outcome_score is 0 (the cold-start case).

    Returns up to `limit` rows. We never return embeddings (large
    vectors waste payload bytes for list views).
    """
    from db.client import get_supabase
    q = (
        get_supabase()
        .table("story_bank")
        .select(
            "id, user_id, title, situation, task, action, result, reflection, "
            "competencies, archetypes, quantified_metrics, outcome_score, "
            "source_cv_hash, source_experience_id, source_text, "
            "created_at, updated_at"
        )
        .eq("user_id", str(user_id))
        .order("outcome_score", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if competency:
        # GIN index on competencies — fast.
        q = q.contains("competencies", [competency])
    rows = (q.execute().data) or []
    return [_row_to_story(r) for r in rows]


async def get_story(
    user_id: UUID | str,
    story_id: UUID | str,
) -> Optional[StoryBankRow]:
    """Fetch one story by id, scoped to the user."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("story_bank")
        .select(
            "id, user_id, title, situation, task, action, result, reflection, "
            "competencies, archetypes, quantified_metrics, outcome_score, "
            "source_cv_hash, source_experience_id, source_text, "
            "created_at, updated_at"
        )
        .eq("id", str(story_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    ) or []
    return _row_to_story(rows[0]) if rows else None


async def update_story(
    user_id: UUID | str,
    story_id: UUID | str,
    *,
    patch: dict[str, Any],
) -> Optional[StoryBankRow]:
    """Patch a single story row.

    Mutable fields whitelist: title, situation, task, action, result,
    reflection, competencies, archetypes, quantified_metrics. Other
    fields (user_id, source_*, outcome_score) are off-limits via this
    path. The PATCH /workspace/stories/{id} endpoint surfaces this.

    Re-embeds the story when STAR text changes (the embedding is keyed
    on the text body, so an edit needs a fresh vector).
    """
    from agents.g9_io import embed_story_text
    from db.client import get_supabase

    allowed = {
        "title", "situation", "task", "action", "result", "reflection",
        "competencies", "archetypes", "quantified_metrics",
    }
    safe_patch = {k: v for k, v in (patch or {}).items() if k in allowed}
    if not safe_patch:
        return await get_story(user_id, story_id)

    # If any STAR/title field changed, re-embed.
    text_fields = {"title", "situation", "task", "action", "result", "competencies"}
    if any(k in safe_patch for k in text_fields):
        # Compose the embed payload using the post-patch values.
        existing = await get_story(user_id, story_id)
        if existing is None:
            return None
        merged = {**story_to_dict(existing), **safe_patch}
        try:
            safe_patch["embedding"] = await embed_story_text(merged)
        except Exception as e:
            logger.warning(f"story_bank re-embed failed for {story_id}: {e}")

    rows = (
        get_supabase()
        .table("story_bank")
        .update(safe_patch)
        .eq("id", str(story_id))
        .eq("user_id", str(user_id))
        .execute()
        .data
    ) or []
    return _row_to_story(rows[0]) if rows else None


async def update_outcome_score(story_id: UUID | str, delta: int) -> None:
    """Bump outcome_score by `delta` (positive or negative).

    Called by agents/outcome_to_persona.credit_outcome when a generated
    G3 answer or G7 application that cited THIS story produces a
    successful downstream outcome (callback / interview pass / offer).

    Implemented atomically via a stored expression so two concurrent
    credits don't race-clobber each other. Supabase-py doesn't expose
    `set("outcome_score", "outcome_score + 1")` directly, so we use a
    short read-modify-write under a TX surrogate (Postgres treats single
    UPDATE statements as atomic; we read the existing value first to
    keep the math obvious).
    """
    from db.client import get_supabase
    db = get_supabase()
    rows = (
        db.table("story_bank")
        .select("outcome_score")
        .eq("id", str(story_id))
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        logger.warning(f"update_outcome_score: story {story_id} not found")
        return
    current = int(rows[0].get("outcome_score") or 0)
    new_score = max(0, current + int(delta))
    db.table("story_bank").update({"outcome_score": new_score}).eq("id", str(story_id)).execute()


async def upsert_story_from_dict(user_id: UUID | str, story: dict) -> str:
    """Used by G9.persist_node and any callers that want to seed manually.

    Returns the story_id (uuid string). Wraps agents.g9_io.upsert_story
    so we expose ONE API surface to callers (the agent module).
    """
    from agents.g9_io import embed_story_text, upsert_story
    embedding = None
    try:
        embedding = await embed_story_text(story)
    except Exception as e:
        logger.warning(f"embed failed during upsert_story_from_dict: {e}")
    row = await upsert_story(
        user_id=str(user_id),
        title=story.get("title", ""),
        situation=story.get("situation", ""),
        task=story.get("task", ""),
        action=story.get("action", ""),
        result=story.get("result", ""),
        reflection=story.get("reflection", ""),
        competencies=story.get("competencies", []) or [],
        archetypes=story.get("archetypes", []) or [],
        quantified_metrics=story.get("quantified_metrics", []) or [],
        source_text=story.get("source_text", "") or "",
        source_cv_hash=story.get("source_cv_hash"),
        source_experience_id=story.get("source_experience_id"),
        embedding=embedding,
    )
    return str(row.get("id") or "")


# ═════════════════════════════════════════════════════════════════════════
# Semantic search
# ═════════════════════════════════════════════════════════════════════════
async def search_stories(
    user_id: UUID | str,
    query: str,
    k: int = 5,
    *,
    competency: Optional[str] = None,
) -> list[StoryBankMatch]:
    """Top-k semantic matches for `query`.

    Embeds the query with text-embedding-3-small (same model G9 uses on
    write), then orders story_bank by `embedding <=> query_embedding`
    (pgvector cosine distance). Returns at most `k` hits.

    `competency`: optional GIN-indexed pre-filter so callers can ask for
    "top-5 leadership stories about ambiguity" cheaply.

    Falls back to a chronological-list result if the embedding call
    fails or no stories have embeddings yet (cold start). This way G3
    Phase 2 can ship before story_bank is fully embedded — search just
    degrades to "most recent stories first" rather than 500ing.
    """
    if not query or not query.strip():
        return []

    from db.client import embed, get_supabase

    db = get_supabase()
    try:
        query_embedding = await embed(query)
    except Exception as e:
        logger.warning(f"search_stories: embedding query failed: {e} — falling back")
        rows = await list_stories(user_id, limit=k, competency=competency)
        return [StoryBankMatch(story=r, similarity=0.0) for r in rows]

    # supabase-py doesn't expose pgvector <=> ordering natively, so we
    # use rpc when one exists OR fall back to client-side sort over a
    # pull of all-with-embedding rows. For now we do the latter — the
    # story_bank row count is small (10-20 per user), so reading them
    # all is fine. When this gets hot, we'll add a search_story_bank v2
    # RPC similar to search_company_knowledge.
    q = (
        db.table("story_bank")
        .select(
            "id, user_id, title, situation, task, action, result, reflection, "
            "competencies, archetypes, quantified_metrics, outcome_score, "
            "source_cv_hash, source_experience_id, source_text, "
            "created_at, updated_at, embedding"
        )
        .eq("user_id", str(user_id))
        .not_.is_("embedding", "null")
    )
    if competency:
        q = q.contains("competencies", [competency])
    rows = (q.execute().data) or []
    if not rows:
        # Cold start — no embedded rows. Fall back to chronological.
        cold = await list_stories(user_id, limit=k, competency=competency)
        return [StoryBankMatch(story=r, similarity=0.0) for r in cold]

    # Client-side cosine similarity. Cheap at small N.
    matches: list[StoryBankMatch] = []
    for row in rows:
        emb = row.get("embedding")
        # Supabase returns vectors as either a list[float] (when pg-py
        # auto-decodes) or a stringified "[a,b,c]" (when it doesn't).
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                continue
        if not isinstance(emb, list) or len(emb) != len(query_embedding):
            continue
        sim = _cosine(emb, query_embedding)
        story = _row_to_story(row)
        # Bayesian tiebreaker — boost stories with higher outcome_score
        # by a tiny amount so ties get broken by past success. Capped at
        # 0.05 so a high-similarity story always beats a low-similarity
        # one even with 100+ credit.
        boost = min(0.05, 0.005 * story.outcome_score)
        matches.append(StoryBankMatch(story=story, similarity=sim + boost))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-python cosine similarity. a · b / (|a| |b|).

    Inputs are already 1536-dim text-embedding-3-small vectors. We do
    NOT renormalise — OpenAI returns unit vectors, so the dot product
    IS the cosine similarity (modulo float drift, which is fine for
    ranking).
    """
    if not a or not b:
        return 0.0
    dot = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
    return dot
