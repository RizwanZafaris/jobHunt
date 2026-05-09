"""
agents/llm_router.py — Unified LLM provider router.

Single ask() method, five providers (Anthropic, OpenAI, Google Gemini,
DeepSeek, Moonshot Kimi). Lazy client instantiation. Per-call cost +
latency tracking. JSON-parse helper. Web-search/grounding support.

Designed so an agent can stay model-agnostic:

    from agents.llm_router import get_router
    result = await get_router().ask(
        provider="google",
        model="gemini-2.5-pro",
        system="You are ...",
        messages=[{"role": "user", "content": "..."}],
        max_tokens=4096,
        temperature=0.3,
    )
    print(result.text, result.cost_usd, result.latency_ms)

Pricing notes:
- Costs are best-effort estimates. Update PRICING_PER_1M as providers change pricing.
- Unknown models → cost_usd is 0.0 but latency + token counts still tracked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

Provider = Literal["anthropic", "openai", "google", "deepseek", "moonshot"]

# ─── Pricing table (USD per 1M tokens, input / output) ───────────────────────
# Approximate as of 2026-Q2. Sources: provider pricing pages.
# Used only for cost telemetry. Don't gate logic on these numbers.
PRICING_PER_1M: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-7":            (15.0, 75.0),
    "claude-opus-4-5-20251101":   (15.0, 75.0),
    "claude-opus-4-5":            (15.0, 75.0),
    "claude-sonnet-4-6":          (3.0, 15.0),
    "claude-sonnet-4-5":          (3.0, 15.0),
    "claude-haiku-4-5-20251001":  (0.80, 4.0),
    "claude-haiku-4-5":           (0.80, 4.0),
    # OpenAI
    "gpt-5":                      (5.0, 20.0),
    "gpt-4.1":                    (2.0, 8.0),
    "gpt-4o":                     (2.50, 10.0),
    "o1":                         (15.0, 60.0),
    "text-embedding-3-small":     (0.02, 0.0),
    # Google
    "gemini-2.5-pro":             (1.25, 5.0),     # ≤200k input
    "gemini-2.5-flash":           (0.30, 2.50),
    "gemini-3.0-pro":             (2.0, 8.0),      # speculative
    # DeepSeek
    "deepseek-chat":              (0.27, 1.10),    # V3
    "deepseek-reasoner":          (0.55, 2.19),    # R1
    # Moonshot Kimi (USD/1M, cache-miss input · output, per platform.kimi.ai/docs/pricing)
    "kimi-k2.6":                  (0.95, 4.0),    # latest, recommended for new code
    "kimi-k2.5":                  (0.60, 3.0),    # cheaper alternative, still SOTA
    "moonshot-v1-128k":           (2.0, 5.0),
    # NB: plain "kimi-k2" is NOT a valid Moonshot model id. Removed from
    # pricing table — calls with that name will 404 at the API. The
    # _supports_json_response_format whitelist defaults to True for kimi-*
    # variants, which is correct (Moonshot supports response_format).
}

# OpenAI-compatible endpoints (DeepSeek + Kimi)
OPENAI_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.ai/v1",
}


@dataclass
class LLMResult:
    """Uniform result object across all providers."""
    text: str
    provider: Provider
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw: Any = field(default=None, repr=False)
    tool_calls: list = field(default_factory=list)

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING_PER_1M.get(model)
    if not pricing:
        # Try a prefix match (e.g. "gemini-2.5-pro-001" → "gemini-2.5-pro")
        for known, prices in PRICING_PER_1M.items():
            if model.startswith(known):
                pricing = prices
                break
    if not pricing:
        return 0.0
    # Coerce None / missing token counts to 0. Some providers (notably
    # Gemini's usage_metadata) sometimes return attributes whose values
    # are None instead of an int, which used to crash this function.
    in_toks = int(input_tokens or 0)
    out_toks = int(output_tokens or 0)
    in_cost = in_toks * pricing[0] / 1_000_000
    out_cost = out_toks * pricing[1] / 1_000_000
    return round(in_cost + out_cost, 6)


def _supports_json_response_format(model: str) -> bool:
    """
    Whether `response_format={"type": "json_object"}` is honoured by the model.

    Known offenders that 400 on json_object:
      - deepseek-reasoner (R1): outputs reasoning + final answer, no native
        JSON-mode. Fixed by setting json_response=False; the LLM router still
        receives the raw text and `_parse_json_loose` handles prose-wrapped
        JSON.

    Defaults to True for everything else (gpt-*, deepseek-chat, kimi-k2,
    moonshot-v1-*). Update this function — never hardcode at the call site.
    """
    if model.startswith("deepseek-reasoner"):
        return False
    return True


def infer_provider(model: str) -> Provider:
    """Best-effort provider inference from model name. Used for back-compat."""
    m = model.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "text-embedding")):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("kimi", "moonshot")):
        return "moonshot"
    raise ValueError(
        f"Cannot infer provider from model '{model}'. "
        f"Pass provider= explicitly to the router."
    )


# ═════════════════════════════════════════════════════════════════════════════
# Router
# ═════════════════════════════════════════════════════════════════════════════
class LLMRouter:
    """
    Lazy, multi-provider async LLM client.
    Instantiate once per process; reuse via get_router().
    """

    def __init__(
        self,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
        google_key: Optional[str] = None,
        deepseek_key: Optional[str] = None,
        moonshot_key: Optional[str] = None,
        log_callback: Optional[callable] = None,
    ):
        self._keys: dict[Provider, Optional[str]] = {
            "anthropic": anthropic_key or os.environ.get("ANTHROPIC_API_KEY"),
            "openai": openai_key or os.environ.get("OPENAI_API_KEY"),
            "google": google_key or os.environ.get("GOOGLE_API_KEY"),
            "deepseek": deepseek_key or os.environ.get("DEEPSEEK_API_KEY"),
            "moonshot": moonshot_key or os.environ.get("KIMI_API_KEY")
                        or os.environ.get("MOONSHOT_API_KEY"),
        }
        self._clients: dict[Provider, Any] = {}
        # Optional callback fired after every successful call.
        # Signature: (result: LLMResult, agent_name: Optional[str]) -> None
        # Used by BaseAgent to write rows into agent_call_log.
        self._log_callback = log_callback

    # ─── Public API ──────────────────────────────────────────────────────
    async def ask(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        tools: Optional[list] = None,
        agent_name: Optional[str] = None,
        json_response: bool = False,
        **provider_kwargs: Any,
    ) -> LLMResult:
        """
        Single entry point for all providers.

        messages: [{"role": "user"|"assistant", "content": "..."}]
        tools: provider-specific tool definitions (e.g. Anthropic web_search,
               Gemini google_search grounding, OpenAI function tools).
        """
        start = time.perf_counter()
        try:
            if provider == "anthropic":
                result = await self._call_anthropic(
                    model, system, messages, max_tokens, temperature, tools, **provider_kwargs
                )
            elif provider == "openai":
                result = await self._call_openai_compatible(
                    "openai", model, system, messages, max_tokens, temperature,
                    tools, json_response, **provider_kwargs,
                )
            elif provider == "deepseek":
                result = await self._call_openai_compatible(
                    "deepseek", model, system, messages, max_tokens, temperature,
                    tools, json_response, **provider_kwargs,
                )
            elif provider == "moonshot":
                result = await self._call_openai_compatible(
                    "moonshot", model, system, messages, max_tokens, temperature,
                    tools, json_response, **provider_kwargs,
                )
            elif provider == "google":
                result = await self._call_google(
                    model, system, messages, max_tokens, temperature, tools, **provider_kwargs
                )
            else:
                raise ValueError(f"Unknown provider: {provider}")
        except Exception as e:
            logger.error(f"LLM call failed: provider={provider} model={model} err={e}")
            raise

        result.latency_ms = int((time.perf_counter() - start) * 1000)
        result.cost_usd = _estimate_cost(model, result.input_tokens, result.output_tokens)

        if self._log_callback:
            try:
                self._log_callback(result, agent_name)
            except Exception as e:
                logger.debug(f"Cost log callback failed: {e}")

        return result

    async def ask_json(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[dict, LLMResult]:
        """
        Like ask() but parses the response as JSON. Strips ```json fences.
        Returns (parsed_dict, full_result).
        """
        result = await self.ask(
            provider=provider,
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=agent_name,
            json_response=(provider in ("openai", "deepseek", "moonshot")),
            **kwargs,
        )
        parsed = _parse_json_loose(result.text)
        return parsed, result

    # ─── Provider implementations ────────────────────────────────────────
    async def _call_anthropic(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[list],
        **kwargs,
    ) -> LLMResult:
        client = self._get_client("anthropic")
        kw = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )
        if tools:
            kw["tools"] = tools
        kw.update(kwargs)
        resp = await client.messages.create(**kw)

        # Collect text blocks and any tool_use blocks
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in resp.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": getattr(block, "id", None),
                    "name": getattr(block, "name", None),
                    "input": getattr(block, "input", None),
                })

        usage = getattr(resp, "usage", None)
        in_toks = (getattr(usage, "input_tokens", 0) or 0) if usage else 0
        out_toks = (getattr(usage, "output_tokens", 0) or 0) if usage else 0
        return LLMResult(
            text="\n".join(text_parts).strip(),
            provider="anthropic",
            model=model,
            input_tokens=int(in_toks),
            output_tokens=int(out_toks),
            raw=resp,
            tool_calls=tool_calls,
        )

    async def _call_openai_compatible(
        self,
        provider: Provider,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[list],
        json_response: bool,
        **kwargs,
    ) -> LLMResult:
        """OpenAI / DeepSeek / Moonshot all share OpenAI's chat completions schema."""
        client = self._get_client(provider)
        all_msgs = [{"role": "system", "content": system}] + messages
        kw: dict = dict(
            model=model,
            messages=all_msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if json_response and _supports_json_response_format(model):
            kw["response_format"] = {"type": "json_object"}
        if tools:
            kw["tools"] = tools
        kw.update(kwargs)

        resp = await client.chat.completions.create(**kw)
        choice = resp.choices[0]
        text = choice.message.content or ""

        tool_calls: list[dict] = []
        if getattr(choice.message, "tool_calls", None):
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": tc.function.arguments,
                })

        usage = getattr(resp, "usage", None)
        in_toks = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        out_toks = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return LLMResult(
            text=text.strip(),
            provider=provider,
            model=model,
            input_tokens=int(in_toks),
            output_tokens=int(out_toks),
            raw=resp,
            tool_calls=tool_calls,
        )

    async def _call_google(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[list],
        **kwargs,
    ) -> LLMResult:
        """Google Gemini via google-genai SDK (async)."""
        client = self._get_client("google")
        from google.genai import types as gtypes

        # Map our messages list to Gemini's "contents" format.
        # Gemini uses role="user"|"model" (not "assistant"). System lives in config.
        contents: list = []
        for m in messages:
            role = "user" if m.get("role") == "user" else "model"
            content = m.get("content", "")
            contents.append(gtypes.Content(
                role=role,
                parts=[gtypes.Part.from_text(text=content)],
            ))

        config_kw: dict = dict(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        # Web grounding
        if tools:
            cfg_tools = []
            for t in tools:
                if isinstance(t, dict) and t.get("type") == "google_search":
                    cfg_tools.append(gtypes.Tool(google_search=gtypes.GoogleSearch()))
                else:
                    cfg_tools.append(t)
            if cfg_tools:
                config_kw["tools"] = cfg_tools
        config = gtypes.GenerateContentConfig(**config_kw)

        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        text = (resp.text or "").strip()

        # Token usage. NB: Gemini's usage_metadata sometimes returns
        # attribute values that are None (not just missing) — getattr's
        # default only fires for missing attrs, so wrap with `or 0`.
        usage = getattr(resp, "usage_metadata", None)
        in_toks = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        out_toks = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

        return LLMResult(
            text=text,
            provider="google",
            model=model,
            input_tokens=int(in_toks),
            output_tokens=int(out_toks),
            raw=resp,
        )

    # ─── Lazy client instantiation ───────────────────────────────────────
    def _get_client(self, provider: Provider):
        if provider in self._clients:
            return self._clients[provider]

        key = self._keys.get(provider)
        if not key:
            env_var = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai":    "OPENAI_API_KEY",
                "google":    "GOOGLE_API_KEY",
                "deepseek":  "DEEPSEEK_API_KEY",
                "moonshot":  "KIMI_API_KEY",
            }[provider]
            raise RuntimeError(
                f"No API key configured for '{provider}'. "
                f"Set {env_var} in your .env."
            )

        if provider == "anthropic":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=key)
        elif provider == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=key)
        elif provider in ("deepseek", "moonshot"):
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=key,
                base_url=OPENAI_COMPATIBLE_BASE_URLS[provider],
            )
        elif provider == "google":
            try:
                from google import genai
            except ImportError as e:
                raise RuntimeError(
                    "Gemini support requires `google-genai`. "
                    "Add it to requirements.txt and pip install."
                ) from e
            client = genai.Client(api_key=key)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        self._clients[provider] = client
        return client

    def has_key(self, provider: Provider) -> bool:
        return bool(self._keys.get(provider))


# ─── Singleton helpers ────────────────────────────────────────────────────
_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """Process-wide singleton router. Call from anywhere."""
    global _router
    if _router is None:
        _router = LLMRouter(log_callback=_default_log_callback)
    return _router


def reset_router() -> None:
    """Test helper. Drops the singleton so the next get_router() rebuilds it."""
    global _router
    _router = None


# ─── Default cost-log callback ────────────────────────────────────────────
def _default_log_callback(result: LLMResult, agent_name: Optional[str]) -> None:
    """
    Best-effort write to agent_call_log table. Non-fatal on failure.
    The table is created lazily — if it doesn't exist yet, this is a no-op.
    """
    try:
        from db.client import get_supabase
        from datetime import datetime, timezone
        get_supabase().table("agent_call_log").insert({
            "agent_name": agent_name,
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "called_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        # Table may not exist yet, or DB unreachable — telemetry is best-effort.
        pass


# ─── JSON helper ──────────────────────────────────────────────────────────
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _parse_json_loose(text: str) -> dict:
    """
    Parse JSON from an LLM response. Strips ```json fences, handles leading/
    trailing prose by extracting the outermost {...} or [...] block.
    """
    if not text:
        raise ValueError("Empty response — cannot parse JSON")
    s = text.strip()
    m = _JSON_FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    # If there's extra prose, slice from first { to last }
    first_brace = s.find("{")
    first_bracket = s.find("[")
    if first_brace == -1 and first_bracket == -1:
        raise ValueError(f"No JSON object/array found in: {s[:200]}")
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        start = first_bracket
        end = s.rfind("]") + 1
    else:
        start = first_brace
        end = s.rfind("}") + 1
    if end <= start:
        raise ValueError(f"Unbalanced braces in JSON response: {s[:200]}")
    return json.loads(s[start:end])
