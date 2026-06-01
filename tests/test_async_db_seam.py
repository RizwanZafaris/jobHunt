"""
Tests for the additive async DB seam (P2-5).

The seam (`db/client.py`) lets async call sites run the synchronous
supabase-py client off the event loop without rewriting the sync helpers:

  - `aexecute(query_builder)` runs `query_builder.execute()` in FastAPI's
    threadpool, bounded by a module-level `asyncio.Semaphore` sized from
    `DB_THREADPOOL_CONCURRENCY`.
  - the `a*` twins (`aget_job`, `aupdate_job`, `aget_company_by_name`,
    `aupsert_job`, `aupsert_company`) are thin wrappers that CALL their sync
    counterpart via `run_in_threadpool` — never reimplement it.

These tests assert two properties:

  1. **Delegation + offload.** Each twin calls the canonical sync function
     (patched), passes args through, returns its result — and the sync work
     actually runs on a threadpool worker thread, not the event-loop thread.
  2. **Bounded concurrency.** The semaphore caps how many `aexecute` calls
     occupy the threadpool at once at `DB_THREADPOOL_CONCURRENCY`.

The seam is purely additive: nothing in the app calls it yet (handler
conversion is P2-8…N), so these tests are the only exercise of the new path.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Stub supabase so importing db.client never needs the real package / network.
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from db import client as dbc  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db_semaphore():
    """Each test gets a fresh, unbound semaphore so a custom concurrency set
    in one test never leaks into the next (the singleton is lazily built)."""
    dbc._db_semaphore = None
    yield
    dbc._db_semaphore = None


# ── 1. aexecute: offloads .execute() to a worker thread ────────────────────


@pytest.mark.asyncio
async def test_aexecute_runs_execute_off_the_event_loop():
    """aexecute calls query_builder.execute() and returns its result, and the
    call happens on a different thread than the event loop (i.e. the threadpool).
    """
    loop_thread = threading.get_ident()
    seen = {}

    class _QB:
        def execute(self):
            seen["thread"] = threading.get_ident()
            return "RESULT"

    result = await dbc.aexecute(_QB())

    assert result == "RESULT"
    assert seen["thread"] != loop_thread, (
        "execute() ran on the event-loop thread — it was not offloaded "
        "to the threadpool"
    )


@pytest.mark.asyncio
async def test_aexecute_propagates_exceptions():
    """A failure inside the sync .execute() surfaces to the awaiter unchanged."""

    class _Boom(RuntimeError):
        pass

    class _QB:
        def execute(self):
            raise _Boom("db down")

    with pytest.raises(_Boom, match="db down"):
        await dbc.aexecute(_QB())


# ── 2. Async twins: call the sync fn via the threadpool, pass through ───────


@pytest.mark.asyncio
async def test_aget_job_delegates_to_sync_get_job():
    sentinel = {"id": 7, "title": "PM"}
    with patch.object(dbc, "get_job", return_value=sentinel) as mock:
        out = await dbc.aget_job(7)
    assert out is sentinel
    mock.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_aupdate_job_delegates_to_sync_update_job():
    updates = {"status": "applied"}
    with patch.object(dbc, "update_job", return_value={"id": 3, **updates}) as mock:
        out = await dbc.aupdate_job(3, updates)
    assert out == {"id": 3, "status": "applied"}
    mock.assert_called_once_with(3, updates)


@pytest.mark.asyncio
async def test_aget_company_by_name_delegates_to_sync():
    row = {"id": 1, "name": "Visa"}
    with patch.object(dbc, "get_company_by_name", return_value=row) as mock:
        out = await dbc.aget_company_by_name("Visa")
    assert out is row
    mock.assert_called_once_with("Visa")


@pytest.mark.asyncio
async def test_aupsert_job_delegates_to_sync_upsert_job():
    job = {"url": "https://x/1", "title": "PM"}
    with patch.object(dbc, "upsert_job", return_value={"id": 9}) as mock:
        out = await dbc.aupsert_job(job, user_id="u-1")
    assert out == {"id": 9}
    mock.assert_called_once_with(job, "u-1")


@pytest.mark.asyncio
async def test_aupsert_company_delegates_to_sync_upsert_company():
    company = {"name": "Adyen"}
    with patch.object(dbc, "upsert_company", return_value={"id": 2}) as mock:
        out = await dbc.aupsert_company(company)
    assert out == {"id": 2}
    # user_id defaults to None when the caller omits it.
    mock.assert_called_once_with(company, None)


@pytest.mark.asyncio
async def test_twin_runs_sync_fn_on_worker_thread():
    """Strong proof a twin offloads: the patched sync fn observes a thread id
    different from the event-loop thread (so it ran in the threadpool)."""
    loop_thread = threading.get_ident()
    seen = {}

    def _fake_get_job(job_id):
        seen["thread"] = threading.get_ident()
        seen["arg"] = job_id
        return {"id": job_id}

    with patch.object(dbc, "get_job", side_effect=_fake_get_job):
        out = await dbc.aget_job(42)

    assert out == {"id": 42}
    assert seen["arg"] == 42
    assert seen["thread"] != loop_thread


# ── 3. The semaphore bounds concurrency ────────────────────────────────────


@pytest.mark.asyncio
async def test_semaphore_is_sized_from_settings(monkeypatch):
    """_get_db_semaphore reads DB_THREADPOOL_CONCURRENCY from settings."""
    # Drop the cached Settings singleton so our env override is picked up.
    import config.settings as settings_mod

    monkeypatch.setenv("DB_THREADPOOL_CONCURRENCY", "5")
    settings_mod._settings = None
    try:
        sem = dbc._get_db_semaphore()
        # asyncio.Semaphore exposes its current value; freshly built == limit.
        assert sem._value == 5
    finally:
        settings_mod._settings = None


@pytest.mark.asyncio
async def test_aexecute_concurrency_is_bounded_by_semaphore(monkeypatch):
    """No more than DB_THREADPOOL_CONCURRENCY aexecute calls may be in-flight
    (inside .execute()) simultaneously, even when many are launched at once."""
    import config.settings as settings_mod

    LIMIT = 3
    LAUNCHED = 10

    monkeypatch.setenv("DB_THREADPOOL_CONCURRENCY", str(LIMIT))
    settings_mod._settings = None
    dbc._db_semaphore = None  # rebuild against the new limit

    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}
    # Block each execute() until the test releases it — guarantees calls pile
    # up so we actually observe the ceiling rather than racing through.
    release = threading.Event()

    class _QB:
        def execute(self):
            with lock:
                state["in_flight"] += 1
                state["max_in_flight"] = max(
                    state["max_in_flight"], state["in_flight"]
                )
            # Wait until the test has had a chance to launch everything.
            release.wait(timeout=5)
            with lock:
                state["in_flight"] -= 1
            return "ok"

    tasks = [asyncio.create_task(dbc.aexecute(_QB())) for _ in range(LAUNCHED)]

    # Give the event loop time to schedule as many as the semaphore allows.
    # Poll until in-flight stabilises at the limit (or a timeout elapses).
    for _ in range(50):
        await asyncio.sleep(0.02)
        with lock:
            if state["max_in_flight"] >= LIMIT:
                break

    with lock:
        observed_peak = state["max_in_flight"]

    release.set()
    results = await asyncio.gather(*tasks)

    assert results == ["ok"] * LAUNCHED
    # The semaphore must never let more than LIMIT run concurrently...
    assert observed_peak <= LIMIT, (
        f"semaphore breach: {observed_peak} concurrent execute() calls > "
        f"limit {LIMIT}"
    )
    # ...and it must actually saturate to the limit (proves it's the binding
    # constraint, not just that fewer than LIMIT happened to run).
    assert observed_peak == LIMIT
