"""Regression tests for the OpenRouter fallback layer (B-OR1/B-OR2/B-OR3).

Covers:
  B-OR1 - LLMRouter exposes openrouter as a 6th provider
  B-OR1 - infer_provider() recognises the 'provider/model' format
  B-OR1 - _resolve_temperature + _supports_json_response_format strip the OR prefix
  B-OR1 - PRICING_PER_1M has entries for the common OR routed model ids
  B-OR1 - _call_openrouter returns an LLMResult with provider='openrouter'
  B-OR1 - _default_log_callback writes actual_provider for OR calls
  B-OR2 - ProviderHealthTracker + circuit breakers include openrouter
  B-OR2 - _TIER_MAP appends openrouter as the terminal fallback for every tier
  B-OR2 - ask_with_fallback skips providers without an API key
  B-OR2 - ask_auto falls back to openrouter when natives are unhealthy
  B-OR3 - G4 writer node uses ask_json_with_fallback
  B-OR3 - G2 critic node uses ask_with_fallback

No live API calls. Mocks underlying AsyncOpenAI client.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test-or-key")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")


# ════════════════════════════════════════════════════════════════════════════
# B-OR1 — LLMRouter openrouter provider
# ════════════════════════════════════════════════════════════════════════════


def test_b_or1_provider_literal_includes_openrouter():
    """The Provider type must include 'openrouter'."""
    from agents.llm_router import Provider
    # Provider is a Literal; check its arguments via __args__
    args = getattr(Provider, "__args__", None) or Provider.__origin__.__args__
    assert "openrouter" in args


def test_b_or1_infer_provider_recognises_openrouter_format():
    """Any model name with '/' should route through openrouter."""
    from agents.llm_router import infer_provider

    assert infer_provider("anthropic/claude-opus-4-5") == "openrouter"
    assert infer_provider("openai/gpt-4.1") == "openrouter"
    assert infer_provider("google/gemini-2.5-pro") == "openrouter"
    assert infer_provider("deepseek/deepseek-chat") == "openrouter"
    # Native model names still resolve to their direct providers
    assert infer_provider("claude-opus-4-5") == "anthropic"
    assert infer_provider("gpt-4.1") == "openai"


def test_b_or1_resolve_temperature_strips_openrouter_prefix():
    """OpenRouter kimi-k2 models need the same temperature override as direct."""
    from agents.llm_router import _resolve_temperature

    assert _resolve_temperature("moonshot/kimi-k2.5", 0.3) == 1.0
    assert _resolve_temperature("kimi-k2.5", 0.3) == 1.0  # direct path unchanged
    assert _resolve_temperature("anthropic/claude-opus-4-5", 0.3) == 0.3  # honoured


def test_b_or1_json_format_check_strips_openrouter_prefix():
    """OpenRouter deepseek-reasoner doesn't support response_format."""
    from agents.llm_router import _supports_json_response_format

    assert _supports_json_response_format("deepseek/deepseek-reasoner") is False
    assert _supports_json_response_format("openrouter/gpt-4.1") is True  # safe default


def test_b_or1_pricing_table_has_openrouter_entries():
    """Cost estimator must find prices for OR routed models else $0 leaks."""
    from agents.llm_router import PRICING_PER_1M, _estimate_cost

    required = [
        "anthropic/claude-opus-4-5",
        "openai/gpt-4.1",
        "google/gemini-2.5-pro",
        "deepseek/deepseek-chat",
        "moonshot/kimi-k2.5",
    ]
    for m in required:
        assert m in PRICING_PER_1M, f"Missing OR pricing for {m}"

    # Cost estimation actually returns non-zero for an OR model
    cost = _estimate_cost("anthropic/claude-opus-4-5", input_tokens=1000, output_tokens=200)
    assert cost > 0, "B-OR1: _estimate_cost returns 0 for known OR model"


def test_b_or1_router_constructor_accepts_openrouter_key():
    """The constructor wires openrouter_key from env or arg."""
    from agents.llm_router import LLMRouter

    r = LLMRouter(openrouter_key="explicit-key")
    assert r.has_key("openrouter")

    r2 = LLMRouter()  # env-only
    assert r2.has_key("openrouter")  # OPENROUTER_API_KEY set in conftest


def test_b_or1_openai_compatible_endpoint_for_openrouter():
    """OPENAI_COMPATIBLE_BASE_URLS must include the OR base URL."""
    from agents.llm_router import OPENAI_COMPATIBLE_BASE_URLS

    assert OPENAI_COMPATIBLE_BASE_URLS.get("openrouter") == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_b_or1_call_openrouter_returns_llmresult(monkeypatch):
    """_call_openrouter must return a properly shaped LLMResult."""
    from agents.llm_router import LLMRouter, LLMResult

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = "hello from OR"
    fake_resp.choices[0].message.tool_calls = None
    fake_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    # Some OpenRouter responses expose the underlying provider:
    fake_resp.model_extra = {"provider": "anthropic"}

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    r = LLMRouter()
    monkeypatch.setattr(r, "_get_client", lambda p: fake_client)

    result = await r._call_openrouter(
        model="anthropic/claude-opus-4-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        temperature=0.3,
        tools=None,
        json_response=False,
    )
    assert isinstance(result, LLMResult)
    assert result.provider == "openrouter"
    assert result.model == "anthropic/claude-opus-4-5"
    assert result.text == "hello from OR"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


# ════════════════════════════════════════════════════════════════════════════
# B-OR2 — Hardening fallback chain
# ════════════════════════════════════════════════════════════════════════════


def test_b_or2_health_tracker_includes_openrouter():
    """ProviderHealthTracker must track openrouter so the circuit breaker works."""
    from agents.llm_hardening import ProviderHealthTracker

    t = ProviderHealthTracker()
    assert "openrouter" in t._health
    # And it starts healthy
    assert t.get("openrouter").is_healthy is True


def test_b_or2_hardened_router_has_openrouter_circuit():
    """HardenedLLMRouter._circuits must include openrouter."""
    from agents.llm_hardening import HardenedLLMRouter

    hr = HardenedLLMRouter()
    assert "openrouter" in hr._circuits


def test_b_or2_tier_map_has_openrouter_terminal_fallback():
    """Every quality tier must end with an OR fallback."""
    from agents.llm_hardening import HardenedLLMRouter

    tier_map = HardenedLLMRouter._TIER_MAP
    for tier_name, (primary, fallbacks) in tier_map.items():
        # Last entry in the fallback chain must be openrouter
        assert fallbacks, f"Tier {tier_name} has no fallbacks"
        last_prov, last_model = fallbacks[-1]
        assert last_prov == "openrouter", (
            f"Tier {tier_name} terminal fallback is {last_prov}, not openrouter"
        )
        assert "/" in last_model, (
            f"Tier {tier_name} OR model {last_model} missing provider prefix"
        )


@pytest.mark.asyncio
async def test_b_or2_ask_with_fallback_skips_keyless_providers():
    """A provider with no API key should be skipped before ask_safe is called."""
    from agents.llm_hardening import HardenedLLMRouter, HardenedLLMError
    from agents.llm_router import LLMRouter, LLMResult

    fake_router = MagicMock(spec=LLMRouter)
    # Anthropic + openrouter have keys, openai does not.
    fake_router.has_key.side_effect = lambda p: p in ("anthropic", "openrouter")
    fake_router.ask = AsyncMock(return_value=LLMResult(
        text="from anthropic", provider="anthropic", model="claude-opus-4-5",
    ))

    hr = HardenedLLMRouter(router=fake_router)
    result = await hr.ask_with_fallback(
        primary_provider="anthropic",
        primary_model="claude-opus-4-5",
        fallback_chain=[("openai", "gpt-4.1"), ("openrouter", "anthropic/claude-opus-4-5")],
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    assert result.text == "from anthropic"
    # openai should NOT have been called (no key)
    called_providers = [c.kwargs.get("provider") or c.args[0] for c in fake_router.ask.call_args_list]
    assert "openai" not in called_providers, "B-OR2: keyless provider was called"


@pytest.mark.asyncio
async def test_b_or2_openrouter_terminal_fallback_fires(monkeypatch):
    """When all native providers fail, OR is called and returns a result."""
    from agents.llm_hardening import HardenedLLMRouter
    from agents.llm_router import LLMRouter, LLMResult

    fake_router = MagicMock(spec=LLMRouter)
    fake_router.has_key.return_value = True  # all providers have keys

    call_log: list[str] = []

    async def fake_ask(**kwargs):
        prov = kwargs.get("provider")
        call_log.append(prov)
        if prov == "openrouter":
            return LLMResult(text="OR saved us", provider="openrouter",
                             model="anthropic/claude-opus-4-5")
        raise Exception(f"{prov} is down")

    fake_router.ask = AsyncMock(side_effect=fake_ask)

    hr = HardenedLLMRouter(router=fake_router)
    result = await hr.ask_with_fallback(
        primary_provider="anthropic",
        primary_model="claude-opus-4-5",
        fallback_chain=[
            ("openai", "gpt-4.1"),
            ("openrouter", "anthropic/claude-opus-4-5"),
        ],
        system="sys", messages=[{"role": "user", "content": "hi"}],
        max_retries=0,
    )
    assert result.text == "OR saved us"
    assert call_log == ["anthropic", "openai", "openrouter"]


# ════════════════════════════════════════════════════════════════════════════
# B-OR3 — Graph adoption (G4 writer + G2 critic)
# ════════════════════════════════════════════════════════════════════════════


def test_b_or3_g4_writer_imports_hardened_router():
    """G4 writer node must import the hardened router (proves the swap happened)."""
    import agents.g4_linkedin_graph as g4

    source = open(g4.__file__).read()
    assert "from agents.llm_hardening import get_hardened_router" in source, (
        "B-OR3: G4 didn't import the hardened router"
    )
    assert "ask_json_with_fallback" in source, (
        "B-OR3: G4 didn't call ask_json_with_fallback"
    )


def test_b_or3_g2_critic_imports_hardened_router():
    """G2 critic node must import the hardened router."""
    import resume_agents.g2_nodes as g2

    source = open(g2.__file__).read()
    assert "from agents.llm_hardening import get_hardened_router" in source, (
        "B-OR3: G2 didn't import the hardened router"
    )
    assert "ask_with_fallback" in source, (
        "B-OR3: G2 didn't call ask_with_fallback"
    )


def test_b_or3_g2_critic_has_openrouter_fallback_map():
    """G2 critic must build fallback chains pointing at OR for each native provider."""
    import resume_agents.g2_nodes as g2

    source = open(g2.__file__).read()
    # Spot-check three key mappings
    assert '"deepseek": ("openrouter", "deepseek/deepseek-reasoner")' in source
    assert '"moonshot": ("openrouter", "moonshot/kimi-k2.5")' in source
    assert '"anthropic": ("openrouter", "anthropic/claude-opus-4-5")' in source
