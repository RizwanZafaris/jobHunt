"""
Tests for the Phase-1 JWT auth + RLS scaffolding (feat/auth-jwt-scaffolding).

Covers:
  - get_current_user: single-user mode returns user_001; JWT mode resolves the
    token's user; rejects bad/missing tokens; service-secret branch resolves the
    system user when no bearer token is present.
  - verify_service_secret / check_service_secret: accept the secret, reject a
    wrong one, fail closed on the placeholder/unset secret.
  - require_admin: allows user_001 (the owner), denies a non-admin; honors the
    ADMIN_USER_IDS env allowlist.
  - get_user_supabase: applies the token to a per-request anon client in
    multi-tenant mode; falls back to the service-role singleton in single-user
    mode and when no token is supplied.

No live Supabase / network: `supabase` is stubbed with a MagicMock and the
user-table accessors are monkeypatched per test.
"""
from __future__ import annotations

import os
import sys
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

from fastapi import HTTPException  # noqa: E402

from api import auth as auth_mod  # noqa: E402
from api import context as context_mod  # noqa: E402
from api.users import RIZWAN_USER_ID, User  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

SEED_ID = str(RIZWAN_USER_ID)
OTHER_ID = "11111111-1111-1111-1111-111111111111"


def _make_user(user_id: str, *, is_admin: bool = False, email: str = "u@example.com") -> User:
    return User(
        id=UUID(user_id),
        email=email,
        full_name="Test User",
        plan="free",
        is_admin=is_admin,
        created_at="2026-05-29T00:00:00Z",
    )


async def _call_get_current_user(authorization=None, x_secret_key=None):
    return await context_mod.get_current_user(
        authorization=authorization, x_secret_key=x_secret_key
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts from a known auth-env baseline."""
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    # Default single-user OFF for most tests; individual tests opt back in.
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    yield


# ── verify_service_secret / check_service_secret ────────────────────────────

def test_verify_service_secret_accepts_correct_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    assert auth_mod.verify_service_secret(x_secret_key="real-secret") is True


def test_verify_service_secret_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    with pytest.raises(HTTPException) as exc:
        auth_mod.verify_service_secret(x_secret_key="nope")
    assert exc.value.status_code == 401


def test_verify_service_secret_rejects_missing_header(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    with pytest.raises(HTTPException) as exc:
        auth_mod.verify_service_secret(x_secret_key=None)
    assert exc.value.status_code == 401


def test_verify_service_secret_fails_closed_on_placeholder(monkeypatch):
    # Even if the caller sends the placeholder and it "matches" config, the
    # server must refuse — it has no real secret configured.
    monkeypatch.setenv("SECRET_KEY", auth_mod._PLACEHOLDER_SECRET)
    with pytest.raises(HTTPException) as exc:
        auth_mod.verify_service_secret(x_secret_key=auth_mod._PLACEHOLDER_SECRET)
    assert exc.value.status_code == 500
    assert exc.value.detail == "auth_misconfigured"


def test_check_service_secret_true_false(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    assert auth_mod.check_service_secret("real-secret") is True
    assert auth_mod.check_service_secret("wrong") is False
    assert auth_mod.check_service_secret(None) is False


def test_check_service_secret_raises_on_placeholder(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", auth_mod._PLACEHOLDER_SECRET)
    with pytest.raises(HTTPException) as exc:
        auth_mod.check_service_secret("anything")
    assert exc.value.status_code == 500


# ── get_current_user: single-user mode ──────────────────────────────────────

@pytest.mark.asyncio
async def test_single_user_mode_returns_user_001(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    seed = _make_user(SEED_ID, is_admin=True, email="rizwanzaffar.pk@gmail.com")
    monkeypatch.setattr(context_mod, "_ensure_rizwan", lambda: seed)

    # No headers at all — single-user mode ignores them.
    user = await _call_get_current_user()
    assert str(user.id) == SEED_ID
    assert user.is_admin is True


# ── get_current_user: JWT mode ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_jwt_mode_resolves_tokens_user(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setattr(
        context_mod, "verify_supabase_jwt", lambda token: {"sub": OTHER_ID, "email": "u@example.com"}
    )
    existing = _make_user(OTHER_ID)
    monkeypatch.setattr(context_mod, "get_user_by_id", lambda uid: existing)

    user = await _call_get_current_user(authorization="Bearer good.token.here")
    assert str(user.id) == OTHER_ID
    # Not in the admin allowlist → stays non-admin.
    assert user.is_admin is False


@pytest.mark.asyncio
async def test_jwt_mode_auto_provisions_first_signin(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setattr(
        context_mod,
        "verify_supabase_jwt",
        lambda token: {"sub": OTHER_ID, "email": "new@example.com",
                       "user_metadata": {"full_name": "New Person"}},
    )
    monkeypatch.setattr(context_mod, "get_user_by_id", lambda uid: None)
    created = {}

    def _create_user(email, full_name=None, **extras):
        created["email"] = email
        created["full_name"] = full_name
        created["id"] = extras.get("id")
        return _make_user(OTHER_ID, email=email)

    monkeypatch.setattr(context_mod, "create_user", _create_user)

    user = await _call_get_current_user(authorization="Bearer good.token.here")
    assert str(user.id) == OTHER_ID
    assert created["email"] == "new@example.com"
    assert created["full_name"] == "New Person"


@pytest.mark.asyncio
async def test_jwt_mode_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    # No Authorization header, no service secret → 401.
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(authorization=None, x_secret_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_jwt_mode_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")

    def _raise(token):
        raise HTTPException(status_code=401, detail="invalid_token")

    monkeypatch.setattr(context_mod, "verify_supabase_jwt", _raise)
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(authorization="Bearer garbage")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_jwt_mode_rejects_non_uuid_sub(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setattr(
        context_mod, "verify_supabase_jwt", lambda token: {"sub": "not-a-uuid", "email": "u@x.com"}
    )
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(authorization="Bearer good.token")
    assert exc.value.status_code == 401


# ── get_current_user: service-secret branch ─────────────────────────────────

@pytest.mark.asyncio
async def test_service_secret_branch_resolves_system_user(monkeypatch):
    """Multi-tenant mode, no bearer token, valid X-Secret-Key → seed/system user."""
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    seed = _make_user(SEED_ID, is_admin=True)
    monkeypatch.setattr(context_mod, "_ensure_rizwan", lambda: seed)

    user = await _call_get_current_user(authorization=None, x_secret_key="real-secret")
    assert str(user.id) == SEED_ID


@pytest.mark.asyncio
async def test_service_secret_branch_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(authorization=None, x_secret_key="wrong-secret")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_takes_precedence_over_service_secret(monkeypatch):
    """A request with both a bearer token and a service key is the end user."""
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    monkeypatch.setattr(
        context_mod, "verify_supabase_jwt", lambda token: {"sub": OTHER_ID, "email": "u@x.com"}
    )
    monkeypatch.setattr(context_mod, "get_user_by_id", lambda uid: _make_user(OTHER_ID))
    # _ensure_rizwan should NOT be called; make it explode if it is.
    monkeypatch.setattr(
        context_mod, "_ensure_rizwan", lambda: (_ for _ in ()).throw(AssertionError("used service branch"))
    )

    user = await _call_get_current_user(
        authorization="Bearer good.token", x_secret_key="real-secret"
    )
    assert str(user.id) == OTHER_ID


# ── require_admin / admin determination ─────────────────────────────────────

@pytest.mark.asyncio
async def test_require_admin_allows_owner_user_001(monkeypatch):
    """user_001 is admin via the env allowlist even if the DB row says false."""
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    # DB row for the seed user with is_admin=FALSE — the allowlist must promote.
    seed_not_admin = _make_user(SEED_ID, is_admin=False)
    monkeypatch.setattr(context_mod, "_ensure_rizwan", lambda: seed_not_admin)

    user = await _call_get_current_user(authorization=None, x_secret_key="real-secret")
    assert user.is_admin is True  # reconciled from allowlist
    # require_admin should pass.
    assert context_mod.require_admin(user) is user


def test_require_admin_denies_non_admin():
    non_admin = _make_user(OTHER_ID, is_admin=False)
    with pytest.raises(HTTPException) as exc:
        context_mod.require_admin(non_admin)
    assert exc.value.status_code == 403
    assert exc.value.detail == "admin_required"


@pytest.mark.asyncio
async def test_admin_user_ids_env_promotes_user(monkeypatch):
    """A user listed in ADMIN_USER_IDS is reconciled to admin."""
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("ADMIN_USER_IDS", OTHER_ID)
    monkeypatch.setattr(
        context_mod, "verify_supabase_jwt", lambda token: {"sub": OTHER_ID, "email": "u@x.com"}
    )
    monkeypatch.setattr(context_mod, "get_user_by_id", lambda uid: _make_user(OTHER_ID, is_admin=False))

    user = await _call_get_current_user(authorization="Bearer good.token")
    assert user.is_admin is True
    assert context_mod.require_admin(user) is user


def test_admin_user_ids_ignores_malformed(monkeypatch):
    monkeypatch.setenv("ADMIN_USER_IDS", "not-a-uuid,,  ")
    ids = context_mod._admin_user_ids()
    # Seed always present; the junk entries are dropped.
    assert SEED_ID in ids
    assert "not-a-uuid" not in ids


# ── get_user_supabase ───────────────────────────────────────────────────────

def test_get_user_supabase_falls_back_to_service_role_in_single_user(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    import db.client as dbc

    sentinel = object()
    monkeypatch.setattr(dbc, "get_supabase", lambda: sentinel)
    # Even with a token, single-user mode returns the service-role singleton.
    assert dbc.get_user_supabase("some.jwt.token") is sentinel


def test_get_user_supabase_falls_back_when_no_token(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    import db.client as dbc

    sentinel = object()
    monkeypatch.setattr(dbc, "get_supabase", lambda: sentinel)
    assert dbc.get_user_supabase(None) is sentinel


def test_get_user_supabase_applies_token_in_multi_tenant(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    import db.client as dbc

    # Settings must carry an anon key for the RLS client path.
    fake_settings = MagicMock()
    fake_settings.supabase_url = "http://test"
    fake_settings.supabase_anon_key = "anon-key"
    monkeypatch.setattr(dbc, "get_settings", lambda: fake_settings)

    fake_client = MagicMock()
    created = {}

    def _create_client(url, key):
        created["url"] = url
        created["key"] = key
        return fake_client

    monkeypatch.setattr(dbc, "create_client", _create_client)

    client = dbc.get_user_supabase("user.jwt.token")
    assert client is fake_client
    # Built with the anon key, not the service key.
    assert created["key"] == "anon-key"
    # The user JWT was applied to PostgREST so auth.uid() resolves.
    fake_client.postgrest.auth.assert_called_once_with("user.jwt.token")


def test_get_user_supabase_falls_back_when_no_anon_key(monkeypatch):
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    import db.client as dbc

    fake_settings = MagicMock()
    fake_settings.supabase_url = "http://test"
    fake_settings.supabase_anon_key = None  # not configured
    monkeypatch.setattr(dbc, "get_settings", lambda: fake_settings)

    sentinel = object()
    monkeypatch.setattr(dbc, "get_supabase", lambda: sentinel)
    assert dbc.get_user_supabase("user.jwt.token") is sentinel
