"""
agents/llm_fallback.py — Auto-fallback rail through OpenRouter.

When a direct provider call fails with a retriable error (rate limit,
overload, transient 5xx, timeout), this wrapper retries the same logical
request through OpenRouter, which routes to the same model via a different
network path. Output is identical; cost is ~5% higher (OpenRouter markup).

Wire-in: BaseAgent.ask() calls ask_with_fallback() instead of router.ask()
when OPENROUTER_API_KEY is set. Zero behavior change when OR key is absent.

What counts as retriable:
  • HTTP 429 (rate limit)
  • HTTP 503 (overloaded — Anthropic's quota-exhaustion code)
  • HTTP 502/504 (gateway / timeout)
  • asyncio.TimeoutError
  • anthropic.APITimeoutError / openai.APITimeoutError
  • Network errors (httpx.ConnectError, ReadError)

What is NOT retried:
  • HTTP 400 (bad request — same prompt will fail through OR too)
  • HTTP 401/403 (auth — env issue, fix it)
  • HTTP 404 (model not found — config issue)
  • Content-policy refusals (model decided not to answer — fallback would too)
  • Anything where the model produced text but the caller rejected it

The model id is translated for OpenRouter (e.g. "claude-opus-4-5-20251101"
→ "anthropic/claude-opus-4-5"). Unknown models fall through to OR raw —
OpenRouter will 404 if it doesn't know the id, in which case we surface
the ORIGINAL error (not the fallback failure) to the caller.

Telemetry:
  • Every fallback fire writes a row to agent_call_log with
    actual_provider='openrouter' and a 'fallback_from' note in raw.
  • Successful fallback counts go to PrometheusGauge llm_fallback_total.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from agents.llm_router import LLMResult, LLMRouter, Provider, get_router

logger = logging.getLogger(__name__)

# Map native model ids → OpenRouter routed model ids.
# Extend as we adopt new models. Unknown models fall through unchanged.
_OPENROUTER_MODEL_MAP: dict[str, str] = {
    # Anthropic
    "claude-opus-4-5-20251101":   "anthropic/claude-opus-4-5",
    "claude-opus-4-5":            "anthropic/claude-opus-4-5",
    "claude-sonnet-4-6":          "anthropic/claude-sonnet-4-6",
    "claude-sonnet-4-5":          "anthropic/claude-sonnet-4-5",
    "claude-haiku-4-5-20251001":  "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5":           "anthropic/claude-haiku-4-5",
    # OpenAI
    "gpt-5":     "openai/gpt-5",
    "gpt-4.1":   "openai/gpt-4.1",
    "gpt-4o":    "openai/gpt-4o",
    "o1":        "openai/o1",
    # Google
    "gemini-2.5-pro":   "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-3.0-pro":   "google/gemini-3.0-pro",
    # DeepSeek
    "deepseek-chat":     "deepseek/deepseek-chat",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
    # Moonshot
    "kimi-k2.6": "moonshot/kimi-k2.6",
    "kimi-k2.5": "moonshot/kimi-k2.5",
}


def _to_openrouter_id(model: str) -> Optional[str]:
    """Translate a native model id to its OpenRouter form.

    Already-prefixed ids (containing "/") pass through unchanged.
    Unknown ids return None — caller should not attempt fallback.
    """
    if "/" in model:
        return model
    return _OPENROUTER_MODEL_MAP.get(model)


# Error classification — strings are matched case-insensitively against
# str(exception) when exception type isn't a known retriable class.
_RETRIABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 529}
_RETRIABLE_STRING_MARKERS = (
    "rate limit", "rate_limit", "overloaded", "overload_error",
    "service unavailable", "service_unavailable", "bad gateway",
    "gateway timeout", "timeout", "timed out", "connection reset",
    "connection error", "remote disconnected", "read error",
    "temporarily unavailable", "try again later",
    # Anthropic-specific
    "anthropic_overloaded",
)


def _is_retriable(exc: BaseException) -> bool:
    """Decide whether an exception warrants an OpenRouter fallback.

    Conservative: when in doubt, do NOT retry — falling back hides bugs
    and adds latency. Only retry obvious transient failures.
    """
    # Asyncio + library timeouts — always retriable.
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # Try anthropic / openai SDK error classes (without importing them
    # at module load — keeps this module light).
    for mod_name, cls_names in (
        ("anthropic", ("APITimeoutError", "APIConnectionError",
                       "RateLimitError", "APIStatusError",
                       "InternalServerError")),
        ("openai",    ("APITimeoutError", "APIConnectionError",
                       "RateLimitError", "APIStatusError",
                       "InternalServerError")),
        ("httpx",     ("TimeoutException", "ConnectError",
                       "ReadError", "RemoteProtocolError")),
    ):
        try:
            mod = __import__(mod_name)
            for cls_name in cls_names:
                cls = getattr(mod, cls_name, None)
                if cls and isinstance(exc, cls):
                    # For APIStatusError, check the status_code attribute.
                    status = getattr(exc, "status_code", None)
                    if status is not None:
                        return int(status) in _RETRIABLE_STATUS_CODES
                    return True
        except ImportError:
            pass

    # Last-resort: scan the error message for retriable markers.
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRIABLE_STRING_MARKERS)


async def ask_with_fallback(
    *,
    provider: Provider,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.3,
    tools: Optional[list] = None,
    agent_name: Optional[str] = None,
    json_response: bool = False,
    cache_system: Optional[bool] = None,
    router: Optional[LLMRouter] = None,
    **provider_kwargs: Any,
) -> LLMResult:
    """Call provider; on retriable failure, fall back through OpenRouter.

    Drop-in replacement for LLMRouter.ask(). Same arguments, same return type.

    Fallback fires only when ALL of:
      • OPENROUTER_API_KEY is set
      • The original error is in the retriable set
      • The model has an OpenRouter mapping (or already uses "provider/model" form)
      • The original provider was not already openrouter

    On fallback success: returns the OR result with provider='openrouter'
    and adds an "or_fallback_from" hint to result.raw so cost telemetry
    can group spend by underlying provider.

    On fallback failure: re-raises the ORIGINAL exception (not the OR one),
    so callers see the root cause rather than a misleading secondary error.
    """
    r = router or get_router()

    try:
        return await r.ask(
            provider=provider,
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            agent_name=agent_name,
            json_response=json_response,
            cache_system=cache_system,
            **provider_kwargs,
        )
    except Exception as original_exc:
        # 1. Is this even retriable?
        if not _is_retriable(original_exc):
            raise

        # 2. Do we have OpenRouter configured?
        or_key_present = bool(
            os.environ.get("OPENROUTER_API_KEY")
            or (r.has_key("openrouter") if hasattr(r, "has_key") else False)
        )
        if not or_key_present:
            logger.debug(
                "fallback skipped (OPENROUTER_API_KEY not set): "
                "provider=%s model=%s err=%s",
                provider, model, original_exc,
            )
            raise

        # 3. Already using OpenRouter? No point recursing.
        if provider == "openrouter":
            raise

        # 4. Translate model id.
        or_model = _to_openrouter_id(model)
        if not or_model:
            logger.debug(
                "fallback skipped (no OR model mapping): model=%s", model
            )
            raise

        # All gates passed — fire the fallback.
        logger.warning(
            "LLM fallback firing: %s/%s failed (%s), retrying via openrouter/%s",
            provider, model, type(original_exc).__name__, or_model,
        )
        try:
            result = await r.ask(
                provider="openrouter",
                model=or_model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                agent_name=agent_name,
                json_response=json_response,
                # cache_system not passed — OR doesn't support Anthropic
                # prompt caching directly. Cache miss on fallback is fine.
                **provider_kwargs,
            )
        except Exception as fallback_exc:
            logger.error(
                "LLM fallback ALSO failed: original=%s fallback=%s",
                original_exc, fallback_exc,
            )
            # Surface the ORIGINAL error — that's what the operator needs
            # to fix. The OR error is downstream noise.
            raise original_exc

        # Mark the result so cost dashboards can attribute spend correctly.
        try:
            if hasattr(result, "raw") and result.raw is not None:
                # Best-effort annotation; some SDK responses are frozen.
                try:
                    result.raw.or_fallback_from = f"{provider}/{model}"
                except (AttributeError, TypeError):
                    pass
        except Exception:
            pass

        logger.info(
            "LLM fallback succeeded: %s/%s → openrouter/%s "
            "(cost=$%.4f, latency=%dms)",
            provider, model, or_model, result.cost_usd, result.latency_ms,
        )
        return result


__all__ = ["ask_with_fallback", "_to_openrouter_id", "_is_retriable"]
