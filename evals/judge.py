"""
evals/judge.py — LLM-as-judge for resume quality.

Reuses agents.llm_router.get_router() so all provider/key/cost telemetry
flows through the same path as the production G2 graph. Do NOT instantiate
provider clients here — that defeats the point of the router.

Public API:
    judge_resume(resume_md, jd, persona, profile, golden=None) -> dict

Returns a structured score across 5 axes plus a pass/fail verdict. The
verdict is intentionally conservative (mean >= 7.5 AND no axis < 6 AND
no hallucinations) so a single regression on any dimension blocks merge.

Design notes:
  - We always ask for JSON via the router's `json_response` flag. Anthropic
    doesn't have a native json_object response_format, so the router falls
    through to text + the judge's own JSON-mode instruction in the system
    prompt. _parse_json_loose() in the router handles fence-stripping if
    the model emits ```json ... ```.
  - The judge model is hardcoded to claude-opus-4-5 because Opus is the
    most reliable evaluator in our internal tests. If you change this,
    update evals/README.md "Why Opus".
  - This module does NOT actually invoke an LLM at import time. All calls
    are inside async judge_resume(). Importing this module is cheap.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────
JUDGE_PROVIDER = "anthropic"
JUDGE_MODEL = "claude-opus-4-5"
JUDGE_MAX_TOKENS = 4096
JUDGE_TEMPERATURE = 0.0  # deterministic-ish; we want stable verdicts across runs

PASS_MEAN_THRESHOLD = 7.5
PASS_MIN_AXIS = 6
AXES = (
    "ats_keyword_coverage",
    "evidence_specificity",
    "persona_fit",
    "hallucination_check",
    "length_discipline",
)


JUDGE_SYSTEM_PROMPT = """You are a rigorous, senior resume-quality auditor evaluating a tailored resume for a specific job. You are NOT a coach — you do not suggest improvements. You score, and you justify each score with a concrete observation.

You will be given:
  1. The candidate's RESUME (markdown).
  2. The JOB DESCRIPTION (jd) including title, company, and the full text.
  3. The COMPANY PERSONA — voice/terminology/seniority signals + an ATS keyword bank.
  4. The CANDIDATE PROFILE — the master CV / experience / certifications. This is the ground truth for what the candidate has actually done.
  5. (Optional) A GOLDEN reference resume — only use this as a calibration anchor for what 8-10 looks like; do NOT penalize the candidate for stylistic divergence.

Score the resume on each of the following 5 axes from 0 to 10 (integer). For each axis, write a one-paragraph rationale citing specific lines or phrases.

  1. ats_keyword_coverage — What percent of the persona's ATS keyword bank appears in the resume, used naturally (not stuffed)? 10 = ~90%+ coverage with natural integration; 6 = ~60% with some awkward placement; 0 = barely any keywords or obvious keyword-stuffing.
  2. evidence_specificity — Does every claim have a concrete metric, scope, or system attached? "Drove growth" is fluff; "drove 23% MAU growth across 4M-user base via 3 A/B tests" is specific. 10 = every bullet has at least one number, system, or scope. 0 = generic verbs throughout.
  3. persona_fit — Does the voice, terminology, and seniority claim match this persona's expectations? Senior PM roles need strategic framing; engineering roles need technical depth. 10 = sounds like an internal candidate. 0 = wrong register entirely.
  4. hallucination_check — Are all claims supported by the candidate profile (or general public knowledge for company/industry context)? Flag any specific claim — a metric, a project name, a system, a credential — that is NOT in the profile. 10 = zero hallucinations. Score 0-5 if you find any unsupported specific claim. List each suspected hallucination explicitly in the rationale.
  5. length_discipline — Is the resume 1-2 pages, no filler, no narrative drift? Estimate page count from word count (~400-500 words/page for resume formatting). 10 = tight 1-2 pages, every line earns its space. 0 = >3 pages or padded with self-praise.

Critical rules:
  - Be skeptical. The cost of a bad resume shipping is higher than the cost of demanding revision.
  - For hallucination_check, treat ANY specific metric, system name, project, employer, or credential not in the profile as a hallucination unless it's well-known public information about the candidate's stated employers.
  - Do not reward effort or generosity. Score what the resume actually delivers.
  - Output ONLY valid JSON in the exact schema below. No prose before or after. No code fences.

Schema:
{
  "axes": {
    "ats_keyword_coverage":  {"score": <int 0-10>, "rationale": "<one paragraph>"},
    "evidence_specificity":  {"score": <int 0-10>, "rationale": "<one paragraph>"},
    "persona_fit":           {"score": <int 0-10>, "rationale": "<one paragraph>"},
    "hallucination_check":   {"score": <int 0-10>, "rationale": "<one paragraph; list every suspected hallucination explicitly>"},
    "length_discipline":     {"score": <int 0-10>, "rationale": "<one paragraph with estimated word count and page count>"}
  },
  "summary": "<2-3 sentence overall assessment, naming the single biggest weakness>",
  "hallucinations_found": ["<verbatim claim 1>", "<verbatim claim 2>", ...]
}
"""


# ─── Public API ───────────────────────────────────────────────────────────
async def judge_resume(
    resume_md: str,
    jd: dict,
    persona: dict,
    profile: dict | str,
    golden: Optional[dict] = None,
) -> dict:
    """
    Score a resume on 5 axes via Claude Opus 4.5.

    Args:
        resume_md: The generated resume in markdown.
        jd: {"title", "company", "description", ...}
        persona: {"voice", "terminology", "seniority", "ats_keyword_bank": [...]}
        profile: master CV. Either a dict {"sections": [...]} or a raw md string.
        golden: optional reference resume {"resume_md": "..."}. Used only as
                a calibration anchor; we do NOT penalize divergence.

    Returns:
        {
          "axes": {axis: {"score": int, "rationale": str}},
          "mean": float,
          "pass": bool,
          "summary": str,
          "hallucinations_found": [str],
          "raw": dict,                 # the raw judge JSON
          "judge": {                   # telemetry
            "provider": "anthropic",
            "model": "claude-opus-4-5",
            "input_tokens": int,
            "output_tokens": int,
            "cost_usd": float,
            "latency_ms": int,
          }
        }

    The function deliberately fails loud if the judge's JSON is malformed —
    a silently-passing eval is worse than a noisy crash.
    """
    from agents.llm_router import get_router

    user_payload = _build_user_payload(resume_md, jd, persona, profile, golden)
    router = get_router()

    result = await router.ask(
        provider=JUDGE_PROVIDER,
        model=JUDGE_MODEL,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_payload}],
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=JUDGE_TEMPERATURE,
        agent_name="evals.judge",
        # The router applies response_format={"type": "json_object"} when
        # json_response=True for OpenAI-compatible providers. Anthropic
        # ignores it — JSON mode is enforced via the system prompt above.
        json_response=True,
    )

    parsed = _parse_judge_response(result.text)
    parsed = _normalize(parsed)

    scores = [parsed["axes"][a]["score"] for a in AXES]
    mean = round(sum(scores) / len(scores), 2)
    halls = parsed.get("hallucinations_found") or []
    min_axis = min(scores)
    passed = (
        mean >= PASS_MEAN_THRESHOLD
        and min_axis >= PASS_MIN_AXIS
        and len(halls) == 0
    )

    return {
        "axes": parsed["axes"],
        "mean": mean,
        "pass": passed,
        "summary": parsed.get("summary", ""),
        "hallucinations_found": halls,
        "raw": parsed,
        "judge": {
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
        },
    }


# ─── Internals ────────────────────────────────────────────────────────────
def _build_user_payload(
    resume_md: str,
    jd: dict,
    persona: dict,
    profile: Any,
    golden: Optional[dict],
) -> str:
    """Assemble the user-message text that gets sent to Opus."""
    profile_str = profile if isinstance(profile, str) else json.dumps(profile, indent=2)

    parts: list[str] = []
    parts.append("=== RESUME (under evaluation) ===\n")
    parts.append(resume_md)
    parts.append("\n\n=== JOB DESCRIPTION ===\n")
    parts.append(json.dumps(jd, indent=2))
    parts.append("\n\n=== COMPANY PERSONA ===\n")
    parts.append(json.dumps(persona, indent=2))
    parts.append("\n\n=== CANDIDATE PROFILE (ground truth — anything not here is a hallucination) ===\n")
    parts.append(profile_str)
    if golden and golden.get("resume_md"):
        parts.append("\n\n=== GOLDEN REFERENCE (calibration only — do not penalize divergence) ===\n")
        parts.append(golden["resume_md"])
    parts.append(
        "\n\nReturn ONLY the JSON object specified in your instructions. "
        "No prose, no code fences, no explanation outside the JSON."
    )
    return "".join(parts)


def _parse_judge_response(text: str) -> dict:
    """
    Parse the judge's response. Reuses the router's loose-JSON helper to
    survive accidental ```json fences or leading prose.
    """
    from agents.llm_router import _parse_json_loose
    if not text or not text.strip():
        raise ValueError("Judge returned empty response")
    return _parse_json_loose(text)


def _normalize(parsed: dict) -> dict:
    """
    Validate the schema and coerce types. Fail loud on missing axes —
    silently zero-filling a missing axis would corrupt the regression gate.
    """
    if "axes" not in parsed or not isinstance(parsed["axes"], dict):
        raise ValueError(f"Judge response missing 'axes' object: {parsed!r}")

    for axis in AXES:
        if axis not in parsed["axes"]:
            raise ValueError(f"Judge response missing axis {axis!r}: {parsed['axes']!r}")
        a = parsed["axes"][axis]
        if not isinstance(a, dict) or "score" not in a:
            raise ValueError(f"Axis {axis!r} malformed: {a!r}")
        # Coerce score to int and clamp to [0, 10] — guard against the
        # judge emitting "8.5" or "9/10".
        try:
            score_int = int(round(float(a["score"])))
        except (TypeError, ValueError):
            raise ValueError(f"Axis {axis!r} score not numeric: {a['score']!r}")
        a["score"] = max(0, min(10, score_int))
        a["rationale"] = str(a.get("rationale", ""))

    parsed.setdefault("summary", "")
    parsed.setdefault("hallucinations_found", [])
    if not isinstance(parsed["hallucinations_found"], list):
        parsed["hallucinations_found"] = [str(parsed["hallucinations_found"])]
    return parsed
