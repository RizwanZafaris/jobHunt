"""Test the prompt-caching opt-in path through agents/llm_router.

The G6 follow-up graph (Phase 1.3) sets `cache_system=True` on every
Anthropic call so its static system prompts pick up the 90% cache-read
discount. These tests pin that behaviour at the router level.

We test the router directly rather than the G6 nodes because:
  - The router is where the wrapping happens (`_call_anthropic`).
  - Testing the nodes would require mocking the entire LangGraph state,
    which is overkill — the contract we care about is "ask() with
    cache_system=True produces a wrapped system arg in the underlying
    Anthropic call".
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.llm_router import LLMRouter, _anthropic_cache_enabled


SHORT_SYSTEM = "You are a follow-up email drafter. Keep it concise."  # <1024 tokens
LONG_SYSTEM = (
    "You are a follow-up email drafter. Keep it concise.\n"
    + "Lorem ipsum dolor sit amet. " * 200  # ~5KB → > 1024 tokens by chars/4
)


def _mock_anthropic_response():
    """Build a minimal AsyncMock that mimics anthropic.AsyncAnthropic."""
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello")],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        stop_reason="end_turn",
    )
    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_message)
    return fake_client


@pytest.mark.asyncio
async def test_short_system_with_cache_system_true_is_wrapped():
    """G6 path: cache_system=True force-wraps even sub-1024-token prompts."""
    fake_client = _mock_anthropic_response()
    router = LLMRouter()
    with patch.object(router, "_get_client", return_value=fake_client), \
         patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "1"}, clear=False):
        await router._call_anthropic(
            model="claude-sonnet-4-5",
            system=SHORT_SYSTEM,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            temperature=0.3,
            tools=None,
            cache_system=True,
        )
    args = fake_client.messages.create.await_args
    system_arg = args.kwargs["system"]
    assert isinstance(system_arg, list), (
        "Expected the system to be wrapped as a list of text blocks when "
        f"cache_system=True. Got: {type(system_arg).__name__}"
    )
    assert len(system_arg) == 1
    block = system_arg[0]
    assert block["type"] == "text"
    assert block["text"] == SHORT_SYSTEM
    assert block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_short_system_default_mode_is_NOT_wrapped():
    """G2 path: short prompts go through unwrapped (chars/4 < 1024 tokens)."""
    fake_client = _mock_anthropic_response()
    router = LLMRouter()
    with patch.object(router, "_get_client", return_value=fake_client), \
         patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "1"}, clear=False):
        await router._call_anthropic(
            model="claude-sonnet-4-5",
            system=SHORT_SYSTEM,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            temperature=0.3,
            tools=None,
            cache_system=None,  # default
        )
    system_arg = fake_client.messages.create.await_args.kwargs["system"]
    assert isinstance(system_arg, str), (
        f"Short system + default mode should stay as a plain string. Got: {type(system_arg).__name__}"
    )
    assert system_arg == SHORT_SYSTEM


@pytest.mark.asyncio
async def test_long_system_default_mode_IS_wrapped():
    """G2 path: long prompts auto-wrap so prompt caching kicks in."""
    fake_client = _mock_anthropic_response()
    router = LLMRouter()
    with patch.object(router, "_get_client", return_value=fake_client), \
         patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "1"}, clear=False):
        await router._call_anthropic(
            model="claude-sonnet-4-5",
            system=LONG_SYSTEM,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            temperature=0.3,
            tools=None,
            cache_system=None,
        )
    system_arg = fake_client.messages.create.await_args.kwargs["system"]
    assert isinstance(system_arg, list)
    assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_cache_system_false_never_wraps_even_long_system():
    """Test / rollback path: cache_system=False disables wrapping unconditionally."""
    fake_client = _mock_anthropic_response()
    router = LLMRouter()
    with patch.object(router, "_get_client", return_value=fake_client), \
         patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "1"}, clear=False):
        await router._call_anthropic(
            model="claude-sonnet-4-5",
            system=LONG_SYSTEM,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            temperature=0.3,
            tools=None,
            cache_system=False,
        )
    system_arg = fake_client.messages.create.await_args.kwargs["system"]
    assert isinstance(system_arg, str), (
        "cache_system=False must keep the system as a string regardless of size."
    )


@pytest.mark.asyncio
async def test_env_var_disabled_skips_wrapping_even_with_force():
    """ANTHROPIC_PROMPT_CACHE_ENABLED=0 disables prompt caching globally."""
    fake_client = _mock_anthropic_response()
    router = LLMRouter()
    with patch.object(router, "_get_client", return_value=fake_client), \
         patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "0"}, clear=False):
        await router._call_anthropic(
            model="claude-sonnet-4-5",
            system=LONG_SYSTEM,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            temperature=0.3,
            tools=None,
            cache_system=True,  # G6 always passes True, but the env var should win
        )
    system_arg = fake_client.messages.create.await_args.kwargs["system"]
    assert isinstance(system_arg, str), (
        "ANTHROPIC_PROMPT_CACHE_ENABLED=0 must disable caching even when "
        "callers explicitly request it (staged rollback signal)."
    )


def test_anthropic_cache_enabled_default_on():
    """Cache is opt-in but defaults to ON (per PR #74)."""
    with patch.dict(os.environ, {}, clear=True):
        assert _anthropic_cache_enabled() is True
    with patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "1"}, clear=False):
        assert _anthropic_cache_enabled() is True
    with patch.dict(os.environ, {"ANTHROPIC_PROMPT_CACHE_ENABLED": "0"}, clear=False):
        assert _anthropic_cache_enabled() is False
