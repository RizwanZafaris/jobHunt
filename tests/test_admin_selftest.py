"""tests/test_admin_selftest.py — /admin/selftest route is registered + admin-gated."""
import pytest


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    import config.settings as _cs
    _cs._settings = None
    import api.server as _srv
    return _srv


def test_selftest_route_registered_and_admin_gated(srv):
    from api.context import require_admin
    route = next(
        (r for r in srv.app.routes
         if getattr(r, "path", None) == "/admin/selftest"),
        None,
    )
    assert route is not None, "/admin/selftest not registered"
    assert "GET" in route.methods
    deps = [d.call for d in route.dependant.dependencies]
    assert require_admin in deps, "/admin/selftest not admin-gated"
