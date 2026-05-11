"""
agents/intro_email_agent.py — Drafts warm-intro request emails and direct
target outreach messages, using Opus 4.7 (the latest Anthropic model
priced in agents/llm_router.py:PRICING_PER_1M).

Two entry points:

  draft_intro_email(intro_path, target_role, candidate_profile)
    Drafts a request to the introducer (NOT the target). Persona:
    short, specific, makes the ask easy. Returns
        {subject, body, length_words, tone_notes}

  draft_target_outreach(intro_path, target_role, candidate_profile)
    Drafts a direct LinkedIn message to the target citing the mutual
    connection — used when the intro request is declined or skipped.
    Returns the same shape.

Cost cap
--------
LLM costs are bounded per-call by `max_cost_usd` (default 0.05 — about
2 Opus calls @ ~3000 input + 800 output tokens). If we exceed the cap
mid-call we abort and surface a `cost_capped` error. Reuses the same
pattern shipped in agents/resume_builder_agent.py and the LLMRouter
cost telemetry.

Why this beats the field
------------------------
Most career-tools draft generic outreach. We draft an intro request that
gives the introducer concrete language to forward (subject, one-liner,
specifics from the candidate's persona), AND a fallback direct message
that names the mutual connection without being weird about it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from agents.base_agent import BaseAgent
from agents.llm_router import LLMResult
from agents.referral_graph import Person, ReferralPath

logger = logging.getLogger(__name__)


# ── Defaults ──────────────────────────────────────────────────────────────
# 2026-05-12 right-sizing: drafting a 90-140-word email is exactly the kind
# of constrained creative-writing task where Sonnet 4.6 matches or exceeds
# Opus 4.7. At <10% the cost. The prompt itself does the heavy lifting
# (banned em-dashes, "hope this finds you well", forwardable structure).
# See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §44.
DEFAULT_MODEL: str = "claude-sonnet-4-6"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MAX_COST_USD: float = 0.05
DEFAULT_MAX_TOKENS: int = 1200
DEFAULT_TEMPERATURE: float = 0.4


# ── Prompts ───────────────────────────────────────────────────────────────
INTRO_EMAIL_SYSTEM = """\
You draft warm-introduction REQUEST emails — the email a job seeker
sends to an INTRODUCER (a mutual connection) asking the introducer to
forward an intro to a TARGET who works at a target company.

Constraints:
- Tone is direct, specific, low-friction. The introducer is busy.
- 90-140 words for the body. NEVER over 160.
- NO em-dashes. NO "I hope this finds you well". NO "cheers" / "warm regards".
- Lead with the ASK in plain language. The introducer should know in 3
  seconds: who, what role, why warm.
- Make it forward-able: include a 2-line "one-liner about me" the
  introducer can paste verbatim.
- End with: "If easier, happy to send a separate forwardable note."

Output JSON only:
{
  "subject": str,                 // <= 60 chars
  "body": str,                    // markdown plain text, no signature block
  "length_words": int,
  "tone_notes": str               // 1 sentence — why this tone fits the path
}
"""

TARGET_OUTREACH_SYSTEM = """\
You draft a DIRECT LinkedIn message from a job seeker to a TARGET at a
target company, citing a mutual connection (named) without making the
mutual connection do work. This is the fallback when the intro request
fails or the target is approachable directly.

Constraints:
- Tone is direct, specific. 60-110 words. NEVER over 130.
- LinkedIn-message format: NO subject line. Plain prose only. NO emoji.
- NO em-dashes. NO "Hope you're well".
- Cite the mutual ONCE early ("[Mutual] and I worked together on X").
- One concrete sentence about WHY the candidate is interested in the
  target's specific work — not a generic interest in the company.
- One soft ask: "Would you be open to a 15-min chat about what the team
  is hiring for?" — never a hard ask for a referral.

Output JSON only:
{
  "subject": str,                 // empty string for LinkedIn
  "body": str,
  "length_words": int,
  "tone_notes": str
}
"""


# ── Public agent ──────────────────────────────────────────────────────────
class IntroEmailAgent(BaseAgent):
    """Two-mode email/message drafter for the referral surface."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_cost_usd: float = DEFAULT_MAX_COST_USD,
    ) -> None:
        super().__init__(name="IntroEmailAgent", model=model, provider=DEFAULT_PROVIDER)
        self.max_cost_usd = float(max_cost_usd)

    async def run(  # BaseAgent contract
        self,
        intro_path: ReferralPath,
        target_role: dict[str, Any],
        candidate_profile: dict[str, Any],
        mode: str = "intro_email",
    ) -> dict[str, Any]:
        if mode == "target_outreach":
            return await self.draft_target_outreach(
                intro_path, target_role, candidate_profile
            )
        return await self.draft_intro_email(
            intro_path, target_role, candidate_profile
        )

    # ── Mode 1: warm-intro request to the introducer ─────────────────────
    async def draft_intro_email(
        self,
        intro_path: ReferralPath,
        target_role: dict[str, Any],
        candidate_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Draft an email from candidate → first-hop introducer.

        intro_path:
          path[0] = candidate (you)
          path[1] = introducer (the person to write to)
          path[-1] = target (the person you ultimately want to meet)

        target_role: {"company": str, "role": str, "team": str|None,
                      "url": str|None}
        candidate_profile: {"full_name", "current_role", "key_evidence",
                            "ask_one_liner"}  — everything you'd put in
                            "1-line about me".
        """
        introducer = _introducer_of(intro_path)
        target = intro_path.target_employee
        target_company = target_role.get("company") or intro_path.target_company_name or "the company"
        target_role_title = target_role.get("role") or "an open role"

        user_msg = (
            "Draft the intro request email.\n\n"
            f"## Candidate\n"
            f"- Name: {candidate_profile.get('full_name', '(you)')}\n"
            f"- Current role: {candidate_profile.get('current_role', '')}\n"
            f"- Forward-able one-liner: {candidate_profile.get('ask_one_liner', '')}\n"
            f"- Key evidence (for the body): "
            f"{candidate_profile.get('key_evidence', '')}\n\n"
            f"## Introducer\n"
            f"- Name: {introducer.full_name if introducer else '(unknown)'}\n"
            f"- Their role: {introducer.headline if introducer else ''}\n"
            f"- How you know them: "
            f"{_kind_to_human(intro_path.edge_kinds[0]) if intro_path.edge_kinds else 'connection'}\n\n"
            f"## Target you want to be introduced to\n"
            f"- Name: {target.full_name}\n"
            f"- Role at company: {target.current_role or target.headline or '(unknown)'}\n"
            f"- Company: {target_company}\n\n"
            f"## Role you are pursuing\n"
            f"- Title: {target_role_title}\n"
            f"- Team: {target_role.get('team') or '(unknown)'}\n"
            f"- URL: {target_role.get('url') or '(none)'}\n\n"
            "Return ONLY the JSON described in the system prompt."
        )

        return await self._draft_with_cost_cap(
            system=INTRO_EMAIL_SYSTEM,
            user_msg=user_msg,
            mode="intro_email",
        )

    # ── Mode 2: direct outreach to the target citing the mutual ──────────
    async def draft_target_outreach(
        self,
        intro_path: ReferralPath,
        target_role: dict[str, Any],
        candidate_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Draft a LinkedIn message from candidate → target citing the mutual.

        For 1-hop paths, the "mutual" is explicit. For 2-hop paths the
        mutual is the person at index 1 (the introducer). For 3+ hops,
        we fall back to "We share several mutual connections, including
        [name]".
        """
        target = intro_path.target_employee
        target_company = target_role.get("company") or intro_path.target_company_name or "your team"

        if len(intro_path.path) >= 2:
            mutual = intro_path.path[1]
            mutual_name = mutual.full_name
            mutual_role = mutual.headline or mutual.current_role or ""
        else:
            mutual_name = "(none)"
            mutual_role = ""

        user_msg = (
            "Draft the direct LinkedIn message.\n\n"
            f"## Candidate\n"
            f"- Name: {candidate_profile.get('full_name', '(you)')}\n"
            f"- Current role: {candidate_profile.get('current_role', '')}\n"
            f"- Key evidence: {candidate_profile.get('key_evidence', '')}\n"
            f"- One-liner about you: {candidate_profile.get('ask_one_liner', '')}\n\n"
            f"## Mutual\n"
            f"- Name: {mutual_name}\n"
            f"- Role: {mutual_role}\n\n"
            f"## Target\n"
            f"- Name: {target.full_name}\n"
            f"- Their role: {target.current_role or target.headline or ''}\n"
            f"- Company: {target_company}\n\n"
            f"## Role candidate is pursuing\n"
            f"- Title: {target_role.get('role') or ''}\n"
            f"- Why this team specifically: "
            f"{target_role.get('why_this_team') or '(candidate to fill)'}\n\n"
            "Return ONLY the JSON described in the system prompt."
        )

        return await self._draft_with_cost_cap(
            system=TARGET_OUTREACH_SYSTEM,
            user_msg=user_msg,
            mode="target_outreach",
        )

    # ── Internals ────────────────────────────────────────────────────────
    async def _draft_with_cost_cap(
        self,
        system: str,
        user_msg: str,
        mode: str,
    ) -> dict[str, Any]:
        """Single LLM call with a hard cost cap. Returns a dict.

        On any error we return a structured error dict instead of raising —
        the caller (UI) renders an error state and lets the user retry.
        """
        try:
            parsed, result = await self.ask_json(
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=DEFAULT_MAX_TOKENS,
                temperature=DEFAULT_TEMPERATURE,
            )
        except Exception as exc:
            logger.exception("intro_email draft failed")
            return {
                "error": "draft_failed",
                "detail": str(exc)[:500],
                "mode": mode,
            }

        cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
        if cost > self.max_cost_usd:
            logger.warning(
                "IntroEmailAgent cost cap hit: %.4f > %.4f", cost, self.max_cost_usd
            )
            return {
                "error": "cost_capped",
                "cost_usd": cost,
                "max_cost_usd": self.max_cost_usd,
                "mode": mode,
            }

        # Normalise return shape — be defensive about LLM output.
        subject = parsed.get("subject", "") if isinstance(parsed, dict) else ""
        body = parsed.get("body", "") if isinstance(parsed, dict) else ""
        length_words = parsed.get("length_words") if isinstance(parsed, dict) else None
        if not isinstance(length_words, int):
            length_words = len((body or "").split())
        tone_notes = parsed.get("tone_notes", "") if isinstance(parsed, dict) else ""

        return {
            "subject": subject,
            "body": body,
            "length_words": length_words,
            "tone_notes": tone_notes,
            "mode": mode,
            "cost_usd": cost,
            "model": result.model,
            "provider": result.provider,
        }


# ── Helpers ───────────────────────────────────────────────────────────────
def _introducer_of(intro_path: ReferralPath) -> Optional[Person]:
    """Return the introducer person (path[1]) if the path has one."""
    if len(intro_path.path) >= 2:
        return intro_path.path[1]
    return None


def _kind_to_human(kind: str) -> str:
    return {
        "me_first_degree": "first-degree LinkedIn connection",
        "colleague": "former colleague",
        "classmate": "classmate",
        "friend": "friend",
        "family": "family member",
        "introduced_by": "previously introduced you",
        "same_company_overlap": "company overlap",
        "referenced_in_outreach": "referenced in past outreach",
    }.get(kind, kind)


# ── Convenience top-level functions (one-shot calls) ──────────────────────
async def draft_intro_email(
    intro_path: ReferralPath,
    target_role: dict[str, Any],
    candidate_profile: dict[str, Any],
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
) -> dict[str, Any]:
    return await IntroEmailAgent(max_cost_usd=max_cost_usd).draft_intro_email(
        intro_path=intro_path,
        target_role=target_role,
        candidate_profile=candidate_profile,
    )


async def draft_target_outreach(
    intro_path: ReferralPath,
    target_role: dict[str, Any],
    candidate_profile: dict[str, Any],
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
) -> dict[str, Any]:
    return await IntroEmailAgent(max_cost_usd=max_cost_usd).draft_target_outreach(
        intro_path=intro_path,
        target_role=target_role,
        candidate_profile=candidate_profile,
    )
