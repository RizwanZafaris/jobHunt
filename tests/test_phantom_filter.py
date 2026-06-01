"""
Regression tests for the CompanyAgent phantom-company guard.

Covers:
  - CompanyAgent refuses to instantiate when the DB flags the name phantom
  - CompanyAgent allows a real company through the DB guard

Note (2026-05-29): the `is_phantom` flag lives on `companies`, not
`company_personas` (BUG-013). Two callers that wrongly queried
`company_personas.is_phantom` were corrected — the /today phantom filter
(`_phantom_company_names`) was retired, and this CompanyAgent guard was
repointed to `companies`. The surviving runtime filters (`_build_incoming_jobs`
and this guard) all query the correct `companies` table.
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


_USER = UUID("11111111-1111-1111-1111-111111111111")


def _mock_db(*, phantom_companies: list[dict] | None = None, job_rows: list[dict] | None = None):
    """Build a MagicMock Supabase client returning canned table data."""

    class _Q:
        def __init__(self, table: str):
            self.table = table
            self._filters: list[tuple] = []

        def select(self, *args, **kw):
            return self

        def eq(self, col, val):
            self._filters.append(("eq", col, val))
            return self

        def ilike(self, col, val):
            self._filters.append(("ilike", col, val))
            return self

        def is_(self, col, val):
            return self

        def not_(self, *args, **kw):
            return self

        def gte(self, col, val):
            return self

        def lt(self, col, val):
            return self

        def lte(self, col, val):
            return self

        def order(self, *args, **kw):
            return self

        def limit(self, n):
            return self

        def in_(self, col, vals):
            return self

        def execute(self):
            if self.table == "companies":
                rows = phantom_companies or []
                # Honor is_phantom filter
                wants_phantom = any(f == ("eq", "is_phantom", True) for f in self._filters)
                if wants_phantom:
                    rows = [r for r in rows if r.get("is_phantom") is True]
                # Honor ilike(name, X) filter
                for f in self._filters:
                    if f[0] == "ilike" and f[1] == "name":
                        rows = [r for r in rows if r.get("name", "").lower() == f[2].lower()]
                return MagicMock(data=list(rows))
            if self.table == "jobs":
                return MagicMock(data=list(job_rows or []))
            return MagicMock(data=[])

    class _Client:
        def table(self, name):
            return _Q(name)

    return _Client()


# ── CompanyAgent refuses to instantiate when DB flags name phantom ──


def test_company_agent_refuses_phantom_via_db_flag(monkeypatch):
    """Even if the regex misses, a DB row with is_phantom=TRUE blocks
    instantiation. Production hit: SuperApp looked legitimate to the
    regex but had a phantom row → real money was spent before this fix.
    """
    from agents import company_agent
    from agents.company_agent import CompanyAgent, PhantomCompanyError

    # Force the regex check to PASS (looks legit) so the DB check is the
    # only thing that can stop instantiation.
    monkeypatch.setattr(
        company_agent, "_is_phantom_company_name", lambda name: False
    )

    # DB returns a phantom row for "SuperApp"
    db = _mock_db(phantom_companies=[
        {"name": "SuperApp", "is_phantom": True},
    ])
    # Patch the lazy get_supabase import inside CompanyAgent.__init__
    import db.client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: db, raising=False)

    with pytest.raises(PhantomCompanyError) as exc_info:
        CompanyAgent("SuperApp")
    assert "is_phantom=TRUE" in str(exc_info.value)


def test_company_agent_allows_real_company_via_db_check(monkeypatch):
    """When DB returns no phantom row, construction proceeds past the
    DB guard (regex guard already passed)."""
    from agents import company_agent
    from agents.company_agent import CompanyAgent

    monkeypatch.setattr(
        company_agent, "_is_phantom_company_name", lambda name: False
    )

    # DB returns empty (no row for "Adyen") → guard does not fire
    db = _mock_db(phantom_companies=[])
    import db.client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: db, raising=False)

    # We don't actually let __init__ complete (it tries to load settings +
    # company knowledge). Just confirm the DB guard didn't raise by
    # patching the super().__init__ call to return immediately.
    monkeypatch.setattr(
        "agents.base_agent.BaseAgent.__init__",
        lambda self, **kw: None,
    )
    # Also bypass the canonicalize lookup which queries companies table
    monkeypatch.setattr(
        CompanyAgent, "_canonicalize", staticmethod(lambda name: name)
    )
    # Settings lookup
    monkeypatch.setattr(
        "agents.company_agent.get_settings",
        lambda: MagicMock(company_agent_model="claude-opus-4-5"),
    )

    # Should NOT raise
    agent = CompanyAgent("Adyen")
    assert agent.company_name == "Adyen"
