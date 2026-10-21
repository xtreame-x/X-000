import os
import time
from typing import List, Optional
from .base import AIProvider, ChatMessage, ChatResponse


class GroqProvider(AIProvider):
    """Groq API provider."""

    name = "groq"
    AVAILABLE_MODELS = [
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
    ]

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("GROQ_API_KEY not set")
            import groq
            self._client = groq.Groq(api_key=self.api_key)
        return self._client

    def is_available(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            return False

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> ChatResponse:
        client = self._get_client()
        model = model or "openai/gpt-oss-120b"

        groq_messages = [{"role": m.role, "content": m.content} for m in messages]

        start = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=groq_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        latency_ms = int((time.time() - start) * 1000)

        choice = response.choices[0]
        usage = response.usage

        return ChatResponse(
            content=choice.message.content,
            provider=self.name,
            model=model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )

    def list_models(self) -> List[str]:
        return self.AVAILABLE_MODELS
