"""
B7 — SIGTERM guard for the RQ worker.

Tests that the worker registers a SIGTERM handler and that the sweep
function correctly flips in-flight builds to failed.

Run: pytest tests/test_b7_sigterm_guard.py -v
"""
from __future__ import annotations

import signal
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# Only stub modules NOT installed in the venv. Blanket-stubbing real
# packages at module-load time leaks MagicMock into sys.modules and
# breaks the collection phase of ~150 other tests (g6_persona_critic,
# comp_research, scoring_agent, network_search). google.genai is the
# only optional dep not pinned in requirements.txt.
if "google.genai" not in sys.modules:
    sys.modules["google.genai"] = MagicMock()


# Import api.worker now, while db.client is still the real module. The
# functions under test reference db.client lazily (`from db.client import
# get_supabase` inside the handler body), so we patch get_supabase per
# test rather than replacing the whole module — that prevents the fake
# module from leaking and causing ImportError: upsert_job in sibling
# tests like test_linkedin_title_parse / test_job_scout_agent.
from api.worker import _on_sigterm, _sweep_in_flight_on_shutdown


@pytest.fixture(autouse=True)
def _patch_db_client():
    """Stub out db.client.get_supabase for each test, leaving the real
    db.client module otherwise intact so its other exports (upsert_job,
    search_rizwan_profile, …) stay importable for sibling test files.

    Yields a shim whose `.get_supabase` attribute is the MagicMock — the
    fixture's original API used by existing tests
    (`_patch_db_client.get_supabase.return_value = db`)."""
    import db.client as _real_db_client
    original = _real_db_client.get_supabase
    fake = MagicMock()
    _real_db_client.get_supabase = fake
    shim = types.SimpleNamespace(get_supabase=fake)
    try:
        yield shim
    finally:
        _real_db_client.get_supabase = original


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_mock_db(build_rows=None, jobs_rows=None):
    """Return a mock supabase client with the fluent query-builder shape."""
    build_rows = build_rows or []
    jobs_rows = jobs_rows or []

    db = MagicMock()
    _tables = {}

    def _table(name):
        if name in _tables:
            return _tables[name]
        tbl = MagicMock()
        sel = tbl.select.return_value
        sel_eq = sel.eq.return_value
        result = MagicMock()
        if name == "resume_builds":
            result.data = build_rows
        elif name == "jobs_runs":
            result.data = jobs_rows
        else:
            result.data = []
        sel_eq.execute.return_value = result

        upd = tbl.update.return_value
        upd_eq = upd.eq.return_value
        upd_eq.execute.return_value = MagicMock(data=[])
        _tables[name] = tbl
        return tbl

    db.table = _table
    return db


# ── Tests ────────────────────────────────────────────────────────────────

class TestSigtermHandler:
    def test_handler_is_callable(self):
        assert callable(_on_sigterm)

    def test_handler_sets_global_flag(self):
        with patch("api.worker._SIGTERM_HANDLED", False):
            with patch("api.worker._sweep_in_flight_on_shutdown") as mock_sweep:
                with patch("api.worker.sys.exit") as mock_exit:
                    _on_sigterm(signal.SIGTERM, MagicMock())
                    mock_sweep.assert_called_once()
                    mock_exit.assert_called_once_with(0)

    def test_handler_is_idempotent(self):
        """Second SIGTERM should be a no-op (flag already True)."""
        with patch("api.worker._SIGTERM_HANDLED", True):
            with patch("api.worker._sweep_in_flight_on_shutdown") as mock_sweep:
                _on_sigterm(signal.SIGTERM, MagicMock())
                mock_sweep.assert_not_called()


class TestSweepInFlight:
    def test_sweep_flips_running_resume_builds(self, _patch_db_client):
        db = _make_mock_db(
            build_rows=[{"id": "build-1"}, {"id": "build-2"}],
            jobs_rows=[],
        )
        _patch_db_client.get_supabase.return_value = db
        with patch("api.worker.logger"):
            counts = _sweep_in_flight_on_shutdown()

        assert counts["resume_builds_flipped"] == 2
        assert counts["jobs_runs_flipped"] == 0
        calls = db.table("resume_builds").update.call_args_list
        assert len(calls) == 2
        for call in calls:
            payload = call[0][0]
            assert payload["status"] == "failed"
            assert payload["error"] == "worker_sigterm_received"
            assert payload["finalized_at"] is not None

    def test_sweep_flips_running_jobs_runs(self, _patch_db_client):
        db = _make_mock_db(
            build_rows=[],
            jobs_rows=[{"id": "job-1"}, {"id": "job-2"}, {"id": "job-3"}],
        )
        _patch_db_client.get_supabase.return_value = db
        with patch("api.worker.logger"):
            counts = _sweep_in_flight_on_shutdown()

        assert counts["resume_builds_flipped"] == 0
        assert counts["jobs_runs_flipped"] == 3
        calls = db.table("jobs_runs").update.call_args_list
        assert len(calls) == 3
        for call in calls:
            payload = call[0][0]
            assert payload["status"] == "failed"
            assert payload["last_error"] == "worker_sigterm_received"
            assert payload["finished_at"] is not None

    def test_sweep_no_running_builds(self, _patch_db_client):
        db = _make_mock_db(build_rows=[], jobs_rows=[])
        _patch_db_client.get_supabase.return_value = db
        with patch("api.worker.logger"):
            counts = _sweep_in_flight_on_shutdown()

        assert counts["resume_builds_flipped"] == 0
        assert counts["jobs_runs_flipped"] == 0
        calls = db.table("resume_builds").update.call_args_list
        assert len(calls) == 0

    def test_sweep_does_not_crash_on_db_failure(self, _patch_db_client):
        """If Supabase is unreachable during shutdown, sweep logs and returns."""
        _patch_db_client.get_supabase.side_effect = RuntimeError(
            "supabase unreachable"
        )
        with patch("api.worker.logger") as mock_logger:
            counts = _sweep_in_flight_on_shutdown()

        assert counts == {"resume_builds_flipped": 0, "jobs_runs_flipped": 0}
        mock_logger.exception.assert_called_once()

    def test_sweep_skips_failed_individual_updates(self, _patch_db_client):
        """If one build update fails, others still proceed (no crash)."""
        db = _make_mock_db(build_rows=[{"id": "ok"}, {"id": "bad"}])

        # Make every .eq().execute() raise — sweep must still complete
        tbl = db.table("resume_builds")
        tbl.update.return_value.eq.return_value.execute.side_effect = (
            RuntimeError("db timeout")
        )

        _patch_db_client.get_supabase.return_value = db
        with patch("api.worker.logger"):
            counts = _sweep_in_flight_on_shutdown()

        # All updates failed — function completed without crash, count is 0
        assert counts["resume_builds_flipped"] == 0
        assert counts["jobs_runs_flipped"] == 0
