"""
Tests for agents/pattern_analyzer.py + api/analytics.py.

Covers:
  1. analyze_funnel returns zeros for an empty user (graceful empty-state)
  2. find_rejection_patterns respects min_sample (no false positives)
  3. find_rejection_patterns surfaces a >= 1.5x lift cluster
  4. funnel endpoint scopes to user.id (RLS regression)
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


# ── Helpers ────────────────────────────────────────────────────────────


def _mock_db(table_rows: dict[str, list[dict]]):
    """Build a chainable MagicMock that returns the right rows per table."""

    class _Query:
        def __init__(self, table_name: str):
            self._table = table_name
            self._filters: list[tuple] = []

        def select(self, *args, **kw):
            return self

        def eq(self, col, val):
            self._filters.append(("eq", col, val))
            return self

        def order(self, *args, **kw):
            return self

        def limit(self, n):
            return self

        def execute(self):
            rows = table_rows.get(self._table, [])
            # Apply eq filters in-memory so multi-user scoping is testable.
            for op, col, val in self._filters:
                if op == "eq":
                    rows = [r for r in rows if r.get(col) == val or r.get(col) == str(val)]
            return MagicMock(data=list(rows))

    class _Client:
        def __init__(self):
            self._last_table: str | None = None
            self.queries: list[str] = []

        def table(self, name):
            self._last_table = name
            self.queries.append(name)
            return _Query(name)

    return _Client()


# ── 1. Empty funnel ─────────────────────────────────────────────────────


def test_analyze_funnel_empty_user_returns_zeros():
    """A user with no application rows returns a zeroed FunnelSnapshot."""
    from agents.pattern_analyzer import analyze_funnel

    db = _mock_db({"v_application_funnel": []})
    snap = analyze_funnel("11111111-1111-1111-1111-111111111111", db)

    assert snap.applied == 0
    assert snap.responded == 0
    assert snap.interviewed == 0
    assert snap.offered == 0
    assert snap.accepted == 0
    assert snap.total == 0
    assert snap.is_sufficient_data is False
    # All conversion rates safely 0
    for v in snap.conversion_rates.values():
        assert v == 0.0


# ── 2. Min-sample gating ───────────────────────────────────────────────


def test_rejection_patterns_respects_min_sample():
    """5 rejections at small companies with min_sample=10 → no patterns."""
    from agents.pattern_analyzer import find_rejection_patterns

    rows = [
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "company_size_band": "<50",
            "letter_grade": "C",
            "archetype": "IC",
            "legitimacy_tier": "tier3",
            "days_to_rejection": 12,
        }
    ] * 5

    db = _mock_db({"v_rejection_signals": rows})
    patterns = find_rejection_patterns(
        "11111111-1111-1111-1111-111111111111", db, min_sample=10
    )
    # Below min_sample for the overall total → must be empty.
    assert patterns == []


# ── 3. Lift detection ──────────────────────────────────────────────────


def test_rejection_patterns_surface_lift():
    """15 rejections (12 at <50, 3 elsewhere) → <50 emitted with lift >= 1.5.

    Pattern math:
      - 4 distinct company_size_band values (<50, 50-200, 200-1000, 1000+)
        baseline = 1/4 = 0.25
      - <50 has 12 of 15 rejections → rate = 0.8
      - lift = 0.8 / 0.25 = 3.2 >= 1.5
    """
    from agents.pattern_analyzer import find_rejection_patterns

    rows: list[dict] = []
    uid = "11111111-1111-1111-1111-111111111111"
    # 12 small-company rejections
    for _ in range(12):
        rows.append({
            "user_id": uid,
            "company_size_band": "<50",
            "letter_grade": "C",
            "archetype": "IC",
            "legitimacy_tier": "tier3",
            "days_to_rejection": 10,
        })
    # 3 spread across the other 3 size bands
    for band in ("50-200", "200-1000", "1000+"):
        rows.append({
            "user_id": uid,
            "company_size_band": band,
            "letter_grade": "B",
            "archetype": "Manager",
            "legitimacy_tier": "tier1",
            "days_to_rejection": 20,
        })

    db = _mock_db({"v_rejection_signals": rows})
    patterns = find_rejection_patterns(uid, db, min_sample=5, min_lift=1.5)

    # At least one pattern emitted on company_size_band:<50
    size_patterns = [p for p in patterns if p.dimension == "company_size_band"]
    assert len(size_patterns) >= 1
    bad_size = next(p for p in size_patterns if p.value == "<50")
    assert bad_size.n == 12
    assert bad_size.lift >= 1.5
    assert bad_size.avg_days_to_rejection == pytest.approx(10.0, abs=0.5)


# ── 4. User scoping (RLS belt + suspenders) ─────────────────────────────


def test_analyze_funnel_filters_by_user():
    """Funnel query MUST filter by user_id — RLS regression test."""
    from agents.pattern_analyzer import analyze_funnel

    rows = [
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "applied": 8,
            "responded": 3,
            "interviewed": 2,
            "offered": 1,
            "accepted": 0,
            "total": 8,
        },
        {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "applied": 50,
            "responded": 30,
            "interviewed": 20,
            "offered": 10,
            "accepted": 5,
            "total": 50,
        },
    ]
    db = _mock_db({"v_application_funnel": rows})
    snap = analyze_funnel("11111111-1111-1111-1111-111111111111", db)
    # We should see ONLY the first row's numbers, not the second user's.
    assert snap.applied == 8
    assert snap.total == 8
    assert snap.is_sufficient_data is True  # 8 >= 5
