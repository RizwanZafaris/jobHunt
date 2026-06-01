"""
Regression tests for the LinkedIn API response shapes (2026-05-13).

User-visible bugs that drove these tests:
  - /linkedin page showed "Drafts (0)" even though 2 rows existed in
    `linkedin_drafts`. Cause: dashboard read `data.drafts` but the
    backend returns `{items, total, limit, offset}`.
  - Header showed "Cadence target: undefined/week". Cause: dashboard
    read `schedule.postsPerWeek` (camelCase) but the backend returns
    `posts_per_week` (snake_case).

These tests pin the actual backend response shape so future schema
drift will fail the test rather than silently bricking the dashboard.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")


class _Chain:
    """Chainable Supabase mock that returns canned rows on .execute()."""

    def __init__(self, rows, count=None):
        self._rows = rows
        self._count = count
        self.not_ = self

    def __getattr__(self, name):
        return lambda *a, **kw: self

    def execute(self):
        return MagicMock(data=list(self._rows), count=self._count)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api import server
    from api.context import get_current_user
    from api.users import User

    server._scheduler_started = True

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


def test_drafts_endpoint_returns_items_shape(client, monkeypatch):
    """Pin the shape: backend returns {items, total, limit, offset} —
    the dashboard fetcher relies on `data.items`. Future drift to
    `{drafts: …}` or `[…]` would silently break the page."""
    from api import linkedin as api_linkedin

    fake_drafts = [
        {"id": "draft-1", "hook": "Hook 1", "status": "draft"},
        {"id": "draft-2", "hook": "Hook 2", "status": "draft"},
    ]
    fake_db = MagicMock()
    fake_db.table.return_value = _Chain(fake_drafts, count=2)
    monkeypatch.setattr(api_linkedin, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/linkedin/drafts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Contract check — dashboard depends on these keys.
    assert "items" in body, f"backend must return `items`, got {list(body.keys())}"
    assert "total" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 2
    assert body["items"][0]["hook"] == "Hook 1"


def test_schedule_endpoint_returns_snake_case(client, monkeypatch):
    """Pin the shape: backend returns posts_per_week (snake_case) —
    the dashboard fetcher must map to camelCase or use this key.
    Earlier draft of fetchLinkedInSchedule looked for postsPerWeek
    and produced "Cadence target: undefined/week"."""
    from api import linkedin as api_linkedin

    fake_slots = [
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "day_of_week": 1,
            "time_of_day": "09:00",
            "posts_per_week": 3,
        }
    ]
    fake_db = MagicMock()
    fake_db.table.return_value = _Chain(fake_slots)
    monkeypatch.setattr(api_linkedin, "get_supabase", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        api_linkedin, "_next_scheduled_slot", lambda uid: None, raising=False
    )

    resp = client.get("/linkedin/posting-schedule")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Backend MUST return posts_per_week (snake_case) — dashboard maps it.
    assert "posts_per_week" in body, f"backend must return `posts_per_week`, got {list(body.keys())}"
    assert body["posts_per_week"] == 3
    assert "slots" in body
    assert "next_slot" in body
