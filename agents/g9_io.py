"""
agents/g9_io.py — All Supabase reads/writes the G9 STAR+R extractor needs.

Centralised so nodes stay LLM-focused and we have a single place to fix
schema-drift issues in the future. Every public function here corresponds
to one entry-point or persist step in the graph.

Tables touched (all in public schema):
  READ   profile_master, profile_experience, profile_certification, profile_education
  WRITE  story_bank (upsert with on_conflict=(user_id, lower(title)))

Embeddings are issued via db.client.embed (OpenAI text-embedding-3-small).
"""
from __future__ import annotations
import hashlib
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# Master CV rendering (re-uses the G2 renderer to keep one source of truth)
# ═════════════════════════════════════════════════════════════════════════
def render_master_cv_md() -> str:
    """Render the candidate's master CV markdown.

    Delegates to resume_agents.g2_io.render_master_resume_md so G2 and G9
    operate over the EXACT same canonical text. If the renderer ever
    changes (new sections, reformatted experience, etc.) both graphs pick
    up the change without drift.
    """
    from resume_agents.g2_io import render_master_resume_md
    return render_master_resume_md()


def hash_cv(md: str) -> str:
    """sha256 of the master CV markdown.

    Used as source_cv_hash on every story so G9 re-runs can detect stories
    that came from older CV versions (and a future migration can prune
    them).
    """
    return hashlib.sha256((md or "").encode("utf-8")).hexdigest()


# ═════════════════════════════════════════════════════════════════════════
# Profile competencies — drives persona_critic's fabrication check
# ═════════════════════════════════════════════════════════════════════════
def load_profile_competencies() -> list[str]:
    """Return profile_master.core_competencies as a flat lowercase list.

    Used by persona_critic_node to detect competencies that the LLM
    invented (no source in the user's profile). Returns [] gracefully if
    the table is empty.
    """
    try:
        from db.client import get_supabase
        rows = (
            get_supabase()
            .table("profile_master")
            .select("core_competencies")
            .limit(1)
            .execute()
            .data
        ) or []
        if not rows:
            return []
        raw = rows[0].get("core_competencies") or []
        return [str(c).strip().lower() for c in raw if c]
    except Exception as e:
        logger.warning(f"G9 load_profile_competencies failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════
# Experience loader — used as a hint for source_experience_id FK
# ═════════════════════════════════════════════════════════════════════════
def load_experiences() -> list[dict]:
    """Return profile_experience rows ordered by sort_order.

    Used by the parser node to suggest source_experience_id for each
    candidate passage. We don't strictly need this — the parser could
    work off master_cv_md alone — but having the FK lets
    outcome_to_persona walk back to the originating role for credit
    attribution.
    """
    try:
        from db.client import get_supabase
        return (
            get_supabase()
            .table("profile_experience")
            .select("id, title, company, dates, summary, highlights, groups")
            .order("sort_order")
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.warning(f"G9 load_experiences failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════
# story_bank upsert — used by persist_node
# ═════════════════════════════════════════════════════════════════════════
def _default_user_id() -> str:
    """Single-user mode default. Mirrors the pattern used in other writers."""
    return os.environ.get(
        "RIZWAN_USER_ID",
        "00000000-0000-0000-0000-000000000001",
    )


async def upsert_story(
    *,
    user_id: str,
    title: str,
    situation: str,
    task: str,
    action: str,
    result: str,
    reflection: str,
    competencies: list[str],
    archetypes: list[str],
    quantified_metrics: list[str],
    source_text: str,
    source_cv_hash: Optional[str] = None,
    source_experience_id: Optional[int] = None,
    embedding: Optional[list[float]] = None,
) -> dict:
    """Insert or replace a story_bank row.

    Conflict resolution: (user_id, lower(title)) — see migration 015.
    When the same title is emitted again (re-run of G9 after a CV edit),
    we overwrite the row in place so consumers of story_bank don't see
    duplicates. The `created_at` column is preserved by Supabase's
    default DDL behaviour on UPDATE.

    user_id is REQUIRED. Pass the seed UUID via env (_default_user_id) in
    single-user mode or the authenticated user's id in multi-tenant mode.
    Never omit — story_bank.user_id is NOT NULL and the writer fails fast
    on the upstream profile_master writer's pattern.

    Returns the persisted row as a dict (with `id`).
    """
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "user_id": user_id,
        "title": title,
        "situation": situation,
        "task": task,
        "action": action,
        "result": result,
        "reflection": reflection,
        "competencies": competencies,
        "archetypes": archetypes,
        "quantified_metrics": quantified_metrics,
        "source_text": source_text,
    }
    if source_cv_hash is not None:
        payload["source_cv_hash"] = source_cv_hash
    if source_experience_id is not None:
        payload["source_experience_id"] = source_experience_id
    if embedding is not None:
        payload["embedding"] = embedding

    # NOTE: supabase-py's `on_conflict` parameter expects the **column
    # names** that form the unique constraint, not the expression. For
    # functional unique indexes (lower(title)) we can't use on_conflict
    # — we manually upsert via select-then-insert/update.
    db = get_supabase()
    existing = (
        db.table("story_bank")
        .select("id")
        .eq("user_id", user_id)
        .ilike("title", title)
        .limit(1)
        .execute()
        .data
    ) or []
    if existing:
        story_id = existing[0]["id"]
        payload.pop("user_id", None)  # don't change ownership
        rows = (
            db.table("story_bank")
            .update(payload)
            .eq("id", story_id)
            .execute()
            .data
        ) or []
        return rows[0] if rows else {"id": story_id}
    rows = (
        db.table("story_bank")
        .insert(payload)
        .execute()
        .data
    ) or []
    if not rows:
        raise RuntimeError(f"story_bank insert failed for title={title!r}")
    return rows[0]


# ═════════════════════════════════════════════════════════════════════════
# Embedding helper — single async call per story
# ═════════════════════════════════════════════════════════════════════════
async def embed_story_text(story: dict) -> list[float]:
    """Build the embedding input for one story and call OpenAI.

    Concatenation order is deterministic so semantic search returns
    consistent results across re-extractions. We include title + STAR
    body (no reflection — reflections are noisier and would dilute the
    embedding for "find me a story about leadership" queries).
    """
    from db.client import embed
    body = "\n".join([
        story.get("title", ""),
        story.get("situation", ""),
        story.get("task", ""),
        story.get("action", ""),
        story.get("result", ""),
        # Include competencies as plain text so the embedding captures them.
        " ".join(story.get("competencies", []) or []),
    ]).strip()
    return await embed(body)
