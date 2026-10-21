import os
import time
import requests
from typing import List, Optional
from .base import AIProvider, ChatMessage, ChatResponse


class NvidiaProvider(AIProvider):
    """NVIDIA NIM API provider — free tier available."""

    name = "nvidia"
    API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

    AVAILABLE_MODELS = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "meta/llama-3.3-70b-instruct",
    ]

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY")

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
            raise ValueError("NVIDIA_API_KEY not set")

        model = model or "nvidia/nemotron-3-ultra-550b-a55b:free"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        start = time.time()
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=60)
        latency_ms = int((time.time() - start) * 1000)

        if resp.status_code != 200:
            raise Exception(f"NVIDIA error {resp.status_code}: {resp.text[:200]}")

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
        return self.AVAILABLE_MODELS
