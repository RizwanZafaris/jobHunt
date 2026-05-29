"""tests/test_redis_circuit_breaker.py — RedisCircuitBreaker (P2-4).

Covers the Redis-backed circuit breaker that gives jobHunt fleet-wide
provider backpressure (docs/SCALABILITY_BUILD_PLAN.md P2-4):

  - state-machine parity with the in-memory ``CircuitBreaker``
    (closed → open after threshold → half_open after cooldown → closed on
    probe success),
  - cross-replica sharing (two breaker instances over the same Redis see each
    other's failures), and
  - the non-negotiable FAIL-OPEN guarantee: any Redis error must let calls
    through, never block them.

Most tests need a working Redis. We use ``fakeredis`` (an in-memory Redis
double) so the suite runs without a live server. ``fakeredis`` is NOT in
``~/crewai-env`` today, so those tests SKIP cleanly when it's missing —
install it (``pip install fakeredis``) to exercise them. The FAIL-OPEN test
needs no Redis at all (it injects a stub that raises) and always runs.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# api.queue imports lazily, but agents.* pull in supabase transitively in some
# paths; stub it like the sibling hardening test does so import never fails.
sys.modules.setdefault("supabase", MagicMock())

from agents.llm_breaker_redis import RedisCircuitBreaker  # noqa: E402

try:
    import fakeredis  # type: ignore

    HAS_FAKEREDIS = True
except ImportError:  # pragma: no cover - depends on env
    HAS_FAKEREDIS = False

requires_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS,
    reason="fakeredis not installed in ~/crewai-env; `pip install fakeredis` to run",
)


@pytest.fixture
def fake_redis():
    """A fresh in-memory fake Redis, wired into api.queue._get_redis.

    The breaker reuses the queue's Redis singleton, so we patch the singleton
    accessor to hand back our fake. ``decode_responses=False`` matches the real
    production connection (``api/queue.py``), which returns ``bytes`` — this is
    exactly the case the breaker's byte-decoding must handle.
    """
    server = fakeredis.FakeStrictRedis(decode_responses=False)
    with patch("api.queue._get_redis", return_value=server):
        yield server


# ─── State-machine parity with the in-memory CircuitBreaker ──────────────

@requires_fakeredis
def test_starts_closed(fake_redis):
    cb = RedisCircuitBreaker(provider="anthropic")
    assert cb.state == "closed"
    assert cb.can_call() is True


@requires_fakeredis
def test_opens_after_threshold(fake_redis):
    cb = RedisCircuitBreaker(provider="anthropic", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # still closed at 2
    assert cb.can_call() is True
    cb.record_failure()
    assert cb.state == "open"  # tripped at 3
    assert cb.can_call() is False


@requires_fakeredis
def test_half_open_after_cooldown(fake_redis):
    cb = RedisCircuitBreaker(
        provider="anthropic", failure_threshold=1, cooldown_seconds=0.05
    )
    cb.record_failure()
    assert cb.state == "open"
    assert cb.can_call() is False
    time.sleep(0.06)  # let cooldown elapse
    assert cb.state == "half_open"
    assert cb.can_call() is True  # probe allowed


@requires_fakeredis
def test_closes_on_probe_success(fake_redis):
    cb = RedisCircuitBreaker(
        provider="anthropic", failure_threshold=1, cooldown_seconds=0.05
    )
    cb.record_failure()
    time.sleep(0.06)
    assert cb.state == "half_open"
    cb.record_success()  # probe succeeds
    assert cb.state == "closed"
    assert cb.can_call() is True


@requires_fakeredis
def test_probe_failure_reopens(fake_redis):
    cb = RedisCircuitBreaker(
        provider="anthropic", failure_threshold=1, cooldown_seconds=0.05
    )
    cb.record_failure()
    time.sleep(0.06)
    assert cb.state == "half_open"
    cb.record_failure()  # probe fails → back to open (threshold=1)
    assert cb.state == "open"
    assert cb.can_call() is False


@requires_fakeredis
def test_success_resets_failure_count(fake_redis):
    """A success while CLOSED clears the consecutive-failure counter, so it
    takes a fresh full streak to trip — matching the in-memory breaker."""
    cb = RedisCircuitBreaker(provider="anthropic", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # resets the streak
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # only 2 since reset, threshold is 3
    cb.record_failure()
    assert cb.state == "open"


# ─── Cross-replica sharing (the whole point of P2-4) ─────────────────────

@requires_fakeredis
def test_state_is_shared_across_instances(fake_redis):
    """Two breaker objects over the same Redis represent two replicas. One
    replica's failures must trip the breaker for the other."""
    worker_a = RedisCircuitBreaker(provider="openai", failure_threshold=3)
    worker_b = RedisCircuitBreaker(provider="openai", failure_threshold=3)

    worker_a.record_failure()
    worker_a.record_failure()
    worker_a.record_failure()  # A trips the shared breaker

    # B never recorded a failure itself, but sees A's state and backs off.
    assert worker_b.state == "open"
    assert worker_b.can_call() is False


@requires_fakeredis
def test_providers_are_isolated(fake_redis):
    """Each provider has its own hash key — tripping one must not affect
    another."""
    anthropic = RedisCircuitBreaker(provider="anthropic", failure_threshold=1)
    openai = RedisCircuitBreaker(provider="openai", failure_threshold=1)
    anthropic.record_failure()
    assert anthropic.state == "open"
    assert openai.state == "closed"  # unaffected
    assert openai.can_call() is True


@requires_fakeredis
def test_uses_expected_redis_key(fake_redis):
    cb = RedisCircuitBreaker(provider="deepseek", failure_threshold=1)
    cb.record_failure()
    assert fake_redis.exists("jh:breaker:deepseek")


# ─── FAIL-OPEN guarantee (runs without fakeredis) ────────────────────────

def _exploding_redis():
    """A Redis stand-in whose every method raises — simulates a Redis outage."""
    boom = MagicMock()
    err = ConnectionError("redis is down")
    for method in ("hmget", "hget", "hset", "hincrby", "exists", "ping"):
        getattr(boom, method).side_effect = err
    return boom


def test_can_call_fails_open_on_redis_error():
    """The single most important property: if Redis errors, can_call() must
    return True (allow the call) — a Redis blip must never block the fleet."""
    with patch("api.queue._get_redis", return_value=_exploding_redis()):
        cb = RedisCircuitBreaker(provider="anthropic")
        assert cb.can_call() is True
        assert cb.state == "closed"  # fail-open reports closed


def test_can_call_fails_open_when_redis_unavailable():
    """If even acquiring the Redis connection raises (redis-py not installed,
    bad URL), can_call() still fails open."""
    with patch("api.queue._get_redis", side_effect=RuntimeError("no redis-py")):
        cb = RedisCircuitBreaker(provider="anthropic")
        assert cb.can_call() is True
        assert cb.state == "closed"


def test_recorders_never_raise_on_redis_error():
    """record_success / record_failure must swallow Redis errors — they sit on
    the LLM hot path and must never turn a provider blip into a crash."""
    with patch("api.queue._get_redis", return_value=_exploding_redis()):
        cb = RedisCircuitBreaker(provider="anthropic")
        cb.record_failure()  # must not raise
        cb.record_success()  # must not raise


def test_public_interface_matches_in_memory_breaker():
    """Guard the drop-in contract: RedisCircuitBreaker must expose the same
    public surface the HardenedLLMRouter relies on."""
    from agents.llm_hardening import CircuitBreaker

    redis_cb = RedisCircuitBreaker(provider="anthropic")
    mem_cb = CircuitBreaker(provider="anthropic")
    for attr in ("can_call", "record_success", "record_failure", "state"):
        assert hasattr(redis_cb, attr), f"missing {attr}"
        assert hasattr(mem_cb, attr)
    assert callable(redis_cb.can_call)
    assert callable(redis_cb.record_success)
    assert callable(redis_cb.record_failure)


# ─── Backend selection in HardenedLLMRouter (default unchanged) ──────────

def test_router_defaults_to_in_memory_breaker():
    """With breaker_backend unset/="memory", the router must build the
    in-memory CircuitBreaker — single-user prod stays byte-for-byte unchanged."""
    from agents.llm_hardening import (
        HardenedLLMRouter,
        CircuitBreaker,
        reset_hardened_router,
    )

    reset_hardened_router()
    settings = MagicMock()
    settings.breaker_backend = "memory"
    with patch("config.settings.get_settings", return_value=settings), \
            patch("agents.llm_router.get_router", return_value=MagicMock()):
        router = HardenedLLMRouter()
    for cb in router._circuits.values():
        assert isinstance(cb, CircuitBreaker)
        assert not isinstance(cb, RedisCircuitBreaker)


def test_router_selects_redis_breaker_when_flagged():
    """breaker_backend="redis" must build RedisCircuitBreaker instances."""
    from agents.llm_hardening import HardenedLLMRouter, reset_hardened_router

    reset_hardened_router()
    settings = MagicMock()
    settings.breaker_backend = "redis"
    with patch("config.settings.get_settings", return_value=settings), \
            patch("agents.llm_router.get_router", return_value=MagicMock()):
        router = HardenedLLMRouter()
    for cb in router._circuits.values():
        assert isinstance(cb, RedisCircuitBreaker)
