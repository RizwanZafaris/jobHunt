"""Regression tests for the P1 launch-readiness fixes shipped 2026-05-16.

Covers:
  B11 - /network/target-coverage queries the real `companies` table
        (not the non-existent `target_companies`) and returns [] when
        no targets exist instead of 500
  B11 - agents.referral_graph._target_company_name() queries `companies`
  B12 - phantom-job archive migration SQL is valid + non-destructive
  B14 - pick_angle_node falls back to candidate_knowledge[*].company_name
        when the LLM omits chosen_company_name, then to a
        company_knowledge DB lookup as a last resort

Out-of-scope but referenced in PR:
  B8  - on-demand PDF render path is intact (_render_resume_md_to_pdf
        still imported by api/workspace.py)
  B10 - mock fallback removal: tested implicitly via dashboard tsc build
        (the mock imports are gone, type-checking would fail otherwise)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")


# ════════════════════════════════════════════════════════════════════════════
# B11 — /network/target-coverage
# ════════════════════════════════════════════════════════════════════════════


class _StubResult:
    def __init__(self, data):
        self.data = data


class _StubQuery:
    """Chain-friendly mock with per-table .execute() payloads."""
    def __init__(self, results_by_table=None):
        self._results = results_by_table or {}
        self._table = None

    def table(self, name):
        self._table = name
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self

    def __call__(self, *args, **kwargs):
        return self

    def execute(self):
        return _StubResult(self._results.get(self._table, []))


def test_b11_target_coverage_queries_companies_not_target_companies():
    """The handler source must reference `companies`, not the dead
    `target_companies` table. Static-check the source."""
    import api.network as net

    src = Path(net.__file__).read_text()
    # Locate the target_coverage function
    body_start = src.find("def target_coverage(")
    assert body_start > 0, "target_coverage function not found"
    body_end = src.find("\n@router", body_start + 1)
    body = src[body_start:body_end if body_end > 0 else len(src)]

    # The whole point of B11: the dead table reference is gone
    assert 'table("target_companies")' not in body, (
        "B11 regression: target_coverage still queries the non-existent "
        "`target_companies` table"
    )
    # And the real table is queried with the target filter
    assert 'table("companies")' in body
    assert 'is_target' in body


def test_b11_referral_graph_target_name_uses_companies():
    """_target_company_name in referral_graph.py must query `companies`."""
    import agents.referral_graph as rg

    src = Path(rg.__file__).read_text()
    func_start = src.find("def _target_company_name(")
    assert func_start > 0
    # Read ~40 lines after
    func_body = src[func_start:func_start + 2000]
    assert 'table("target_companies")' not in func_body
    assert 'table("companies")' in func_body


def test_b11_target_coverage_returns_empty_on_no_targets(monkeypatch):
    """Empty company set → returns [], not 500.

    Call the handler directly (skip HTTP layer + user-context middleware)
    by passing a SimpleNamespace stand-in for `user` — the handler only
    touches user.id."""
    from api.network import target_coverage
    from uuid import uuid4

    fake_user = SimpleNamespace(id=uuid4())

    stub_db = _StubQuery(results_by_table={"companies": []})
    from db import client as db_client
    monkeypatch.setattr(db_client, "get_supabase", lambda: stub_db)

    out = target_coverage(user=fake_user)
    assert out == []


# ════════════════════════════════════════════════════════════════════════════
# B12 — phantom-job archive migration
# ════════════════════════════════════════════════════════════════════════════


def test_b12_migration_file_exists_and_is_idempotent():
    """The 035 migration exists, uses IF NOT EXISTS guards, and moves
    rows (not deletes destructively)."""
    repo_root = Path(__file__).resolve().parent.parent
    mig = repo_root / "db" / "migrations" / "2026_05_16_035_archive_phantom_jobs.sql"
    assert mig.exists(), f"missing migration: {mig}"

    sql = mig.read_text()
    # Idempotent guards
    assert "CREATE TABLE IF NOT EXISTS public.jobs_archive" in sql
    assert "DROP POLICY IF EXISTS jobs_archive_select_own" in sql

    # Atomic move: DELETE...RETURNING into INSERT
    assert "DELETE FROM public.jobs" in sql
    assert "INSERT INTO public.jobs_archive" in sql
    assert "validation_failed = 'phantom_company_string'" in sql

    # RLS on the new table
    assert "ENABLE ROW LEVEL SECURITY" in sql


# ════════════════════════════════════════════════════════════════════════════
# B14 — pick_angle_node source_company_name fallback
# ════════════════════════════════════════════════════════════════════════════


def _stub_router(parsed: dict):
    """Async router stub whose ask_json returns (parsed, llm_result_stub)."""
    result_stub = SimpleNamespace(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_usd=0.001,
        latency_ms=42,
        text="",
    )

    class Stub:
        async def ask_json(self, **kw):
            return parsed, result_stub

    return Stub()


def _stub_company_knowledge_db(rows: list[dict]):
    """Build a supabase stub whose company_knowledge lookup returns `rows`
    and whose companies lookup returns []."""
    return _StubQuery(results_by_table={
        "company_knowledge": rows,
        "companies": [],  # so chosen_company_id stays None
    })


@pytest.mark.asyncio
async def test_b14_chosen_company_name_passes_through_when_llm_provides_it():
    """Happy path: LLM returns chosen_company_name → used verbatim."""
    from agents.g4_linkedin_graph import pick_angle_node

    stub = _stub_router({
        "chosen_knowledge_id": "kid-1",
        "chosen_company_name": "Mastercard",
        "angle": "news_commentary",
        "rationale": "...",
    })

    with patch("agents.g4_linkedin_graph.get_router", return_value=stub), \
         patch("agents.g4_linkedin_graph.get_supabase",
               return_value=_stub_company_knowledge_db([])):
        out = await pick_angle_node({
            "user_id": "u",
            "voice_profile": {},
            "cv_md": "",
            "candidate_knowledge": [
                {"id": "kid-1", "company_name": "Mastercard", "content": "x", "scraped_at": ""},
            ],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 0.0,
        })
    assert out["chosen_company_name"] == "Mastercard"


@pytest.mark.asyncio
async def test_b14_falls_back_to_candidate_knowledge_when_llm_omits_company():
    """LLM omits chosen_company_name → look up by chosen_knowledge_id
    in the candidate_knowledge list (no DB round trip)."""
    from agents.g4_linkedin_graph import pick_angle_node

    stub = _stub_router({
        "chosen_knowledge_id": "kid-2",
        # chosen_company_name intentionally absent
        "angle": "contrarian_take",
        "rationale": "...",
    })

    with patch("agents.g4_linkedin_graph.get_router", return_value=stub), \
         patch("agents.g4_linkedin_graph.get_supabase",
               return_value=_stub_company_knowledge_db([])):
        out = await pick_angle_node({
            "user_id": "u",
            "voice_profile": {},
            "cv_md": "",
            "candidate_knowledge": [
                {"id": "kid-2", "company_name": "Plaid", "content": "x", "scraped_at": ""},
            ],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 0.0,
        })
    assert out["chosen_company_name"] == "Plaid", (
        "B14 regression: source_company_name fell back to None"
    )


@pytest.mark.asyncio
async def test_b14_falls_back_to_company_knowledge_db_when_candidate_missing_name():
    """If candidate_knowledge has no company_name for the picked id,
    fall back to a company_knowledge DB lookup."""
    from agents.g4_linkedin_graph import pick_angle_node

    stub = _stub_router({
        "chosen_knowledge_id": "kid-3",
        "angle": "industry_analysis",
        "rationale": "...",
    })

    db = _stub_company_knowledge_db([{"company_name": "Stripe"}])
    with patch("agents.g4_linkedin_graph.get_router", return_value=stub), \
         patch("agents.g4_linkedin_graph.get_supabase", return_value=db):
        out = await pick_angle_node({
            "user_id": "u",
            "voice_profile": {},
            "cv_md": "",
            "candidate_knowledge": [
                # No company_name on this row → triggers DB fallback
                {"id": "kid-3", "company_name": None, "content": "x", "scraped_at": ""},
            ],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 0.0,
        })
    assert out["chosen_company_name"] == "Stripe"


# ════════════════════════════════════════════════════════════════════════════
# B8 — on-demand PDF render path is intact
# ════════════════════════════════════════════════════════════════════════════


def test_b8_pdf_endpoint_uses_on_demand_render():
    """resume_pdf_url=NULL is by design — the /resume.pdf endpoint renders
    markdown → PDF on demand via _render_resume_md_to_pdf. Lock that path
    in so a future refactor doesn't accidentally remove it."""
    import api.workspace as ws

    src = Path(ws.__file__).read_text()
    # Endpoint registered
    assert '@router.get("/{job_id}/resume.{fmt}")' in src
    # On-demand render call present
    assert "_render_resume_md_to_pdf(" in src
    # Falls back gracefully when no pre-rendered url
    assert 'build.get("resume_pdf_url")' in src


def test_b8_reportlab_in_requirements():
    """The on-demand PDF render path uses reportlab; the dep must stay."""
    repo_root = Path(__file__).resolve().parent.parent
    reqs = (repo_root / "requirements.txt").read_text()
    assert "reportlab" in reqs.lower(), "B8: reportlab dropped from requirements.txt"


# ════════════════════════════════════════════════════════════════════════════
# B10 — mock fallback removal (source-level guard)
# ════════════════════════════════════════════════════════════════════════════


def _strip_comments_ts(src: str) -> str:
    """Crude TS/TSX comment stripper: drops /* ... */ blocks and // lines.
    Good enough to tell "code reference to MOCK_X" from "comment mentions MOCK_X"."""
    import re
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def test_b10_today_page_does_not_import_mock_actions():
    """`MOCK_TODAY_ACTIONS` must not be imported by /today/page.tsx — once
    it's imported, the temptation to fall back to it is one git-stash away."""
    repo_root = Path(__file__).resolve().parent.parent
    page = repo_root / "dashboard" / "src" / "app" / "today" / "page.tsx"
    src = page.read_text()
    assert "from '@/lib/mock/today'" not in src, (
        "B10 regression: /today/page.tsx re-imports the mock"
    )
    # Strip comments so references in change-log comments don't trip the test
    code = _strip_comments_ts(src)
    assert "MOCK_TODAY_ACTIONS" not in code


def test_b10_linkedin_page_does_not_import_mock_drafts():
    """Same as today, for /linkedin/page.tsx."""
    repo_root = Path(__file__).resolve().parent.parent
    page = repo_root / "dashboard" / "src" / "app" / "linkedin" / "page.tsx"
    src = page.read_text()
    assert "from '@/lib/mock/linkedin'" not in src, (
        "B10 regression: /linkedin/page.tsx re-imports the mocks"
    )
    code = _strip_comments_ts(src)
    assert "MOCK_DRAFTS" not in code
    assert "MOCK_POSTING_SCHEDULE" not in code
    assert "MOCK_LINKEDIN_STATS" not in code


def test_b10_today_shows_error_state_instead_of_mock():
    """When fetch fails, /today should render the error banner — confirm
    the banner string is present in the source."""
    repo_root = Path(__file__).resolve().parent.parent
    page = repo_root / "dashboard" / "src" / "app" / "today" / "page.tsx"
    src = page.read_text()
    assert "Couldn" in src and "reach the backend" in src
