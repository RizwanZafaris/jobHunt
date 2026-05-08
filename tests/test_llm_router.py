"""
Unit tests for agents/llm_router.py.
No live API calls — purely tests the dispatch logic, JSON parsing,
cost estimation, and provider inference.

Run with: pytest tests/test_llm_router.py -v
"""
from __future__ import annotations

import json
import pytest

from agents.llm_router import (
    LLMResult,
    PRICING_PER_1M,
    _estimate_cost,
    _parse_json_loose,
    infer_provider,
)


# ─── Provider inference ────────────────────────────────────────────────
class TestInferProvider:
    def test_claude_models(self):
        assert infer_provider("claude-opus-4-5-20251101") == "anthropic"
        assert infer_provider("claude-sonnet-4-6") == "anthropic"
        assert infer_provider("claude-haiku-4-5-20251001") == "anthropic"

    def test_openai_models(self):
        assert infer_provider("gpt-4.1") == "openai"
        assert infer_provider("gpt-5") == "openai"
        assert infer_provider("o1") == "openai"
        assert infer_provider("text-embedding-3-small") == "openai"

    def test_gemini_models(self):
        assert infer_provider("gemini-2.5-pro") == "google"
        assert infer_provider("gemini-2.5-flash") == "google"

    def test_deepseek_models(self):
        assert infer_provider("deepseek-chat") == "deepseek"
        assert infer_provider("deepseek-reasoner") == "deepseek"

    def test_kimi_models(self):
        assert infer_provider("kimi-k2") == "moonshot"
        assert infer_provider("moonshot-v1-128k") == "moonshot"

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Cannot infer provider"):
            infer_provider("llama-3-70b")


# ─── Cost estimation ───────────────────────────────────────────────────
class TestCostEstimation:
    def test_known_model_exact_match(self):
        # claude-opus-4-5-20251101 = $15/$75 per 1M
        cost = _estimate_cost("claude-opus-4-5-20251101", 1_000_000, 1_000_000)
        assert cost == pytest.approx(15.0 + 75.0)

    def test_deepseek_v3_cheap(self):
        # deepseek-chat = $0.27/$1.10 per 1M
        cost = _estimate_cost("deepseek-chat", 100_000, 100_000)
        # 0.1 * 0.27 + 0.1 * 1.10 = 0.137
        assert cost == pytest.approx(0.137, abs=0.001)

    def test_unknown_model_returns_zero(self):
        cost = _estimate_cost("never-heard-of-this-model", 10000, 5000)
        assert cost == 0.0

    def test_prefix_match_for_versioned_model(self):
        # "gemini-2.5-pro-001" should match "gemini-2.5-pro"
        cost = _estimate_cost("gemini-2.5-pro-001", 1_000_000, 1_000_000)
        # 1.25 + 5.0 = 6.25
        assert cost == pytest.approx(6.25)

    def test_zero_tokens(self):
        assert _estimate_cost("claude-opus-4-5", 0, 0) == 0.0

    def test_pricing_table_consistency(self):
        # Sanity: every entry should be a 2-tuple of non-negative numbers
        for model, prices in PRICING_PER_1M.items():
            assert isinstance(model, str)
            assert len(prices) == 2
            assert prices[0] >= 0 and prices[1] >= 0, f"Bad pricing for {model}"


# ─── JSON parsing ──────────────────────────────────────────────────────
class TestParseJsonLoose:
    def test_plain_json(self):
        result = _parse_json_loose('{"foo": "bar", "n": 42}')
        assert result == {"foo": "bar", "n": 42}

    def test_with_json_fence(self):
        text = '```json\n{"score": 95}\n```'
        assert _parse_json_loose(text) == {"score": 95}

    def test_with_unlabeled_fence(self):
        text = '```\n{"a": 1}\n```'
        assert _parse_json_loose(text) == {"a": 1}

    def test_with_leading_prose(self):
        text = 'Here is the JSON:\n{"verdict": "filled"}'
        assert _parse_json_loose(text) == {"verdict": "filled"}

    def test_with_trailing_prose(self):
        text = '{"verdict": "filled"}\nHope this helps!'
        assert _parse_json_loose(text) == {"verdict": "filled"}

    def test_array_response(self):
        text = '[{"id": 1}, {"id": 2}]'
        assert _parse_json_loose(text) == [{"id": 1}, {"id": 2}]

    def test_nested_object(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        assert _parse_json_loose(text) == {"outer": {"inner": [1, 2, 3]}}

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty response"):
            _parse_json_loose("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _parse_json_loose("just plain text no braces")

    def test_complex_real_world_response(self):
        # Mimics an actual ATS Critic response
        text = """Here's my analysis:
```json
{
  "ats_score": 87,
  "missing_keywords": ["tokenization", "merchant of record"],
  "specific_fixes": [
    "Add 'real-time payments' to summary",
    "Quantify the Mastercard role outcome"
  ]
}
```
Let me know if you need anything else."""
        result = _parse_json_loose(text)
        assert result["ats_score"] == 87
        assert "tokenization" in result["missing_keywords"]
        assert len(result["specific_fixes"]) == 2


# ─── LLMResult dataclass ───────────────────────────────────────────────
class TestLLMResult:
    def test_default_values(self):
        r = LLMResult(text="hello", provider="anthropic", model="claude-opus-4-5")
        assert r.text == "hello"
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cost_usd == 0.0
        assert r.latency_ms == 0
        assert r.tool_calls == []

    def test_to_log_dict_drops_raw(self):
        r = LLMResult(
            text="hi",
            provider="openai",
            model="gpt-4.1",
            input_tokens=100,
            output_tokens=50,
            raw={"big": "blob"},  # should be dropped from log dict
        )
        d = r.to_log_dict()
        assert "raw" not in d
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["model"] == "gpt-4.1"

    def test_serializable_for_logging(self):
        r = LLMResult(text="x", provider="google", model="gemini-2.5-pro")
        # Should be JSON-serializable (after raw dropped)
        d = r.to_log_dict()
        json.dumps(d)  # raises if not serializable
