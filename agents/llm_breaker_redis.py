#!/usr/bin/env python3
"""
agents/llm_breaker_redis.py — Redis-backed circuit breaker for fleet-wide
provider backpressure.

Background (docs/SCALABILITY.md, build-plan PR P2-4)
────────────────────────────────────────────────────
The in-memory ``CircuitBreaker`` in ``agents/llm_hardening.py`` is a per-process
dataclass: its failure counter and OPEN/CLOSED state live only inside one
worker. When jobHunt runs as a single replica that's perfect — but the moment
you scale to N workers, each worker independently re-discovers that a provider
is 429ing. Worker A trips its breaker and stops calling Anthropic; workers
B…N keep hammering the same rate-limited endpoint because they never saw A's
failures. The provider stays angry, and the fleet never collectively backs off.

``RedisCircuitBreaker`` externalizes the breaker state into a shared Redis hash
so all replicas read/write the *same* counter. One worker's failures push the
breaker OPEN for everyone; one worker's probe success closes it for everyone.

Public interface — byte-for-byte compatible with ``CircuitBreaker``
────────────────────────────────────────────────────────────────────
    cb = RedisCircuitBreaker(provider="anthropic")
    cb.can_call()        -> bool
    cb.record_success()  -> None
    cb.record_failure()  -> None
    cb.state             -> "closed" | "open" | "half_open"

State machine (identical semantics to the in-memory breaker):
    CLOSED    → normal operation
    OPEN      → after ``failure_threshold`` consecutive failures; all calls
                fast-fail until ``cooldown_seconds`` elapse
    HALF_OPEN → after cooldown; the next call is a probe. A probe success
                closes the breaker; a probe failure re-opens it.

Two deliberate differences from the in-memory version, both forced by the
fact that this state is shared across processes:

  1. **Wall-clock time** (``time.time()``) instead of ``time.perf_counter()``.
     ``perf_counter()`` is only monotonic *within* one process — its zero
     point differs per process, so a timestamp written by worker A is
     meaningless to worker B. Wall-clock is comparable across the fleet.

  2. **FAIL OPEN on any Redis error.** This is the single most important
     property of this class. A circuit breaker exists to *protect* the system;
     it must never *become* the outage. If Redis is unreachable, slow, or
     returns garbage, ``can_call()`` returns ``True`` (allow the call) and
     ``record_*`` silently no-op. A Redis blip must never block every LLM call
     across the whole fleet. The native ``HardenedLLMRouter`` retry/fallback
     machinery is the backstop while Redis is down.

Storage layout — Redis hash ``jh:breaker:<provider>``:
    state              b"closed" | b"open" | b"half_open"
    failure_count      integer (consecutive failures)
    last_failure_time  float (wall-clock epoch seconds of the last failure)

The Redis singleton is reused from ``api.queue._get_redis`` so we share one
connection pool with the RQ job queue (it is created with
``decode_responses=False``, so hash reads come back as ``bytes`` — handled
below).
"""
from __future__ import annotations

import logging
import time
from typing import Literal

logger = logging.getLogger(__name__)

# Mirror the in-memory defaults so behavior is identical apart from the backend.
_DEFAULT_FAILURE_THRESHOLD = 3
_DEFAULT_COOLDOWN_SECONDS = 300.0  # 5 minutes

_KEY_PREFIX = "jh:breaker:"

State = Literal["closed", "open", "half_open"]


class RedisCircuitBreaker:
    """Per-provider circuit breaker whose state lives in a shared Redis hash.

    Drop-in replacement for ``agents.llm_hardening.CircuitBreaker`` — same
    public surface (``can_call`` / ``record_success`` / ``record_failure`` /
    ``state``). Selected via ``settings.breaker_backend == "redis"``.

    CRITICAL invariant: every Redis interaction is wrapped so that any failure
    (connection error, timeout, unexpected reply) results in FAIL-OPEN
    behavior — ``can_call`` returns ``True`` and the recorders no-op. A Redis
    outage degrades us to "no shared breaker", never to "all calls blocked".
    """

    def __init__(
        self,
        provider: str,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._key = f"{_KEY_PREFIX}{provider}"

    # ─── Redis access (lazy, shared singleton) ───────────────────────────
    def _redis(self):
        """Return the shared Redis connection.

        Imported lazily (and inside the method, not at module import) so that:
          - importing this module never forces a redis-py dependency, mirroring
            ``api.queue`` (lets CI/lint run without redis installed), and
          - we reuse the exact same connection pool as the RQ job queue.
        """
        from api.queue import _get_redis
        return _get_redis()

    @staticmethod
    def _as_str(value) -> str | None:
        """Normalize a Redis hash value (bytes or str or None) to str.

        The shared connection is created with ``decode_responses=False`` so
        string fields come back as ``bytes``; tolerate both forms so this class
        works regardless of how the pool was configured.
        """
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)

    # ─── State property ──────────────────────────────────────────────────
    @property
    def state(self) -> State:
        """Current breaker state, reading shared Redis state.

        Lazily transitions OPEN → HALF_OPEN once ``cooldown_seconds`` have
        elapsed since the last recorded failure (matching the in-memory
        breaker). The transition is written back so that whichever replica
        observes the elapsed cooldown first hands the probe slot to the fleet.

        FAIL-OPEN: on any Redis error we report ``"closed"`` (normal
        operation) so callers proceed — never report a fabricated OPEN that
        would block calls.
        """
        try:
            r = self._redis()
            data = r.hmget(self._key, "state", "last_failure_time")
            raw_state = self._as_str(data[0]) if data else None
            if raw_state is None:
                # No record yet → never tripped → closed.
                return "closed"
            if raw_state != "open":
                # "closed" or "half_open" are returned verbatim.
                return raw_state  # type: ignore[return-value]

            # state == "open": check whether the cooldown has elapsed.
            last_failure = self._as_str(data[1]) if len(data) > 1 else None
            last_failure_time = float(last_failure) if last_failure else 0.0
            elapsed = time.time() - last_failure_time
            if elapsed >= self.cooldown_seconds:
                # Promote to half_open for the whole fleet so a single probe
                # can flow. Use HSET (not a transaction) — worst case two
                # replicas both promote, which is harmless: both probe, the
                # first success closes the breaker.
                r.hset(self._key, "state", "half_open")
                logger.info(
                    f"[circuit:redis] {self.provider} → HALF_OPEN after {elapsed:.0f}s"
                )
                return "half_open"
            return "open"
        except Exception as e:  # noqa: BLE001 — FAIL OPEN on ANY Redis error
            logger.warning(
                f"[circuit:redis] state read failed for {self.provider}, "
                f"failing open (treating as closed): {type(e).__name__}: {e}"
            )
            return "closed"

    # ─── Decision ─────────────────────────────────────────────────────────
    def can_call(self) -> bool:
        """Whether a call may proceed.

        Allowed when CLOSED (normal) or HALF_OPEN (probe). Blocked only when
        OPEN.

        FAIL-OPEN: ``state`` already returns ``"closed"`` on any Redis error,
        so this method inherits the fail-open guarantee — a Redis outage can
        never make ``can_call`` return ``False`` and block the fleet.
        """
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            return True  # allow the probe call
        return False

    # ─── Recorders ──────────────────────────────────────────────────────
    def record_success(self):
        """Record a successful call.

        From HALF_OPEN this closes the breaker for the whole fleet (probe
        succeeded). From CLOSED it resets the shared failure counter. No-ops
        on any Redis error (a lost success record is harmless — the breaker
        simply stays where it is).
        """
        try:
            r = self._redis()
            current = self._as_str(r.hget(self._key, "state")) or "closed"
            if current == "half_open":
                r.hset(self._key, mapping={"state": "closed", "failure_count": 0})
                logger.info(f"[circuit:redis] {self.provider} → CLOSED (probe succeeded)")
            else:
                # CLOSED (or absent) → just clear the consecutive-failure count.
                r.hset(self._key, mapping={"state": "closed", "failure_count": 0})
        except Exception as e:  # noqa: BLE001 — never raise from the success path
            logger.warning(
                f"[circuit:redis] record_success failed for {self.provider}, "
                f"ignoring: {type(e).__name__}: {e}"
            )

    def record_failure(self):
        """Record a failed call.

        Atomically increments the shared consecutive-failure counter (HINCRBY)
        and stamps the wall-clock failure time. Once the counter reaches
        ``failure_threshold`` the breaker trips OPEN for the whole fleet.
        No-ops on any Redis error (a lost failure record just means we trip a
        little later — acceptable; the local retry/fallback layer still
        protects this call).
        """
        try:
            r = self._redis()
            failure_count = r.hincrby(self._key, "failure_count", 1)
            r.hset(self._key, "last_failure_time", time.time())
            if failure_count >= self.failure_threshold:
                r.hset(self._key, "state", "open")
                logger.warning(
                    f"[circuit:redis] {self.provider} → OPEN after "
                    f"{failure_count} consecutive failures (fleet-wide)"
                )
        except Exception as e:  # noqa: BLE001 — never raise from the failure path
            logger.warning(
                f"[circuit:redis] record_failure failed for {self.provider}, "
                f"ignoring: {type(e).__name__}: {e}"
            )
