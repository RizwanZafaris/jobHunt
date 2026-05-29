"""
Per-request / per-task tenant context (Phase 1, P1-1).

A single ``ContextVar`` carrying the ``user_id`` for the currently executing
request or background job. It is *set* here in Phase 1 (by the
``api.context.get_current_user`` dependency and by service entrypoints that
resolve a system user) and *read* later by Phase 2 (the per-tenant budget gate
in ``BaseAgent.ask()`` and the per-user rate limiter) — so the agent call path
is not re-plumbed twice. See docs/SCALABILITY_BUILD_PLAN.md §1 rule 3.

Design notes:

- ``ContextVar`` is the right primitive: it is coroutine-safe and isolates
  value per asyncio Task, so concurrent requests on one event-loop worker never
  see each other's ``user_id``. ``threading.local`` would not survive the
  ``await`` boundary.
- The default is ``None`` ("no tenant in scope"). Today, in single-user mode,
  callers that never set it still work exactly as before — nothing in the live
  hot path reads this yet. It exists so Phase 2 can read it without touching
  Phase 1's call sites again.
- ``set_current_user_id`` returns the ``Token`` from ``ContextVar.set`` so a
  caller can ``reset`` it; ``user_scope`` wraps that in a context manager for
  worker/cron entrypoints that process one tenant's job at a time.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from typing import Iterator, Optional

# The current tenant's user_id (stringified UUID) or None when no tenant is in
# scope (e.g. truly system-level work, or before any dependency has run).
_current_user_id: ContextVar[Optional[str]] = ContextVar(
    "jobhunt_current_user_id", default=None
)


def set_current_user_id(user_id: Optional[str]) -> Token:
    """Bind ``user_id`` to the current context. Returns a reset token."""
    return _current_user_id.set(str(user_id) if user_id is not None else None)


def get_current_user_id() -> Optional[str]:
    """Return the current tenant's user_id, or None if none is in scope."""
    return _current_user_id.get()


def reset_current_user_id(token: Token) -> None:
    """Restore the context var to its prior value using ``token``."""
    _current_user_id.reset(token)


@contextlib.contextmanager
def user_scope(user_id: Optional[str]) -> Iterator[None]:
    """Context manager binding ``user_id`` for the duration of the block.

    Intended for worker/cron entrypoints that handle one tenant's job at a
    time::

        with user_scope(run.user_id):
            do_the_work()
    """
    token = set_current_user_id(user_id)
    try:
        yield
    finally:
        reset_current_user_id(token)
