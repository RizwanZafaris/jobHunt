"""Tests for agents.legitimacy_agent — Tier 2 §4.3 legitimacy v1.

Covers:
  * Each signal scorer in isolation (with mocked deps).
  * Weighted composite math.
  * Tier threshold boundaries.
  * 24h cache behaviour for news + URL signals.
  * Wallet guard refuses stale jobs.

HTTP and Perplexity are mocked — these run fast and offline.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from agents.legitimacy_agent import (
    AGE_DEAD_DAYS,
    AGE_FRESH_DAYS,
    LegitimacyResult,
    NEWS_CACHE_TTL_HOURS,
    REPOST_HIGH_COUNT_SCORE,
    SignalContribution,
    TIER_CAUTION_MIN,
    TIER_LEGITIMATE_MIN,
    URL_CACHE_TTL_HOURS,
    WEIGHT_AGE,
    WEIGHT_NEWS,
    WEIGHT_REPOST,
    WEIGHT_URL,
    _classify_news,
    _is_fresh_cache,
    _parse_dt,
    _score_age,
    _score_news,
    _score_repost,
    _score_url,
    _tier_for,
    _url_score_for_status,
    score_legitimacy,
)


_USER = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ─── Signal 1 — Posting age ──────────────────────────────────────────────
class TestSignalAge:
    def test_fresh_posting_full_score(self):
        # Posted yesterday → score 1.0
        c = _score_age(_iso(_NOW - timedelta(days=1)))
        assert c.normalised == 1.0
        assert c.weight == WEIGHT_AGE
        assert c.contribution == WEIGHT_AGE

    def test_exactly_fresh_boundary(self):
        # < AGE_FRESH_DAYS → 1.0
        c = _score_age(_iso(_NOW - timedelta(days=AGE_FRESH_DAYS - 1)))
        assert c.normalised == 1.0

    def test_dead_posting_zero_score(self):
        # 60 days old → 0.0
        c = _score_age(_iso(_NOW - timedelta(days=AGE_DEAD_DAYS + 1)))
        assert c.normalised == 0.0
        assert c.contribution == 0.0

    def test_linear_decay_midpoint(self):
        # Halfway between 7d and 60d → ~0.5
        mid = (AGE_FRESH_DAYS + AGE_DEAD_DAYS) / 2
        c = _score_age(_iso(_NOW - timedelta(days=mid)))
        assert 0.45 <= c.normalised <= 0.55

    def test_none_discovered_at_neutral(self):
        c = _score_age(None)
        assert c.normalised == 0.5

    def test_unparseable_discovered_at_neutral(self):
        c = _score_age("not-a-date")
        assert c.normalised == 0.5

    def test_z_suffix_iso_parsed(self):
        c = _score_age((_NOW - timedelta(days=2)).isoformat().replace(
            "+00:00", "Z"
        ))
        assert c.normalised == 1.0


# ─── Signal 2 — URL reachability ─────────────────────────────────────────
class TestSignalUrl:
    def test_url_score_for_200(self):
        assert _url_score_for_status(200) == 1.0

    def test_url_score_for_302(self):
        assert _url_score_for_status(302) == 1.0

    def test_url_score_for_404(self):
        assert _url_score_for_status(404) == 0.5

    def test_url_score_for_410(self):
        assert _url_score_for_status(410) == 0.0

    def test_url_score_for_500(self):
        assert _url_score_for_status(503) == 0.0

    def test_url_score_for_timeout(self):
        # 0 = timeout / DNS error
        assert _url_score_for_status(0) == 0.0

    def test_url_score_other_4xx_partial(self):
        # 401 paywall → ambiguous
        assert _url_score_for_status(401) == 0.3

    @pytest.mark.asyncio
    async def test_no_url_returns_zero(self):
        c = await _score_url(url=None)
        assert c.normalised == 0.0

    @pytest.mark.asyncio
    async def test_cache_hit_skips_probe(self):
        cached = {
            "status": 200,
            "cached_at": _iso(_NOW - timedelta(hours=1)),
        }
        # Patch _probe_url; if called, the test fails.
        with patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(side_effect=AssertionError("should not probe")),
        ):
            c = await _score_url(url="https://x", cached=cached)
        assert c.normalised == 1.0

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_probe(self):
        cached = {
            "status": 200,
            "cached_at": _iso(_NOW - timedelta(hours=URL_CACHE_TTL_HOURS + 1)),
        }
        with patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(return_value=404),
        ):
            c = await _score_url(url="https://x", cached=cached)
        assert c.normalised == 0.5  # 404

    @pytest.mark.asyncio
    async def test_live_probe_410(self):
        with patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(return_value=410),
        ):
            c = await _score_url(url="https://x")
        assert c.normalised == 0.0


# ─── Signal 3 — Repost pattern ───────────────────────────────────────────
class TestSignalRepost:
    def _mock_db(self, count: int):
        """Build a Supabase-PostgREST-style chained-mock returning .count."""
        # Each chained call returns the same mock; .execute() returns a
        # response whose `.count` attribute is `count`.
        m = MagicMock()
        chain = MagicMock()
        for method in ("table", "select", "eq", "ilike", "gte", "neq", "limit"):
            getattr(chain, method).return_value = chain
        resp = MagicMock()
        resp.count = count
        resp.data = []
        chain.execute.return_value = resp
        m.table.return_value = chain
        return m

    def test_zero_duplicates_full_score(self):
        db = self._mock_db(0)
        c = _score_repost(
            db=db, user_id=_USER, company="Acme",
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == 1.0
        assert c.raw_value["duplicate_count"] == 0

    def test_one_duplicate_mild_flag(self):
        db = self._mock_db(1)
        c = _score_repost(
            db=db, user_id=_USER, company="Acme",
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == 0.7

    def test_two_duplicates_moderate_flag(self):
        db = self._mock_db(2)
        c = _score_repost(
            db=db, user_id=_USER, company="Acme",
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == 0.4

    def test_high_count_strong_flag(self):
        db = self._mock_db(5)
        c = _score_repost(
            db=db, user_id=_USER, company="Acme",
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == REPOST_HIGH_COUNT_SCORE
        assert c.normalised == 0.1

    def test_missing_company_neutral(self):
        db = MagicMock()
        c = _score_repost(
            db=db, user_id=_USER, company=None,
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == 0.5
        db.table.assert_not_called()  # short-circuited

    def test_db_error_fails_open(self):
        # If the query blows up, fail-open at 0.5 — don't punish jobs for
        # an infrastructure hiccup.
        db = MagicMock()
        db.table.side_effect = RuntimeError("db down")
        c = _score_repost(
            db=db, user_id=_USER, company="Acme",
            title="Senior PM", self_job_id=42,
        )
        assert c.normalised == 0.5


# ─── Signal 5 — Recent news ───────────────────────────────────────────────
class TestSignalNews:
    def test_classify_positive_hiring(self):
        summary = (
            "Acme Inc announced today that they are hiring across product "
            "and engineering, with open roles in Singapore. The company "
            "raised $200m in their Series D round last month."
        )
        verdict, score = _classify_news(summary)
        assert verdict == "positive"
        assert score == 1.0

    def test_classify_negative_layoffs(self):
        summary = (
            "Acme Corp announced layoffs of 200 engineers this week as "
            "the company restructures after a difficult quarter. A "
            "hiring freeze is in effect across all departments."
        )
        verdict, score = _classify_news(summary)
        assert verdict == "negative"
        assert score == 0.3

    def test_classify_neutral_no_keywords(self):
        # 60 chars but no positive/negative keywords → neutral
        summary = (
            "Acme Corp released a financial statement showing steady "
            "revenue growth and stable margins quarter over quarter."
        )
        verdict, score = _classify_news(summary)
        assert verdict == "neutral"
        assert score == 0.7

    def test_classify_no_signal_empty(self):
        verdict, score = _classify_news("")
        assert verdict == "no_signal"
        assert score == 0.5

    def test_classify_no_signal_short(self):
        verdict, score = _classify_news("Nothing")
        assert verdict == "no_signal"
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_news_cache_hit_skips_api(self):
        cached = {
            "summary": "Acme is hiring globally for product roles",
            "verdict": "positive",
            "normalised": 1.0,
            "citations": [{"url": "https://acme.com/news"}],
            "cached_at": _iso(_NOW - timedelta(hours=1)),
        }
        with patch(
            "agents.legitimacy_agent._call_perplexity_news",
            new=AsyncMock(side_effect=AssertionError("should not call")),
        ):
            contrib, cost = await _score_news(
                company="Acme",
                role="Senior PM",
                cached=cached,
                user_id=_USER,
            )
        assert contrib.normalised == 1.0
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_news_cache_stale_triggers_call(self):
        cached = {
            "summary": "old",
            "verdict": "positive",
            "normalised": 1.0,
            "cached_at": _iso(_NOW - timedelta(hours=NEWS_CACHE_TTL_HOURS + 1)),
        }
        with patch(
            "agents.legitimacy_agent._call_perplexity_news",
            new=AsyncMock(return_value=(
                "Acme announced layoffs and a hiring freeze.",
                [{"url": "https://news.com/a"}],
                0.005,
            )),
        ):
            contrib, cost = await _score_news(
                company="Acme", role="PM",
                cached=cached, user_id=_USER,
            )
        assert contrib.normalised == 0.3
        assert cost == 0.005
        # Citations from the fresh call appear in the raw_value blob.
        assert contrib.raw_value["citations"][0]["url"] == "https://news.com/a"

    @pytest.mark.asyncio
    async def test_news_missing_company_neutral(self):
        contrib, cost = await _score_news(
            company=None, role="PM", cached=None, user_id=_USER,
        )
        assert contrib.normalised == 0.5
        assert cost == 0.0


# ─── Cache freshness helper ──────────────────────────────────────────────
class TestCacheFresh:
    def test_fresh(self):
        assert _is_fresh_cache(
            _iso(_NOW - timedelta(hours=1)), ttl_hours=24,
        )

    def test_stale(self):
        assert not _is_fresh_cache(
            _iso(_NOW - timedelta(hours=25)), ttl_hours=24,
        )

    def test_none(self):
        assert not _is_fresh_cache(None, ttl_hours=24)

    def test_garbage_returns_false(self):
        assert not _is_fresh_cache("not-a-date", ttl_hours=24)


# ─── Tier mapping (3.4.2 threshold boundaries) ───────────────────────────
class TestTierThresholds:
    def test_legitimate_at_threshold(self):
        assert _tier_for(TIER_LEGITIMATE_MIN) == "legitimate"
        assert _tier_for(0.85) == "legitimate"
        assert _tier_for(1.0) == "legitimate"

    def test_caution_just_below_legitimate(self):
        assert _tier_for(TIER_LEGITIMATE_MIN - 0.001) == "caution"

    def test_caution_at_threshold(self):
        assert _tier_for(TIER_CAUTION_MIN) == "caution"
        assert _tier_for(0.65) == "caution"

    def test_suspicious_just_below_caution(self):
        assert _tier_for(TIER_CAUTION_MIN - 0.001) == "suspicious"

    def test_suspicious_zero(self):
        assert _tier_for(0.0) == "suspicious"


# ─── Composite math (4 signals, equal weights) ────────────────────────────
class TestCompositeMath:
    def test_all_max_yields_one(self):
        # 4 × 1.0 × 0.25 = 1.0 → legitimate
        contribs = [
            SignalContribution(raw_value=None, normalised=1.0, weight=w, contribution=w)
            for w in (WEIGHT_AGE, WEIGHT_URL, WEIGHT_REPOST, WEIGHT_NEWS)
        ]
        total = round(sum(c.contribution for c in contribs), 4)
        assert total == 1.0
        assert _tier_for(total) == "legitimate"

    def test_all_min_yields_zero(self):
        contribs = [
            SignalContribution(raw_value=None, normalised=0.0, weight=w, contribution=0.0)
            for w in (WEIGHT_AGE, WEIGHT_URL, WEIGHT_REPOST, WEIGHT_NEWS)
        ]
        total = round(sum(c.contribution for c in contribs), 4)
        assert total == 0.0
        assert _tier_for(total) == "suspicious"

    def test_mixed_lands_caution(self):
        # age=1.0, url=0.5, repost=0.7, news=0.5 → 0.25+0.125+0.175+0.125 = 0.675
        c1 = SignalContribution(None, 1.0, WEIGHT_AGE, 0.25)
        c2 = SignalContribution(None, 0.5, WEIGHT_URL, 0.125)
        c3 = SignalContribution(None, 0.7, WEIGHT_REPOST, 0.175)
        c4 = SignalContribution(None, 0.5, WEIGHT_NEWS, 0.125)
        total = round(c1.contribution + c2.contribution + c3.contribution + c4.contribution, 4)
        assert abs(total - 0.675) < 1e-6
        assert _tier_for(total) == "caution"

    def test_weights_sum_to_one(self):
        assert abs(WEIGHT_AGE + WEIGHT_URL + WEIGHT_REPOST + WEIGHT_NEWS - 1.0) < 1e-6


# ─── End-to-end score_legitimacy with full mocks ──────────────────────────
class _SupabaseFake:
    """Tiny chainable supabase mock with selectable response payloads."""

    def __init__(self, *, job_row: dict, repost_count: int = 0):
        self._job_row = job_row
        self._repost_count = repost_count
        self.updates: list[dict] = []

    def table(self, name: str) -> "_SupabaseFake":
        self._name = name
        self._builder = _ChainFake(self)
        return self._builder


class _ChainFake:
    def __init__(self, parent: _SupabaseFake):
        self.parent = parent
        self._is_count_query = False
        self._is_update = False
        self._update_data: Optional[dict] = None

    def select(self, *a, **kw):
        if kw.get("count") == "exact":
            self._is_count_query = True
        return self

    def update(self, data: dict):
        self._is_update = True
        self._update_data = data
        return self

    def eq(self, *a, **kw): return self
    def neq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def ilike(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def insert(self, *a, **kw): return self

    def execute(self):
        resp = MagicMock()
        if self._is_update:
            self.parent.updates.append(self._update_data or {})
            resp.data = [self._update_data]
            resp.count = None
            return resp
        if self._is_count_query:
            resp.count = self.parent._repost_count
            resp.data = []
            return resp
        # Default: it's the initial job fetch.
        resp.data = [self.parent._job_row]
        resp.count = None
        return resp


@pytest.mark.asyncio
class TestScoreLegitimacyEndToEnd:
    async def test_all_signals_max_yields_legitimate(self):
        """Fresh posting + URL 200 + no reposts + positive news → legitimate."""
        job_row = {
            "id": 1,
            "user_id": str(_USER),
            "title": "Senior Product Manager",
            "company": "Acme",
            "url": "https://acme.com/jobs/1",
            "discovered_at": _iso(_NOW - timedelta(days=2)),
            "legitimacy_signals": {},
            "posting_closed_at": None,
            "validation_failed": None,
        }
        fake = _SupabaseFake(job_row=job_row, repost_count=0)

        with patch(
            "db.client.get_supabase",
            new=lambda: fake,
        ), patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(return_value=200),
        ), patch(
            "agents.legitimacy_agent._call_perplexity_news",
            new=AsyncMock(return_value=(
                "Acme is hiring globally for product and engineering "
                "roles, and just raised $200m in Series D funding.",
                [{"url": "https://news.com/acme"}],
                0.005,
            )),
        ):
            result = await score_legitimacy(
                job_id=1, user_id=_USER, force=False,
            )

        assert isinstance(result, LegitimacyResult)
        assert result.score == 1.0
        assert result.tier == "legitimate"
        assert result.cost_usd == 0.005
        # Persistence path was exercised.
        assert any(
            u.get("legitimacy_tier") == "legitimate" for u in fake.updates
        )
        assert any(
            u.get("legitimacy_score") == 1.0 for u in fake.updates
        )

    async def test_dead_url_old_posting_yields_suspicious(self):
        job_row = {
            "id": 2,
            "user_id": str(_USER),
            "title": "Junior PM",
            "company": "Acme",
            "url": "https://acme.com/jobs/2",
            "discovered_at": _iso(_NOW - timedelta(days=90)),  # 0.0
            "legitimacy_signals": {},
            "posting_closed_at": None,
            "validation_failed": None,
        }
        fake = _SupabaseFake(job_row=job_row, repost_count=5)  # 0.1

        with patch(
            "db.client.get_supabase", new=lambda: fake,
        ), patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(return_value=410),  # 0.0
        ), patch(
            "agents.legitimacy_agent._call_perplexity_news",
            new=AsyncMock(return_value=(
                "Acme announced massive layoffs and a hiring freeze.",
                [],
                0.005,
            )),  # 0.3
        ):
            result = await score_legitimacy(
                job_id=2, user_id=_USER, force=False,
            )

        # 0.0*0.25 + 0.0*0.25 + 0.1*0.25 + 0.3*0.25 = 0.1
        assert result.score <= 0.15
        assert result.tier == "suspicious"

    async def test_force_bypasses_news_cache(self):
        """With force=True the cached news entry is ignored."""
        job_row = {
            "id": 3,
            "user_id": str(_USER),
            "title": "PM",
            "company": "Acme",
            "url": "https://acme.com/jobs/3",
            "discovered_at": _iso(_NOW - timedelta(days=1)),
            "legitimacy_signals": {
                "news": {
                    "summary": "old hiring news",
                    "verdict": "positive",
                    "normalised": 1.0,
                    "cached_at": _iso(_NOW - timedelta(minutes=10)),
                },
            },
            "posting_closed_at": None,
            "validation_failed": None,
        }
        fake = _SupabaseFake(job_row=job_row, repost_count=0)
        spy = AsyncMock(return_value=(
            "Acme just announced layoffs.", [], 0.005,
        ))

        with patch(
            "db.client.get_supabase", new=lambda: fake,
        ), patch(
            "agents.legitimacy_agent._probe_url",
            new=AsyncMock(return_value=200),
        ), patch(
            "agents.legitimacy_agent._call_perplexity_news", new=spy,
        ):
            result = await score_legitimacy(
                job_id=3, user_id=_USER, force=True,
            )

        # The cached "positive" was ignored — the fresh call returned a
        # "negative" summary, so news contribution is 0.3 * 0.25 = 0.075.
        spy.assert_awaited_once()
        assert result.cost_usd == 0.005
        # Composite: 1.0 + 1.0 + 1.0 + 0.3 -> /4 = 0.825
        assert result.tier in ("legitimate", "caution")

    async def test_unknown_job_raises(self):
        empty = _SupabaseFake(job_row={}, repost_count=0)

        class _EmptyChain(_ChainFake):
            def execute(self):
                resp = MagicMock()
                resp.data = []
                resp.count = None
                return resp

        empty.table = lambda *a, **kw: _EmptyChain(empty)

        with patch("db.client.get_supabase", new=lambda: empty):
            with pytest.raises(ValueError, match="job_not_found"):
                await score_legitimacy(
                    job_id=999, user_id=_USER, force=False,
                )


# ─── Wallet guard refuses stale jobs (load_open_job integration) ─────────
class TestWalletGuard:
    """The /workspace/{id}/legitimacy-check endpoint wraps with load_open_job.

    We test the guard helper directly — it's the same guard used by the
    G2 resume builder, so we don't need a full HTTP roundtrip.
    """

    def test_load_open_job_refuses_closed(self):
        from fastapi import HTTPException

        from api._job_guards import load_open_job

        closed_row = {
            "id": 7,
            "user_id": str(_USER),
            "title": "PM",
            "company": "Acme",
            "posting_closed_at": _iso(_NOW),
            "validation_failed": None,
        }

        class _Db:
            def table(self, *a, **kw): return self
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            def execute(self):
                r = MagicMock(); r.data = [closed_row]; return r

        with pytest.raises(HTTPException) as exc:
            load_open_job(
                _Db(),
                job_id=7,
                user_id=_USER,
                force=False,
                cost_label="legitimacy check (~$0.005)",
            )
        assert exc.value.status_code == 409
        # Cost label flows through to the structured error body.
        assert exc.value.detail.get("cost_label", "").startswith(
            "legitimacy check"
        )

    def test_load_open_job_force_bypasses(self):
        from api._job_guards import load_open_job

        closed_row = {
            "id": 7,
            "user_id": str(_USER),
            "title": "PM",
            "company": "Acme",
            "posting_closed_at": _iso(_NOW),
        }

        class _Db:
            def table(self, *a, **kw): return self
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            def execute(self):
                r = MagicMock(); r.data = [closed_row]; return r

        # Should NOT raise.
        row = load_open_job(
            _Db(),
            job_id=7,
            user_id=_USER,
            force=True,
            cost_label="legitimacy check (~$0.005)",
        )
        assert row["id"] == 7
