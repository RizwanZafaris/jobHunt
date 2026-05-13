"""
agents/g8/offer_parser.py — G8 Node 1: Offer Parser.

Pure-code extraction node (zero LLM cost). Parses compensation components
from free-form offer text using regex heuristics + deterministic logic.

Supported formats:
    $150,000          → 150000 USD
    150K USD          → 150000 USD
    AED 45,000/month  → 540000 AED → USD-converted
    GBP120k base      → 120000 GBP → USD-converted
    RSU 50,000 over 4 years → 50000 equity → 12500/yr amortized

Outputs a `ParsedOffer` dict with values normalised to USD annual.
Equity is amortized over 4 years (grant_total / 4 → equity_grant_usd).

Engineering principles
----------------------
- $0 cost — no LLM call.
- Append parse_warnings for anything ambiguous (missing equity, currency
  detection failed, etc.) so the synthesizer downstream can flag it.
- DOES NOT hit Supabase — pure function on state input.
"""
from __future__ import annotations

import logging
import re
from typing import Final

from agents.g8_state import OfferEvaluationState, ParsedOffer, make_g8_turn

logger = logging.getLogger(__name__)

# Currency conversion → USD (annualized defaults, refresh quarterly).
# These are best-effort static rates; for production a future iteration
# can hit comp_cache.currency_rates_usd if the table is populated.
_CURRENCY_RATES: Final[dict[str, float]] = {
    "usd": 1.0,
    "eur": 1.08,
    "gbp": 1.27,
    "aed": 0.272,
    "inr": 0.012,
    "cad": 0.74,
    "aud": 0.66,
    "sgd": 0.74,
    "jpy": 0.0066,
    "hkd": 0.128,
}

# Regex patterns
_CURRENCY_SYM_RE = re.compile(
    r"(?P<sym>\$|£|€|¥|₹|AED|USD|EUR|GBP|JPY|INR|AED|CAD|AUD|SGD|HKD)\s?"
    r"(?P<num>[0-9][0-9,]*(?:\.[0-9]+)?)\s?(?P<suffix>[kKmM]?)",
    re.IGNORECASE,
)
_K_RE = re.compile(r"(?P<num>[0-9]+(?:\.[0-9]+)?)\s?[kK]\b")
_PERCENT_RE = re.compile(r"(?P<pct>[0-9]+(?:\.[0-9]+)?)\s?%")
_PER_MONTH_RE = re.compile(r"per\s+month|/\s?month|monthly", re.IGNORECASE)


def _sym_to_currency(sym: str) -> str:
    """Map a currency symbol or code to a lowercase ISO code."""
    sym = sym.strip().lower()
    if sym in {"$", "usd"}:
        return "usd"
    if sym in {"£", "gbp"}:
        return "gbp"
    if sym in {"€", "eur"}:
        return "eur"
    if sym in {"¥", "jpy"}:
        return "jpy"
    if sym in {"₹", "inr"}:
        return "inr"
    return sym  # already an ISO code like "aed"


def _amount_from_match(num_str: str, suffix: str) -> float:
    """Resolve "150" / "150,000" / "150K" → float."""
    val = float(num_str.replace(",", ""))
    if suffix and suffix.lower() == "k":
        val *= 1_000
    elif suffix and suffix.lower() == "m":
        val *= 1_000_000
    return val


def _find_amount_near(text: str, label_re: re.Pattern[str], window: int = 60) -> tuple[float | None, str]:
    """Find the first currency amount within `window` chars after the label.

    Returns (amount_in_local_currency, currency_iso). (None, "usd") if no
    match was found.
    """
    m = label_re.search(text)
    if not m:
        return None, "usd"
    start = m.end()
    chunk = text[start : start + window]
    cm = _CURRENCY_SYM_RE.search(chunk)
    if cm:
        amount = _amount_from_match(cm.group("num"), cm.group("suffix") or "")
        currency = _sym_to_currency(cm.group("sym"))
        # If "per month" appears nearby, annualize.
        if _PER_MONTH_RE.search(chunk):
            amount *= 12
        return amount, currency
    # Fall back to bare K-format
    km = _K_RE.search(chunk)
    if km:
        return float(km.group("num")) * 1_000, "usd"
    return None, "usd"


def _to_usd(amount: float | None, currency: str) -> float | None:
    if amount is None:
        return None
    rate = _CURRENCY_RATES.get(currency.lower(), 1.0)
    return round(amount * rate, 2)


async def offer_parser_node(state: OfferEvaluationState) -> dict:
    """Parse compensation components from the raw offer text.

    Reads `state.offer_text` and `state.application_row` (if hydrated)
    for company / role context. Returns a state patch with `parsed_offer`,
    `total_comp_year_1`, and a transcript turn.
    """
    text = state.get("offer_text") or ""
    if not text.strip():
        return {
            "parsed_offer": ParsedOffer(parse_warnings=["empty offer_text"]),
            "transcript": [
                make_g8_turn("offer_parser", error="empty offer_text")
            ],
        }

    warnings: list[str] = []

    # --- Base salary ----------------------------------------------------
    base_amount, base_currency = _find_amount_near(
        text, re.compile(r"\b(?:base|annual\s+salary|salary|gross\s+pay)\b", re.IGNORECASE)
    )
    if base_amount is None:
        # Fallback: first currency amount in the text
        m = _CURRENCY_SYM_RE.search(text)
        if m:
            base_amount = _amount_from_match(m.group("num"), m.group("suffix") or "")
            base_currency = _sym_to_currency(m.group("sym"))
            warnings.append("base salary inferred from first currency mention")
        else:
            warnings.append("no base salary detected")
    base_usd = _to_usd(base_amount, base_currency)

    # --- Target bonus ---------------------------------------------------
    # Order of preference:
    #   1. "X% bonus" or "bonus ... X%" — percentage of base
    #   2. "bonus $X" — absolute dollar amount near the FIRST "bonus" mention
    #      that is NOT prefixed by "sign-on" / "signing" / "joining"
    bonus_usd: float | None = None
    # Pattern 1a: "X% (target) bonus"
    pct_then = re.search(
        r"(?P<pct>[0-9]+(?:\.[0-9]+)?)\s?%\s+(?:target\s+)?bonus",
        text,
        re.IGNORECASE,
    )
    # Pattern 1b: "(target) bonus: X%" or "bonus of X%"  — % within 40 chars
    bonus_then_pct = re.search(
        r"\b(?:target\s+)?bonus[^.\n]{0,40}?(?P<pct>[0-9]+(?:\.[0-9]+)?)\s?%",
        text,
        re.IGNORECASE,
    )
    if (pct_then or bonus_then_pct) and base_usd:
        m = pct_then or bonus_then_pct
        bonus_usd = round(base_usd * float(m.group("pct")) / 100, 2)
    else:
        # Pattern 2: absolute "$X bonus" style, but skip sign-on bonus
        # matches by anchoring on bonus mentions that are NOT preceded by
        # sign/signing/joining within ~10 chars.
        for m in re.finditer(r"\bbonus\b", text, re.IGNORECASE):
            preceding = text[max(0, m.start() - 20) : m.start()].lower()
            if any(k in preceding for k in ("sign-on", "sign on", "signing", "joining")):
                continue
            sub_text = text[max(0, m.start() - 20) : m.start() + 60]
            bonus_amt, bonus_cur = _find_amount_near(sub_text, re.compile(r"\bbonus\b", re.IGNORECASE))
            if bonus_amt is not None:
                bonus_usd = _to_usd(bonus_amt, bonus_cur)
                break

    # --- Equity grant ---------------------------------------------------
    equity_total_usd: float | None = None
    eq_amt, eq_cur = _find_amount_near(text, re.compile(r"\b(?:equity|rsu|stock\s+grant|options?)\b", re.IGNORECASE))
    if eq_amt is not None:
        equity_total_usd = _to_usd(eq_amt, eq_cur)
    else:
        warnings.append("no equity grant detected")
    equity_amortized: float | None = (
        round(equity_total_usd / 4, 2) if equity_total_usd else None
    )

    # --- Sign-on bonus --------------------------------------------------
    signon_amt, signon_cur = _find_amount_near(text, re.compile(r"\b(?:sign[\s\-]?on|signing\s+bonus|joining\s+bonus)\b", re.IGNORECASE))
    signon_usd = _to_usd(signon_amt, signon_cur)

    # --- Total comp Y1 --------------------------------------------------
    total_y1 = sum(
        v for v in [base_usd, bonus_usd, equity_amortized, signon_usd] if v
    )
    total_y1 = round(total_y1, 2) if total_y1 else None

    # --- Avg over 4 yr (excludes signon after year 1) -------------------
    total_4yr_avg: float | None = None
    if base_usd is not None:
        total_4yr_avg = round(
            (base_usd or 0)
            + (bonus_usd or 0)
            + (equity_amortized or 0),
            2,
        )

    # --- Metadata from application_row ----------------------------------
    app_row = state.get("application_row") or {}
    parsed: ParsedOffer = ParsedOffer(
        base_salary_usd=base_usd,
        target_bonus_usd=bonus_usd,
        equity_grant_usd=equity_amortized,
        sign_on_bonus_usd=signon_usd,
        other_benefits_usd=None,
        total_comp_year_1=total_y1,
        total_comp_4yr_avg=total_4yr_avg,
        raw_currency=base_currency,
        conversion_rate_to_usd=_CURRENCY_RATES.get(base_currency, 1.0),
        level=app_row.get("level"),
        title=app_row.get("role") or app_row.get("title"),
        location=app_row.get("location"),
        company=app_row.get("company"),
        parse_warnings=warnings,
        raw_text_excerpt=text[:400],
    )

    logger.info(
        "[g8.offer_parser] base=%s bonus=%s equity_amort=%s total_y1=%s warnings=%d",
        base_usd, bonus_usd, equity_amortized, total_y1, len(warnings),
    )

    return {
        "parsed_offer": parsed,
        "total_comp_year_1": total_y1,
        "transcript": [
            make_g8_turn(
                "offer_parser",
                input_summary=f"text_len={len(text)}",
                output={"total_y1": total_y1, "warnings_count": len(warnings)},
                cost_usd=0.0,
                latency_ms=0,
            )
        ],
    }
