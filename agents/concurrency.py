"""
agents/concurrency.py — small async concurrency helpers.

`gather_bounded` is the project-standard way to fan a list of per-item
LLM/HTTP coroutines out across an `asyncio.Semaphore` instead of an
unbounded `asyncio.gather(*[...])`.

Why this exists (Phase 2, SCALABILITY.md P1 "Unbounded asyncio.gather
fan-outs"): an unbounded gather over N rows starts N coroutines at once.
For LLM/embedding/HTTP fan-outs that means N concurrent provider calls —
which blows past provider rate limits (429s), spikes cost, and at
multi-tenant scale lets one large batch saturate a shared key. Bounding
with a semaphore caps in-flight calls to `limit` while preserving result
order and behaviour.

Several agent paths already inline this pattern with their own
`asyncio.Semaphore` + a closure (e.g. the job scout's discovery/validation
fan-outs). This helper exists so new fan-outs don't have to re-roll it.

The input is *coroutine factories* (zero-arg callables returning a
coroutine), not coroutines, so that no coroutine is created — and thus
none can be left un-awaited — until a semaphore slot is free.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

# Default in-flight cap for LLM/HTTP fan-outs. Matches the more
# conservative of the limits the codebase already uses by hand
# (Semaphore(3) for Perplexity/Apify discovery; Semaphore(8) for the
# lighter validation HTTP fan-out). Callers should pass an explicit
# limit when they have a reason to.
DEFAULT_GATHER_LIMIT = 5


async def gather_bounded(
    coro_factories: list[Callable[[], Awaitable[T]]],
    limit: int = DEFAULT_GATHER_LIMIT,
    *,
    return_exceptions: bool = False,
) -> list[T]:
    """Run `coro_factories` concurrently, capping in-flight calls at `limit`.

    Args:
        coro_factories: list of zero-arg callables, each returning a
            coroutine (e.g. ``lambda c=c: _embed(c)``). Factories — not
            coroutines — so nothing is scheduled until a semaphore slot
            opens, which avoids "coroutine was never awaited" warnings if
            construction is cheap-but-eager.
        limit: max number of coroutines in flight at once (>= 1).
        return_exceptions: forwarded to ``asyncio.gather``; when True,
            exceptions are returned in-place instead of propagating.

    Returns:
        Results in the same order as `coro_factories` (gather semantics).
    """
    sem = asyncio.Semaphore(max(1, limit))

    async def _run(factory: Callable[[], Awaitable[T]]) -> T:
        async with sem:
            return await factory()

    return await asyncio.gather(
        *[_run(cf) for cf in coro_factories],
        return_exceptions=return_exceptions,
    )
