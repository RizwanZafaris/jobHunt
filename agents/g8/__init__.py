"""G8 Offer Evaluation Graph — node package.

Public entry point lives in `agents/g8_io.py`. This package contains the
5 graph nodes; they are pure async functions of (state) -> dict patch.

    from agents.g8_io import run_g8_for_offer
    eval_id = await run_g8_for_offer(user_id, application_id, offer_text)
"""
from __future__ import annotations

from agents.g8.offer_parser import offer_parser_node
from agents.g8.market_analyzer import market_analyzer_node
from agents.g8.negotiation_strategist import negotiation_strategist_node
from agents.g8.risk_detector import risk_detector_node
from agents.g8.synthesizer import synthesizer_node

__all__ = [
    "offer_parser_node",
    "market_analyzer_node",
    "negotiation_strategist_node",
    "risk_detector_node",
    "synthesizer_node",
]
