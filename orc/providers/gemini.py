import os
import time
from collections import deque
from typing import List, Optional, Callable
from .base import AIProvider, ChatMessage, ChatResponse
from orc.logger import get_logger

logger = get_logger('providers.gemini')


class GeminiProvider(AIProvider):
    """Google Gemini API provider using official google-genai SDK."""

    name = "gemini"
    AVAILABLE_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]
    DEFAULT_MODEL = "gemini-3.5-flash-lite"

    # Free tier rate limits: 15 RPM, 1,000,000 TPM
    MAX_RPM = 14  # proactive buffer under 15
    MIN_REQUEST_INTERVAL_SEC = 0.25

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = None
        self._call_timestamps: deque = deque()
        self._last_call_time = 0.0

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY / GOOGLE_API_KEY not set")
            if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
                os.environ.pop("GEMINI_API_KEY", None)
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _enforce_rate_limit(self):
        """Proactively pace requests to avoid hitting Free Tier 15 RPM limit."""
        now = time.time()

        # Enforce minimum gap between calls
        elapsed_since_last = now - self._last_call_time
        if elapsed_since_last < self.MIN_REQUEST_INTERVAL_SEC:
            time.sleep(self.MIN_REQUEST_INTERVAL_SEC - elapsed_since_last)
            now = time.time()

        # Evict timestamps older than 60 seconds
        while self._call_timestamps and (now - self._call_timestamps[0] > 60.0):
            self._call_timestamps.popleft()

        # If at max RPM in sliding window, pause until oldest drops off
        if len(self._call_timestamps) >= self.MAX_RPM:
            wait_time = 60.0 - (now - self._call_timestamps[0]) + 0.1
            if wait_time > 0:
                logger.warning(f"Rate pacing: approaching 15 RPM free tier limit. Waiting {wait_time:.1f}s")
                time.sleep(wait_time)
                now = time.time()
                while self._call_timestamps and (now - self._call_timestamps[0] > 60.0):
                    self._call_timestamps.popleft()

        self._call_timestamps.append(now)
        self._last_call_time = now

    def _format_contents_and_config(self, messages: List[ChatMessage], max_tokens: int, temperature: float, tools: Optional[List[Callable]] = None):
        from google.genai import types

        system_parts = []
        raw_turns = []

        for m in messages:
            if m.role == 'system':
                system_parts.append(m.content)
            elif m.role == 'assistant':
                raw_turns.append({'role': 'model', 'text': m.content})
            else:
                raw_turns.append({'role': 'user', 'text': m.content})

        # Merge adjacent turns with identical roles to satisfy Gemini multi-turn requirements
        merged_turns = []
        for turn in raw_turns:
            if merged_turns and merged_turns[-1]['role'] == turn['role']:
                merged_turns[-1]['text'] += "\n\n" + turn['text']
            else:
                merged_turns.append(turn)

        # Fallback if no user messages exist
        if not merged_turns:
            merged_turns.append({'role': 'user', 'text': 'Hello'})

        contents = [
            types.Content(
                role=t['role'],
                parts=[types.Part.from_text(text=t['text'])]
            )
            for t in merged_turns
        ]

        system_instruction = "\n\n".join(system_parts) if system_parts else None

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=max_tokens,
            temperature=temperature,
            tools=tools if tools else None,
        )

        return contents, config

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        max_tokens: int = 1500,
        temperature: float = 0.7,
        tools: Optional[List[Callable]] = None,
    ) -> ChatResponse:
        client = self._get_client()
        model = model or self.DEFAULT_MODEL

        self._enforce_rate_limit()

        contents, config = self._format_contents_and_config(messages, max_tokens, temperature, tools=tools)

        start = time.time()
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            err_msg = str(e).lower()
            if ('503' in str(e) or 'unavailable' in err_msg or 'high demand' in err_msg or 'overloaded' in err_msg) and model == "gemini-3.8-flash":
                logger.warning("gemini-3.8-flash high demand (503); failing over to gemini-3.5-flash-lite")
                model = "gemini-3.5-flash-lite"
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            else:
                raise
        latency_ms = int((time.time() - start) * 1000)

        # Safe text extraction handling thinking tokens & multi-part responses
        content = ""
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            text_parts = [
                part.text for part in response.candidates[0].content.parts
                if getattr(part, 'text', None)
            ]
            content = "".join(text_parts)
        if not content and hasattr(response, 'text') and response.text:
            content = response.text

        tokens_in = 0
        tokens_out = 0
        if response.usage_metadata:
            tokens_in = response.usage_metadata.prompt_token_count or 0
            tokens_out = response.usage_metadata.candidates_token_count or 0

        return ChatResponse(
            content=content,
            provider=self.name,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )

    def count_tokens(self, contents, model: Optional[str] = None) -> int:
        """Count tokens accurately using the free Gemini count_tokens API."""
        client = self._get_client()
        model = model or self.DEFAULT_MODEL
        try:
            res = client.models.count_tokens(model=model, contents=contents)
            return getattr(res, 'total_tokens', 0)
        except Exception as e:
            logger.debug(f"count_tokens failed: {e}")
            return 0

    def list_models(self) -> List[str]:
        return self.AVAILABLE_MODELS
