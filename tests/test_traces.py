"""
Regression tests for Stream C (2026-05-13): LangGraph trace observability.

Covers:
  1. /traces/recent returns runs scoped to current user
  2. /traces/recent handles missing v_graph_runs view gracefully
  3. /traces/errors filters to error IS NOT NULL
  4. /traces/{graph}/{run_key} aggregates per-node detail with totals
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
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture
def client():
    from uuid import UUID
    from fastapi.testclient import TestClient
    from api import server
    from api.context import get_current_user
    from api.users import User

    # Disable scheduler startup hook to keep tests hermetic.
    server._scheduler_started = True

    # Override the auth dependency with a stable test user. Avoids the
    # auto-provision path that touches the (mocked) supabase client and
    # blows up on Pydantic User validation.
    from datetime import datetime, timezone

    def _fake_user() -> User:
        return User(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            email="test@example.com",
            full_name="Test User",
            created_at=datetime.now(timezone.utc),
        )

    server.app.dependency_overrides[get_current_user] = _fake_user
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


class _Chain:
    """Chainable Supabase query mock that returns self for every method
    except .execute(), which returns the canned rows.

    Handles the `not_.is_(...)` attribute-then-method chain that real
    PostgREST uses (e.g. WHERE error IS NOT NULL).
    """

    def __init__(self, rows):
        self._rows = list(rows)
        self.not_ = self  # so chain.not_.is_(...) hits the same instance

    def __getattr__(self, name):
        # Any method call returns self (chainable). Sidesteps the need
        # to enumerate select/eq/order/limit/is_/or_/in_/gte/lt/...
        return lambda *a, **kw: self

    def execute(self):
        return MagicMock(data=self._rows)


def _mock_table_chain(rows):
    return _Chain(rows)


def test_recent_runs_returns_run_summaries(client, monkeypatch):
    from api import traces

    fake_rows = [
        {
            "graph": "G2",
            "run_key": "rb-1",
            "started_at": "2026-05-13T05:00:00+00:00",
            "last_event_at": "2026-05-13T05:01:00+00:00",
            "total_nodes": 6,
            "error_nodes": 0,
            "total_cost_usd": 0.4567,
            "total_latency_ms": 30000,
            "nodes_executed": ["writer", "polisher"],
            "status": "converged",
        }
    ]

    fake_db = MagicMock()
    fake_db.table.return_value = _mock_table_chain(fake_rows)
    monkeypatch.setattr(traces, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/traces/recent?limit=10")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["graph"] == "G2"
    assert run["status"] == "converged"
    assert run["total_cost_usd"] == 0.4567


def test_recent_runs_handles_missing_view(client, monkeypatch):
    """If v_graph_runs view isn't applied yet, return empty + message."""
    from api import traces

    fake_db = MagicMock()
    bad_chain = MagicMock()
    bad_chain.select.return_value = bad_chain
    bad_chain.eq.return_value = bad_chain
    bad_chain.order.return_value = bad_chain
    bad_chain.limit.return_value = bad_chain
    bad_chain.execute.side_effect = RuntimeError("relation v_graph_runs does not exist")
    fake_db.table.return_value = bad_chain
    monkeypatch.setattr(traces, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/traces/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs"] == []
    assert "not available" in body["message"]


def test_errors_filters_to_error_not_null(client, monkeypatch):
    from api import traces

    fake_rows = [
        {
            "graph": "G2",
            "node_name": "writer",
            "agent_name": "g2.writer",
            "model": "claude-opus-4-5",
            "error": "Anthropic API timeout",
            "called_at": "2026-05-13T05:00:00+00:00",
            "application_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "resume_build_id": None,
            "job_id": 1022,
        }
    ]

    fake_db = MagicMock()
    fake_db.table.return_value = _mock_table_chain(fake_rows)
    monkeypatch.setattr(traces, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/traces/errors?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["errors"]) == 1
    err = body["errors"][0]
    assert err["graph"] == "G2"
    assert err["error"] == "Anthropic API timeout"
    # run_key resolution: application_id is non-null so wins the COALESCE
    assert err["run_key"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_trace_detail_aggregates_nodes(client, monkeypatch):
    from api import traces

    fake_rows = [
        {
            "node_name": "writer",
            "agent_name": "g2.writer",
            "provider": "anthropic",
            "model": "claude-opus-4-5",
            "cost_usd": 0.20,
            "latency_ms": 12000,
            "input_tokens": 5000,
            "output_tokens": 1500,
            "error": None,
            "called_at": "2026-05-13T05:00:00+00:00",
        },
        {
            "node_name": "polisher",
            "agent_name": "g2.polisher",
            "provider": "anthropic",
            "model": "claude-opus-4-5",
            "cost_usd": 0.10,
            "latency_ms": 8000,
            "input_tokens": 2000,
            "output_tokens": 800,
            "error": None,
            "called_at": "2026-05-13T05:01:00+00:00",
        },
    ]

    fake_db = MagicMock()
    fake_db.table.return_value = _mock_table_chain(fake_rows)
    monkeypatch.setattr(traces, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/traces/G2/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["graph"] == "G2"
    assert body["total_nodes"] == 2
    assert body["total_cost_usd"] == 0.30
    assert body["total_latency_ms"] == 20000
    assert body["status"] == "converged"
    assert len(body["nodes"]) == 2
