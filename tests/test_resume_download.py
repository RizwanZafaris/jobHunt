"""
Tests for the resume-render + download path and its P1 hardening.

Covers:
  1. resume_agents.render — pure Markdown→DOCX / Markdown→PDF renderers
     (python-docx + reportlab, no pandoc, no network).
  2. GET /resume-builds/{id}/download — serves md AND renders docx/pdf on
     demand from the resume_builds.resume_md DB column; empty render → 503;
     unsupported fmt → 400; missing build → 404; non-UUID id → 404.
  3. P1-1 hardening: UUID guards on every {build_id} route, and bounded
     `limit` query params (Query(ge=1, le=200) → 422 instead of a PostgREST
     500 on out-of-range values).
  4. _safe_resume_build_row — surfaces scores always + resume_md when asked.

P1-3 fix: api/server.py constructs Settings() at import time, which raises a
pydantic ValidationError when the required env vars are unset. The previous
version of this file imported `api.server` inside each test body WITHOUT
setting env first, so 9/14 tests blew up with a ValidationError. We now set
env vars FIRST in a fixture (mirroring tests/test_admin_rebuild_personas.py)
and import the server through it. There are deliberately NO top-level
`from api...server` imports here.
"""
import inspect
import types

import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────
# Env-first server import. Settings() must see env BEFORE construction; we
# also reset the cached settings singleton so a prior import in the same
# process can't leave a half-built Settings around.
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def server(monkeypatch):
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


# ─────────────────────────────────────────────────────────────────────
# 1. Pure renderer tests (no server import needed → no env needed)
# ─────────────────────────────────────────────────────────────────────
SAMPLE_MD = """# RIZWAN ZAFAR

**Head of Product | Issuing & Payments** | NYC | a@b.com | linkedin.com/in/x

---

## PROFESSIONAL SUMMARY

Product leader. Scaled <Simpaisa> 0→$1B+ GTV & drove 14% uplift > target.

## EXPERIENCE

### Simpaisa — Chief Product Officer
*Karachi | 2020 – Present*

- Built embedded finance platform incl. **Visa** & *Mastercard*
- Owned full P&L
"""


def test_docx_render_returns_valid_zip():
    from resume_agents.render import markdown_to_docx_bytes
    data = markdown_to_docx_bytes(SAMPLE_MD)
    assert data, "expected non-empty docx bytes"
    # .docx is a zip archive → starts with the PK local-file-header magic.
    assert data[:2] == b"PK"
    assert len(data) > 1000


def test_pdf_render_returns_valid_pdf():
    from resume_agents.render import markdown_to_pdf_bytes
    data = markdown_to_pdf_bytes(SAMPLE_MD)
    assert data, "expected non-empty pdf bytes"
    assert data[:5] == b"%PDF-"


def test_renderers_empty_input_returns_empty_bytes():
    from resume_agents.render import markdown_to_docx_bytes, markdown_to_pdf_bytes
    assert markdown_to_docx_bytes("") == b""
    assert markdown_to_docx_bytes("   \n  ") == b""
    assert markdown_to_pdf_bytes("") == b""


def test_pdf_render_escapes_xml_significant_chars():
    """`<`, `>`, `&` in resume text must not break reportlab markup."""
    from resume_agents.render import markdown_to_pdf_bytes
    data = markdown_to_pdf_bytes("## A & B <C> grew > 10%\n\n- x & y < z")
    assert data[:5] == b"%PDF-"


def test_docx_render_handles_bold_italic_without_crashing():
    from resume_agents.render import markdown_to_docx_bytes
    data = markdown_to_docx_bytes("**bold** then *italic* then plain **a*b***")
    assert data[:2] == b"PK"


# ─────────────────────────────────────────────────────────────────────
# Shared test helpers
# ─────────────────────────────────────────────────────────────────────
# A valid UUID so the new _valid_uuid guard lets the request through to the
# (mocked) DB layer.
GOOD_ID = "11111111-2222-4333-8444-555555555555"


def _build_row(**over):
    row = {
        "id": GOOD_ID,
        "job_id": 42,
        "company_name": "Adyen",
        "user_edited_md": None,
        "resume_md": SAMPLE_MD,
        "user_id": "user-1",
    }
    row.update(over)
    return row


def _fake_user(uid="user-1"):
    return types.SimpleNamespace(id=uid)


class _Result:
    def __init__(self, data):
        self.data = data


def _patch_single_select(row):
    """Return a fake aexecute whose call returns [row] (or [] if row None)."""
    async def fake_aexecute(_builder):
        return _Result([row] if row is not None else [])
    return fake_aexecute


class _Query:
    """A PostgREST query-builder stand-in supporting the exact chain that
    list_jobs + filter_open_jobs_query build, returning self on every call."""

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def in_(self, *a, **k): return self


class _Supabase:
    def table(self, *a, **k): return _Query()


# ─────────────────────────────────────────────────────────────────────
# 2. Download endpoint tests (direct coroutine call)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_download_md_from_db_column(server):
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build(GOOD_ID, fmt="md", user=_fake_user())
    assert resp.media_type == "text/markdown"
    body = resp.body if isinstance(resp.body, bytes) else resp.body.encode()
    assert b"RIZWAN ZAFAR" in body
    assert resp.headers["content-disposition"].endswith('.md"')


@pytest.mark.asyncio
async def test_download_docx_renders_on_demand(server):
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build(GOOD_ID, fmt="docx", user=_fake_user())
    assert "wordprocessingml" in resp.media_type
    assert resp.body[:2] == b"PK"
    assert resp.headers["content-disposition"].endswith('.docx"')


@pytest.mark.asyncio
async def test_download_pdf_renders_on_demand(server):
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build(GOOD_ID, fmt="pdf", user=_fake_user())
    assert resp.media_type == "application/pdf"
    assert resp.body[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_download_prefers_user_edit_over_db_column(server):
    row = _build_row(user_edited_md="# EDITED RESUME\n\nmy edit")
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build(GOOD_ID, fmt="md", user=_fake_user())
    body = resp.body if isinstance(resp.body, bytes) else resp.body.encode()
    assert b"EDITED RESUME" in body
    assert b"RIZWAN ZAFAR" not in body


@pytest.mark.asyncio
async def test_download_unsupported_format_400(server):
    from fastapi import HTTPException
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build(GOOD_ID, fmt="rtf", user=_fake_user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_download_missing_build_404(server):
    from fastapi import HTTPException
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(None)):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build(GOOD_ID, fmt="md", user=_fake_user())
    assert ei.value.status_code == 404


# ── P1-1: non-UUID build_id → 404 BEFORE any DB call ───────────────────
async def _boom(_b):  # used to prove the guard short-circuits before the DB
    raise AssertionError("DB must not be touched for a bad UUID")


@pytest.mark.asyncio
async def test_download_bad_uuid_returns_404(server):
    from fastapi import HTTPException
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _boom):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build("not-a-uuid", fmt="md", user=_fake_user())
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_resume_build_bad_uuid_returns_404(server):
    from fastapi import HTTPException
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _boom):
        with pytest.raises(HTTPException) as ei:
            await server.get_resume_build("12345", user=_fake_user())
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_markdown_bad_uuid_returns_404(server):
    from fastapi import HTTPException
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _boom):
        with pytest.raises(HTTPException) as ei:
            await server.get_resume_build_markdown("nope", user=_fake_user())
    assert ei.value.status_code == 404


# ── P0-1 503 gap: a degraded renderer (b"") → 503, not an empty 200.
#    download_resume_build resolves the renderer via the module-level
#    _RESUME_RENDERERS dict (it captured the original function reference at
#    import time). We mutate that exact dict object IN PLACE — the same object
#    the handler reads through its __globals__ — and restore it afterwards, so
#    the swap can't miss due to module-identity quirks under full-suite runs.
def _swap_renderer(server, fmt, fn):
    """Context-managed in-place swap of _RESUME_RENDERERS[fmt]'s function."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        d = server._RESUME_RENDERERS
        orig = d[fmt]
        d[fmt] = (fn, orig[1], orig[2])
        try:
            yield
        finally:
            d[fmt] = orig
    return _cm()


@pytest.mark.asyncio
async def test_download_docx_renderer_unavailable_returns_503(server):
    from fastapi import HTTPException
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)), \
         _swap_renderer(server, "docx", lambda _md: b""):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build(GOOD_ID, fmt="docx", user=_fake_user())
    assert ei.value.status_code == 503
    assert "fmt=md" in ei.value.detail


@pytest.mark.asyncio
async def test_download_pdf_renderer_unavailable_returns_503(server):
    from fastapi import HTTPException
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)), \
         _swap_renderer(server, "pdf", lambda _md: b""):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build(GOOD_ID, fmt="pdf", user=_fake_user())
    assert ei.value.status_code == 503
    assert "fmt=md" in ei.value.detail


# ─────────────────────────────────────────────────────────────────────
# 3. P1-1 limit-bound validation.
#
#    (a) Declarative check: each list endpoint's `limit` default is a FastAPI
#        Query carrying ge=1 / le=200. In FastAPI/pydantic v2 those live in
#        the Query object's `.metadata` as annotated-types Ge/Le, not as bare
#        attributes — so we read them from there.
#    (b) Behavioural check on a throwaway app exposing ONLY list_jobs (so the
#        shared app's rate-limiter / cross-test stub state can't interfere):
#        -5/0/999 → 422, 1/50/200 → 200.
# ─────────────────────────────────────────────────────────────────────
_LIMIT_ENDPOINTS = [
    "list_jobs",
    "list_my_jobs",
    "get_profile_keywords",
    "admin_rebuild_personas",
    "costs_by_resume_build",
    "costs_recent_calls",
]


def test_all_limit_params_are_bounded(server):
    """Every `limit: int = N` in api/server.py must be bound as
    Query(N, ge=1, le=200), so out-of-range values are rejected with 422
    instead of reaching PostgREST and 500-ing.

    Asserted against the on-disk source text (read straight from the file),
    which is immune to the module/linecache/code-object staleness that a full
    pytest run can introduce when earlier suites import api.server first.
    """
    import re
    src = open(server.__file__, encoding="utf-8").read()
    # No bare, unbounded limit params should remain anywhere in the module.
    unbounded = re.findall(r"limit: int = \d+(?!\d|,? ge=)", src)
    assert not unbounded, f"unbounded limit param(s) remain: {unbounded}"
    # And there must be the expected number of bounded ones.
    bounded = re.findall(r"limit: int = Query\(\d+, ge=1, le=200\)", src)
    assert len(bounded) == len(_LIMIT_ENDPOINTS), (
        f"expected {len(_LIMIT_ENDPOINTS)} bounded limit params, "
        f"found {len(bounded)}"
    )


def _jobs_app(server):
    """Throwaway FastAPI app exposing ONLY list_jobs, with auth overridden, so
    the Query(ge=1, le=200) bound is exercised in isolation."""
    from fastapi import FastAPI
    from api.context import get_current_user

    app = FastAPI()
    app.add_api_route("/jobs", server.list_jobs, methods=["GET"])
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return app


@pytest.mark.parametrize("bad", ["-5", "0", "999"])
def test_out_of_range_limit_rejected_422(server, bad):
    from fastapi.testclient import TestClient

    async def _ax(_b):
        return _Result([])
    _orig_sb, _orig_ax = server.get_supabase, server.aexecute
    server.get_supabase = lambda *a, **k: _Supabase()
    server.aexecute = _ax
    try:
        r = TestClient(_jobs_app(server), raise_server_exceptions=False).get(
            f"/jobs?limit={bad}"
        )
    finally:
        server.get_supabase, server.aexecute = _orig_sb, _orig_ax
    assert r.status_code == 422


@pytest.mark.parametrize("good", ["1", "50", "200"])
def test_in_range_limit_accepted_200(server, good):
    from fastapi.testclient import TestClient

    async def _ax(_b):
        return _Result([{"id": 1, "title": "x"}])
    _orig_sb, _orig_ax = server.get_supabase, server.aexecute
    server.get_supabase = lambda *a, **k: _Supabase()
    server.aexecute = _ax
    try:
        r = TestClient(_jobs_app(server), raise_server_exceptions=False).get(
            f"/jobs?limit={good}"
        )
    finally:
        server.get_supabase, server.aexecute = _orig_sb, _orig_ax
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# 4. Serializer tests
# ─────────────────────────────────────────────────────────────────────
def test_serializer_hides_md_by_default_but_flags_presence(server):
    out = server._safe_resume_build_row(_build_row())
    assert "resume_md" not in out          # heavy blob gated out of lists
    assert out["has_resume_md"] is True    # but presence is signalled
    assert out["polisher_score"] is None or "polisher_score" in out


def test_serializer_includes_md_when_requested(server):
    out = server._safe_resume_build_row(_build_row(), include_md=True)
    assert out["resume_md"] == SAMPLE_MD
    assert out["has_resume_md"] is True


def test_serializer_scores_surfaced(server):
    out = server._safe_resume_build_row(
        _build_row(polisher_score=88, ats_score_a=90, ats_score_b=85)
    )
    assert out["polisher_score"] == 88
    assert out["ats_score_a"] == 90
    assert out["ats_score_b"] == 85
