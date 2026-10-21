"""Provider-agnostic LLM client.

Two real providers (Anthropic, OpenAI chat completions) and one scripted
provider for tests. Messages use a small neutral format so the agent loop never
branches on vendor::

    {"role": "user" | "assistant", "blocks": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
        {"type": "tool_result", "tool_use_id": "...", "blocks": [...], "is_error": false},
    ]}

Providers translate that to their wire format. No vendor SDK is required: httpx
talks to both APIs directly, which keeps the dependency surface small.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class LLMError(RuntimeError):
    """Raised when a provider call fails; includes status and body snippet."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    blocks: list[dict]
    stop_reason: str = "end_turn"
    usage: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.blocks if b.get("type") == "text").strip()

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in self.blocks
            if b.get("type") == "tool_use"
        ]


class Provider(Protocol):
    name: str
    model: str

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, max_tokens: int = 4096, timeout: int = 120) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _messages(self, messages: list[dict]) -> list[dict]:
        converted = []
        for message in messages:
            content = []
            for block in message["blocks"]:
                kind = block["type"]
                if kind == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif kind == "tool_use":
                    content.append(
                        {"type": "tool_use", "id": block["id"], "name": block["name"],
                         "input": block.get("input") or {}}
                    )
                elif kind == "tool_result":
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block["tool_use_id"],
                            "content": [b if b["type"] != "image" else {
                                "type": "image",
                                "source": {"type": "base64", "media_type": b["media_type"],
                                           "data": b["data"]},
                            } for b in block.get("blocks", [])],
                            "is_error": bool(block.get("is_error", False)),
                        }
                    )
            converted.append({"role": message["role"], "content": content})
        return converted

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": self._messages(messages),
            "tools": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["input_schema"],
                }
                for tool in tools
            ],
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                content=json.dumps(payload),
            )
        if response.status_code >= 400:
            raise LLMError(
                f"Anthropic API error {response.status_code}",
                status=response.status_code,
                body=response.text[:800],
            )
        data = response.json()
        blocks = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                blocks.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "tool_use":
                blocks.append(
                    {"type": "tool_use", "id": block["id"], "name": block["name"],
                     "input": block.get("input") or {}}
                )
        return LLMResponse(
            blocks=blocks,
            stop_reason=data.get("stop_reason", "end_turn"),
            usage=data.get("usage", {}),
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, *, max_tokens: int = 4096,
                 timeout: int = 120, base_url: str = OPENAI_URL) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.base_url = base_url

    def _messages(self, system: str, messages: list[dict]) -> list[dict]:
        converted: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            if message["role"] == "assistant":
                text = "".join(
                    b["text"] for b in message["blocks"] if b["type"] == "text"
                )
                calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                    }
                    for b in message["blocks"]
                    if b["type"] == "tool_use"
                ]
                entry: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls
                converted.append(entry)
                continue

            tool_results = [b for b in message["blocks"] if b["type"] == "tool_result"]
            if tool_results:
                for block in tool_results:
                    text = "\n".join(
                        item.get("text", "") for item in block.get("blocks", [])
                        if item.get("type") == "text"
                    )
                    converted.append(
                        {"role": "tool", "tool_call_id": block["tool_use_id"], "content": text}
                    )
                continue

            text = "\n".join(b["text"] for b in message["blocks"] if b["type"] == "text")
            converted.append({"role": "user", "content": text})
        return converted

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._messages(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ],
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                content=json.dumps(payload),
            )
        if response.status_code >= 400:
            raise LLMError(
                f"OpenAI API error {response.status_code}",
                status=response.status_code,
                body=response.text[:800],
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        blocks: list[dict] = []
        if message.get("content"):
            blocks.append({"type": "text", "text": message["content"]})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": function.get("arguments")}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.get("id") or f"call_{len(blocks)}",
                    "name": function.get("name", ""),
                    "input": arguments,
                }
            )
        return LLMResponse(
            blocks=blocks,
            stop_reason=choice.get("finish_reason", "end_turn"),
            usage=data.get("usage", {}),
        )


# ---------------------------------------------------------------------------
# Scripted provider (tests, offline demos)
# ---------------------------------------------------------------------------
class ScriptedProvider:
    """Replays canned responses, recording the tools it was offered."""

    name = "scripted"

    def __init__(self, responses: Iterable[LLMResponse | list[dict]], model: str = "scripted") -> None:
        self.queue = [r if isinstance(r, LLMResponse) else LLMResponse(blocks=r) for r in responses]
        self.model = model
        self.calls: list[dict] = []

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages)),
                           "tools": [t["name"] for t in tools]})
        if not self.queue:
            return LLMResponse(blocks=[{"type": "text", "text": "(scripted provider exhausted)"}])
        return self.queue.pop(0)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_provider(settings, *, provider: str | None = None) -> Provider:
    """Pick a provider from an explicit choice, else from available API keys."""
    choice = (provider or os.environ.get("AGENT_PROVIDER") or "").strip().lower()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if choice in {"", "auto"}:
        if anthropic_key:
            choice = "anthropic"
        elif openai_key:
            choice = "openai"
        else:
            raise LLMError(
                "no LLM credentials found: set ANTHROPIC_API_KEY or OPENAI_API_KEY "
                "(or AGENT_PROVIDER=scripted for an offline demo)"
            )

    model = settings.model
    if choice == "anthropic":
        if not anthropic_key:
            raise LLMError("AGENT_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(anthropic_key, model if model.startswith("claude") else
                                 "claude-sonnet-4-5")
    if choice == "openai":
        if not openai_key:
            raise LLMError("AGENT_PROVIDER=openai but OPENAI_API_KEY is not set")
        return OpenAIProvider(openai_key, model if not model.startswith("claude") else "gpt-4.1")
    if choice == "scripted":
        return ScriptedProvider([])
    raise LLMError(f"unknown provider {choice!r}; use anthropic, openai or scripted")


__all__ = [
    "AnthropicProvider",
    "LLMError",
    "LLMResponse",
    "OpenAIProvider",
    "Provider",
    "ScriptedProvider",
    "ToolCall",
    "build_provider",
]
