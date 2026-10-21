import os
import time
import requests
from typing import List, Optional
from .base import AIProvider, ChatMessage, ChatResponse


class OpenRouterProvider(AIProvider):
    """OpenRouter API provider — access 100+ models."""

    name = "openrouter"
    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MODELS_URL = "https://openrouter.ai/api/v1/models"

    # Popular models
    POPULAR_MODELS = [
        "anthropic/claude-3.5-sonnet",
        "anthropic/claude-3-haiku",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/gpt-4-turbo",
        "google/gemini-2.0-flash-001",
        "google/gemini-pro-1.5",
        "meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.1-8b-instruct",
        "deepseek/deepseek-chat",
        "mistralai/mistral-large-latest",
        "qwen/qwen-2.5-72b-instruct",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
    ]

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._models_cache = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> ChatResponse:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

        model = model or "anthropic/claude-3.5-sonnet"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jaya-prakash1123/oryn",
            "X-Title": "Oryn AI Agent",
        }

        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        start = time.time()
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=30)
        latency_ms = int((time.time() - start) * 1000)

        if resp.status_code != 200:
            raise Exception(f"OpenRouter error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ChatResponse(
            content=choice["message"]["content"],
            provider=self.name,
            model=model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )

    def list_models(self) -> List[str]:
        if not self.api_key:
            return self.POPULAR_MODELS

        if self._models_cache:
            return self._models_cache

        try:
            resp = requests.get(self.MODELS_URL, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self._models_cache = [m["id"] for m in data.get("data", [])]
                return self._models_cache
        except Exception:
            pass

        return self.POPULAR_MODELS
