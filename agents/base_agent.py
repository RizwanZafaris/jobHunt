"""
BaseAgent — shared functionality for all agents.
"""

from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from config.settings import get_settings
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class BaseAgent(ABC):
    """All agents inherit from this."""

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.settings = get_settings()
        self._anthropic: Optional[AsyncAnthropic] = None
        self._openai: Optional[AsyncOpenAI] = None

    @property
    def anthropic(self) -> AsyncAnthropic:
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._anthropic

    @property
    def openai(self) -> AsyncOpenAI:
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
        """Call Claude (Anthropic) with the given messages."""
        response = await self.anthropic.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    async def ask_openai(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Call OpenAI GPT with the given messages."""
        all_messages = [{"role": "system", "content": system}] + messages
        response = await self.openai.chat.completions.create(
            model=self.model,
            messages=all_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content

    def log(self, msg: str, style: str = "cyan"):
        console.print(f"[{style}][{self.name}][/{style}] {msg}")

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        """Main entry point for the agent."""
        pass
