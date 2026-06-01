"""
tests/test_onboarding_endpoints.py — Phase 4 onboarding backend (P4-1).

Exercises GET/POST /me/onboarding by calling the async handlers directly
(passing user= explicitly and monkeypatching the aexecute seam) — no TestClient
lifespan, no network, no keys. Plus route-registration + auth-gating checks and
a migration-shape sanity test.
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

SEED = UUID("00000000-0000-0000-0000-000000000001")


def _fake_user():
    from api.users import User
    return User(
        id=SEED, email="t@example.com", full_name="T", plan="free",
        is_admin=False, created_at="2026-05-31T00:00:00Z",
    )


@pytest.fixture
def srv(monkeypatch):
    """Import api.server with the minimal env + reset settings singleton.
    Mirrors tests/test_admin_scheduler.py (no global conftest)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    import sys
    sys.modules.setdefault("supabase", MagicMock())
    import config.settings as _cs
    _cs._settings = None
    import api.server as _srv
    monkeypatch.setattr(_srv, "get_supabase", lambda: MagicMock())
    return _srv


def _patch_aexecute(monkeypatch, srv, rows=None, boom=False):
    async def _ax(_q):
        if boom:
            raise RuntimeError("column \"onboarded_at\" does not exist")
        return SimpleNamespace(data=rows if rows is not None else [])
    monkeypatch.setattr(srv, "aexecute", _ax)


# ── GET /me/onboarding ──────────────────────────────────────────────────────
def test_get_reports_not_onboarded_when_timestamp_null(srv, monkeypatch):
    _patch_aexecute(monkeypatch, srv, rows=[
        {"onboarded_at": None, "onboarding_step": "welcome", "signup_source": "google"},
    ])
    res = asyncio.run(srv.get_me_onboarding(user=_fake_user()))
    assert res["onboarded"] is False
    assert res["step"] == "welcome"
    assert res["signup_source"] == "google"


def test_get_reports_onboarded_when_timestamp_present(srv, monkeypatch):
    _patch_aexecute(monkeypatch, srv, rows=[
        {"onboarded_at": "2026-05-31T00:00:00Z", "onboarding_step": "done", "signup_source": "google"},
    ])
    res = asyncio.run(srv.get_me_onboarding(user=_fake_user()))
    assert res["onboarded"] is True
    assert res["step"] == "done"


def test_get_degrades_to_onboarded_when_columns_missing(srv, monkeypatch):
    # Migration 045 not applied → aexecute raises → fail safe to onboarded=true.
    _patch_aexecute(monkeypatch, srv, boom=True)
    res = asyncio.run(srv.get_me_onboarding(user=_fake_user()))
    assert res["onboarded"] is True
    assert res.get("degraded") is True


# ── POST /me/onboarding ─────────────────────────────────────────────────────
def test_post_complete_stamps_onboarded(srv, monkeypatch):
    # select(existing onboarded_at=None) then update — both via aexecute.
    _patch_aexecute(monkeypatch, srv, rows=[{"onboarded_at": None}])
    res = asyncio.run(srv.update_me_onboarding(
        payload=srv.OnboardingUpdate(complete=True), user=_fake_user(),
    ))
    assert res["ok"] is True
    assert res["onboarded"] is True
    assert res["step"] == "done"


def test_post_step_only_advances_cursor(srv, monkeypatch):
    _patch_aexecute(monkeypatch, srv, rows=[])
    res = asyncio.run(srv.update_me_onboarding(
        payload=srv.OnboardingUpdate(step="profile"), user=_fake_user(),
    ))
    assert res["ok"] is True
    assert res["step"] == "profile"
    assert res["onboarded"] is False  # no completion stamp


def test_post_empty_payload_is_400(srv):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.update_me_onboarding(
            payload=srv.OnboardingUpdate(), user=_fake_user(),
        ))
    assert ei.value.status_code == 400


def test_post_storage_unavailable_is_503(srv, monkeypatch):
    _patch_aexecute(monkeypatch, srv, boom=True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.update_me_onboarding(
            payload=srv.OnboardingUpdate(step="welcome"), user=_fake_user(),
        ))
    assert ei.value.status_code == 503


# ── routes registered + auth-gated ──────────────────────────────────────────
def test_routes_registered_and_user_gated(srv):
    from api.context import get_current_user

    paths = {getattr(r, "path", None): r for r in srv.app.routes}
    for p in ("/me/onboarding",):
        assert p in paths, f"{p} not registered"
    get_route = next(
        r for r in srv.app.routes
        if getattr(r, "path", None) == "/me/onboarding" and "GET" in getattr(r, "methods", set())
    )
    post_route = next(
        r for r in srv.app.routes
        if getattr(r, "path", None) == "/me/onboarding" and "POST" in getattr(r, "methods", set())
    )
    for route in (get_route, post_route):
        deps = [d.call for d in route.dependant.dependencies]
        assert get_current_user in deps, "onboarding endpoint not gated by get_current_user"


# ── migration shape ─────────────────────────────────────────────────────────
def test_migration_045_is_idempotent_and_additive():
    import pathlib
    sql = pathlib.Path("db/migrations/2026_05_31_045_user_onboarding.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS onboarded_at" in sql
    assert "ADD COLUMN IF NOT EXISTS onboarding_step" in sql
    assert "ADD COLUMN IF NOT EXISTS signup_source" in sql
    # additive only — no destructive ops
    assert "DROP COLUMN" not in sql.upper()
    assert "DROP TABLE" not in sql.upper()
    # owner backfilled so the live single-user owner skips onboarding
    assert "00000000-0000-0000-0000-000000000001" in sql
    assert "BEGIN;" in sql and "COMMIT;" in sql
