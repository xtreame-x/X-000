"""Tool dispatch: validation, deferred-tool gating, error normalisation, audit.

Every failure becomes a ``ToolResult(is_error=True)`` with a machine-readable
``error`` code, so the model can recover instead of the process dying.  Errors
are never swallowed silently: they are logged and returned.
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent.config import NotConfigured, Settings
from agent.platform_compat import InvalidPath
from agent.self_edit import SelfEditError
from agent.state import StateStore, now_iso
from agent.tools.base import REGISTRY, Registry, Tool, ToolContext, ToolResult


@dataclass
class CallRecord:
    name: str
    arguments: dict
    duration_ms: float
    is_error: bool
    error_code: str | None = None


class Dispatcher:
    def __init__(
        self,
        ctx: ToolContext,
        *,
        registry: Registry | None = None,
        audit: bool = True,
    ) -> None:
        self.ctx = ctx
        self.registry = (registry or REGISTRY).load_all()
        self.audit = audit
        self.history: list[CallRecord] = []

    # --- gating --------------------------------------------------------------
    def _gate(self, tool_obj: Tool) -> ToolResult | None:
        if not tool_obj.requires_tool_search:
            return None
        if tool_obj.name in self.ctx.loaded_tools:
            return None
        return ToolResult.error(
            f"tool {tool_obj.name!r} is deferred. Call tool_search with query "
            f"{tool_obj.name.split('__')[-1]!r} to load it, then retry.",
            error="tool_not_loaded",
            tool=tool_obj.name,
        )

    # --- dispatch ------------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments or {}
        started = time.monotonic()
        result: ToolResult

        try:
            tool_obj = self.registry.get(name)
        except KeyError:
            result = self._unknown_tool(name)
            self._log(name, args, result, started, error_code="unknown_tool")
            return result

        gated = self._gate(tool_obj)
        if gated is not None:
            self._log(name, args, gated, started, error_code="tool_not_loaded")
            return gated

        try:
            result = tool_obj.invoke(args, self.ctx)
        except ValidationError as exc:
            result = ToolResult.error(
                f"invalid arguments for {name}: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
                ),
                error="invalid_arguments",
                tool=name,
                details=json.loads(exc.json()),
            )
            self._log(name, args, result, started, error_code="invalid_arguments")
            return result
        except NotConfigured as exc:
            # ``to_dict`` also carries a 'message' key, which collides with the
            # positional argument of ToolResult.error.
            details = exc.to_dict()
            details.pop("message", None)
            result = ToolResult.error(str(exc), **details)
            self._log(name, args, result, started, error_code="not_configured")
            return result
        except (SelfEditError, InvalidPath, FileNotFoundError, PermissionError) as exc:
            result = ToolResult.error(f"{type(exc).__name__}: {exc}", error="bad_request", tool=name)
            self._log(name, args, result, started, error_code="bad_request")
            return result
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the loop
            detail = traceback.format_exc(limit=6)
            result = ToolResult.error(
                f"{name} failed: {type(exc).__name__}: {exc}",
                error="tool_crashed",
                tool=name,
                traceback=detail,
            )
            self._log(name, args, result, started, error_code="tool_crashed")
            return result

        self._log(name, args, result, started, error_code=result.structured.get("error"))
        return result

    def call_many(self, calls: list[dict]) -> list[ToolResult]:
        results: list[ToolResult] = []
        for item in calls:
            results.append(self.call(item["name"], item.get("arguments", {})))
        return results

    def _unknown_tool(self, name: str) -> ToolResult:
        suggestions = [t.name for t in self.registry.search(name, limit=5)]
        return ToolResult.error(
            f"unknown tool: {name}"
            + (f". Closest matches: {', '.join(suggestions)}" if suggestions else ""),
            error="unknown_tool",
            tool=name,
            suggestions=suggestions,
        )

    # --- audit ---------------------------------------------------------------
    def _log(self, name: str, args: dict, result: ToolResult, started: float,
             *, error_code: str | None) -> None:
        duration = (time.monotonic() - started) * 1000
        self.history.append(
            CallRecord(name=name, arguments=args, duration_ms=duration,
                       is_error=result.is_error, error_code=error_code)
        )
        if not self.audit:
            return
        record = {
            "timestamp": now_iso(),
            "tool": name,
            "arguments": _truncate(args),
            "isError": result.is_error,
            "errorCode": error_code,
            "durationMs": round(duration, 2),
        }
        try:
            self.ctx.state.append_jsonl("audit.jsonl", record)
        except OSError:
            # Auditing must never break the call it is recording.
            pass


def _truncate(value: Any, limit: int = 2000) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return {"_truncated": True, "preview": text[:limit]}


def build_context(settings: Settings, *, emit=None, ask=None) -> ToolContext:
    state = StateStore(settings.state_dir)
    return ToolContext(settings=settings, state=state, emit=emit, ask=ask)


__all__ = ["CallRecord", "Dispatcher", "build_context"]
