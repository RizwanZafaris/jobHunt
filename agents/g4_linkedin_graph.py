"""
agents/g4_linkedin_graph.py — LangGraph topology for the LinkedIn presence engine (P1.2).

Layout:
    entry
      │
      ▼
    pick_angle      Sonnet — chooses angle + anchor knowledge row
      │
      ▼
    draft_v1        Opus 4.5 — drafts hook/body/cta/hashtags from voice + cv + news
      │
      ▼
    critique        Sonnet — flags humble-brags / AI tells / generic claims / spam
      │
      ▼
    polish          Opus 4.5 — applies critique, returns final draft
      │
      ▼
    persist         pure code — INSERT INTO linkedin_drafts (status='draft')
      │
      ▼
    END

Cost cap per draft: $0.15 (Sonnet+Opus on 1k-token contexts is ~$0.05-0.10).
The graph never auto-posts. Output is always status='draft' for the user
to review at /linkedin.

The 4-node decomposition is deliberate:
  1. pick_angle is cheap (Sonnet), so we can iterate on angles quickly.
  2. draft_v1 burns the expensive call (Opus) on a single first draft.
  3. critique is independent + cheap, runs once on the draft.
  4. polish is the final Opus pass that makes the post good.

Why not loop? On test: 1 polish round is enough. The critic rarely flags
P0 issues post-polish; if it does, the user catches it on review. We
trade a second Opus call (~$0.06) for shipping the human-in-the-loop
gate that the audit's risk-2 mitigation requires anyway.

Mirrors resume_agents/g2_graph.py — same pattern, smaller graph.
"""
from __future__ import annotations

import json
import logging
import time
from operator import add
from typing import Annotated, Any, Optional, TypedDict

from agents.llm_router import _parse_json_loose, get_router
from db.client import get_supabase

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# State
# ═════════════════════════════════════════════════════════════════════════
class TranscriptTurn(TypedDict, total=False):
    """One row appended to LinkedInState.transcript per node invocation."""
    node: str
    provider: str
    model: str
    input_summary: str
    output: Any
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class LinkedInState(TypedDict, total=False):
    # ─── Inputs (set at entry) ──────────────────────────────────────────
    user_id: str                            # uuid string
    voice_profile: dict                     # row from linkedin_voice_profile
    candidate_knowledge: list[dict]         # recent company_knowledge rows (last 7d)
    cv_md: str                              # rendered profile (cv.md or profile_master)
    request_angle: Optional[str]            # user-pinned angle, or None
    request_target_company_id: Optional[str]  # user-pinned company, or None

    # ─── Node outputs ───────────────────────────────────────────────────
    chosen_angle: str                       # 'news_commentary' | 'contrarian_take' | …
    chosen_knowledge_id: Optional[str]      # uuid of company_knowledge row, or None
    chosen_company_id: Optional[str]
    chosen_company_name: Optional[str]
    angle_rationale: str                    # 1-line "why this angle"

    draft_v1_hook: str
    draft_v1_body: str
    draft_v1_cta: Optional[str]
    draft_v1_hashtags: list[str]
    draft_v1_why_it_works: str

    critique: dict                          # {issues: [{severity, kind, note}]}

    final_hook: str
    final_body: str
    final_cta: Optional[str]
    final_hashtags: list[str]

    # ─── Image brief (node 5) ───────────────────────────────────────────
    image_brief: Optional[dict]             # see IMAGE_BRIEF_SYSTEM schema

    # ─── Persistence + telemetry ────────────────────────────────────────
    draft_id: Optional[str]                 # set by persist_node
    transcript: Annotated[list[TranscriptTurn], add]
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    error: Optional[str]
    # 2026-05-12 (G4-5): exposed so api/linkedin.py can render the draft
    # with a "polish failed; v1 body shipped as-is" annotation.
    polish_status: Optional[str]            # "ok" | "failed" | None (not yet run)
    polish_error: Optional[str]

    # 2026-05-12 (G4-2): per-draft hard cost cap (mirrors G2 Phase 1.11 /
    # G3 Phase 2 design). Set by run_g4_graph from settings.g4_max_cost_usd.
    # Each LLM-using node pre-checks cumulative cost against this and short-
    # circuits with cost_capped=True instead of spending more.
    cost_cap_usd: float
    cost_capped: bool


def _over_cap(state: "LinkedInState") -> bool:
    """Return True if cumulative cost is at or above the per-draft cost cap.

    Used by every LLM-using G4 node as a pre-call guard. The check is
    >= (not >) so a single Sonnet call that lands exactly at the cap
    doesn't slip through and trigger an Opus follow-up.
    """
    cost_so_far = float(state.get("cost_usd_total") or 0.0)
    cap = float(state.get("cost_cap_usd") or 0.0)
    if cap <= 0:
        return False  # cap unset → unbounded (preserves prior behaviour)
    return cost_so_far >= cap


def _cost_capped_turn(node: str, model: str, state: "LinkedInState") -> "TranscriptTurn":
    """Build the transcript turn emitted when a node short-circuits on cap."""
    return _make_turn(
        node=node,
        provider="anthropic",
        model=model,
        input_summary=f"cost_capped before call (cost_so_far=${float(state.get('cost_usd_total') or 0.0):.4f}, cap=${float(state.get('cost_cap_usd') or 0.0):.2f})",
        output={"status": "cost_capped"},
        cost_usd=0.0,
        latency_ms=0,
    )


def _make_turn(
    node: str, provider: str, model: str, input_summary: str,
    output: Any, cost_usd: float, latency_ms: int, error: Optional[str] = None,
) -> TranscriptTurn:
    return {
        "node": node, "provider": provider, "model": model,
        "input_summary": input_summary[:500], "output": output,
        "cost_usd": cost_usd, "latency_ms": latency_ms, "error": error,
    }


# ═════════════════════════════════════════════════════════════════════════
# Models
# ═════════════════════════════════════════════════════════════════════════
# Cheap critic / picker: Sonnet. Heavy generator: Opus.
# 2026-05-12: now read from settings so we can A/B without redeploying.
# Fall back to hardcoded values if settings aren't loadable (e.g. during
# `python -c "from agents.g4_linkedin_graph import ..."` smoke tests
# without env). See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §G4-3.
try:
    from config.settings import get_settings as _g4_get_settings
    _g4_s = _g4_get_settings()
    SONNET_MODEL = _g4_s.g4_sonnet_model
    OPUS_MODEL   = _g4_s.g4_opus_model
except Exception:
    SONNET_MODEL = "claude-sonnet-4-6"
    OPUS_MODEL   = "claude-opus-4-7"


# ═════════════════════════════════════════════════════════════════════════
# Node 1 — pick_angle (Sonnet)
#
# Picks one of five angles + the specific company_knowledge row to anchor
# the post to. Cheap enough that we can call it for every draft (no caching).
# ═════════════════════════════════════════════════════════════════════════
PICK_ANGLE_SYSTEM = """You are a senior LinkedIn content strategist for a fintech / payments product leader.

Your job: pick the SINGLE BEST angle for one post, anchored to one real news event from the candidate's target-industry knowledge feed. The user posts 3x/week and the algorithm rewards consistency, so each post must feel earned — not generic.

The five available angles are:
- news_commentary    — straight take on a company event (funding, launch, regulation). Best when the news is concrete and the user has direct domain expertise that lets them say something a generalist can't.
- contrarian_take    — disagree with the consensus reaction. Best when the news is hyped and the user has a non-obvious counter-position grounded in their experience.
- build_in_public    — share a problem the user is solving NOW that's adjacent to the news. Best for people building products; weakest for pure managers.
- lesson_learned     — "we tried X at <past company>; here's what the news event makes me reconsider." Best for senior ICs/managers with a war-story bank.
- industry_analysis  — zoom out: "this isn't about <one company>, it's about <theme>." Best when the user is positioning as a thought leader.

How to choose:
1. Pick the angle the news event most naturally supports. Don't force a contrarian take onto bland news.
2. Prefer angles where the user's CV gives them a credible voice. A program manager can't credibly do "build_in_public" on a deep technical product.
3. Avoid the same angle two posts in a row when possible (the user's recent_angles list is provided).
4. The chosen knowledge row should be RECENT (last 7 days preferred) and have a hard fact in it (a number, a date, a company action).

Output ONLY valid JSON. No prose, no markdown. Schema:
{
  "chosen_knowledge_id": "<uuid>",        // exact id from candidate_knowledge[*].id
  "chosen_company_name": "<string>",      // company the news is about, or null if industry_analysis
  "angle": "<one of the five above>",
  "rationale": "<1 sentence — WHY this angle for this news, given this CV>"
}
"""


PICK_ANGLE_USER_TEMPLATE = """USER CV (excerpt — use to gauge what angles the user can credibly pull off):
{cv_excerpt}

USER VOICE PROFILE:
- Tone: {tone_directives}
- Avoid these phrases: {avoid_phrases}

CANDIDATE NEWS EVENTS (most recent first — pick exactly ONE id):
{candidate_block}

USER OVERRIDES (may be empty):
- Pinned angle (if any): {request_angle}
- Pinned company id (if any): {request_target_company_id}

Recent angles used (avoid repeating the most recent if you can): {recent_angles}

Pick the single best (knowledge_row, angle) pair and output the JSON.
"""


def _format_candidate_block(rows: list[dict]) -> str:
    if not rows:
        return "(no recent knowledge rows — fall back to industry_analysis with chosen_knowledge_id=null)"
    lines = []
    for r in rows[:15]:                         # cap at 15 to keep prompt small
        content = (r.get("content") or "")[:600]
        lines.append(
            f"- id={r['id']}\n"
            f"  company={r.get('company_name','?')}  section={r.get('section','?')}  "
            f"scraped_at={r.get('scraped_at','?')}\n"
            f"  excerpt: {content.strip()}"
        )
    return "\n\n".join(lines)


async def pick_angle_node(state: LinkedInState) -> dict:
    """Sonnet picks angle + anchor knowledge row."""
    started = time.perf_counter()

    # 2026-05-12 (G4-2): hard cost-cap pre-check. If somehow we entered the
    # graph already over budget (e.g. a checkpointed resume that's been
    # billed), short-circuit and fail-soft with a clear signal.
    if _over_cap(state):
        return {
            "cost_capped": True,
            "transcript": [_cost_capped_turn("pick_angle", SONNET_MODEL, state)],
            "error": "cost_capped before pick_angle could run",
        }

    voice = state.get("voice_profile") or {}
    cv_md = state.get("cv_md", "")
    candidates = state.get("candidate_knowledge") or []

    # Recent-angles look-back (defensive: only if the row exists).
    recent_angles: list[str] = []
    try:
        db = get_supabase()
        rs = (
            db.table("linkedin_drafts")
            .select("angle")
            .eq("user_id", state["user_id"])
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        recent_angles = [r["angle"] for r in (rs.data or []) if r.get("angle")]
    except Exception as e:
        logger.debug(f"pick_angle: recent-angles lookup skipped: {e}")

    user_msg = PICK_ANGLE_USER_TEMPLATE.format(
        cv_excerpt=cv_md[:3000],
        tone_directives=voice.get("tone_directives", "(default)"),
        avoid_phrases=", ".join(voice.get("avoid_phrases") or []),
        candidate_block=_format_candidate_block(candidates),
        request_angle=state.get("request_angle") or "(none)",
        request_target_company_id=state.get("request_target_company_id") or "(none)",
        recent_angles=", ".join(recent_angles) if recent_angles else "(none)",
    )

    router = get_router()
    try:
        parsed, result = await router.ask_json(
            provider="anthropic",
            model=SONNET_MODEL,
            system=PICK_ANGLE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
            temperature=0.4,
            agent_name="g4.pick_angle",
        )
    except Exception as e:
        logger.exception("pick_angle failed")
        return {
            "transcript": [_make_turn(
                "pick_angle", "anthropic", SONNET_MODEL,
                user_msg[:200], None, 0.0,
                int((time.perf_counter() - started) * 1000),
                error=str(e),
            )],
            "error": f"pick_angle: {type(e).__name__}: {e}",
        }

    angle = parsed.get("angle") or "industry_analysis"
    chosen_kid = parsed.get("chosen_knowledge_id")
    chosen_company = parsed.get("chosen_company_name")

    # Resolve chosen_company_id from name if possible.
    chosen_company_id: Optional[str] = None
    if chosen_company:
        try:
            db = get_supabase()
            rs = (
                db.table("companies").select("id")
                .ilike("name", chosen_company).limit(1).execute()
            )
            chosen_company_id = (rs.data or [{}])[0].get("id")
        except Exception:
            pass

    return {
        "chosen_angle": angle,
        "chosen_knowledge_id": chosen_kid,
        "chosen_company_id": chosen_company_id,
        "chosen_company_name": chosen_company,
        "angle_rationale": parsed.get("rationale", ""),
        "transcript": [_make_turn(
            "pick_angle", result.provider, result.model,
            user_msg[:200], parsed, result.cost_usd, result.latency_ms,
        )],
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 2 — draft_v1 (Opus 4.5)
#
# This is the expensive node. It writes the post. Heavy on system-prompt
# discipline so the output is on-voice and not generic.
# ═════════════════════════════════════════════════════════════════════════
DRAFT_V1_SYSTEM = """You are ghostwriting a LinkedIn post for a senior fintech / payments product leader. Your single job: write a post that sounds like THEM, not like an AI, and that earns engagement because it says something specific.

Hard rules — break any of these and the user will reject the draft:
1. NEVER use these phrases: "delve", "tapestry", "unpack", "journey", "at the end of the day", "a testament to", "in today's fast-paced world", "navigate the complexities", "in this digital age". Add the user's `avoid_phrases` to this banned list.
2. NEVER humble-brag. Replace "I was fortunate enough to lead a team of 40 engineers and process $1B+ TPV" with "Led 40 engineers; processed $1B+ TPV; took 4 years."
3. NEVER use em-dash strings to look thoughtful. ONE em-dash per post is the cap. (AI tells lean on em-dashes.)
4. NEVER post unsupported claims. Every assertion must trace to (a) a number from the user's CV, (b) the news anchor, or (c) a clearly-marked opinion.
5. NEVER end with "What do you think?" — replace with a specific question that sounds like the user's voice.
6. NEVER spam hashtags. Cap at 4. Use only hashtags a real PM/payments person would use (#payments #fintech #productmanagement #regtech, NOT #grind #hustle).

Structure:
- HOOK (1-3 lines): the first 1-2 lines decide whether anyone reads further. Lead with a specific number, a sharp opinion, or a concrete moment. Never start with "I'm thrilled to share" / "Excited to announce" / "Reflecting on…".
- BODY (3-7 short paragraphs, line-broken aggressively): tell ONE story or make ONE argument. If lesson_learned: setup → turn → lesson. If contrarian: claim → evidence → reframe. If news_commentary: news → user's specific take → why it matters. Use specific numbers from the CV where they fit.
- CTA (optional, 1 line): a question or a stake-in-the-ground that invites peer engagement. Skip if the body already lands.
- HASHTAGS (1-4 max): topical, not promotional.

Length: 800-1500 characters. LinkedIn truncates around 200; the hook is your real estate.

Tone: plainspoken, specific, opinionated. The user is not a hype-bro; they're a senior operator. Treat the reader as a peer who has also shipped real product.

Output ONLY valid JSON, this exact shape:
{
  "hook": "<1-3 lines>",
  "body": "<3-7 short paragraphs separated by blank lines>",
  "cta": "<one line, or null>",
  "hashtags": ["#payments", "#fintech", ...],   // 1-4 entries, lowercase, leading #
  "why_it_works": "<one line — why this specific opening + angle will land>"
}
"""


DRAFT_V1_USER_TEMPLATE = """USER CV (full — use real numbers verbatim where possible):
{cv_md}

USER VOICE PROFILE:
- Tone: {tone_directives}
- Avoid: {avoid_phrases}
- Example posts the user admires (mimic cadence + paragraph length, NOT topic):
{example_posts}

ANGLE CHOSEN: {angle}
ANGLE RATIONALE: {rationale}

NEWS ANCHOR (the post must reference this concretely if angle != industry_analysis):
{news_block}

Write the post. Output the JSON.
"""


def _format_news_anchor(state: LinkedInState) -> str:
    kid = state.get("chosen_knowledge_id")
    if not kid:
        return "(no anchor — angle is industry_analysis; lean on the user's domain expertise.)"
    rows = state.get("candidate_knowledge") or []
    row = next((r for r in rows if str(r.get("id")) == str(kid)), None)
    if not row:
        return f"(anchor id {kid} not found in candidates — fall back to industry_analysis.)"
    return (
        f"Company: {row.get('company_name','?')}\n"
        f"Section: {row.get('section','?')}\n"
        f"Scraped: {row.get('scraped_at','?')}\n\n"
        f"{(row.get('content') or '')[:2500]}"
    )


def _format_example_posts(voice: dict) -> str:
    examples = voice.get("example_posts") or []
    if not examples:
        return "(none provided — use plainspoken-operator default cadence: short paragraphs, line breaks every 1-2 sentences.)"
    return "\n\n---\n\n".join(e for e in examples[:3])


async def draft_v1_node(state: LinkedInState) -> dict:
    """Opus drafts the first version."""
    started = time.perf_counter()

    # 2026-05-12 (G4-2): hard cost-cap pre-check. Opus call ~$0.04 expected.
    # If cost_so_far is already at the cap, do NOT spend that on a node
    # whose output downstream nodes will also short-circuit on.
    if _over_cap(state):
        return {
            "cost_capped": True,
            "draft_v1_hook": "(cost cap reached — draft skipped)",
            "draft_v1_body": "",
            "draft_v1_cta": None,
            "draft_v1_hashtags": [],
            "draft_v1_why_it_works": "",
            "transcript": [_cost_capped_turn("draft_v1", OPUS_MODEL, state)],
        }

    voice = state.get("voice_profile") or {}

    user_msg = DRAFT_V1_USER_TEMPLATE.format(
        cv_md=state.get("cv_md", "")[:8000],
        tone_directives=voice.get("tone_directives", "(default)"),
        avoid_phrases=", ".join(voice.get("avoid_phrases") or []),
        example_posts=_format_example_posts(voice),
        angle=state.get("chosen_angle", "industry_analysis"),
        rationale=state.get("angle_rationale", ""),
        news_block=_format_news_anchor(state),
    )

    router = get_router()
    try:
        parsed, result = await router.ask_json(
            provider="anthropic",
            model=OPUS_MODEL,
            system=DRAFT_V1_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            temperature=0.6,
            agent_name="g4.draft_v1",
        )
    except Exception as e:
        logger.exception("draft_v1 failed")
        return {
            "transcript": [_make_turn(
                "draft_v1", "anthropic", OPUS_MODEL,
                user_msg[:200], None, 0.0,
                int((time.perf_counter() - started) * 1000),
                error=str(e),
            )],
            "error": f"draft_v1: {type(e).__name__}: {e}",
        }

    return {
        "draft_v1_hook": parsed.get("hook", "").strip(),
        "draft_v1_body": parsed.get("body", "").strip(),
        "draft_v1_cta": (parsed.get("cta") or None),
        "draft_v1_hashtags": [h for h in (parsed.get("hashtags") or []) if isinstance(h, str)][:4],
        "draft_v1_why_it_works": parsed.get("why_it_works", "").strip(),
        "transcript": [_make_turn(
            "draft_v1", result.provider, result.model,
            user_msg[:200], parsed, result.cost_usd, result.latency_ms,
        )],
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 3 — critique (Sonnet)
#
# Looks for: humble-brags, AI tells (em-dashes-everywhere, banned phrases),
# unsupported claims, generic vs specific, hashtag spam, length issues.
# Output: severity-tagged JSON the polish node consumes.
# ═════════════════════════════════════════════════════════════════════════
CRITIQUE_SYSTEM = """You are a senior LinkedIn content critic. Your job: rip the draft you're given. You want to ship the BEST possible post, so you flag anything that would make a peer roll their eyes.

Audit checklist (apply ALL — no skipping):
1. AI tells: any of "delve / tapestry / unpack / journey / a testament to" → P0 issue. Two or more em-dashes in the body → P1. Generic openers like "Excited to share / I'm thrilled to / Reflecting on…" → P0.
2. Humble-brags: "I was fortunate enough to / Honored to / Grateful that I got to lead…" → P0. The user must own their wins flatly.
3. Unsupported claims: any number or strong assertion that doesn't trace to (a) the news anchor or (b) the user's CV → P0 with the exact span.
4. Generic vs specific: paragraphs that could appear in any post about anything → P1. Concrete numbers + named companies = good. "We learned a lot" = bad.
5. Hashtag spam: more than 4 hashtags, or non-topical ones (#hustle #grind #motivation) → P1.
6. Length: post body is <100 chars (too thin) or >1500 chars (LinkedIn cuts) → P1.
7. CTA quality: "What do you think?" / "Thoughts?" → P1 (replace with a specific question that names a real tension).
8. Hook quality: hook is buried below line 3 → P0; hook reads like a press release → P1.
9. Voice profile: any phrase from `avoid_phrases` appears → P0.

Each issue gets a severity:
- P0: must fix. Will get the post rejected by the user.
- P1: should fix. Will materially raise quality.
- P2: nice-to-have. Take if cheap.

Output ONLY valid JSON, this exact shape:
{
  "issues": [
    {"severity": "P0|P1|P2", "kind": "<short slug like 'humble_brag' or 'ai_tell'>", "span": "<verbatim text from the draft, max 80 chars>", "fix": "<concrete one-line fix>"}
  ],
  "passes": ["<short string per check that passed cleanly>"],
  "verdict": "ship_after_fix | ship_as_is | rewrite"
}

If there are no issues, return `issues: []` and `verdict: "ship_as_is"`. Don't pad — be honest.
"""


CRITIQUE_USER_TEMPLATE = """DRAFT TO CRITIQUE:

HOOK:
{hook}

BODY:
{body}

CTA: {cta}
HASHTAGS: {hashtags}

USER avoid_phrases: {avoid_phrases}
USER tone_directives: {tone_directives}

NEWS ANCHOR (so you can verify claims trace back):
{news_block}

USER CV (so you can verify numbers trace back):
{cv_excerpt}

Apply the full audit checklist. Output JSON.
"""


async def critique_node(state: LinkedInState) -> dict:
    """Sonnet audits the draft."""
    started = time.perf_counter()

    # 2026-05-12 (G4-2): hard cost-cap pre-check. If we got here from a
    # cost-capped draft_v1, fail through with an empty critique so polish
    # also short-circuits and persist still produces a (skinny) draft row.
    if _over_cap(state) or state.get("cost_capped"):
        return {
            "critique": {"verdict": "ship_as_is", "issues": [], "_cost_capped": True},
            "transcript": [_cost_capped_turn("critique", SONNET_MODEL, state)],
        }

    voice = state.get("voice_profile") or {}

    user_msg = CRITIQUE_USER_TEMPLATE.format(
        hook=state.get("draft_v1_hook", ""),
        body=state.get("draft_v1_body", ""),
        cta=state.get("draft_v1_cta") or "(none)",
        hashtags=", ".join(state.get("draft_v1_hashtags") or []),
        avoid_phrases=", ".join(voice.get("avoid_phrases") or []),
        tone_directives=voice.get("tone_directives", "(default)"),
        news_block=_format_news_anchor(state)[:1500],
        cv_excerpt=state.get("cv_md", "")[:3000],
    )

    router = get_router()
    try:
        parsed, result = await router.ask_json(
            provider="anthropic",
            model=SONNET_MODEL,
            system=CRITIQUE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1024,
            temperature=0.2,
            agent_name="g4.critique",
        )
    except Exception as e:
        logger.exception("critique failed")
        # Fail-soft: fall through to polish with empty critique.
        return {
            "critique": {"issues": [], "passes": [], "verdict": "ship_as_is",
                         "_error": str(e)},
            "transcript": [_make_turn(
                "critique", "anthropic", SONNET_MODEL,
                user_msg[:200], None, 0.0,
                int((time.perf_counter() - started) * 1000),
                error=str(e),
            )],
        }

    return {
        "critique": parsed,
        "transcript": [_make_turn(
            "critique", result.provider, result.model,
            user_msg[:200], parsed, result.cost_usd, result.latency_ms,
        )],
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 4 — polish (Opus 4.5)
#
# Applies the critique, returns the final draft.
# Short-circuit when critique.verdict == "ship_as_is" — no second LLM call.
# ═════════════════════════════════════════════════════════════════════════
POLISH_SYSTEM = """You are revising a LinkedIn draft based on a peer-quality critique. Your job: apply EVERY P0 fix and as many P1 fixes as you can without breaking the post's voice. Do not introduce new claims, new numbers, or new companies — only revise what's there.

Hard rules:
1. Every P0 fix MUST be applied — verbatim if the critic gave a verbatim fix.
2. Apply P1 fixes unless they fight an earlier P0 fix.
3. Do NOT rewrite from scratch. Surgical edits only.
4. Preserve the user's voice — short paragraphs, plainspoken, opinionated.
5. Re-check banned phrases after edits — sometimes a P0 fix introduces a new AI tell.
6. Length cap: 1500 chars body. If you went over, trim a paragraph rather than soften the rest.
7. Hashtags: keep at most 4. Drop the most generic ones first.

Output ONLY valid JSON, this exact shape:
{
  "hook": "<final hook>",
  "body": "<final body>",
  "cta": "<final cta or null>",
  "hashtags": ["#a", ...],   // 1-4 entries
  "fixes_applied": ["<short slug per fix>"],
  "fixes_skipped": ["<short slug per fix you intentionally skipped, with reason>"]
}
"""


POLISH_USER_TEMPLATE = """ORIGINAL DRAFT:

HOOK:
{hook}

BODY:
{body}

CTA: {cta}
HASHTAGS: {hashtags}

CRITIC FINDINGS (apply all P0; apply P1 where possible):
{critique_json}

USER VOICE PROFILE:
- Tone: {tone_directives}
- Avoid: {avoid_phrases}

Revise. Output JSON.
"""


async def polish_node(state: LinkedInState) -> dict:
    """Opus applies the critique and emits the final post."""
    started = time.perf_counter()
    crit = state.get("critique") or {}
    verdict = crit.get("verdict") or "ship_as_is"
    issues = crit.get("issues") or []

    # 2026-05-12 (G4-2): hard cost-cap pre-check. If we got here over budget
    # or via a cost-capped upstream node, reuse the (possibly empty) draft_v1
    # verbatim. Polish is the most expensive node — never run it over-budget.
    if _over_cap(state) or state.get("cost_capped"):
        return {
            "final_hook":     state.get("draft_v1_hook", ""),
            "final_body":     state.get("draft_v1_body", ""),
            "final_cta":      state.get("draft_v1_cta"),
            "final_hashtags": state.get("draft_v1_hashtags") or [],
            "transcript": [_cost_capped_turn("polish", OPUS_MODEL, state)],
        }

    # Short-circuit: nothing to fix → final = draft_v1.
    if verdict == "ship_as_is" and not issues:
        return {
            "final_hook":     state.get("draft_v1_hook", ""),
            "final_body":     state.get("draft_v1_body", ""),
            "final_cta":      state.get("draft_v1_cta"),
            "final_hashtags": state.get("draft_v1_hashtags") or [],
            "transcript": [_make_turn(
                "polish", "(skipped)", "(skipped)",
                "verdict=ship_as_is", {"skipped": True}, 0.0, 0,
            )],
        }

    voice = state.get("voice_profile") or {}
    user_msg = POLISH_USER_TEMPLATE.format(
        hook=state.get("draft_v1_hook", ""),
        body=state.get("draft_v1_body", ""),
        cta=state.get("draft_v1_cta") or "(none)",
        hashtags=", ".join(state.get("draft_v1_hashtags") or []),
        critique_json=json.dumps(crit, indent=2),
        tone_directives=voice.get("tone_directives", "(default)"),
        avoid_phrases=", ".join(voice.get("avoid_phrases") or []),
    )

    router = get_router()
    try:
        parsed, result = await router.ask_json(
            provider="anthropic",
            model=OPUS_MODEL,
            system=POLISH_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            temperature=0.5,
            agent_name="g4.polish",
        )
    except Exception as e:
        # 2026-05-12 (G4-5): emit polish_status=failed into the transcript so
        # boss_agent's weekly audit can surface polish failure-rate trends.
        # The fail-soft (reuse draft_v1) is the correct call; what was missing
        # is observability — a 7/20 polish failure rate was invisible.
        logger.exception("polish failed — reusing draft_v1 verbatim")
        error_summary = str(e)[:200]
        return {
            "final_hook":     state.get("draft_v1_hook", ""),
            "final_body":     state.get("draft_v1_body", ""),
            "final_cta":      state.get("draft_v1_cta"),
            "final_hashtags": state.get("draft_v1_hashtags") or [],
            "polish_status":  "failed",
            "polish_error":   error_summary,
            "transcript": [_make_turn(
                "polish", "anthropic", OPUS_MODEL,
                user_msg[:200],
                {"status": "failed", "error": error_summary},
                0.0,
                int((time.perf_counter() - started) * 1000),
                error=error_summary,
            )],
        }

    return {
        "final_hook":     parsed.get("hook", "").strip(),
        "final_body":     parsed.get("body", "").strip(),
        "final_cta":      (parsed.get("cta") or None),
        "final_hashtags": [h for h in (parsed.get("hashtags") or []) if isinstance(h, str)][:4],
        "polish_status":  "ok",
        "transcript": [_make_turn(
            "polish", result.provider, result.model,
            user_msg[:200], parsed, result.cost_usd, result.latency_ms,
        )],
        "cost_usd_total": result.cost_usd,
        "latency_ms_total": result.latency_ms,
    }


# ═════════════════════════════════════════════════════════════════════════
# Node 5 — image_brief (Sonnet, ~$0.01)
#
# Briefs the visual asset for the post. Does NOT generate the image.
# Output is a structured creation brief the user can either feed to a
# text-to-image model OR (preferred when applicable) use to pull a real
# screenshot from the news anchor's source URL.
#
# Why brief, not generate:
#   1. AI-generated LinkedIn images are increasingly recognised and
#      undermine the post's credibility (audit's "AI tells" rule).
#   2. Reference screenshots from real news / earnings decks land
#      harder and cost $0.
#   3. Generation is provider-specific ($0.04/image varies); briefing
#      is provider-agnostic and lets the user pick.
#
# Bias order (encoded in the system prompt):
#   reference_news_image > data_viz > screenshot_quote > infographic > diagram
# ═════════════════════════════════════════════════════════════════════════
IMAGE_BRIEF_SYSTEM = """You are choosing the visual asset for a LinkedIn post by a senior fintech / payments product leader.

Read the post. Decide what visual will land best for THIS specific post — not generic best practice.

Visual kinds (pick one):
  - reference_news_image: a known existing image (e.g. the news article's hero photo, an earnings-deck slide, a public dashboard screenshot) — use this when the post cites a specific source. ALMOST ALWAYS the strongest choice when applicable: it's free, credible, and not AI-generated.
  - data_viz: a chart or numerical comparison — use when the post leans on 2+ specific numbers worth contrasting.
  - screenshot_quote: an excerpt screenshot from a news article / docs / repo — use when one phrase from a source is the post's anchor.
  - infographic: a relationship or trade-off explained visually — use when the post explains a concept (use sparingly; AI infographics look generated).
  - diagram: a flow / system / architecture — use when post explains a process (very rarely the best choice for senior PM posts).

Bias hard toward `reference_news_image` when there's a real source (news_url, earnings deck, press release) that's already credible. Avoid generic AI infographics — they look generated and undermine senior credibility.

When you write the prompt for a generated image:
  - Be specific about subject, composition, palette, and type style.
  - Under 100 words.
  - Editorial / restrained / no clutter (the audience is senior operators, not consumer marketing).
  - Numbers as visual focal points when applicable.
  - Reference the user's name / domain only if a portrait is genuinely warranted (rarely).

Output ONLY valid JSON in this exact shape — no prose, no fences:
{
  "kind": "reference_news_image" | "data_viz" | "screenshot_quote" | "infographic" | "diagram",
  "prompt": "<text-to-image prompt; subject, composition, palette, type style; under 100 words>",
  "composition_notes": "<one line — aspect ratio (default 16:9 for LinkedIn) + the single most important composition rule>",
  "data_anchors": ["<each specific number, claim, or quoted phrase the image must illustrate>"],
  "reference_url": "<URL of the news article / source image — null if none applies>",
  "alt_text": "<one sentence — what the image shows, accessibility-grade>",
  "recommended_provider": "dall-e-3" | "imagen-3" | "midjourney" | "stock_photo" | "screenshot_only",
  "ai_disclosure_recommended": true | false,
  "rationale": "<one line — why this kind for this post>"
}

`recommended_provider`:
  - "screenshot_only" when kind == reference_news_image — no generation needed; user pulls from reference_url.
  - "stock_photo" only when there's a perfect free Unsplash/Pexels match.
  - generator names otherwise.

`ai_disclosure_recommended`:
  - true when the user will generate an AI image — they should add "Made with [provider]" at the end of the post.
  - false when reference_news_image / screenshot_only / stock_photo.
"""


IMAGE_BRIEF_USER_TEMPLATE = """POST (final, after polish):

HOOK:
{hook}

BODY:
{body}

CTA: {cta}
HASHTAGS: {hashtags}

ANGLE: {angle}
COMPANY ANCHOR: {company_name}
NEWS SOURCE URL (if any): {source_url}
NEWS EXCERPT (if any):
{news_excerpt}

USER VOICE NOTES (one line): {tone_directives}
"""


async def image_brief_node(state: LinkedInState) -> dict:
    """Pick visual kind + write the creation brief. ~$0.01 on Sonnet."""
    started = time.perf_counter()

    # 2026-05-12 (G4-2): hard cost-cap pre-check. Image brief is the last
    # LLM call before persist; cheap (~$0.005) but skip it if we're over
    # cap so persist still completes and the user gets a draft row with
    # image_brief=NULL + a clear "skipped_due_to_cost_cap" note.
    if _over_cap(state) or state.get("cost_capped"):
        return {
            "image_brief": None,
            "transcript": [_cost_capped_turn("image_brief", SONNET_MODEL, state)],
        }

    # Pull the news excerpt + URL from chosen knowledge anchor (if any).
    chosen_kid = state.get("chosen_knowledge_id")
    news_excerpt = ""
    source_url = None
    if chosen_kid and state.get("candidate_knowledge"):
        for row in state["candidate_knowledge"]:
            if str(row.get("id") or "") == str(chosen_kid):
                news_excerpt = (row.get("content") or "")[:800]
                source_url = row.get("source_url")
                break

    user_msg = IMAGE_BRIEF_USER_TEMPLATE.format(
        hook=state.get("final_hook") or state.get("draft_v1_hook") or "",
        body=(state.get("final_body") or state.get("draft_v1_body") or "")[:1500],
        cta=state.get("final_cta") or "(none)",
        hashtags=", ".join(state.get("final_hashtags") or state.get("draft_v1_hashtags") or []),
        angle=state.get("chosen_angle") or "industry_analysis",
        company_name=state.get("chosen_company_name") or "(none)",
        source_url=source_url or "(none)",
        news_excerpt=news_excerpt or "(none)",
        tone_directives=(state.get("voice_profile", {}) or {}).get("tone_directives", ""),
    )

    # 2026-05-11 FIX — image_brief_node was silently broken since ship.
    # Used kwargs (user=, response_format=, graph=, node_name=) that don't
    # exist on LLMRouter.ask; **provider_kwargs swallowed them and Anthropic
    # rejected the request. Every linkedin_drafts row before this fix has
    # image_brief = NULL on disk. Aligning to the canonical `ask_json` shape
    # used by the other 4 G4 nodes (pick_angle / draft_v1 / critique / polish).
    router = get_router()
    try:
        parsed, result = await router.ask_json(
            provider="anthropic",
            model=SONNET_MODEL,
            system=IMAGE_BRIEF_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=900,
            temperature=0.4,
            agent_name="g4.image_brief",
        )
        brief = parsed if isinstance(parsed, dict) else {}

        # Light defensive normalisation — never let a malformed brief block persistence.
        brief.setdefault("kind", "reference_news_image" if source_url else "data_viz")
        brief.setdefault("prompt", "")
        brief.setdefault("composition_notes", "")
        brief.setdefault("data_anchors", [])
        brief.setdefault("reference_url", source_url)
        brief.setdefault("alt_text", "")
        brief.setdefault("recommended_provider",
                         "screenshot_only" if brief.get("kind") == "reference_news_image" else "dall-e-3")
        brief.setdefault("ai_disclosure_recommended",
                         brief.get("kind") not in ("reference_news_image", "screenshot_only", "stock_photo"))
        brief.setdefault("rationale", "")

        cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
        latency = int((time.perf_counter() - started) * 1000)

        return {
            "image_brief": brief,
            "cost_usd_total": cost,
            "latency_ms_total": latency,
            "transcript": [_make_turn(
                "image_brief", "anthropic", SONNET_MODEL,
                f"angle={state.get('chosen_angle')} company={state.get('chosen_company_name')}",
                {"kind": brief["kind"], "provider": brief["recommended_provider"]},
                cost, latency,
            )],
        }
    except Exception as e:
        # Image brief is enhancement, not load-bearing — degrade gracefully
        # rather than failing the whole graph.
        logger.exception("image_brief failed; persisting without it")
        return {
            "image_brief": None,
            "transcript": [_make_turn(
                "image_brief", "anthropic", SONNET_MODEL,
                "image_brief request",
                None, 0.0, int((time.perf_counter() - started) * 1000),
                error=str(e),
            )],
        }


# ═════════════════════════════════════════════════════════════════════════
# Node 6 — persist (pure code)
#
# Writes the final draft into linkedin_drafts with status='draft'. The
# user reviews at /linkedin and clicks Approve / Reject / Edit.
# ═════════════════════════════════════════════════════════════════════════
async def persist_node(state: LinkedInState) -> dict:
    """Insert the row. Returns draft_id for downstream APIs."""
    started = time.perf_counter()

    payload = {
        "user_id":              state["user_id"],
        "source_company_id":    state.get("chosen_company_id"),
        # 2026-05-12 (migration 012): denormalise the company name alongside
        # the FK so api/actions.py::_build_linkedin_post_due can read it in
        # a single round-trip on /today. `chosen_company_name` is already
        # in state from node_pick_angle; we just persist it.
        "source_company_name":  state.get("chosen_company_name"),
        "source_knowledge_id":  state.get("chosen_knowledge_id"),
        "angle":                state.get("chosen_angle") or "industry_analysis",
        "hook":                 state.get("final_hook") or state.get("draft_v1_hook") or "",
        "body":                 state.get("final_body") or state.get("draft_v1_body") or "",
        "cta":                  state.get("final_cta"),
        "hashtags":             state.get("final_hashtags") or state.get("draft_v1_hashtags") or [],
        "status":               "draft",
        "critique":             state.get("critique") or {},
        "polish_round":         1,
        "image_brief":          state.get("image_brief"),
    }

    try:
        db = get_supabase()
        result = db.table("linkedin_drafts").insert(payload).execute()
        rows = result.data or []
        if not rows:
            raise RuntimeError("linkedin_drafts insert returned no rows")
        draft_id = str(rows[0]["id"])
    except Exception as e:
        logger.exception("persist failed")
        return {
            "transcript": [_make_turn(
                "persist", "(db)", "(supabase)",
                f"insert linkedin_drafts user={state['user_id']}",
                None, 0.0, int((time.perf_counter() - started) * 1000),
                error=str(e),
            )],
            "error": f"persist: {type(e).__name__}: {e}",
        }

    return {
        "draft_id": draft_id,
        "transcript": [_make_turn(
            "persist", "(db)", "(supabase)",
            f"insert linkedin_drafts user={state['user_id']}",
            {"draft_id": draft_id, "angle": payload["angle"]},
            0.0, int((time.perf_counter() - started) * 1000),
        )],
    }


# ═════════════════════════════════════════════════════════════════════════
# Graph composition (LangGraph)
# ═════════════════════════════════════════════════════════════════════════
def build_g4_graph(checkpointer: Optional[object] = None):
    """
    Compile the LangGraph state machine.

    checkpointer: optional langgraph checkpointer (Postgres-backed in
    production) — when None the graph runs in-memory only. Mirrors
    resume_agents/g2_graph.py's signature so the worker can use the
    same pattern.
    """
    from langgraph.graph import StateGraph, END

    g = StateGraph(LinkedInState)

    g.add_node("pick_angle",   pick_angle_node)
    g.add_node("draft_v1",     draft_v1_node)
    g.add_node("critique",     critique_node)
    g.add_node("polish",       polish_node)
    g.add_node("image_brief",  image_brief_node)
    g.add_node("persist",      persist_node)

    g.set_entry_point("pick_angle")
    g.add_edge("pick_angle",  "draft_v1")
    g.add_edge("draft_v1",    "critique")
    g.add_edge("critique",    "polish")
    g.add_edge("polish",      "image_brief")
    g.add_edge("image_brief", "persist")
    g.add_edge("persist",     END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


# ═════════════════════════════════════════════════════════════════════════
# Top-level runner — used by api/worker.py and one-off CLI smoke tests.
# ═════════════════════════════════════════════════════════════════════════
async def run_g4_graph(
    user_id: str,
    *,
    request_angle: Optional[str] = None,
    request_target_company_id: Optional[str] = None,
    days_back: int = 7,
    knowledge_limit: int = 12,
    max_cost_usd: float = 0.15,
) -> LinkedInState:
    """Run the G4 graph end-to-end for one user.

    1. Loads the user's voice profile (auto-seeds with defaults if missing
       so a brand-new user gets a draft instead of an error).
    2. Pulls recent company_knowledge rows (last `days_back` days), ranked
       by recency. Filters by target_company if pinned.
    3. Loads the user's CV (cv.md if present; future: render from
       profile_master). For now we read cv.md from disk for the seed user.
    4. Compiles + runs the graph.

    Cost control: the cost_usd_total accumulates across nodes; a warning
    is logged if it crosses `max_cost_usd`. We do not abort mid-graph
    because the polish node short-circuits on ship_as_is anyway.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    db = get_supabase()

    # ── voice profile ────────────────────────────────────────────────
    voice_rows = (
        db.table("linkedin_voice_profile")
        .select("*").eq("user_id", user_id).limit(1).execute()
    )
    voice_profile: dict = (voice_rows.data or [{}])[0]
    if not voice_profile:
        # 2026-05-12 (G4-6): neutral cold-start defaults — only the universal
        # anti-AI-tell guardrails. The seed user's persona-specific phrasing
        # (e.g. "plainspoken senior PM, never humble-brags") used to leak
        # here, meaning user #2 got user #1's voice until they ran the
        # voice extractor. Now any new user gets a neutral baseline and the
        # extractor populates user-specific tone the first time it runs.
        # See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §G4-6.
        voice_profile = {
            "tone_directives": (
                "specific, evidence-led, avoids hype; no em-dashes-everywhere "
                "AI tells; never opens with 'I'm thrilled to' / 'Excited to'"
            ),
            "avoid_phrases": [
                "delve", "tapestry", "unpack", "journey",
                "at the end of the day", "a testament to",
                "in today's fast-paced world", "navigate the complexities",
                "in this digital age",
            ],
            "example_posts": [],
            "profile_md": "",
            "_default_voice_in_use": True,  # surfaced so the UI can prompt
                                            # the user to run the extractor.
        }

    # ── candidate knowledge rows ─────────────────────────────────────
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    q = (
        db.table("company_knowledge")
        .select("id, company_id, company_name, section, content, scraped_at, source_url")
        .gte("scraped_at", cutoff)
        .order("scraped_at", desc=True)
        .limit(knowledge_limit)
    )
    if request_target_company_id:
        q = q.eq("company_id", request_target_company_id)
    candidate_knowledge = (q.execute().data) or []

    # ── CV (read from disk for the seed user; future: profile_master) ─
    cv_md = ""
    cv_path = Path(__file__).resolve().parents[1] / "cv.md"
    try:
        cv_md = cv_path.read_text(encoding="utf-8")
    except Exception:
        cv_md = voice_profile.get("profile_md") or ""

    # ── compile + run ────────────────────────────────────────────────
    graph = build_g4_graph()
    state: LinkedInState = {
        "user_id": user_id,
        "voice_profile": voice_profile,
        "candidate_knowledge": candidate_knowledge,
        "cv_md": cv_md,
        "request_angle": request_angle,
        "request_target_company_id": request_target_company_id,
        "transcript": [],
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        # 2026-05-12 (G4-2): hard cap. Nodes pre-check against this and
        # short-circuit (cost_capped=True) rather than overrun.
        "cost_cap_usd": float(max_cost_usd),
        "cost_capped": False,
    }
    final_state: LinkedInState = await graph.ainvoke(state)

    cost = float(final_state.get("cost_usd_total", 0.0) or 0.0)
    if final_state.get("cost_capped"):
        logger.warning(
            f"G4: cost cap fired (${cost:.4f} >= ${max_cost_usd:.2f}) for user={user_id} "
            f"— draft persisted with partial content"
        )
    elif cost > max_cost_usd:
        # Should be rare now that we pre-check, but keep the log for the
        # edge case where one LLM call's actual cost overruns its estimate.
        logger.warning(
            f"G4: cost ${cost:.4f} exceeded cap ${max_cost_usd:.2f} for user={user_id} "
            f"(no pre-check fired — likely a single-call overrun)"
        )
    return final_state
