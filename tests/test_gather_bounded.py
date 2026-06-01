"""
tests/test_gather_bounded.py — proves the Phase-2 concurrency bound.

SCALABILITY.md P1 ("Unbounded asyncio.gather fan-outs") asked for every
per-item LLM/HTTP gather in agents/ to be capped with a Semaphore. This
suite proves:

  1. The shared `agents.concurrency.gather_bounded` helper never lets more
     than `limit` coroutines run at once (tracked via a live counter),
     while preserving result order and `return_exceptions` semantics.
  2. A real call site that was previously unbounded — the embedding
     fan-out in `persona_deep_research._upsert_knowledge` — is now bounded
     at `EMBED_CONCURRENCY`, verified end-to-end by monkeypatching the
     embed coroutine with a concurrency tracker.

These are pure-asyncio tests with no network/DB; the DB client is stubbed.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.concurrency import DEFAULT_GATHER_LIMIT, gather_bounded


class _ConcurrencyTracker:
    """Records the max number of simultaneously-running coroutines.

    Uses a tiny `hold` sleep so peers overlap rather than entering and
    exiting one at a time. `max_seen` is an upper-bound observation: it
    proves the cap is never *exceeded*. Asserting it *reaches* an exact
    value is timing-dependent under a loaded event loop, so the strict
    saturation proof lives in the barrier-based test below instead.
    """

    def __init__(self) -> None:
        self.current = 0
        self.max_seen = 0
        self.calls = 0
        self._lock = asyncio.Lock()

    async def run(self, value, *, hold: float = 0.02):
        async with self._lock:
            self.calls += 1
            self.current += 1
            self.max_seen = max(self.max_seen, self.current)
        try:
            await asyncio.sleep(hold)
            return value
        finally:
            async with self._lock:
                self.current -= 1


@pytest.mark.asyncio
async def test_gather_bounded_caps_concurrency_strict():
    """Deterministic saturation proof using a barrier.

    Each task increments a live counter, then waits on a barrier that only
    releases once `limit` tasks have arrived. If `gather_bounded` ever let
    more than `limit` run at once the counter would exceed `limit`; if it
    let fewer than `limit` run, the barrier would deadlock (caught by the
    timeout). This pins the cap exactly without depending on sleep timing.
    """
    n, limit = 20, 4
    current = 0
    max_seen = 0
    lock = asyncio.Lock()
    # A barrier that releases each cohort of exactly `limit` tasks.
    barrier = asyncio.Barrier(limit)

    async def _task(value):
        nonlocal current, max_seen
        async with lock:
            current += 1
            max_seen = max(max_seen, current)
        # Block until `limit` tasks are simultaneously parked here. This
        # can only complete if the semaphore admits a full cohort at once.
        await barrier.wait()
        async with lock:
            current -= 1
        return value

    factories = [lambda i=i: _task(i) for i in range(n)]
    # n is a multiple of limit so every cohort fills exactly; wrap in a
    # timeout so a regression to under-admitting fails loudly, not hangs.
    results = await asyncio.wait_for(
        gather_bounded(factories, limit=limit), timeout=10
    )

    assert results == list(range(n))   # all ran, order preserved
    assert max_seen == limit           # exactly `limit` overlapped — never more, never fewer


@pytest.mark.asyncio
async def test_gather_bounded_caps_concurrency():
    """With 20 tasks and limit=3, the cap is never exceeded."""
    tracker = _ConcurrencyTracker()
    n, limit = 20, 3

    factories = [lambda i=i: tracker.run(i) for i in range(n)]
    results = await gather_bounded(factories, limit=limit)

    assert results == list(range(n))            # all ran, order preserved
    assert tracker.max_seen <= limit            # never exceeded the cap
    assert tracker.max_seen >= 2                 # and genuinely ran concurrently


@pytest.mark.asyncio
async def test_gather_bounded_limit_one_is_sequential():
    tracker = _ConcurrencyTracker()
    factories = [lambda i=i: tracker.run(i) for i in range(6)]

    results = await gather_bounded(factories, limit=1)

    assert results == list(range(6))
    assert tracker.max_seen == 1


@pytest.mark.asyncio
async def test_gather_bounded_preserves_order_under_jitter():
    """Later tasks that finish first must still land in input order."""

    async def _delayed(value, delay):
        await asyncio.sleep(delay)
        return value

    # Task 0 sleeps longest, task 4 shortest — finish order is reversed.
    factories = [lambda i=i: _delayed(i, (5 - i) * 0.01) for i in range(5)]
    results = await gather_bounded(factories, limit=5)

    assert results == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_gather_bounded_propagates_exceptions_by_default():
    async def _boom():
        raise ValueError("kaboom")

    async def _ok():
        return 1

    with pytest.raises(ValueError, match="kaboom"):
        await gather_bounded([_ok, _boom, _ok], limit=2)


@pytest.mark.asyncio
async def test_gather_bounded_return_exceptions_passthrough():
    async def _boom():
        raise ValueError("kaboom")

    async def _ok():
        return 1

    results = await gather_bounded(
        [_ok, _boom, _ok], limit=2, return_exceptions=True
    )
    assert results[0] == 1
    assert isinstance(results[1], ValueError)
    assert results[2] == 1


@pytest.mark.asyncio
async def test_gather_bounded_empty_input():
    assert await gather_bounded([], limit=DEFAULT_GATHER_LIMIT) == []


@pytest.mark.asyncio
async def test_upsert_knowledge_embed_fan_out_is_bounded(monkeypatch):
    """Real call site: the embedding fan-out in persona_deep_research's
    _upsert_knowledge must never run more than EMBED_CONCURRENCY embed
    calls at once, even when handed many sections.
    """
    import sys

    from agents import persona_deep_research as pdr

    # _upsert_knowledge does a *call-time* `from db.client import embed`,
    # which resolves `sys.modules["db.client"]`. Patch that exact module
    # object — not a freshly `import`ed alias, which under a full-suite run
    # can resolve to a *different* db.client instance (duplicate import),
    # leaving our patch on the wrong object while the real embed fires a
    # live OpenAI call that _safe_embed silently swallows.
    db_client = sys.modules["db.client"]

    tracker = _ConcurrencyTracker()

    async def _tracked_embed(text: str):
        # Returns a dummy vector; the value doesn't matter for this test.
        return await tracker.run([0.0, 0.0, 0.0])

    monkeypatch.setattr(db_client, "embed", _tracked_embed)
    assert db_client.embed is _tracked_embed

    # Stub the Supabase client so no network/DB is touched. The batch
    # upsert path returns a row count; we only care that embeds were bounded.
    class _FakeResult:
        data = [{}]

    class _FakeQuery:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def upsert(self, payload, *a, **k):
            self._payload = payload
            return self

        def execute(self):
            # companies.id lookup returns no rows; knowledge upsert returns one.
            return _FakeResult()

    class _FakeDB:
        def table(self, name):
            return _FakeQuery()

    monkeypatch.setattr(db_client, "get_supabase", lambda: _FakeDB())

    # Feed every allowed section so the fan-out is comfortably wider than
    # the concurrency cap (EMBED_CONCURRENCY).
    sections = {sec: f"content for {sec}" for sec in pdr.SECTIONS}
    assert len(sections) > pdr.EMBED_CONCURRENCY  # otherwise the test is vacuous

    await pdr._upsert_knowledge("ACME Corp", sections)

    # Guard: prove our stub actually ran (one call per section). If this
    # fails the real embed was used and the concurrency numbers are noise.
    assert tracker.calls == len(sections)

    # The load-bearing property: the call site never exceeds the cap, even
    # with 13 sections. We don't assert exact saturation here — under a
    # loaded full-suite event loop the embeds (20ms each) may not all
    # overlap at the sampled instant. Exact saturation is proven
    # deterministically in test_gather_bounded_caps_concurrency_strict.
    assert tracker.max_seen <= pdr.EMBED_CONCURRENCY  # cap held
    assert tracker.max_seen >= 2                      # genuinely concurrent, not serialized
