"""
agents/g11_io.py — Supabase reads/writes for G11 voice calibration.

Tables touched (all in public schema):
    READ   writing_samples, profile_master
    WRITE  profile_master.voice_calibration (UPDATE),
           writing_samples.used_in_calibration (UPDATE)

The high-level orchestrator `run_g11(user_id)` is the public entry point
for the API endpoint POST /profile/voice-calibration/run, the
auto-trigger on the 5th writing sample upload, and tests. It builds
the initial VoiceCalibrationState, compiles the graph, runs it, and
returns the final state as a plain dict.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _resolve_user_id(explicit: Optional[str] = None) -> str:
    """Multi-tenant-safe user_id resolution.

    Matches the convention used in g6_io / g9_io — env override so the
    cron + worker code paths don't have to plumb user_id through every
    call site.
    """
    if explicit:
        return str(explicit)
    return os.environ.get("RIZWAN_USER_ID", _DEFAULT_USER_ID)


# ═════════════════════════════════════════════════════════════════════════
# Reads
# ═════════════════════════════════════════════════════════════════════════
def load_writing_samples_for_calibration(
    user_id: str,
    *,
    limit: int = 15,
) -> list[dict]:
    """Return writing_samples for the user, prioritising fresh samples.

    Strategy:
      1. Always include every used_in_calibration=FALSE row (the user's
         newest uploads — these are why the calibration is running).
      2. Top up with the most recent used_in_calibration=TRUE rows until
         we reach `limit` so the corpus stays stable across runs (a
         single new sample shouldn't swing the voice profile wildly —
         the previously-calibrated baseline anchors it).

    Returns a list of dicts in the WritingSample shape used by g11_state.
    """
    from db.client import get_supabase
    db = get_supabase()

    fresh = (
        db.table("writing_samples")
        .select("id, user_id, title, body, kind, source, used_in_calibration")
        .eq("user_id", user_id)
        .eq("used_in_calibration", False)
        .order("created_at", desc=True)
        .execute()
        .data
    ) or []

    out: list[dict] = list(fresh[:limit])
    remaining = limit - len(out)
    if remaining > 0:
        already = (
            db.table("writing_samples")
            .select("id, user_id, title, body, kind, source, used_in_calibration")
            .eq("user_id", user_id)
            .eq("used_in_calibration", True)
            .order("created_at", desc=True)
            .limit(remaining)
            .execute()
            .data
        ) or []
        out.extend(already)
    return out


def load_voice_calibration(user_id: str) -> Optional[dict]:
    """Return profile_master.voice_calibration for the user, or {}."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("profile_master")
        .select("voice_calibration")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        return None
    return rows[0].get("voice_calibration") or {}


def load_last_calibration_at(user_id: str):
    """Return the datetime of the last calibration run, or None.

    Used by the auto-trigger to decide whether ≥1 day has passed since
    the last run. Pulls from voice_calibration.extracted_at.
    """
    from datetime import datetime
    blob = load_voice_calibration(user_id) or {}
    raw = blob.get("extracted_at") if isinstance(blob, dict) else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def count_writing_samples(user_id: str) -> int:
    """Total writing_samples count for the user (used for auto-trigger gating)."""
    from db.client import get_supabase
    rs = (
        get_supabase()
        .table("writing_samples")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    return int(rs.count or 0)


# ═════════════════════════════════════════════════════════════════════════
# Writes
# ═════════════════════════════════════════════════════════════════════════
def update_profile_voice_calibration(
    *,
    user_id: str,
    voice_calibration: dict,
) -> dict:
    """UPDATE profile_master.voice_calibration for the user.

    profile_master has integer id PK + UUID user_id; we filter by user_id
    so the right tenant's row is updated. Returns the updated row (or
    an empty dict if no row matched — the caller decides whether to
    raise).
    """
    from db.client import get_supabase
    rs = (
        get_supabase()
        .table("profile_master")
        .update({"voice_calibration": voice_calibration})
        .eq("user_id", user_id)
        .execute()
    )
    rows = rs.data or []
    return rows[0] if rows else {}


def mark_samples_used(*, user_id: str, sample_ids: list[str]) -> int:
    """Flip used_in_calibration=TRUE for the listed sample ids.

    Filters by user_id too — never trust an id alone in a multi-tenant
    table even with RLS.
    Returns the number of rows updated.
    """
    if not sample_ids:
        return 0
    from db.client import get_supabase
    rs = (
        get_supabase()
        .table("writing_samples")
        .update({"used_in_calibration": True})
        .eq("user_id", user_id)
        .in_("id", [str(x) for x in sample_ids])
        .execute()
    )
    return len(rs.data or [])


def insert_writing_sample(
    *,
    user_id: str,
    title: str,
    body: str,
    kind: str,
    source: Optional[str] = None,
) -> dict:
    """INSERT a single writing_samples row. Returns the inserted row."""
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "kind": kind,
    }
    if source is not None:
        payload["source"] = source
    rs = (
        get_supabase()
        .table("writing_samples")
        .insert(payload)
        .execute()
    )
    rows = rs.data or []
    if not rows:
        raise RuntimeError("writing_samples insert returned no rows")
    return rows[0]


def list_writing_samples(user_id: str, *, limit: int = 100) -> list[dict]:
    """Return the user's writing_samples, newest first."""
    from db.client import get_supabase
    return (
        get_supabase()
        .table("writing_samples")
        .select("id, user_id, title, body, kind, source, used_in_calibration, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    ) or []


def delete_writing_sample(*, user_id: str, sample_id: str) -> bool:
    """DELETE one writing_samples row owned by user_id. Returns True on hit."""
    from db.client import get_supabase
    rs = (
        get_supabase()
        .table("writing_samples")
        .delete()
        .eq("user_id", user_id)
        .eq("id", sample_id)
        .execute()
    )
    return bool(rs.data)


# ═════════════════════════════════════════════════════════════════════════
# Orchestrator
# ═════════════════════════════════════════════════════════════════════════
# Process-wide compiled graph (matches the G9 pattern).
_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        from agents.g11_graph import build_g11_graph
        _GRAPH = build_g11_graph(checkpointer=None)
        logger.info("G11 graph compiled (checkpointer=off)")
    return _GRAPH


async def run_g11(user_id: Optional[str] = None) -> dict:
    """Run the G11 voice calibration graph for one user.

    Returns the final state dict, including:
      - persisted: bool
      - voice_calibration_json: the JSONB written to profile_master
      - samples_marked_used: int
      - cost_usd_total: float
      - latency_ms_total: int
      - error: str or None

    Cost: ~$0.08 per run (Sonnet 4.6 × 2 calls; persist is free).
    """
    uid = _resolve_user_id(user_id)
    samples = load_writing_samples_for_calibration(uid, limit=15)

    if not samples:
        logger.info(f"G11 run skipped: user_id={uid} has 0 writing_samples")
        return {
            "persisted": False,
            "samples_marked_used": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "error": "no_samples",
            "transcript": [],
        }

    initial_state: dict = {
        "user_id": uid,
        "samples": samples,
        "sample_ids": [str(s.get("id")) for s in samples if s.get("id")],
        "transcript": [],
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
    }

    graph = _get_graph()
    try:
        final = await graph.ainvoke(initial_state)
        logger.info(
            f"G11 run done: user_id={uid} "
            f"persisted={final.get('persisted')} "
            f"samples={final.get('samples_marked_used')} "
            f"cost=${final.get('cost_usd_total', 0):.4f}"
        )
        return dict(final)
    except Exception:
        logger.exception(f"G11 run failed for user_id={uid}")
        raise
