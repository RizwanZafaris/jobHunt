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
    # Anthropic prompt-caching telemetry. Only Anthropic returns these today;
    # other providers leave them at 0. cache_creation_input_tokens = tokens
    # written to the cache on this call (billed at 1.25× input rate);
    # cache_read_input_tokens = tokens read from a previously cached prefix
    # (billed at 0.10× input rate — the 90% discount).
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


def _anthropic_cache_enabled() -> bool:
    """
    Feature flag for Anthropic prompt caching. Defaults ON (the cost is
    purely positive — a cache miss costs the same as no cache, and a cache
    hit gives a 90% input-token discount). Set ANTHROPIC_PROMPT_CACHE_ENABLED=0
    to disable for staged rollback.
    """
    return os.environ.get("ANTHROPIC_PROMPT_CACHE_ENABLED", "1") != "0"


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
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
    cache_create = int(cache_creation_input_tokens or 0)
    cache_read = int(cache_read_input_tokens or 0)
    in_rate, out_rate = pricing[0], pricing[1]
    in_cost = in_toks * in_rate / 1_000_000
    out_cost = out_toks * out_rate / 1_000_000
    # Anthropic prompt-cache pricing: writes cost 1.25× the input rate, reads
    # cost 0.10× the input rate. Per the Anthropic prompt-caching docs.
    # Non-Anthropic providers always pass 0 for these so the math is a no-op.
    cache_create_cost = cache_create * in_rate * 1.25 / 1_000_000
    cache_read_cost = cache_read * in_rate * 0.10 / 1_000_000
    return round(in_cost + out_cost + cache_create_cost + cache_read_cost, 6)


def _resolve_temperature(model: str, requested: float) -> float:
    """
    Some models force a fixed temperature and 400 if you pass anything else.

    Known cases:
      - kimi-k2.5 / kimi-k2.6: Moonshot's K2.x reasoning models REQUIRE
        temperature=1. They return HTTP 400 with "invalid temperature: only
        1 is allowed for this model" otherwise.

    For everything else we honour the caller's requested temperature.
    Update this function — never hardcode at the call site.
    """
    if model.startswith("kimi-k2"):
        return 1.0
    return requested


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
        result.cost_usd = _estimate_cost(
            model,
            result.input_tokens,
            result.output_tokens,
            result.cache_creation_input_tokens,
            result.cache_read_input_tokens,
        )

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

        # Prompt caching: wrap large system prompts in a single text block with
        # cache_control={"type":"ephemeral"} so Anthropic caches the prefix.
        # On a cache hit, those input tokens are billed at 10% of the input
        # rate (the 90% discount). Anthropic's minimum is 1024 tokens; below
        # that the cache_control field is silently ignored, so we gate on a
        # chars/4 heuristic to skip the wrapping when it can't help.
        # Per https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching.
        system_arg: Any = system
        if (
            isinstance(system, str)
            and len(system) >= 1024 * 4  # ~1024 tokens, chars/4 heuristic
            and _anthropic_cache_enabled()
        ):
            system_arg = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        kw = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_arg,
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
        # Anthropic returns cache hits/misses on usage:
        #   cache_creation_input_tokens — tokens written to the cache (1.25× rate)
        #   cache_read_input_tokens     — tokens read from the cache (0.10× rate)
        # Both default to 0 when caching is disabled, the prefix is too short,
        # or the cache is not yet warm.
        cache_create = (
            (getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        )
        cache_read = (
            (getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        )
        return LLMResult(
            text="\n".join(text_parts).strip(),
            provider="anthropic",
            model=model,
            input_tokens=int(in_toks),
            output_tokens=int(out_toks),
            cache_creation_input_tokens=int(cache_create),
            cache_read_input_tokens=int(cache_read),
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
            temperature=_resolve_temperature(model, temperature),
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
            # Reasoning models (deepseek-reasoner, kimi-k2.x) routinely take
            # 90-120s on iter 2+ critic calls where the resume + JD prompt is
            # large. The OpenAI SDK's default 60s read timeout aborts mid-call
            # and triggers internal retries, which can wedge a build for
            # 10+ min (e.g. Adyen 1022 build 55210ffa stalled here on
            # 2026-05-09). 180s gives Kimi K2.5 enough head-room for full
            # reasoning + JSON emission with our 8000/16000 max_tokens budget.
            client = AsyncOpenAI(
                api_key=key,
                base_url=OPENAI_COMPATIBLE_BASE_URLS[provider],
                timeout=180.0,
                max_retries=2,
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
def _derive_graph_and_node(agent_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Derive (graph, node_name) from an agent_name string.

    Convention:
      - g1.*  / JobScout* / scout.*           -> graph='G1', node = stripped
      - g2.*                                   -> graph='G2', node = stripped
      - g3.*                                   -> graph='G3', node = stripped
      - g4.* / linkedin.*                      -> graph='G4', node = stripped
      - persona.* / company.* / boss.* /
        profile.* / debug.*                    -> graph=None (utility),
                                                 node = stripped
      - Any other label (RizwanAgent,
        ResumeBuilder, InterviewAgent,
        CompanyAgent[X], BossAgent, ...)       -> graph=None, node = full name

    2026-05-12 BUG-024: agent_call_log.graph was 100% NULL (1,092/1,092)
    because nothing ever populated it. Without graph, the cost dashboard
    cannot attribute spend to G1/G2/G3/G4 — $32.40 of telemetry was
    untraceable. We now derive both fields at insert time from the only
    populated identifier (agent_name).
    """
    if not agent_name:
        return None, None
    name = agent_name
    lower = name.lower()
    # G1 — discovery / JobScout family.
    if lower.startswith("g1.") or lower.startswith("jobscout") or lower.startswith("scout."):
        node = name.split(".", 1)[1] if "." in name else name
        return "G1", node
    # G2 — resume builder family.
    if lower.startswith("g2."):
        return "G2", name.split(".", 1)[1]
    # G3 — coach family.
    if lower.startswith("g3."):
        return "G3", name.split(".", 1)[1]
    # G4 — LinkedIn / outreach family.
    if lower.startswith("g4.") or lower.startswith("linkedin."):
        node = name.split(".", 1)[1] if "." in name else name
        return "G4", node
    # G5 — reserved for the next phase; routed here so cost reports already
    # have a slot when it lands. No-op until agents start emitting g5.*.
    if lower.startswith("g5."):
        return "G5", name.split(".", 1)[1]
    # G6 — follow-up cadence family (Phase 1.3, 2026-05-12).
    if lower.startswith("g6."):
        return "G6", name.split(".", 1)[1]
    # G7 — application answers (Tier 3, in flight).
    if lower.startswith("g7."):
        return "G7", name.split(".", 1)[1]
    # G9 — STAR+R story extractor (Phase 1.2, 2026-05-12).
    if lower.startswith("g9."):
        return "G9", name.split(".", 1)[1]
    # G11 — voice calibration (Tier 4, 2026-05-12).
    if lower.startswith("g11."):
        return "G11", name.split(".", 1)[1]
    # Utility / non-graph workers — strip prefix for node_name, leave graph=None.
    for prefix in ("persona.", "company.", "boss.", "profile.", "debug."):
        if lower.startswith(prefix):
            return None, name.split(".", 1)[1]
    # Unknown / legacy bare names — keep as node_name verbatim.
    return None, name


def _default_log_callback(result: LLMResult, agent_name: Optional[str]) -> None:
    """
    Best-effort write to agent_call_log table. Non-fatal on failure.
    The table is created lazily — if it doesn't exist yet, this is a no-op.

    2026-05-12 (Bug #7 fix): agent_call_log gained user_id NOT NULL in the
    multi-tenancy migration 001, but this writer was never updated. Every
    INSERT silently failed with code 23502 because we wrapped the call in
    a `try/except: pass`. Net result: 24 hours of production LLM calls
    with ZERO rows in agent_call_log, breaking every /costs/* endpoint
    (cost dashboard returned empty).

    Same archetype as the 5 other user_id-NOT-NULL writers fixed earlier
    today (rizwan_profile, jobs, companies, resume_builds, applications).

    2026-05-12 (BUG-024): also derive `graph` and `node_name` from
    agent_name so cost attribution by graph works again (was 100% NULL).
    """
    try:
        from db.client import get_supabase
        from datetime import datetime, timezone
        user_id = os.environ.get(
            "RIZWAN_USER_ID",
            "00000000-0000-0000-0000-000000000001",
        )
        graph, node_name = _derive_graph_and_node(agent_name)
        get_supabase().table("agent_call_log").insert({
            "agent_name": agent_name,
            "graph": graph,
            "node_name": node_name,
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "called_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
        }).execute()
    except Exception as e:
        # Table may not exist yet, or DB unreachable — telemetry is best-effort.
        # 2026-05-12: surface the error so silent failures can be diagnosed.
        # Audit confirmed agent_call_log had 0 rows in 24h before this fix.
        logger.debug(f"agent_call_log insert failed (non-fatal): {type(e).__name__}: {e}")


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
