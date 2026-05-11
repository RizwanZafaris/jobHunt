"""
agents/linkedin_voice_extractor.py — One-time voice-profile bootstrap.

Reads a user's CV markdown, extracts a draft `linkedin_voice_profile` row
(domain, seniority, signature topics, tone directives), and upserts it
into Supabase. The user can edit later via PUT /linkedin/voice-profile.

This runs ONCE per user — typically during onboarding. After that the
voice profile is owned by the user, not the LLM.

CLI:
    python -m agents.linkedin_voice_extractor \
        --user-id 00000000-0000-0000-0000-000000000001 \
        --cv-path cv.md

The CLI prints the extracted JSON and (unless --dry-run) writes the row.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from agents.llm_router import _parse_json_loose, get_router
from db.client import get_supabase

logger = logging.getLogger(__name__)


# 2026-05-12 right-sizing: distilling a voice profile from a CV is structured
# JSON extraction with a rich system prompt — Sonnet 4.6 has been observed
# to match Opus 4.7 here. Used once per user at onboarding so the saving is
# modest, but the prompt complexity doesn't warrant Opus.
EXTRACTOR_MODEL = "claude-sonnet-4-6"


# ─── Default avoid-phrases shipped with every new profile ────────────────
DEFAULT_AVOID = [
    "delve", "tapestry", "unpack", "journey",
    "at the end of the day", "a testament to",
    "navigate the complexities", "in this digital age",
    "in today's fast-paced world",
]

DEFAULT_TONE = (
    "plainspoken, specific, opinionated; never humble-brags; uses concrete "
    "metrics; avoids em-dashes-everywhere AI tells"
)


# ─── Prompt ──────────────────────────────────────────────────────────────
EXTRACT_SYSTEM = """You are a senior LinkedIn ghostwriter. Your job: read one CV and produce a starter `voice_profile` JSON for a draft-generator system. The user can edit later.

You do NOT write LinkedIn posts here. You write the profile that an LLM will USE to write posts on behalf of this user.

Output ONLY valid JSON, this exact shape:
{
  "domain": "<the user's professional domain in 2-4 words — e.g. 'fintech / payments product'>",
  "seniority": "<IC | manager | senior_manager | director | exec>",
  "signature_topics": [
    "<5-10 topics this user has direct, credible authority on, in their voice>"
  ],
  "credible_angles": [
    "<3-6 angles a ghostwriter could safely take in their name — be specific to their domain>"
  ],
  "tone_directives": "<one sentence describing how they should sound — 'plainspoken, opinionated, leads with metrics' etc>",
  "avoid_phrases": [
    "<5-12 phrases this user would NOT say — pull from common AI tells, plus any boilerplate the CV avoids>"
  ],
  "profile_md": "<a 2-3 paragraph markdown profile of the user that an LLM can be handed as 'this is who you are writing as'. Ground it in the CV: real numbers, real companies, real seniority. No hype words.>"
}

Rules:
- Be specific. Pull real numbers and company names from the CV.
- The user is a peer-quality professional — never write generic 'thought leader' fluff.
- `avoid_phrases` should always include 'delve', 'tapestry', 'unpack', 'journey', plus any phrases the CV's writing style itself avoids.
- `profile_md` is the load-bearing field — this is what the draft-generator will read every time. Make it dense and useful.
"""


EXTRACT_USER_TEMPLATE = """USER CV:

{cv_md}

Produce the voice_profile JSON.
"""


async def extract_voice_from_cv(cv_md: str) -> dict[str, Any]:
    """Call Opus to read the CV and emit a voice_profile JSON.

    Returns the parsed dict; the caller decides whether to write to DB.
    """
    if not cv_md.strip():
        raise ValueError("cv_md is empty")

    router = get_router()
    parsed, result = await router.ask_json(
        provider="anthropic",
        model=EXTRACTOR_MODEL,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": EXTRACT_USER_TEMPLATE.format(cv_md=cv_md[:12000])}],
        max_tokens=2048,
        temperature=0.3,
        agent_name="linkedin.voice_extractor",
    )

    # Merge defaults defensively: avoid_phrases must include the AI tells.
    avoid = list(parsed.get("avoid_phrases") or [])
    for p in DEFAULT_AVOID:
        if p not in avoid:
            avoid.append(p)
    parsed["avoid_phrases"] = avoid
    parsed.setdefault("tone_directives", DEFAULT_TONE)
    parsed.setdefault("profile_md", "")
    parsed.setdefault("signature_topics", [])
    parsed.setdefault("credible_angles", [])

    parsed["_meta"] = {
        "model": result.model,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    return parsed


def upsert_voice_profile(user_id: UUID | str, profile: dict[str, Any]) -> dict:
    """Insert or update the linkedin_voice_profile row.

    `profile` is the dict returned by `extract_voice_from_cv`. We persist
    the load-bearing fields and discard the conversation metadata.
    """
    db = get_supabase()
    row = {
        "user_id":         str(user_id),
        "profile_md":      profile.get("profile_md") or "",
        "tone_directives": profile.get("tone_directives") or DEFAULT_TONE,
        "avoid_phrases":   profile.get("avoid_phrases") or DEFAULT_AVOID,
        "example_posts":   profile.get("example_posts") or [],
    }
    result = (
        db.table("linkedin_voice_profile")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    rows = result.data or []
    if not rows:
        # Fall back to a fresh read.
        existing = (
            db.table("linkedin_voice_profile")
            .select("*").eq("user_id", str(user_id)).limit(1).execute()
        )
        rows = existing.data or []
    return rows[0] if rows else {}


# ─── CLI ─────────────────────────────────────────────────────────────────
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents.linkedin_voice_extractor",
        description="Bootstrap a linkedin_voice_profile from a CV.",
    )
    p.add_argument("--user-id", required=True, help="Target user UUID.")
    p.add_argument("--cv-path", default="cv.md",
                   help="Path to the CV markdown (default: cv.md).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print extracted JSON; do not write to DB.")
    return p


async def _main_async(args) -> int:
    cv_path = Path(args.cv_path)
    if not cv_path.exists():
        print(f"CV not found: {cv_path}", file=sys.stderr)
        return 2

    cv_md = cv_path.read_text(encoding="utf-8")
    profile = await extract_voice_from_cv(cv_md)
    print(json.dumps(profile, indent=2, default=str))

    if args.dry_run:
        print("\n--dry-run set; not writing.", file=sys.stderr)
        return 0

    written = upsert_voice_profile(args.user_id, profile)
    print(f"\nWrote linkedin_voice_profile.id={written.get('id')}", file=sys.stderr)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _build_argparser().parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
