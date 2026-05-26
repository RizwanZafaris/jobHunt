"""
BaseAgent — shared functionality for all agents.

Phase 0 refactor: now delegates to agents.llm_router for provider-agnostic
calls. The legacy ask_claude() / ask_openai() methods are kept as
back-compat shims so existing agents keep working unchanged.

New code should use:

    result = await self.ask(system, messages, max_tokens=4096)
    # result.text, result.cost_usd, result.latency_ms

The agent's (provider, model) is inferred from `self.model` by default.
Pass `provider=` explicitly in __init__ to override.
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from rich.console import Console

from config.settings import get_settings
from agents.llm_router import (
    LLMRouter,
    LLMResult,
    Provider,
    get_router,
    infer_provider,
)
from agents.llm_fallback import ask_with_fallback

logger = logging.getLogger(__name__)
console = Console()


class BaseAgent(ABC):
    """All agents inherit from this."""

    def __init__(
        self,
        name: str,
        model: str,
        provider: Optional[Provider] = None,
    ):
        self.name = name
        self.model = model
        self.settings = get_settings()
        # Provider is inferred from model name unless explicitly passed.
        # JobScoutAgent (gpt-4.1) → openai, CompanyAgent (claude-opus-...) → anthropic, etc.
        self.provider: Provider = provider or infer_provider(model)
        # Back-compat lazy clients (kept for any agents still touching them directly)
        self._anthropic: Optional[AsyncAnthropic] = None
        self._openai: Optional[AsyncOpenAI] = None

    # ─── Router-based primary API ──────────────────────────────────────
    @property
    def router(self) -> LLMRouter:
        """Process-wide singleton multi-provider router."""
        return get_router()

    async def ask(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        tools: Optional[list] = None,
        provider: Optional[Provider] = None,
        model: Optional[str] = None,
        disable_fallback: bool = False,
        **kwargs: Any,
    ) -> LLMResult:
        """
        Provider-agnostic LLM call.
        Defaults to (self.provider, self.model). Override per-call when
        you want to send THIS request to a different model than the agent's
        default (e.g. ensemble critic running on R1 + K2 from one node).

        2026-05-26: now wraps router.ask() with OpenRouter auto-fallback
        when OPENROUTER_API_KEY is set. On retriable provider failure
        (rate limit, overload, transient 5xx, timeout), the same logical
        request is retried through OpenRouter. Set disable_fallback=True
        to opt out (e.g. tests that want to surface raw provider errors).
        """
        if disable_fallback:
            return await self.router.ask(
                provider=provider or self.provider,
                model=model or self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                agent_name=self.name,
                **kwargs,
            )
        return await ask_with_fallback(
            provider=provider or self.provider,
            model=model or self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            agent_name=self.name,
            router=self.router,
            **kwargs,
        )

    async def ask_json(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        provider: Optional[Provider] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[dict, LLMResult]:
        """
        Provider-agnostic JSON call. Returns (parsed_dict, full_result).
        Strips ```json fences and handles partial-prose responses.
        """
        return await self.router.ask_json(
            provider=provider or self.provider,
            model=model or self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=self.name,
            **kwargs,
        )

    # ─── Back-compat shims (existing agents) ───────────────────────────
    @property
    def anthropic(self) -> AsyncAnthropic:
        """Lazy native Anthropic client. Kept for back-compat."""
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._anthropic

    @property
    def openai(self) -> AsyncOpenAI:
        """Lazy native OpenAI client. Kept for back-compat."""
        if self._openai is None:
            self._openai = AsyncOpenAI(api_key=self.settings.openai_api_key)
        return self._openai

    async def ask_claude(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """
        DEPRECATED: prefer self.ask(). Kept so 9 existing agents keep working.
        Routes through the LLMRouter so cost/latency are still tracked.
        """
        result = await self.router.ask(
            provider="anthropic",
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=self.name,
        )
        return result.text

    async def ask_openai(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """
        DEPRECATED: prefer self.ask(). Kept for JobScoutAgent.
        Routes through the LLMRouter so cost/latency are still tracked.
        """
        result = await self.router.ask(
            provider="openai",
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_name=self.name,
        )
        return result.text

    def log(self, msg: str, style: str = "cyan"):
        console.print(f"[{style}][{self.name}][/{style}] {msg}")

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        """Main entry point for the agent."""
        pass
