from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Callable


@dataclass
class ChatMessage:
    role: str  # system, user, assistant
    content: str


@dataclass
class ChatResponse:
    content: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        tools: Optional[List[Callable]] = None,
    ) -> ChatResponse:
        """Send messages and get response."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is configured and ready."""
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """List available models."""
        pass
