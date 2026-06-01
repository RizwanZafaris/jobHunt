"""
Integration tests for the G8 Offer Evaluation Graph (agents.g8_*).

Covers (per the engineering principles in the roadmap):
  1. offer_parser extracts comp components from a synthetic offer
  2. The full graph persists a row to offer_evaluations with the right shape
  3. Wallet guard: closed parent job → 409
  4. Persona-as-critic: banned keyword in script → confidence downgrade +
     recommendation bump toward "consider"

No live API calls. LLM router + Supabase client are monkey-patched.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("PERPLEXITY_API_KEY", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════
SAMPLE_OFFER_TEXT = """
Dear Candidate,

We're delighted to extend an offer for the Senior Product Manager position
at Acme Payments in San Francisco. Compensation summary:

  - Base salary: $185,000 annual
  - Target bonus: 20% of base
  - Equity grant: $200,000 RSU over 4 years
  - Sign-on bonus: $30,000

We hope you'll accept by next Friday.

Best,
Acme Recruiting
"""


class _MockLLMResult:
    """Mirrors the shape of agents.llm_router.LLMResult."""
    def __init__(self, text: str, *, cost_usd: float = 0.05, latency_ms: int = 200, provider: str = "anthropic", model: str = "claude-opus-4-5"):
        self.text = text
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.provider = provider
        self.model = model


@pytest.fixture
def mock_router():
    """LLM router that returns canned JSON based on the node."""
    class _Router:
        async def ask(self, *, provider, model, system, messages, node="", **kw):
            # Map by node (passed in kwargs by our nodes)
            if node == "market_analyzer":
                return _MockLLMResult(
                    text='{"p25": 280000, "p50": 320000, "p75": 360000, "p90": 410000, '
                         '"sources": ["https://levels.fyi/x", "https://glassdoor.com/y"]}',
                    cost_usd=0.005,
                    latency_ms=400,
                    provider="perplexity",
                    model="sonar",
                )
            if node == "negotiation_strategist":
                return _MockLLMResult(
                    text='{"anchor": 360000, "walkaway": 280000, '
                         '"script": "Thank you for the offer. Based on '
                         'market data showing comparable roles at the p75 '
                         'of $360K total comp, I would like to discuss a '
                         'closer-to-market package. Specifically I am '
                         'looking for $210K base, retaining 20% bonus, '
                         'and a $250K equity grant.", '
                         '"archetype_strategy": "IC"}',
                    cost_usd=0.18,
                    latency_ms=800,
                )
            if node == "risk_detector":
                return _MockLLMResult(
                    text='{"factors": [{"kind": "financial_health", '
                         '"severity": 0.2, "description": "Stable funding", '
                         '"evidence": null, "cite_ids": []}], '
                         '"risk_score": 0.2}',
                    cost_usd=0.05,
                    latency_ms=600,
                    provider="deepseek",
                    model="deepseek-reasoner",
                )
            if node == "synthesizer":
                return _MockLLMResult(
                    text='{"recommendation": "negotiate", "confidence": 0.8, '
                         '"sleep_on_it_score": 0.4, '
                         '"rationale_md": "**Negotiate** — offer is at p50, anchor at p75 is reasonable. Risk score low."}',
                    cost_usd=0.12,
                    latency_ms=700,
                )
            # Default: empty JSON
            return _MockLLMResult(text="{}", cost_usd=0.0, latency_ms=10)

    return _Router()


@pytest.fixture
def mock_supabase():
    """In-memory Supabase client with a recorder dict.

    The .table(...).select(...).eq(...).execute() chain returns a
    canned response. The .table(...).insert(...) calls record their
    payloads into `inserted_rows`.
    """
    class _MockQuery:
        def __init__(self, table_name: str, recorder: dict):
            self._table_name = table_name
            self._recorder = recorder
            self._filters: list[tuple] = []
            self._inserted: dict | None = None
            self._updated: dict | None = None

        def select(self, *args, **kw):
            return self

        def eq(self, col, val):
            self._filters.append(("eq", col, val))
            return self

        def ilike(self, col, val):
            self._filters.append(("ilike", col, val))
            return self

        def limit(self, n):
            return self

        def order(self, *args, **kw):
            return self

        def insert(self, row):
            self._inserted = row
            self._recorder.setdefault("inserted", {}).setdefault(self._table_name, []).append(row)
            return self

        def update(self, row):
            self._updated = row
            self._recorder.setdefault("updated", {}).setdefault(self._table_name, []).append(row)
            return self

        def execute(self):
            recorder = self._recorder
            if self._inserted is not None:
                # Mock insert returning the inserted row + a synthetic id.
                return MagicMock(data=[{**self._inserted, "id": "11111111-1111-1111-1111-111111111111"}])
            if self._updated is not None:
                return MagicMock(data=[self._updated])
            # SELECT path — look up by table_name
            table = recorder.get(self._table_name, [])
            return MagicMock(data=list(table))

    recorder: dict = {
        "applications": [
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "job_id": 42,
                "status": "applied",
                "company": "Acme Payments",
                "role": "Senior Product Manager",
                "level": "Senior",
                "location": "San Francisco, CA",
            }
        ],
        "jobs": [
            {
                "id": 42,
                "user_id": "11111111-1111-1111-1111-111111111111",
                "company": "Acme Payments",
                "title": "Senior Product Manager",
                "posting_closed_at": None,
                "validation_failed": None,
            }
        ],
        "company_personas": [
            {
                "id": "p1",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "company_name": "Acme Payments",
                "banned_keywords": [],
                "success_patterns": ["payments infrastructure"],
            }
        ],
        "company_knowledge": [],
        "offer_evaluations": [],
    }

    class _Client:
        def table(self, name):
            return _MockQuery(name, recorder)

    return _Client(), recorder


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_offer_parser_extracts_comp():
    """Pure-code parser must extract base/bonus/equity/signon correctly."""
    from agents.g8.offer_parser import offer_parser_node

    state = {
        "offer_text": SAMPLE_OFFER_TEXT,
        "application_row": {
            "company": "Acme Payments",
            "role": "Senior Product Manager",
            "level": "Senior",
            "location": "San Francisco, CA",
        },
    }
    patch_dict = await offer_parser_node(state)
    parsed = patch_dict["parsed_offer"]

    # Base = $185K → 185000
    assert parsed.get("base_salary_usd") == 185000.0
    # Bonus = 20% of 185000 = 37000
    assert parsed.get("target_bonus_usd") == pytest.approx(37000.0, abs=1.0)
    # Equity amortized = 200K / 4 = 50000
    assert parsed.get("equity_grant_usd") == 50000.0
    # Sign-on = 30000
    assert parsed.get("sign_on_bonus_usd") == 30000.0
    # Total y1 = 185000 + 37000 + 50000 + 30000 = 302000
    assert parsed.get("total_comp_year_1") == pytest.approx(302000.0, abs=1.0)
    # Currency detected
    assert parsed.get("raw_currency", "usd").lower() == "usd"


@pytest.mark.asyncio
async def test_run_g8_persists_row(mock_router, mock_supabase, monkeypatch):
    """Full graph: hydrate → 5 nodes → persist. Verify offer_evaluations
    row contains the right columns."""
    client, recorder = mock_supabase

    # Patch the lazy supabase import in every relevant module.
    monkeypatch.setattr("db.client.get_supabase", lambda: client, raising=False)

    # Patch the LLM router.
    monkeypatch.setattr("agents.llm_router.get_router", lambda: mock_router, raising=False)

    from agents.g8_io import run_g8_for_offer

    evaluation_id = await run_g8_for_offer(
        user_id="11111111-1111-1111-1111-111111111111",
        application_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        offer_text=SAMPLE_OFFER_TEXT,
        force=False,
    )

    # We get back the synthetic id from the mock
    assert evaluation_id == "11111111-1111-1111-1111-111111111111"

    # Verify an insert happened to offer_evaluations
    inserts = recorder.get("inserted", {}).get("offer_evaluations", [])
    assert len(inserts) == 1
    row = inserts[0]

    # Schema columns are present
    for col in ("user_id", "application_id", "base_salary_usd",
                "total_comp_year_1", "market_p75", "negotiation_anchor",
                "risk_score", "recommendation", "confidence",
                "rationale_md", "total_cost_usd", "cites"):
        assert col in row, f"missing column {col} in persisted row"

    # FK shapes
    assert row["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert row["application_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert row["job_id"] == 42

    # Recommendation came from the synth mock
    assert row["recommendation"] == "negotiate"

    # cites is a list (may be empty in this mock)
    assert isinstance(row["cites"], list)


@pytest.mark.asyncio
async def test_load_open_job_raises_409_on_closed(mock_supabase, monkeypatch):
    """Wallet guard: closed parent job → HTTPException(409).

    Regression test for the OKX-1641 archetype. Hydration runs
    `load_open_job(force=False)` which MUST raise 409 on stale jobs
    before any LLM spend.
    """
    from fastapi import HTTPException

    client, recorder = mock_supabase
    # Mark the job as closed.
    recorder["jobs"][0]["posting_closed_at"] = "2025-01-01T00:00:00Z"

    monkeypatch.setattr("db.client.get_supabase", lambda: client, raising=False)

    from agents.g8_io import run_g8_for_offer

    with pytest.raises(HTTPException) as exc_info:
        await run_g8_for_offer(
            user_id="11111111-1111-1111-1111-111111111111",
            application_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            offer_text=SAMPLE_OFFER_TEXT,
            force=False,
        )
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "posting_closed"
    assert detail.get("cost_label") == "G8:evaluate-offer"


@pytest.mark.asyncio
async def test_synthesizer_downgrades_on_banned_keyword(mock_router, monkeypatch):
    """Persona-as-critic: a banned keyword in the negotiation script must
    downgrade confidence by 0.15 AND bump recommendation toward 'consider'.
    """
    monkeypatch.setattr("agents.llm_router.get_router", lambda: mock_router, raising=False)

    from agents.g8.synthesizer import synthesizer_node

    state = {
        "user_id": "11111111-1111-1111-1111-111111111111",
        "parsed_offer": {
            "total_comp_year_1": 302000,
            "company": "Acme Payments",
            "title": "Senior Product Manager",
        },
        "market_bands": {"p25": 280000, "p50": 320000, "p75": 360000, "p90": 410000},
        "negotiation_script": "Open with mention of payments infrastructure and synergy unlock.",
        "negotiation_anchor": 360000,
        "risk_score": 0.2,
        "risk_factors": [],
        "company_persona": {
            "banned_keywords": ["synergy", "unlock"],  # Both present in script
            "success_patterns": ["payments infrastructure"],
        },
    }
    patch = await synthesizer_node(state)

    # The mock LLM returned recommendation='negotiate' confidence=0.8;
    # the persona downgrade gate should bump to 'consider' AND drop
    # confidence by 0.15 → 0.65.
    assert patch["recommendation"] == "consider"
    assert patch["confidence"] == pytest.approx(0.65, abs=0.01)
    critique = patch.get("persona_critique") or {}
    assert critique.get("downgraded") is True
    assert "synergy" in critique.get("banned_keyword_hits", [])
    assert "unlock" in critique.get("banned_keyword_hits", [])
