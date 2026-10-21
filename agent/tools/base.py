"""Tool primitives: results, context, schema derivation and the registry.

Schemas are *derived from the Python signature* with pydantic rather than
hand-written, so a handler and its advertised JSON Schema cannot drift apart.
Descriptions come from Google-style docstrings.

``extra="forbid"`` is set on every argument model: a model that invents an
argument name gets a precise validation error instead of a silent no-op.
"""
from __future__ import annotations

import base64
import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, create_model

from agent.config import NotConfigured, Settings
from agent.platform_compat import atomic_write, slugify
from agent.state import StateStore, now_iso

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """A tool's return value, shaped as content blocks plus structured data."""

    blocks: list[dict] = field(default_factory=list)
    structured: dict = field(default_factory=dict)
    is_error: bool = False

    @classmethod
    def text(cls, text: str, **structured: Any) -> "ToolResult":
        return cls(blocks=[{"type": "text", "text": text}], structured=structured)

    @classmethod
    def json(cls, data: Any, *, text: str | None = None, **structured: Any) -> "ToolResult":
        import json as _json

        payload = _json.dumps(data, indent=2, ensure_ascii=False, default=str)
        return cls(
            blocks=[{"type": "text", "text": text or payload}],
            structured={"data": data, **structured},
        )

    @classmethod
    def error(cls, message: str, **details: Any) -> "ToolResult":
        return cls(blocks=[{"type": "text", "text": message}], structured=details, is_error=True)

    @classmethod
    def image(cls, path: str | Path, *, media_type: str | None = None) -> "ToolResult":
        target = Path(path)
        data = base64.b64encode(target.read_bytes()).decode("ascii")
        guessed = media_type or _guess_media_type(target.suffix)
        return cls(
            blocks=[{"type": "image", "data": data, "media_type": guessed, "path": str(target)}],
            structured={"path": str(target), "mediaType": guessed, "bytes": target.stat().st_size},
        )

    def text_content(self) -> str:
        return "\n".join(b.get("text", "") for b in self.blocks if b.get("type") == "text")

    def to_dict(self) -> dict:
        return {"blocks": self.blocks, "structured": self.structured, "isError": self.is_error}


def _guess_media_type(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
@dataclass
class ToolContext:
    """Everything a handler may touch besides its own arguments."""

    settings: Settings
    state: StateStore
    #: Sink for side-channel UI events (asked questions, charts, cards).
    emit: Callable[[dict], None] | None = None
    #: Blocking user-input callback; ``None`` in non-interactive runs.
    ask: Callable[[str, list[str] | None], str] | None = None
    #: Tools unlocked by ``tool_search`` in this session.
    loaded_tools: set[str] = field(default_factory=set)
    stop_requested: bool = False
    stop_reason: str | None = None

    def emit_event(self, event: dict) -> None:
        if self.emit is not None:
            self.emit({"timestamp": now_iso(), **event})

    def require_secret(self, env_var: str, provider: str) -> str:
        return self.settings.require_secret(env_var, provider)

    def resolve_path(self, path: str, *, must_exist: bool = False) -> Path:
        from agent.platform_compat import InvalidPath, is_within, resolve_workspace_path

        target = resolve_workspace_path(self.settings.workspace, path)
        if self.settings.restrict_to_workspace and not is_within(target, self.settings.workspace):
            raise InvalidPath(
                f"{path!r} resolves outside the workspace ({self.settings.workspace}); "
                "set AGENT_ALLOW_OUTSIDE_WORKSPACE=1 to permit it"
            )
        if must_exist and not target.exists():
            raise FileNotFoundError(f"no such file: {target}")
        return target

    def save_artifact(self, content: str | bytes, *, name: str, ext: str, mime: str) -> dict:
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        stem = slugify(name)
        target = self.settings.artifacts_dir / f"{stem}.{ext.lstrip('.')}"
        counter = 2
        while target.exists():
            target = self.settings.artifacts_dir / f"{stem}-{counter}.{ext.lstrip('.')}"
            counter += 1
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            atomic_write(target, content)
        result = {"path": str(target), "mimeType": mime, "bytes": target.stat().st_size}
        self.emit_event({"type": "artifact", **result})
        return result


# ---------------------------------------------------------------------------
# Schema derivation
# ---------------------------------------------------------------------------
_ARGS_HEADER = re.compile(r"^(Args|Arguments|Parameters):\s*$", re.MULTILINE)


def split_docstring(docstring: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (description, {arg: description})."""
    if not docstring:
        return "", {}
    cleaned = inspect.cleandoc(docstring)
    match = _ARGS_HEADER.search(cleaned)
    if match:
        description = cleaned[: match.start()].strip()
        arg_block = cleaned[match.end() :]
    else:
        description = cleaned.strip()
        arg_block = ""

    params: dict[str, str] = {}
    current: str | None = None
    trailing_sections = {"Returns", "Return", "Raises", "Yields", "Example", "Examples", "Note", "Notes"}
    for line in arg_block.splitlines():
        if not line.strip():
            continue
        entry = re.match(r"^\s{0,8}([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if entry and entry.group(1) in trailing_sections:
            # A new docstring section ends the argument list.
            current = None
            continue
        if entry:
            current = entry.group(1)
            params[current] = entry.group(2).strip()
        elif current:
            params[current] = (params[current] + " " + line.strip()).strip()
    return description, {k: v for k, v in params.items() if v}


def _is_context_annotation(annotation: Any) -> bool:
    return annotation is ToolContext or annotation.__class__ is type and annotation.__name__ == "ToolContext"


def params_model(func: Callable, *, model_name: str | None = None) -> type[BaseModel]:
    """Derive a pydantic argument model from a handler signature."""
    signature = inspect.signature(func)
    try:
        hints = inspect.get_annotations(func, eval_str=True)
    except Exception:  # pragma: no cover - unresolved forward refs
        hints = {}
    _, arg_docs = split_docstring(func.__doc__)

    fields: dict[str, tuple[Any, Any]] = {}
    for name, param in signature.parameters.items():
        if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
            continue
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise TypeError(f"{func.__name__}: parameter {name!r} needs a type annotation")
        if _is_context_annotation(annotation):
            continue
        if name in {"ctx", "context"} and annotation is inspect.Parameter.empty:
            continue

        description = arg_docs.get(name, "")
        origin = get_origin(annotation)
        if origin is Annotated:
            base, *meta = get_args(annotation)
            description = " ".join(str(m) for m in meta if isinstance(m, str)) or description
            annotation = base

        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (annotation, Field(default, description=description or None))

    model = create_model(
        model_name or f"{func.__name__}Args",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return model


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    group: str
    description: str
    params: type[BaseModel]
    handler: Callable[..., ToolResult]
    requires_tool_search: bool = False
    tags: tuple[str, ...] = ()
    #: True when the tool needs credentials that may be absent at runtime.
    needs_configuration: tuple[str, ...] = ()

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.params.model_json_schema(),
        }

    def validate(self, arguments: dict) -> BaseModel:
        return self.params.model_validate(arguments)

    def invoke(self, arguments: dict, ctx: ToolContext) -> ToolResult:
        validated = self.validate(arguments)
        kwargs = validated.model_dump()
        if _wants_context(self.handler):
            kwargs["ctx"] = ctx
        result = self.handler(**kwargs)
        if not isinstance(result, ToolResult):
            raise TypeError(f"{self.name} returned {type(result).__name__}, expected ToolResult")
        return result


def _wants_context(func: Callable) -> bool:
    try:
        hints = inspect.get_annotations(func, eval_str=True)
    except Exception:  # pragma: no cover
        return False
    for name, annotation in hints.items():
        if name in {"ctx", "context"} and _is_context_annotation(annotation):
            return True
        if _is_context_annotation(annotation):
            return True
    return False


class Registry:
    """Name -> Tool map with group bookkeeping and keyword search."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._loaded = False

    def add(self, value: Tool) -> Tool:
        if value.name in self._tools:
            raise ValueError(f"duplicate tool name: {value.name}")
        self._tools[value.name] = value
        return value

    def load_all(self) -> "Registry":
        if not self._loaded:
            from agent.tools import load_all_tools

            load_all_tools()
            self._loaded = True
        return self

    def get(self, name: str) -> Tool:
        tool_obj = self._tools.get(name)
        if tool_obj is None:
            raise KeyError(f"unknown tool: {name}")
        return tool_obj

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def by_group(self) -> dict[str, list[Tool]]:
        groups: dict[str, list[Tool]] = {}
        for tool_obj in self._tools.values():
            groups.setdefault(tool_obj.group, []).append(tool_obj)
        return groups

    def schemas(self, *, include_deferred: bool = False) -> list[dict]:
        return [
            t.schema()
            for t in self._tools.values()
            if include_deferred or not t.requires_tool_search
        ]

    def search(self, query: str, *, limit: int = 10) -> list[Tool]:
        terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
        if not terms:
            return []
        scored: list[tuple[float, Tool]] = []
        for tool_obj in self._tools.values():
            haystack = " ".join(
                [tool_obj.name, tool_obj.group, tool_obj.description, *tool_obj.tags]
            ).lower()
            # Underscores are separators, not content: 'webfetch' must still find
            # 'web_fetch'.
            squashed_name = tool_obj.name.lower().replace("_", "")
            score = 0.0
            for term in terms:
                squashed_term = term.replace("_", "")
                if term in tool_obj.name.lower() or squashed_term in squashed_name:
                    score += 3.0
                if term in tool_obj.group.lower():
                    score += 1.5
                if term in haystack:
                    score += 1.0
            if score:
                scored.append((score, tool_obj))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [t for _, t in scored[:limit]]

    def stats(self) -> dict:
        groups = self.by_group()
        return {
            "total": len(self._tools),
            "deferred": sum(1 for t in self._tools.values() if t.requires_tool_search),
            "groups": {name: len(items) for name, items in groups.items()},
        }


REGISTRY = Registry()


def tool(
    *,
    name: str | None = None,
    group: str,
    description: str | None = None,
    requires_tool_search: bool = False,
    tags: Iterable[str] = (),
    needs_configuration: Iterable[str] = (),
    registry: Registry | None = None,
) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
    """Register a handler as a tool.

    Args:
        name: Override the tool name (defaults to the function name, which is
            kept identical to the catalog name so the two cannot drift).
        group: Provider or capability family, used for docs and search.
        description: Override the description taken from the docstring.
        requires_tool_search: Tool stays hidden until ``tool_search`` unlocks it.
        tags: Extra keywords for tool search.
    """

    def decorator(func: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        tool_name = name or func.__name__
        doc_desc, _ = split_docstring(func.__doc__)
        target = registry or REGISTRY
        target.add(
            Tool(
                name=tool_name,
                group=group,
                description=description or doc_desc or tool_name.replace("_", " "),
                params=params_model(func),
                handler=func,
                requires_tool_search=requires_tool_search,
                tags=tuple(tags),
                needs_configuration=tuple(needs_configuration),
            )
        )
        return func

    return decorator


def summarise_tools(tools: Iterable[Tool]) -> str:
    return "\n".join(f"- {t.name} ({t.group}): {t.description.splitlines()[0]}" for t in tools)


__all__ = [
    "REGISTRY",
    "NotConfigured",
    "Registry",
    "Tool",
    "ToolContext",
    "ToolResult",
    "params_model",
    "split_docstring",
    "summarise_tools",
    "tool",
]
