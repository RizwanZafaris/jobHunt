"""
Tests for the resume-render + download path.

Covers the 2026-05-31 fix that made converged builds actually yield a
downloadable / previewable resume:

  1. resume_agents.render — pure Markdown→DOCX / Markdown→PDF renderers
     (python-docx + reportlab, no pandoc, no network).
  2. GET /resume-builds/{id}/download — now serves md AND renders docx/pdf
     on demand, sourcing from the resume_builds.resume_md DB column.
  3. _safe_resume_build_row — surfaces scores always + resume_md when asked.

We don't spin up a real DB — we monkeypatch the module-level
`get_supabase` and `aexecute` seams the endpoint uses, and call the async
endpoint coroutine directly.
"""
import types

import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────
# 1. Pure renderer tests
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
# Test helpers for the async endpoint
# ─────────────────────────────────────────────────────────────────────
def _build_row(**over):
    row = {
        "id": "build-1",
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
    """Patch api.server.aexecute so the FIRST call (resume_builds select)
    returns [row], and api.server.get_supabase to a harmless mock."""
    async def fake_aexecute(_builder):
        return _Result([row] if row is not None else [])
    return fake_aexecute


# ─────────────────────────────────────────────────────────────────────
# 2. Download endpoint tests
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_download_md_from_db_column():
    from api import server
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build("build-1", fmt="md", user=_fake_user())
    assert resp.media_type == "text/markdown"
    body = resp.body if isinstance(resp.body, bytes) else resp.body.encode()
    assert b"RIZWAN ZAFAR" in body
    assert resp.headers["content-disposition"].endswith('.md"')


@pytest.mark.asyncio
async def test_download_docx_renders_on_demand():
    from api import server
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build("build-1", fmt="docx", user=_fake_user())
    assert "wordprocessingml" in resp.media_type
    assert resp.body[:2] == b"PK"
    assert resp.headers["content-disposition"].endswith('.docx"')


@pytest.mark.asyncio
async def test_download_pdf_renders_on_demand():
    from api import server
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build("build-1", fmt="pdf", user=_fake_user())
    assert resp.media_type == "application/pdf"
    assert resp.body[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_download_prefers_user_edit_over_db_column():
    from api import server
    row = _build_row(user_edited_md="# EDITED RESUME\n\nmy edit")
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        resp = await server.download_resume_build("build-1", fmt="md", user=_fake_user())
    body = resp.body if isinstance(resp.body, bytes) else resp.body.encode()
    assert b"EDITED RESUME" in body
    assert b"RIZWAN ZAFAR" not in body


@pytest.mark.asyncio
async def test_download_unsupported_format_400():
    from api import server
    from fastapi import HTTPException
    row = _build_row()
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(row)):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build("build-1", fmt="rtf", user=_fake_user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_download_missing_build_404():
    from api import server
    from fastapi import HTTPException
    with patch.object(server, "get_supabase", MagicMock()), \
         patch.object(server, "aexecute", _patch_single_select(None)):
        with pytest.raises(HTTPException) as ei:
            await server.download_resume_build("nope", fmt="md", user=_fake_user())
    assert ei.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 3. Serializer tests
# ─────────────────────────────────────────────────────────────────────
def test_serializer_hides_md_by_default_but_flags_presence():
    from api.server import _safe_resume_build_row
    out = _safe_resume_build_row(_build_row())
    assert "resume_md" not in out          # heavy blob gated out of lists
    assert out["has_resume_md"] is True    # but presence is signalled
    assert out["polisher_score"] is None or "polisher_score" in out


def test_serializer_includes_md_when_requested():
    from api.server import _safe_resume_build_row
    out = _safe_resume_build_row(_build_row(), include_md=True)
    assert out["resume_md"] == SAMPLE_MD
    assert out["has_resume_md"] is True


def test_serializer_scores_surfaced():
    from api.server import _safe_resume_build_row
    out = _safe_resume_build_row(_build_row(polisher_score=88, ats_score_a=90, ats_score_b=85))
    assert out["polisher_score"] == 88
    assert out["ats_score_a"] == 90
    assert out["ats_score_b"] == 85
