"""
agents/voice_injector.py — Shared utility for injecting the G11 voice
profile block into downstream writer prompts.

Why a shared module?
--------------------
G11 extracts the user's voice ONCE and persists it on
profile_master.voice_calibration.voice_profile_block. Every writer node
(G2 resume writer, G6 follow-up draft_generator, G7 application answer
generator) needs to prepend that block to its user message so generated
content matches the user's voice — not generic Sonnet output.

Two design choices worth flagging:

1. We prepend to the USER MESSAGE, not the SYSTEM PROMPT.
   The system prompts in G2/G6/G7 are large (1.5K-3K tokens) and
   prompt-cached. Splicing user-specific content into the system prompt
   would defeat the cache (different prefix per user). The voice block
   is small (~200-400 tokens) and changes only when the user re-runs
   G11 — keeping it in the user message preserves the system cache.

2. We return None on cold-start (no calibration yet).
   Writers MUST handle a None gracefully — that's the cold-start path
   where a user hasn't uploaded samples yet. The writer falls back to
   the generic style produced by its system prompt alone. No errors,
   no warnings — silent fallback.

Call sites (current):
  - resume_agents/g2_nodes.py::writer_node
  - agents/g6_nodes.py::draft_generator_node

Call sites (future, OUT OF SCOPE this PR):
  - agents/g7_nodes.py::answer_generator_node (Tier 3, not merged yet)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _resolve_user_id(explicit: Optional[str] = None) -> str:
    """Mirror the convention in g11_io / g6_io for single-user mode."""
    if explicit:
        return str(explicit)
    return os.environ.get("RIZWAN_USER_ID", _DEFAULT_USER_ID)


def get_voice_profile_block(user_id: Optional[str] = None) -> Optional[str]:
    """Return the cached voice profile block for injection into writer prompts.

    Reads profile_master.voice_calibration.voice_profile_block. Returns
    None when:
      - The user_id can't be resolved.
      - The user has no profile_master row.
      - voice_calibration is {} (cold-start, no G11 run yet).
      - voice_calibration.voice_profile_block is empty/missing.

    Used by:
      - resume_agents/g2_nodes.py::writer_node
      - agents/g6_nodes.py::draft_generator_node
      - agents/g7_nodes.py::answer_generator_node (Tier 3, not merged yet)

    Returns None on graceful fallback — writers generate without voice
    injection rather than erroring. This keeps the cold-start path
    working (a user with zero writing_samples just gets generic Sonnet
    output, which is fine as a default).
    """
    uid = _resolve_user_id(user_id)
    try:
        from db.client import get_supabase
        rows = (
            get_supabase()
            .table("profile_master")
            .select("voice_calibration")
            .eq("user_id", uid)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as e:
        # Any DB failure — silent fallback. We never want voice injection
        # to break a resume build.
        logger.debug(
            f"voice_injector: failed to read voice_calibration for "
            f"user_id={uid}: {e}"
        )
        return None

    if not rows:
        return None
    blob = rows[0].get("voice_calibration") or {}
    if not isinstance(blob, dict):
        return None
    block = blob.get("voice_profile_block")
    if not isinstance(block, str):
        return None
    block = block.strip()
    if not block:
        return None
    return block


def prepend_voice_block(
    user_message: str,
    user_id: Optional[str] = None,
) -> str:
    """Prepend the voice profile block (with a divider) to `user_message`.

    No-op when there's no calibration — returns the original message
    unchanged. The divider keeps the block visually + structurally
    separate from the writer's actual brief so the LLM doesn't confuse
    voice directives with content directives.
    """
    block = get_voice_profile_block(user_id)
    if not block:
        return user_message
    return (
        f"{block}\n"
        f"────────────────────────────────────────────────────────────────────────\n"
        f"{user_message}"
    )
