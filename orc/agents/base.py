from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseAgent(ABC):
    """Base class for all agents."""

    name: str = "base"

    def __init__(self, providers, orchestrator):
        self.providers = providers
        self.orchestrator = orchestrator

    @abstractmethod
    def execute(self, task: str, context: Dict[str, Any]) -> str:
        """Execute a task and return result string."""
        pass

    def _chat(self, system: str, user: str, coding: bool = False) -> str:
        """Helper for one-shot AI calls."""
        from orc.providers import ChatMessage
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ]
        response = self.providers.chat(messages, coding=coding)
        return response.content
