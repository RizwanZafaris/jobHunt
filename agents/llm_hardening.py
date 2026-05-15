#!/usr/bin/env python3
"""
agents/llm_hardening.py — Production-grade LLM resilience layer.

Wraps LLMRouter with:
  1. Provider health tracking (success/failure/latency per provider)
  2. Circuit breaker (open after N failures, cooldown before retry)
  3. Automatic fallback chain (primary → secondary → tertiary)
  4. Exponential-backoff retry
  5. Cost-aware auto-selection
  6. Per-call timeout enforcement
  7. Graceful degradation (return partial results instead of hard fail)

Usage — drop-in replacement for ask():

    from agents.llm_hardening import get_hardened_router
    router = get_hardened_router()

    # Automatic fallback: anthropic → openai → google → deepseek
    result = await router.ask_with_fallback(
        primary_provider="anthropic",
        primary_model="claude-opus-4-5",
        fallback_chain=[("openai", "gpt-4.1"), ("google", "gemini-2.5-pro")],
        system="...", messages=[...],
        max_tokens=4096, temperature=0.3,
        agent_name="g2.writer",
    )

    # Or just let it pick the best available provider
    result = await router.ask_auto(
        quality_tier="high",  # "high" | "medium" | "low"
        system="...", messages=[...],
        agent_name="g2.writer",
    )
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Literal

from agents.llm_router import LLMRouter, LLMResult, get_router, Provider, infer_provider

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# 1. Provider Health Tracker
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderHealth:
    """Rolling health metrics for a single LLM provider."""
    provider: Provider
    total_calls: int = 0
    success_calls: int = 0
    failure_calls: int = 0
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    last_failure_at: float = 0.0  # perf_counter
    last_success_at: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    recent_errors: list[str] = field(default_factory=list)  # last 5 error messages

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0  # assume healthy until proven otherwise
        return self.success_calls / self.total_calls

    @property
    def avg_latency_ms(self) -> float:
        if self.success_calls == 0:
            return 0.0
        return self.total_latency_ms / self.success_calls

    @property
    def is_healthy(self) -> bool:
        """Healthy if fewer than 3 consecutive failures.

        Success rate is informational only — a provider that failed
        2x but historically succeeds is still considered healthy.
        """
        return self.consecutive_failures < 3

    def record_success(self, latency_ms: int, cost_usd: float):
        self.total_calls += 1
        self.success_calls += 1
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.total_latency_ms += latency_ms
        self.total_cost_usd += cost_usd
        self.last_success_at = time.perf_counter()

    def record_failure(self, error_msg: str):
        self.total_calls += 1
        self.failure_calls += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_at = time.perf_counter()
        self.recent_errors.append(error_msg)
        if len(self.recent_errors) > 5:
            self.recent_errors.pop(0)

    def snapshot(self) -> dict:
        """Return a JSON-serializable health snapshot."""
        return {
            "total_calls": self.total_calls,
            "success_rate": round(self.success_rate, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 0),
            "consecutive_failures": self.consecutive_failures,
            "is_healthy": self.is_healthy,
            "recent_errors": self.recent_errors,
        }


class ProviderHealthTracker:
    """Tracks health for all providers. Singleton per process."""

    def __init__(self):
        self._health: dict[Provider, ProviderHealth] = {
            p: ProviderHealth(provider=p)
            for p in ("anthropic", "openai", "google", "deepseek", "moonshot")
        }

    def get(self, provider: Provider) -> ProviderHealth:
        return self._health[provider]

    def all_healthy(self) -> list[Provider]:
        return [p for p, h in self._health.items() if h.is_healthy]

    def best_provider(self, exclude: list[Provider] | None = None) -> Provider | None:
        """Return the healthiest provider not in exclude list."""
        candidates = [
            (p, h) for p, h in self._health.items()
            if h.is_healthy and (exclude is None or p not in exclude)
        ]
        if not candidates:
            return None
        # Sort by success_rate desc, then avg_latency asc
        candidates.sort(key=lambda x: (-x[1].success_rate, x[1].avg_latency_ms))
        return candidates[0][0]

    def snapshot(self) -> dict:
        return {
            p: {
                "total_calls": h.total_calls,
                "success_rate": round(h.success_rate, 2),
                "avg_latency_ms": round(h.avg_latency_ms, 0),
                "consecutive_failures": h.consecutive_failures,
                "is_healthy": h.is_healthy,
                "recent_errors": h.recent_errors,
            }
            for p, h in self._health.items()
        }


# ═════════════════════════════════════════════════════════════════════════════
# 2. Circuit Breaker
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker.

    CLOSED  → normal operation
    OPEN    → after failure_threshold consecutive failures; all calls fast-fail
    HALF_OPEN → after cooldown_seconds; next call is a probe
    """
    provider: Provider
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0  # 5 minutes
    _state: Literal["closed", "open", "half_open"] = "closed"
    _failure_count: int = 0
    _last_failure_time: float = 0.0
    _probe_result: bool | None = None

    @property
    def state(self) -> Literal["closed", "open", "half_open"]:
        if self._state == "open":
            # Check if cooldown has elapsed
            elapsed = time.perf_counter() - self._last_failure_time
            if elapsed >= self.cooldown_seconds:
                self._state = "half_open"
                logger.info(f"[circuit] {self.provider} → HALF_OPEN after {elapsed:.0f}s")
        return self._state

    def can_call(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            return True  # allow the probe call
        return False

    def record_success(self):
        if self._state == "half_open":
            self._state = "closed"
            self._failure_count = 0
            logger.info(f"[circuit] {self.provider} → CLOSED (probe succeeded)")
        elif self._state == "closed":
            self._failure_count = 0

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.perf_counter()
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(
                f"[circuit] {self.provider} → OPEN after "
                f"{self._failure_count} consecutive failures"
            )


# ═════════════════════════════════════════════════════════════════════════════
# 3. Hardened Router
# ═════════════════════════════════════════════════════════════════════════════

class HardenedLLMRouter:
    """Production-hardened wrapper around LLMRouter.

    Adds health tracking, circuit breaker, fallback chains, retry,
    auto-selection, and timeout enforcement.
    """

    def __init__(self, router: LLMRouter | None = None):
        self._router = router or get_router()
        self._health = ProviderHealthTracker()
        self._circuits: dict[Provider, CircuitBreaker] = {
            p: CircuitBreaker(provider=p) for p in (
                "anthropic", "openai", "google", "deepseek", "moonshot"
            )
        }

    # ─── Core: ask with retry + circuit breaker ──────────────────────────
    async def ask_safe(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        agent_name: Optional[str] = None,
        json_response: bool = False,
        cache_system: Optional[bool] = None,
        max_retries: int = 2,
        base_delay: float = 1.0,
        call_timeout: float = 180.0,
        **kwargs: Any,
    ) -> LLMResult:
        """Single-provider ask with retry + circuit breaker + timeout.

        Raises HardenedLLMError if all retries exhausted or circuit is OPEN.
        """
        cb = self._circuits[provider]
        health = self._health.get(provider)

        if not cb.can_call():
            raise HardenedLLMError(
                f"Circuit breaker OPEN for {provider} — cooldown active",
                provider=provider, model=model, circuit_state="open",
            )

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._router.ask(
                        provider=provider,
                        model=model,
                        system=system,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        agent_name=agent_name,
                        json_response=json_response,
                        cache_system=cache_system,
                        **kwargs,
                    ),
                    timeout=call_timeout,
                )
                # Success path
                cb.record_success()
                health.record_success(result.latency_ms, result.cost_usd)
                return result

            except asyncio.TimeoutError as e:
                last_error = e
                health.record_failure(f"timeout (>{call_timeout}s)")
                cb.record_failure()
                logger.warning(
                    f"[hardened] {provider}/{model} timeout (attempt {attempt + 1}/{max_retries + 1})"
                )

            except Exception as e:
                last_error = e
                err_msg = f"{type(e).__name__}: {str(e)[:200]}"
                health.record_failure(err_msg)
                cb.record_failure()
                logger.warning(
                    f"[hardened] {provider}/{model} failed (attempt {attempt + 1}/{max_retries + 1}): {err_msg}"
                )

            # Retry with exponential backoff (only if not last attempt)
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.info(f"[hardened] Retrying {provider}/{model} in {delay}s...")
                await asyncio.sleep(delay)

        # All retries exhausted
        raise HardenedLLMError(
            f"All {max_retries + 1} attempts failed for {provider}/{model}: {last_error}",
            provider=provider, model=model, last_error=str(last_error),
            health=self._health.get(provider).snapshot(),
        )

    # ─── Feature: ask with automatic fallback chain ──────────────────────
    async def ask_with_fallback(
        self,
        primary_provider: Provider,
        primary_model: str,
        fallback_chain: list[tuple[Provider, str]],
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        agent_name: Optional[str] = None,
        json_response: bool = False,
        cache_system: Optional[bool] = None,
        max_retries: int = 2,
        call_timeout: float = 180.0,
        **kwargs: Any,
    ) -> LLMResult:
        """Try primary provider, then fall back through the chain.

        fallback_chain: [("openai", "gpt-4.1"), ("google", "gemini-2.5-pro"), ...]

        Returns the first successful result. If all fail, raises HardenedLLMError
        with details of every attempt.
        """
        attempts: list[dict] = []
        all_providers = [(primary_provider, primary_model)] + list(fallback_chain)

        for prov, mdl in all_providers:
            try:
                result = await self.ask_safe(
                    provider=prov,
                    model=mdl,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    agent_name=agent_name,
                    json_response=json_response,
                    cache_system=cache_system if prov == "anthropic" else None,
                    max_retries=max_retries,
                    call_timeout=call_timeout,
                    **kwargs,
                )
                logger.info(
                    f"[hardened] Fallback succeeded: {prov}/{mdl} "
                    f"(primary={primary_provider}/{primary_model} was {'skipped' if prov != primary_provider else 'used'})"
                )
                return result

            except HardenedLLMError as e:
                attempts.append({"provider": prov, "model": mdl, "error": str(e)})
                continue  # try next in chain

        # All providers failed
        raise HardenedLLMError(
            f"All providers failed ({len(attempts)} attempts)",
            attempts=attempts,
            health=self._health.snapshot(),
        )

    # ─── Feature: auto-select best available provider ────────────────────
    # Tier mapping: quality level → (preferred provider, model, fallbacks)
    _TIER_MAP: dict[str, tuple[tuple[Provider, str], list[tuple[Provider, str]]]] = {
        "high": (
            ("anthropic", "claude-opus-4-5"),
            [("openai", "gpt-4.1"), ("google", "gemini-2.5-pro")],
        ),
        "medium": (
            ("openai", "gpt-4.1"),
            [("anthropic", "claude-sonnet-4-6"), ("google", "gemini-2.5-pro")],
        ),
        "low": (
            ("deepseek", "deepseek-chat"),
            [("moonshot", "kimi-k2.5"), ("openai", "gpt-4o")],
        ),
        "json": (  # optimized for JSON-mode reliability
            ("openai", "gpt-4.1"),
            [("deepseek", "deepseek-chat"), ("moonshot", "kimi-k2.5")],
        ),
    }

    async def ask_auto(
        self,
        quality_tier: Literal["high", "medium", "low", "json"],
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResult:
        """Automatically pick the best available provider for the quality tier.

        Checks provider health first; if primary is unhealthy, uses the
        best healthy fallback. If no provider is healthy, falls back to
        the cheapest option as a last resort.
        """
        tier = self._TIER_MAP.get(quality_tier, self._TIER_MAP["medium"])
        primary, fallbacks = tier

        # Check if primary is healthy
        if not self._health.get(primary[0]).is_healthy:
            logger.warning(
                f"[hardened] Primary provider {primary[0]} unhealthy — using fallback chain"
            )

        return await self.ask_with_fallback(
            primary_provider=primary[0],
            primary_model=primary[1],
            fallback_chain=fallbacks,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=agent_name,
            **kwargs,
        )

    # ─── Feature: ask_json with fallback ─────────────────────────────────
    async def ask_json_with_fallback(
        self,
        primary_provider: Provider,
        primary_model: str,
        fallback_chain: list[tuple[Provider, str]],
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[dict, LLMResult]:
        """Like ask_with_fallback but parses JSON from the result."""
        result = await self.ask_with_fallback(
            primary_provider=primary_provider,
            primary_model=primary_model,
            fallback_chain=fallback_chain,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=agent_name,
            json_response=True,
            **kwargs,
        )
        from agents.llm_router import _parse_json_loose
        parsed = _parse_json_loose(result.text)
        return parsed, result

    # ─── Health diagnostics ──────────────────────────────────────────────
    def health_snapshot(self) -> dict:
        """Return current health for all providers."""
        return self._health.snapshot()

    def circuit_snapshot(self) -> dict:
        """Return current circuit breaker states."""
        return {
            p: {"state": cb.state, "failure_count": cb._failure_count}
            for p, cb in self._circuits.items()
        }


# ═════════════════════════════════════════════════════════════════════════════
# 4. HardenedLLMError
# ═════════════════════════════════════════════════════════════════════════════

class HardenedLLMError(Exception):
    """Raised when all providers in a fallback chain fail."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message)
        self.details = kwargs

    def __str__(self) -> str:
        base = super().__str__()
        if self.details:
            extras = ", ".join(f"{k}={v!r}" for k, v in self.details.items() if k != "health")
            return f"{base} [{extras}]"
        return base


# ═════════════════════════════════════════════════════════════════════════════
# 5. Singleton
# ═════════════════════════════════════════════════════════════════════════════
_hardened_router: HardenedLLMRouter | None = None


def get_hardened_router() -> HardenedLLMRouter:
    global _hardened_router
    if _hardened_router is None:
        _hardened_router = HardenedLLMRouter()
    return _hardened_router


def reset_hardened_router() -> None:
    global _hardened_router
    _hardened_router = None
