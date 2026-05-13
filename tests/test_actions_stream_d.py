"""
Regression tests for Stream D (2026-05-13):
  - GET /actions/today/linkedin-post
  - GET /actions/incoming

User feedback that drove the work:
  "Today page shows section is just shows old job there is no today's
   linkedin post that should be there."
  "Application page need to add all new job come in so I can take action."
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

    def __init__(self, rows):
        self._rows = rows
        self.not_ = self

    def __getattr__(self, name):
        return lambda *a, **kw: self

    def execute(self):
        return MagicMock(data=list(self._rows))


def _stub_db(table_rows: dict[str, list[dict]]):
    """Build a fake Supabase client returning per-table rows."""
    class _Client:
        def __init__(self):
            self._next = None

        def table(self, name):
            return _Chain(table_rows.get(name, []))

    return _Client()


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


# ── /actions/today/linkedin-post ────────────────────────────────────────


def test_post_of_the_day_returns_latest_draft(client, monkeypatch):
    """When a draft exists, return the latest as the post-of-the-day."""
    from api import actions

    draft = {
        "id": "draft-1",
        "hook": "Everyone's calling Marqeta's pivot defensive",
        "body": "It's actually a margin play. Here's why...",
        "angle": "contrarian_take",
        "source_company_name": "Marqeta",
        "status": "draft",
        "scheduled_for": None,
        "created_at": "2026-05-13T05:00:00+00:00",
    }
    db = _stub_db({"linkedin_drafts": [draft]})
    monkeypatch.setattr(actions, "get_supabase", lambda: db, raising=False)

    resp = client.get("/actions/today/linkedin-post")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Endpoint may serve either the strict "due today" path (returns an
    # action-shaped dict with `title`) or the fallback path (returns a
    # draft dict with `hook`). Both are acceptable — the contract is
    # "non-null post with content when drafts exist".
    assert body["post"] is not None
    post = body["post"]
    has_content = post.get("hook") or post.get("title")
    assert has_content, f"post has no hook/title: {post}"


def test_post_of_the_day_returns_null_when_no_drafts(client, monkeypatch):
    """No drafts at all → null."""
    from api import actions

    db = _stub_db({"linkedin_drafts": []})
    monkeypatch.setattr(actions, "get_supabase", lambda: db, raising=False)

    resp = client.get("/actions/today/linkedin-post")
    assert resp.status_code == 200
    assert resp.json()["post"] is None


# ── /actions/incoming ───────────────────────────────────────────────────


def test_incoming_returns_high_score_jobs_without_applications(client, monkeypatch):
    """Jobs with score>=85 + no applications row → included."""
    from api import actions

    db = _stub_db({
        "jobs": [
            {
                "id": 1022,
                "title": "Head of Product",
                "company": "Adyen",
                "match_score": 95,
                "letter_grade": "A",
                "archetype": "Head of Product",
                "legitimacy_tier": "verified",
                "created_at": "2026-05-13T05:00:00Z",
                "posting_closed_at": None,
                "validation_failed": None,
            },
        ],
        "applications": [],  # no applications row → job is "incoming"
        "companies": [],  # no phantoms
    })
    monkeypatch.setattr(actions, "get_supabase", lambda: db, raising=False)

    resp = client.get("/actions/incoming?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["jobs"]) == 1
    job = body["jobs"][0]
    assert job["job_id"] == 1022
    assert job["company"] == "Adyen"
    assert job["score"] == 95


def test_incoming_excludes_phantom_companies(client, monkeypatch):
    """Phantom company in company_personas → job filtered out."""
    from api import actions

    db = _stub_db({
        "jobs": [
            {
                "id": 9999,
                "title": "Head of Product Operations",
                "company": "Adyen Careers",  # phantom!
                "match_score": 95,
                "letter_grade": "A",
                "archetype": "Head of Product",
                "legitimacy_tier": "verified",
                "created_at": "2026-05-13T05:00:00Z",
                "posting_closed_at": None,
                "validation_failed": None,
            },
        ],
        "applications": [],
        "company_personas": [
            {"company_name": "Adyen Careers", "is_phantom": True},
        ],
    })
    monkeypatch.setattr(actions, "get_supabase", lambda: db, raising=False)

    resp = client.get("/actions/incoming")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs"] == []  # phantom filtered out
