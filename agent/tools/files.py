"""File and shell tools (6): bash_tool, create_file, str_replace, view,
present_files, Artifact.

``bash_tool`` keeps its documented name for API compatibility but executes
through the platform shell selected in :mod:`agent.platform_compat` — pwsh,
powershell or cmd on Windows, bash on POSIX.  Writes go through
:class:`agent.self_edit.SelfEditor`, so every edit is snapshotted and
syntax-checked, including edits to the agent's own source.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from agent.platform_compat import is_within, run_command
from agent.self_edit import SelfEditor
from agent.tools.base import ToolContext, ToolResult, tool
from agent.state import now_iso

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

DESTRUCTIVE_PATTERNS = [
    (r"\brm\s+(-[a-z]*\s+)*-?[rf]{1,2}\s+/(\s|$)", "recursive delete of /"),
    (r"\brm\s+-[a-z]*[rf]{1,2}\s+/(\s|$)", "recursive delete of /"),
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\bdd\b[^\n]*\bof=/dev/", "raw device write"),
    (r":\(\)\s*\{\s*:\|\s*:", "fork bomb"),
    (r"Remove-Item[^\n]*-Recurse[^\n]*\b[A-Za-z]:\\(\s|$|\*)", "recursive delete of a drive root"),
    (r"\bformat\s+[A-Za-z]:", "drive format"),
    (r"\bdel\s+/[sf]\b[^\n]*\b[A-Za-z]:\\", "forced recursive delete of a drive root"),
    (r"\b(shutdown|reboot)\b", "machine shutdown"),
    (r"\bgit\s+push\s+.*--force\b", "force push"),
]
_DESTRUCTIVE = [(re.compile(p, re.IGNORECASE), why) for p, why in DESTRUCTIVE_PATTERNS]


def _editor(ctx: ToolContext) -> SelfEditor:
    return SelfEditor(
        ctx.settings.workspace,
        ctx.state,
        restrict_to_workspace=ctx.settings.restrict_to_workspace,
    )


@tool(group="Files and code", tags=("shell", "terminal", "run", "command", "execute"))
def bash_tool(
    ctx: ToolContext,
    command: str,
    cwd: str | None = None,
    timeout: int | None = None,
) -> ToolResult:
    """Run a shell command in the workspace and capture its output.

    On Windows the command runs through PowerShell (pwsh, else powershell, else
    cmd) with a UTF-8 console; on POSIX it runs through bash -lc. Use PowerShell
    syntax on Windows and POSIX syntax elsewhere.

    Args:
        command: The command line to execute.
        cwd: Working directory, relative to the workspace. Defaults to the workspace root.
        timeout: Seconds before the process tree is killed. Defaults to the configured timeout.
    """
    if not command.strip():
        return ToolResult.error("command must not be empty", error="invalid_arguments")

    for pattern, why in _DESTRUCTIVE:
        if pattern.search(command):
            if not ctx.settings.allow_destructive_commands:
                return ToolResult.error(
                    f"refused: looks like {why}. Set AGENT_ALLOW_DESTRUCTIVE_COMMANDS=1 to override.",
                    error="destructive_command_refused",
                    pattern=pattern.pattern,
                )

    workdir = ctx.settings.workspace
    if cwd:
        candidate = ctx.resolve_path(cwd)
        if not candidate.is_dir():
            return ToolResult.error(f"cwd is not a directory: {candidate}", error="bad_request")
        if not is_within(candidate, ctx.settings.workspace) and ctx.settings.restrict_to_workspace:
            return ToolResult.error(
                f"cwd escapes the workspace: {cwd}", error="bad_request"
            )
        workdir = candidate

    effective_timeout = timeout or ctx.settings.command_timeout
    result = run_command(
        command,
        cwd=workdir,
        timeout=effective_timeout,
        shell=ctx.settings.shell or None,
    )

    header = f"$ {command}\nshell={result.shell} exit={result.exit_code} ({result.duration_s:.2f}s)"
    if result.timed_out:
        header += f" TIMED OUT after {effective_timeout}s"
    body = "\n".join(
        part
        for part in (
            result.stdout.rstrip(),
            f"[stderr]\n{result.stderr.rstrip()}" if result.stderr.strip() else "",
        )
        if part
    )
    text = f"{header}\n{body}".rstrip() or header
    if result.timed_out:
        return ToolResult(
            blocks=[{"type": "text", "text": text}],
            structured={**result.to_dict(), "error": "timeout"},
            is_error=True,
        )
    return ToolResult(
        blocks=[{"type": "text", "text": text}],
        structured=result.to_dict(),
        is_error=not result.ok,
    )


@tool(group="Files and code", tags=("write", "file", "create", "edit"))
def create_file(
    ctx: ToolContext,
    path: str,
    content: str,
    overwrite: bool = False,
) -> ToolResult:
    """Create a UTF-8 text file atomically, with syntax validation for .py/.json.

    Parent directories are created. Both LF and CRLF input are accepted; the
    file keeps its original line-ending style when it already exists.

    Args:
        path: File path, relative to the workspace or absolute.
        content: Complete file contents.
        overwrite: Replace an existing file instead of failing.
    """
    outcome = _editor(ctx).write_file(path, content, overwrite=overwrite)
    return ToolResult.text(
        f"wrote {outcome.bytes_written} bytes to {outcome.path}",
        **outcome.to_dict(),
    )


@tool(group="Files and code", tags=("edit", "replace", "patch", "file"))
def str_replace(
    ctx: ToolContext,
    path: str,
    replacements: list[dict],
    snapshot: bool = True,
) -> ToolResult:
    """Apply exact-match replacements to a text file.

    CRLF files are matched correctly: text is compared after newline
    normalisation and the original line endings are restored on write. A
    replacement that matches more than once is rejected unless it sets
    allowMultiple.

    Args:
        path: File to edit, relative to the workspace or absolute.
        replacements: List of objects with 'old', 'new' and optional 'allowMultiple'.
        snapshot: Take a checkpoint of the file before editing.
    """
    outcome = _editor(ctx).str_replace(path, replacements, snapshot=snapshot)
    return ToolResult.text(
        f"applied {outcome.replacements} replacement(s) to {outcome.path}",
        **outcome.to_dict(),
    )


@tool(group="Files and code", tags=("read", "file", "cat", "inspect", "ls"))
def view(
    ctx: ToolContext,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> ToolResult:
    """Read a file or list a directory.

    Text files come back with line numbers. Images come back as image content.
    Binary files report their type and size instead of dumping bytes.

    Args:
        path: File or directory path, relative to the workspace or absolute.
        offset: First line to show (1-indexed). Defaults to the start of the file.
        limit: Maximum number of lines to show. Defaults to 2000.
    """
    target = ctx.resolve_path(path)
    if target.is_dir():
        entries = []
        for entry in sorted(target.iterdir())[:500]:
            kind = "dir" if entry.is_dir() else "file"
            size = "" if entry.is_dir() else f" ({entry.stat().st_size} bytes)"
            entries.append(f"{kind:4} {entry.name}{size}")
        return ToolResult.text(
            f"{target}:\n" + ("\n".join(entries) if entries else "(empty)"),
            path=str(target),
            kind="directory",
            entries=len(entries),
        )

    if not target.exists():
        return ToolResult.error(f"no such file: {target}", error="not_found", path=str(target))

    if target.suffix.lower() in IMAGE_SUFFIXES:
        return ToolResult.image(target)

    raw = target.read_bytes()
    if b"\x00" in raw[:8192]:
        return ToolResult.text(
            f"{target} is binary ({len(raw)} bytes); not displayed.",
            path=str(target),
            kind="binary",
            bytes=len(raw),
        )

    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    lines = text.split("\n")
    start = max((offset or 1) - 1, 0)
    if start >= len(lines) and lines:
        return ToolResult.error(
            f"offset {offset} is past the end of the file ({len(lines)} lines)",
            error="bad_request",
        )
    window = lines[start : start + (limit or 2000)]
    numbered = "\n".join(f"{start + i + 1:6}\t{line}" for i, line in enumerate(window))
    remaining = len(lines) - (start + len(window))
    suffix = f"\n... {remaining} more line(s); pass offset={start + len(window) + 1}" if remaining > 0 else ""
    return ToolResult.text(
        numbered + suffix,
        path=str(target),
        totalLines=len(lines),
        shownLines=len(window),
        offset=start + 1,
    )


@tool(group="Files and code", tags=("show", "present", "open", "artifact"))
def present_files(ctx: ToolContext, paths: list[str], title: str | None = None) -> ToolResult:
    """Mark files as presented to the user, so the client can open them.

    Args:
        paths: Files to present, relative to the workspace or absolute.
        title: Optional heading for the presentation.
    """
    resolved = []
    missing = []
    for raw in paths:
        target = ctx.resolve_path(raw)
        if target.exists():
            resolved.append({"path": str(target), "bytes": target.stat().st_size})
        else:
            missing.append(raw)

    ctx.emit_event({"type": "present_files", "title": title, "files": resolved})
    ctx.state.append_jsonl(
        "presented.jsonl", {"timestamp": now_iso(), "title": title, "files": resolved}
    )
    if missing and not resolved:
        return ToolResult.error(
            f"none of the files exist: {', '.join(missing)}", error="not_found", missing=missing
        )
    text = "\n".join(f"- {item['path']} ({item['bytes']} bytes)" for item in resolved)
    if missing:
        text += f"\nMissing: {', '.join(missing)}"
    return ToolResult.text(text, files=resolved, missing=missing)


@tool(
    name="Artifact",
    group="Files and code",
    tags=("artifact", "output", "html", "report", "deliverable"),
)
def Artifact(
    ctx: ToolContext,
    content: str,
    mime_type: Literal[
        "text/markdown", "text/html", "text/plain", "application/json", "image/svg+xml"
    ] = "text/markdown",
    title: str = "artifact",
) -> ToolResult:
    """Create a durable artifact file and return its path.

    Use for reports, snippets, HTML pages or SVG images that the user should be
    able to open later.

    Args:
        content: Artifact body. Text content is written as UTF-8.
        mime_type: Content type, which selects the file extension.
        title: Human-readable name used for the file name.
    """
    extension = {
        "text/markdown": "md",
        "text/html": "html",
        "text/plain": "txt",
        "application/json": "json",
        "image/svg+xml": "svg",
    }[mime_type]
    info = ctx.save_artifact(content, name=title, ext=extension, mime=mime_type)
    link = f"[{title}]({Path(info['path']).name})"
    return ToolResult.text(f"created {link} ({info['bytes']} bytes)", **info, title=title)
