"""
tests/test_llm_fallback.py — unit tests for OpenRouter auto-fallback.

Pure-function tests against agents.llm_fallback. No network, no API keys
required — all upstream calls are mocked.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents import llm_fallback as F
from agents.llm_router import LLMResult


# ─── Model-id translation ────────────────────────────────────────────────
class TestModelTranslation:
    def test_known_anthropic_model_mapped(self):
        assert F._to_openrouter_id("claude-opus-4-5-20251101") == "anthropic/claude-opus-4-5"

    def test_known_openai_model_mapped(self):
        assert F._to_openrouter_id("gpt-4.1") == "openai/gpt-4.1"

    def test_already_prefixed_passes_through(self):
        assert F._to_openrouter_id("anthropic/claude-opus-4-5") == "anthropic/claude-opus-4-5"

    def test_unknown_model_returns_none(self):
        assert F._to_openrouter_id("some-future-model") is None


# ─── Retriable classification ────────────────────────────────────────────
class TestRetriable:
    def test_asyncio_timeout_is_retriable(self):
        assert F._is_retriable(asyncio.TimeoutError())

    def test_rate_limit_string_is_retriable(self):
        assert F._is_retriable(RuntimeError("rate_limit exceeded"))

    def test_overloaded_is_retriable(self):
        assert F._is_retriable(RuntimeError("anthropic_overloaded"))

    def test_503_service_unavailable_is_retriable(self):
        assert F._is_retriable(RuntimeError("503 service unavailable"))

    def test_bad_request_not_retriable(self):
        # 400 is the caller's fault — retrying won't help.
        assert not F._is_retriable(RuntimeError("400 bad_request: malformed prompt"))

    def test_auth_error_not_retriable(self):
        assert not F._is_retriable(RuntimeError("401 unauthorized"))

    def test_generic_exception_not_retriable(self):
        # Don't auto-retry on truly unknown errors — that hides bugs.
        assert not F._is_retriable(ValueError("something weird"))


# ─── Fallback orchestration ──────────────────────────────────────────────
def _ok_result(provider="openrouter", model="anthropic/claude-opus-4-5") -> LLMResult:
    return LLMResult(
        text="hello",
        provider=provider,
        model=model,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        latency_ms=300,
        raw=MagicMock(),
    )


class TestFallback:
    @pytest.mark.asyncio
    async def test_success_path_no_fallback(self):
        """Happy path: primary succeeds, no fallback fires."""
        mock_router = MagicMock()
        mock_router.ask = AsyncMock(return_value=_ok_result(provider="anthropic"))
        mock_router.has_key = MagicMock(return_value=True)

        result = await F.ask_with_fallback(
            provider="anthropic",
            model="claude-opus-4-5",
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            router=mock_router,
        )
        assert result.provider == "anthropic"
        assert mock_router.ask.await_count == 1

    @pytest.mark.asyncio
    async def test_retriable_failure_triggers_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        mock_router = MagicMock()
        # First call raises retriable, second returns OK.
        mock_router.ask = AsyncMock(side_effect=[
            asyncio.TimeoutError("provider overloaded"),
            _ok_result(provider="openrouter"),
        ])
        mock_router.has_key = MagicMock(return_value=True)

        result = await F.ask_with_fallback(
            provider="anthropic",
            model="claude-opus-4-5",
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            router=mock_router,
        )
        assert result.provider == "openrouter"
        assert mock_router.ask.await_count == 2
        # Verify the fallback call used the translated model id.
        fallback_kwargs = mock_router.ask.await_args_list[1].kwargs
        assert fallback_kwargs["provider"] == "openrouter"
        assert fallback_kwargs["model"] == "anthropic/claude-opus-4-5"

    @pytest.mark.asyncio
    async def test_non_retriable_no_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        mock_router = MagicMock()
        # Non-retriable error (e.g. 400) — propagate, do NOT retry.
        mock_router.ask = AsyncMock(side_effect=ValueError("400 bad prompt"))
        mock_router.has_key = MagicMock(return_value=True)

        with pytest.raises(ValueError, match="400"):
            await F.ask_with_fallback(
                provider="anthropic",
                model="claude-opus-4-5",
                system="s",
                messages=[],
                router=mock_router,
            )
        assert mock_router.ask.await_count == 1

    @pytest.mark.asyncio
    async def test_no_or_key_no_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        mock_router = MagicMock()
        mock_router.ask = AsyncMock(side_effect=asyncio.TimeoutError("rate limit"))
        mock_router.has_key = MagicMock(return_value=False)

        with pytest.raises(asyncio.TimeoutError):
            await F.ask_with_fallback(
                provider="anthropic",
                model="claude-opus-4-5",
                system="s",
                messages=[],
                router=mock_router,
            )
        assert mock_router.ask.await_count == 1

    @pytest.mark.asyncio
    async def test_unknown_model_no_fallback(self, monkeypatch):
        """Model not in the OR map → don't attempt, surface original error."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        mock_router = MagicMock()
        mock_router.ask = AsyncMock(side_effect=RuntimeError("rate_limit"))
        mock_router.has_key = MagicMock(return_value=True)

        with pytest.raises(RuntimeError, match="rate_limit"):
            await F.ask_with_fallback(
                provider="anthropic",
                model="custom-future-model",  # not in _OPENROUTER_MODEL_MAP
                system="s",
                messages=[],
                router=mock_router,
            )
        assert mock_router.ask.await_count == 1

    @pytest.mark.asyncio
    async def test_fallback_also_fails_surfaces_original(self, monkeypatch):
        """When both primary AND fallback fail, the ORIGINAL error wins —
        that's what the operator needs to debug."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        original = RuntimeError("primary_overloaded")
        fallback = RuntimeError("openrouter_500")
        mock_router = MagicMock()
        mock_router.ask = AsyncMock(side_effect=[original, fallback])
        mock_router.has_key = MagicMock(return_value=True)

        with pytest.raises(RuntimeError) as exc_info:
            await F.ask_with_fallback(
                provider="anthropic",
                model="claude-opus-4-5",
                system="s",
                messages=[],
                router=mock_router,
            )
        # Original error is preserved.
        assert "primary_overloaded" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openrouter_provider_does_not_recurse(self, monkeypatch):
        """If the original call already used openrouter, don't recurse."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        mock_router = MagicMock()
        mock_router.ask = AsyncMock(side_effect=RuntimeError("rate_limit"))
        mock_router.has_key = MagicMock(return_value=True)

        with pytest.raises(RuntimeError):
            await F.ask_with_fallback(
                provider="openrouter",
                model="anthropic/claude-opus-4-5",
                system="s",
                messages=[],
                router=mock_router,
            )
        assert mock_router.ask.await_count == 1
