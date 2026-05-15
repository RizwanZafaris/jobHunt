"""tests/test_llm_hardening.py — Regression tests for LLM hardening layer."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.modules.setdefault("supabase", MagicMock())

from agents.llm_hardening import (
    HardenedLLMRouter,
    HardenedLLMError,
    ProviderHealth,
    ProviderHealthTracker,
    CircuitBreaker,
    get_hardened_router,
    reset_hardened_router,
)
from agents.llm_router import LLMResult


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    reset_hardened_router()
    yield


@pytest.fixture
def mock_router():
    """A mock LLMRouter with controllable ask() behavior."""
    r = MagicMock()
    r.ask = AsyncMock()
    return r


@pytest.fixture
def hardened(mock_router):
    return HardenedLLMRouter(router=mock_router)


# ─── ProviderHealth tests ───────────────────────────────────────────────

def test_provider_health_tracks_success_rate():
    h = ProviderHealth(provider="anthropic")
    assert h.success_rate == 1.0  # default
    assert h.is_healthy is True

    h.record_failure("timeout")
    h.record_failure("timeout")
    assert h.success_rate == 0.0
    assert h.consecutive_failures == 2
    assert h.is_healthy is True  # still < 3 consecutive

    h.record_failure("timeout")
    assert h.consecutive_failures == 3
    assert h.is_healthy is False  # circuit should open

    h.record_success(latency_ms=100, cost_usd=0.01)
    assert h.consecutive_failures == 0
    assert h.is_healthy is True


def test_provider_health_rolling_errors():
    h = ProviderHealth(provider="openai")
    for i in range(7):
        h.record_failure(f"err_{i}")
    assert len(h.recent_errors) == 5
    assert h.recent_errors[-1] == "err_6"
    assert h.recent_errors[0] == "err_2"


# ─── CircuitBreaker tests ───────────────────────────────────────────────

def test_circuit_starts_closed():
    cb = CircuitBreaker(provider="anthropic")
    assert cb.state == "closed"
    assert cb.can_call() is True


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(provider="anthropic", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # still closed at 2
    cb.record_failure()
    assert cb.state == "open"
    assert cb.can_call() is False


def test_circuit_half_open_after_cooldown():
    cb = CircuitBreaker(provider="anthropic", failure_threshold=1, cooldown_seconds=0.01)
    cb.record_failure()
    assert cb.state == "open"
    # Small sleep to let cooldown elapse
    import time
    time.sleep(0.02)
    assert cb.state == "half_open"
    assert cb.can_call() is True


def test_circuit_closes_on_probe_success():
    cb = CircuitBreaker(provider="anthropic", failure_threshold=1)
    cb.record_failure()
    # Manually set to half_open
    cb._state = "half_open"
    cb.record_success()
    assert cb.state == "closed"


# ─── ProviderHealthTracker tests ────────────────────────────────────────

def test_tracker_best_provider():
    t = ProviderHealthTracker()
    # All start healthy — anthropic should win (highest in sort)
    best = t.best_provider()
    assert best is not None

    # Kill anthropic
    for _ in range(3):
        t.get("anthropic").record_failure("down")
    best = t.best_provider()
    assert best != "anthropic"


def test_tracker_snapshot():
    t = ProviderHealthTracker()
    t.get("anthropic").record_success(latency_ms=100, cost_usd=0.01)
    snap = t.snapshot()
    assert snap["anthropic"]["total_calls"] == 1
    assert snap["anthropic"]["success_rate"] == 1.0


# ─── HardenedLLMRouter.ask_safe tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_ask_safe_success(hardened, mock_router):
    """Happy path: single successful call."""
    mock_router.ask.return_value = LLMResult(
        text="hello", provider="anthropic", model="claude-opus-4-5",
        latency_ms=100, cost_usd=0.01,
    )
    result = await hardened.ask_safe(
        provider="anthropic", model="claude-opus-4-5",
        system="sys", messages=[{"role": "user", "content": "hi"}],
    )
    assert result.text == "hello"
    assert hardened._health.get("anthropic").success_calls == 1


@pytest.mark.asyncio
async def test_ask_safe_retries_then_succeeds(hardened, mock_router):
    """First call fails, retry succeeds."""
    mock_router.ask.side_effect = [
        Exception("network error"),
        LLMResult(text="hello", provider="anthropic", model="claude-opus-4-5"),
    ]
    result = await hardened.ask_safe(
        provider="anthropic", model="claude-opus-4-5",
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=2, base_delay=0.01,  # fast delay for test
    )
    assert result.text == "hello"
    assert mock_router.ask.call_count == 2


@pytest.mark.asyncio
async def test_ask_safe_all_retries_fail(hardened, mock_router):
    """All retries exhausted → HardenedLLMError."""
    mock_router.ask.side_effect = Exception("always fails")
    with pytest.raises(HardenedLLMError) as exc_info:
        await hardened.ask_safe(
            provider="anthropic", model="claude-opus-4-5",
            system="sys", messages=[{"role": "user", "content": "hi"}],
            max_retries=1, base_delay=0.01,
        )
    assert "always fails" in str(exc_info.value)
    assert exc_info.value.details["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_ask_safe_timeout(hardened, mock_router):
    """Call that exceeds timeout is caught and retried."""
    async def slow_call(**kwargs):
        await asyncio.sleep(10)
        return LLMResult(text="never", provider="anthropic", model="x")

    mock_router.ask = slow_call
    with pytest.raises(HardenedLLMError) as exc_info:
        await hardened.ask_safe(
            provider="anthropic", model="claude-opus-4-5",
            system="sys", messages=[{"role": "user", "content": "hi"}],
            call_timeout=0.05, max_retries=0, base_delay=0.01,
        )
    assert "timeout" in str(exc_info.value).lower() or "All" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ask_safe_circuit_opens(hardened, mock_router):
    """3 consecutive failures open the circuit."""
    mock_router.ask.side_effect = Exception("down")
    # First 3 calls should fail and open the circuit
    for _ in range(3):
        with pytest.raises(HardenedLLMError):
            await hardened.ask_safe(
                provider="anthropic", model="claude-opus-4-5",
                system="sys", messages=[{"role": "user", "content": "hi"}],
                max_retries=0, base_delay=0.01,
            )
    # 4th call should fail fast (circuit open)
    with pytest.raises(HardenedLLMError) as exc_info:
        await hardened.ask_safe(
            provider="anthropic", model="claude-opus-4-5",
            system="sys", messages=[{"role": "user", "content": "hi"}],
            max_retries=0,
        )
    assert "OPEN" in str(exc_info.value) or "Circuit" in str(exc_info.value)


# ─── ask_with_fallback tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_primary_succeeds(hardened, mock_router):
    """Primary works — no fallback needed."""
    mock_router.ask.return_value = LLMResult(
        text="from primary", provider="anthropic", model="claude-opus-4-5",
    )
    result = await hardened.ask_with_fallback(
        primary_provider="anthropic", primary_model="claude-opus-4-5",
        fallback_chain=[("openai", "gpt-4.1")],
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    assert result.text == "from primary"
    assert result.provider == "anthropic"


@pytest.mark.asyncio
async def test_fallback_to_secondary(hardened, mock_router):
    """Primary fails, secondary succeeds."""
    mock_router.ask.side_effect = [
        Exception("anthropic down"),
        LLMResult(text="from openai", provider="openai", model="gpt-4.1"),
    ]
    result = await hardened.ask_with_fallback(
        primary_provider="anthropic", primary_model="claude-opus-4-5",
        fallback_chain=[("openai", "gpt-4.1")],
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    assert result.text == "from openai"
    assert result.provider == "openai"


@pytest.mark.asyncio
async def test_fallback_all_fail(hardened, mock_router):
    """All providers fail → HardenedLLMError with attempt details."""
    mock_router.ask.side_effect = Exception("all down")
    with pytest.raises(HardenedLLMError) as exc_info:
        await hardened.ask_with_fallback(
            primary_provider="anthropic", primary_model="claude-opus-4-5",
            fallback_chain=[("openai", "gpt-4.1"), ("google", "gemini-2.5-pro")],
            system="sys", messages=[{"role": "user", "content": "hi"}],
            max_retries=0,
        )
    assert "All providers failed" in str(exc_info.value)


# ─── ask_auto tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ask_auto_selects_healthy_provider(hardened, mock_router):
    """Auto mode picks the best healthy provider.

    anthropic is unhealthy (circuit open) → should fall back to openai.
    We mock the router to fail for anthropic so the fallback kicks in.
    """
    # Make anthropic unhealthy + open its circuit
    for _ in range(3):
        hardened._health.get("anthropic").record_failure("down")
        hardened._circuits["anthropic"].record_failure()

    # Router fails for anthropic, succeeds for openai
    call_count = {"n": 0}
    async def _router_ask(*, provider, model, **kwargs):
        call_count["n"] += 1
        if provider == "anthropic":
            raise Exception("anthropic circuit open")
        return LLMResult(text="auto", provider="openai", model="gpt-4.1")

    mock_router.ask = _router_ask
    result = await hardened.ask_auto(
        quality_tier="high",
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    assert result.text == "auto"
    assert result.provider == "openai"
    assert call_count["n"] == 1  # circuit prevented anthropic call, only openai called


# ─── HardenedLLMError tests ─────────────────────────────────────────────

def test_error_details():
    err = HardenedLLMError("failed", provider="anthropic", model="x")
    assert err.details["provider"] == "anthropic"
    assert "anthropic" in str(err)


# ─── Singleton tests ────────────────────────────────────────────────────

def test_singleton():
    r1 = get_hardened_router()
    r2 = get_hardened_router()
    assert r1 is r2
    reset_hardened_router()
    r3 = get_hardened_router()
    assert r3 is not r1
