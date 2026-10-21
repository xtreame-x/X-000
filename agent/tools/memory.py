"""Memory tools (6) backed by Markdown files under ``<state>/memory``.

Memory is the agent's long-term scratchpad: facts about the user, project
conventions, decisions. Names are slugified to a single safe file name, so a
memory name can never traverse directories or hit a Windows reserved name.
"""
from __future__ import annotations

from agent.platform_compat import atomic_write, normalize_newlines, slugify
from agent.tools.base import ToolContext, ToolResult, tool
from agent.state import now_iso

MAX_MEMORY_BYTES = 512_000


def _memory_path(ctx: ToolContext, name: str):
    if not name.strip():
        raise ValueError("memory name must not be empty")
    return ctx.settings.memory_dir / f"{slugify(name)}.md"


def _read(ctx: ToolContext, name: str) -> str:
    path = _memory_path(ctx, name)
    if not path.exists():
        return ""
    return normalize_newlines(path.read_text(encoding="utf-8-sig"))


def _write(ctx: ToolContext, name: str, content: str) -> int:
    path = _memory_path(ctx, name)
    if len(content.encode("utf-8")) > MAX_MEMORY_BYTES:
        raise ValueError(f"memory exceeds {MAX_MEMORY_BYTES} bytes; split it into several entries")
    atomic_write(path, content)
    return len(content.encode("utf-8"))


@tool(group="Memory", tags=("memory", "list", "notes", "remember"))
def memory_list(ctx: ToolContext) -> ToolResult:
    """List stored memory entries with their size and first line."""
    folder = ctx.settings.memory_dir
    if not folder.exists():
        return ToolResult.text("No memory entries yet.", entries=[])
    entries = []
    for path in sorted(folder.glob("*.md")):
        text = normalize_newlines(path.read_text(encoding="utf-8-sig"))
        first = next((line.strip() for line in text.split("\n") if line.strip()), "")
        entries.append(
            {"name": path.stem, "bytes": path.stat().st_size, "preview": first[:120]}
        )
    if not entries:
        return ToolResult.text("No memory entries yet.", entries=[])
    body = "\n".join(f"- {e['name']} ({e['bytes']} bytes): {e['preview']}" for e in entries)
    return ToolResult.text(body, entries=entries)


@tool(group="Memory", tags=("memory", "read", "recall"))
def memory_read(ctx: ToolContext, name: str) -> ToolResult:
    """Read a memory entry.

    Args:
        name: Memory entry name (matched case-insensitively after slugifying).
    """
    path = _memory_path(ctx, name)
    if not path.exists():
        return ToolResult.error(f"no memory named {name!r}", error="not_found", name=slugify(name))
    text = _read(ctx, name)
    return ToolResult.text(text or "(empty)", name=path.stem, bytes=len(text.encode("utf-8")))


@tool(group="Memory", tags=("memory", "write", "save", "remember", "store"))
def memory_write(ctx: ToolContext, name: str, content: str) -> ToolResult:
    """Create or replace a memory entry.

    Args:
        name: Memory entry name.
        content: Full Markdown content to store.
    """
    size = _write(ctx, name, content)
    return ToolResult.text(
        f"stored memory {slugify(name)!r} ({size} bytes)",
        name=slugify(name),
        bytes=size,
        action="write",
    )


@tool(group="Memory", tags=("memory", "append", "add", "note"))
def memory_append(ctx: ToolContext, name: str, content: str) -> ToolResult:
    """Append to a memory entry, creating it if needed.

    Args:
        name: Memory entry name.
        content: Text to append; a newline is added if the entry does not end with one.
    """
    existing = _read(ctx, name)
    addition = content if not content.endswith("\n") else content
    if existing and not existing.endswith("\n"):
        existing += "\n"
    updated = existing + addition
    size = _write(ctx, name, updated)
    return ToolResult.text(
        f"appended {len(content)} characters to {slugify(name)!r} ({size} bytes total)",
        name=slugify(name),
        bytes=size,
        action="append",
    )


@tool(group="Memory", tags=("memory", "replace", "edit"))
def memory_str_replace(
    ctx: ToolContext,
    name: str,
    old: str,
    new: str,
    allow_multiple: bool = False,
) -> ToolResult:
    """Replace text inside a memory entry.

    Args:
        name: Memory entry name.
        old: Exact text to find.
        new: Replacement text.
        allow_multiple: Replace every occurrence instead of requiring exactly one.
    """
    text = _read(ctx, name)
    if not text:
        return ToolResult.error(f"no memory named {name!r}", error="not_found", name=slugify(name))
    occurrences = text.count(old)
    if occurrences == 0:
        return ToolResult.error(
            "text not found in memory entry", error="not_found", name=slugify(name)
        )
    if occurrences > 1 and not allow_multiple:
        return ToolResult.error(
            f"text occurs {occurrences} times; set allow_multiple=true or add context",
            error="ambiguous_match",
            occurrences=occurrences,
        )
    updated = text.replace(old, new) if allow_multiple else text.replace(old, new, 1)
    size = _write(ctx, name, updated)
    return ToolResult.text(
        f"updated memory {slugify(name)!r} ({occurrences} match(es), {size} bytes)",
        name=slugify(name),
        bytes=size,
        occurrences=occurrences,
    )


@tool(group="Memory", tags=("memory", "delete", "forget", "remove"))
def memory_delete(ctx: ToolContext, name: str) -> ToolResult:
    """Delete a memory entry.

    Args:
        name: Memory entry name.
    """
    path = _memory_path(ctx, name)
    if not path.exists():
        return ToolResult.error(f"no memory named {name!r}", error="not_found", name=slugify(name))
    path.unlink()
    ctx.state.append_jsonl("memory_deletions.jsonl", {"timestamp": now_iso(), "name": path.stem})
    return ToolResult.text(f"deleted memory {path.stem!r}", name=path.stem, action="delete")


__all__ = [
    "memory_list",
    "memory_read",
    "memory_write",
    "memory_append",
    "memory_str_replace",
    "memory_delete",
]
