"""
tests/test_observability_noop.py — guardrails for the P3-2/P3-3 observability core.

Two contracts this PR must keep:

  1. init_logging / init_sentry / init_otel are HARD NO-OPS when their env vars
     are unset — so dev + single-user prod boot byte-for-byte unchanged. We
     assert no logging handler is swapped, no Sentry client is created, and no
     OTel instrumentation is installed when the env is clean.

  2. The /ready readiness probe returns a well-formed 200 OR 503 with a
     per-dependency `checks` breakdown (Redis + DB). In CI Redis isn't running,
     so we accept either status and assert the SHAPE, not a specific value.

No live Supabase / Redis: supabase is monkey-patched to a MagicMock (so the DB
probe "succeeds") and Redis is simply unreachable (so it reports "down").
"""
from __future__ import annotations

import logging
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

# The env vars that gate each initializer. The no-op tests clear all of them.
_OBS_ENV_VARS = (
    "LOG_JSON",
    "SENTRY_DSN",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


@pytest.fixture
def clean_obs_env(monkeypatch):
    """Ensure none of the observability env vars are set, and reset the module
    one-shot guards so each test sees a pristine, never-initialised module."""
    import api.observability as obs

    for var in _OBS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(obs, "_logging_initialized", False, raising=False)
    monkeypatch.setattr(obs, "_sentry_initialized", False, raising=False)
    monkeypatch.setattr(obs, "_otel_initialized", False, raising=False)
    return obs


# ── 1. No-op contract ──────────────────────────────────────────────────────


def test_init_logging_is_noop_without_env(clean_obs_env):
    """LOG_JSON unset → root logger handlers are left exactly as they were."""
    obs = clean_obs_env
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    obs.init_logging()

    assert obs._logging_initialized is False
    assert list(root.handlers) == handlers_before  # no handler swap


def test_init_sentry_is_noop_without_env(clean_obs_env):
    """SENTRY_DSN unset → init_sentry returns without creating a client.

    If sentry_sdk happens to be importable, assert no hub/client got bound;
    either way the guard must stay False.
    """
    obs = clean_obs_env

    obs.init_sentry()

    assert obs._sentry_initialized is False
    try:
        import sentry_sdk
        client = sentry_sdk.Hub.current.client if hasattr(sentry_sdk, "Hub") else None
        assert client is None
    except ImportError:
        pass  # lib not installed → definitively a no-op


def test_init_otel_is_noop_without_env(clean_obs_env):
    """OTEL endpoint unset → init_otel(app) does nothing and never raises,
    even when passed a dummy app object."""
    obs = clean_obs_env

    obs.init_otel(object())  # must not touch the object or raise

    assert obs._otel_initialized is False


def test_all_initializers_safe_to_call_together(clean_obs_env):
    """Calling all three on a clean env (as server.py does at boot) is free
    and side-effect-free."""
    obs = clean_obs_env

    obs.init_logging()
    obs.init_sentry()
    obs.init_otel(object())

    assert obs._logging_initialized is False
    assert obs._sentry_initialized is False
    assert obs._otel_initialized is False


def test_observability_module_imports_without_heavy_deps():
    """The module must import cleanly even if the OTel/Sentry wheels aren't
    installed — every third-party import lives inside an init function."""
    import importlib
    import api.observability as obs

    importlib.reload(obs)
    for fn in ("init_logging", "init_sentry", "init_otel"):
        assert callable(getattr(obs, fn))


# ── 2. /ready readiness probe shape ─────────────────────────────────────────


@pytest.fixture
def client():
    """TestClient against the real app (scheduler startup hook skipped)."""
    from fastapi.testclient import TestClient
    from api import server

    server._scheduler_started = True
    return TestClient(server.app)


def test_ready_returns_200_or_503_with_checks_shape(client):
    """/ready answers with status + a per-dependency checks map. In CI Redis
    is down, so 503 is expected, but we accept 200 too (e.g. local Redis up)."""
    resp = client.get("/ready")

    assert resp.status_code in (200, 503), resp.text
    body = resp.json()
    assert body["status"] in ("ready", "not_ready")
    assert "checks" in body
    assert set(body["checks"]) == {"redis", "db"}
    for state in body["checks"].values():
        assert state in ("ok", "down")
    # Status must be consistent with the checks breakdown.
    all_ok = all(v == "ok" for v in body["checks"].values())
    assert (resp.status_code == 200) == all_ok
    assert (body["status"] == "ready") == all_ok


def test_health_stays_static_and_cheap(client):
    """/health must remain a static 200 with no dependency probing (liveness)."""
    resp = client.get("/health")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    # No dependency breakdown on the liveness probe — that's /ready's job.
    assert "checks" not in body
