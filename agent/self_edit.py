"""Self-modification: the agent editing its own source, safely.

Rules that make self-editing survivable on Windows:

1. **Snapshot first.** Every write checkpoints the touched file (and on demand the
   whole package) under ``state/snapshots`` so a bad edit is one call away from
   being reverted.
2. **Syntax gate.** ``.py`` edits are compiled before they land; invalid code
   never reaches disk, so the agent cannot break its own import.
3. **Preserve line endings.** Reads are LF-normalised, writes restore the file's
   original CRLF/LF style, and replacements are matched against normalised text.
4. **Atomic writes with retry.** Windows raises ``PermissionError`` when an
   editor or virus scanner holds the file open; :func:`atomic_write` retries.
5. **No secrets.** ``.env*`` and the state directory are never writable through
   self-edit, so the agent cannot exfiltrate or corrupt its own credentials.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from agent.platform_compat import (
    InvalidPath,
    atomic_write,
    is_within,
    normalize_newlines,
    read_text_normalized,
    resolve_workspace_path,
    slugify,
    validate_path,
)
from agent.state import StateStore, now_iso

PROTECTED_DIRS = {"state", ".git", ".hg", ".svn", "__pycache__"}
PROTECTED_FILE_PATTERNS = (".env", ".env.local", ".env.production", "secrets.json")


class SelfEditError(RuntimeError):
    pass


@dataclass
class EditOutcome:
    path: str
    bytes_written: int
    replacements: int
    newline: str
    syntax_checked: bool
    snapshot: str | None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "bytesWritten": self.bytes_written,
            "replacements": self.replacements,
            "newline": "CRLF" if self.newline == "\r\n" else "LF",
            "syntaxChecked": self.syntax_checked,
            "snapshot": self.snapshot,
        }


class SelfEditor:
    def __init__(self, workspace: Path, state: StateStore, *, restrict_to_workspace: bool = True) -> None:
        self.workspace = Path(workspace)
        self.state = state
        self.restrict_to_workspace = restrict_to_workspace
        self.snapshots_dir = state.root / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.history = state.root / "self_edits.jsonl"

    # --- path policy ---------------------------------------------------------
    def resolve(self, path: str) -> Path:
        validate_path(path)
        target = resolve_workspace_path(self.workspace, path)
        if self.restrict_to_workspace and not is_within(target, self.workspace):
            raise InvalidPath(f"{path!r} is outside the workspace and self-edit is restricted")
        for part in target.relative_to(self.workspace).parts if is_within(target, self.workspace) else ():
            if part in PROTECTED_DIRS:
                raise SelfEditError(f"{path!r} is inside protected directory {part!r}")
        if target.name.startswith(PROTECTED_FILE_PATTERNS) or target.name in PROTECTED_FILE_PATTERNS:
            raise SelfEditError(f"{path!r} holds secrets and is not writable by the agent")
        return target

    # --- syntax gate ---------------------------------------------------------
    @staticmethod
    def check_syntax(path: Path, text: str) -> bool:
        """Validate a prospective file body. Raises SyntaxError without writing."""
        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                compile(text, str(path), "exec", dont_inherit=True)
            except SyntaxError as exc:
                raise SelfEditError(
                    f"refusing to write {path.name}: syntax error at line {exc.lineno}: {exc.msg}"
                ) from exc
            return True
        if suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise SelfEditError(f"refusing to write {path.name}: invalid JSON: {exc}") from exc
            return True
        return False

    # --- snapshots -----------------------------------------------------------
    def snapshot(self, label: str, *, paths: list[str] | None = None) -> str:
        """Copy files (or the whole workspace) into a timestamped snapshot."""
        stamp = now_iso().replace(":", "").replace("-", "").replace("+0000", "Z")
        snapshot_id = f"{stamp}-{slugify(label, max_length=40)}"
        target = self.snapshots_dir / snapshot_id
        target.mkdir(parents=True, exist_ok=True)

        manifest: list[str] = []
        if paths:
            for raw in paths:
                source = self.resolve(raw)
                if not source.exists():
                    continue
                destination = target / source.relative_to(self.workspace)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                manifest.append(str(source.relative_to(self.workspace)))
        else:
            for entry in self.workspace.iterdir():
                if entry.name in {"state", ".git", "__pycache__", "artifacts"}:
                    continue
                destination = target / entry.name
                if entry.is_dir():
                    shutil.copytree(
                        entry,
                        destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
                    )
                else:
                    shutil.copy2(entry, destination)
                manifest.append(entry.name)

        atomic_write(target / "manifest.json", json.dumps({"label": label, "files": manifest,
                                                           "createdAt": now_iso()}, indent=2) + "\n")
        self._record({"action": "snapshot", "snapshot": snapshot_id, "files": manifest})
        return snapshot_id

    def restore(self, snapshot_id: str) -> list[str]:
        source = self.snapshots_dir / snapshot_id
        if not source.is_dir():
            raise SelfEditError(f"no such snapshot: {snapshot_id}")
        restored: list[str] = []
        for entry in source.rglob("*"):
            if entry.is_dir() or entry.name == "manifest.json":
                continue
            relative = entry.relative_to(source)
            destination = self.workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, destination)
            restored.append(str(relative))
        self._record({"action": "restore", "snapshot": snapshot_id, "files": restored})
        return restored

    def list_snapshots(self) -> list[str]:
        return sorted(p.name for p in self.snapshots_dir.iterdir() if p.is_dir())

    # --- writes --------------------------------------------------------------
    def write_file(self, path: str, content: str, *, overwrite: bool = True,
                   snapshot: bool = True) -> EditOutcome:
        target = self.resolve(path)
        if target.exists() and not overwrite:
            raise SelfEditError(f"{path} already exists; pass overwrite=True to replace it")

        newline = "\n"
        if target.exists():
            try:
                _, newline = read_text_normalized(target)
            except ValueError:
                newline = "\n"
            if snapshot:
                self.snapshot(f"pre-write-{target.name}", paths=[path])

        checked = self.check_syntax(target, normalize_newlines(content))
        atomic_write(target, content, newline=None)
        outcome = EditOutcome(
            path=str(target),
            bytes_written=target.stat().st_size,
            replacements=0,
            newline=newline,
            syntax_checked=checked,
            snapshot=None,
        )
        self._record({"action": "write", **outcome.to_dict()})
        return outcome

    def str_replace(self, path: str, replacements: list[dict], *,
                    snapshot: bool = True) -> EditOutcome:
        """Exact-match replacement, robust to CRLF files and duplicate matches."""
        if not replacements:
            raise SelfEditError("no replacements supplied")
        target = self.resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"no such file: {target}")

        if snapshot:
            self.snapshot(f"pre-edit-{target.name}", paths=[path])

        text, newline = read_text_normalized(target)
        applied = 0
        for index, item in enumerate(replacements):
            old = normalize_newlines(str(item.get("old", "")))
            new = normalize_newlines(str(item.get("new", "")))
            allow_multiple = bool(item.get("allow_multiple", False))
            if not old:
                raise SelfEditError(f"replacement #{index + 1}: 'old' must not be empty")
            occurrences = text.count(old)
            if occurrences == 0:
                raise SelfEditError(
                    f"replacement #{index + 1}: text not found in {target.name}: {old[:120]!r}"
                )
            if occurrences > 1 and not allow_multiple:
                raise SelfEditError(
                    f"replacement #{index + 1}: matched {occurrences} times in {target.name}; "
                    "add more context or set allow_multiple"
                )
            text = text.replace(old, new) if allow_multiple else text.replace(old, new, 1)
            applied += 1

        checked = self.check_syntax(target, text)
        atomic_write(target, text, newline=newline)
        outcome = EditOutcome(
            path=str(target),
            bytes_written=target.stat().st_size,
            replacements=applied,
            newline=newline,
            syntax_checked=checked,
            snapshot=self.list_snapshots()[-1] if self.list_snapshots() else None,
        )
        self._record({"action": "str_replace", **outcome.to_dict()})
        return outcome

    # --- reload --------------------------------------------------------------
    def reload(self, module_names: list[str] | None = None) -> list[str]:
        """Reload changed modules so self-edits take effect without a restart."""
        if module_names is None:
            module_names = [
                name for name, module in list(sys.modules.items())
                if name == "agent" or name.startswith("agent.") and getattr(module, "__file__", None)
            ]
        reloaded: list[str] = []
        for name in module_names:
            module = sys.modules.get(name)
            if module is None or getattr(module, "__file__", None) is None:
                continue
            try:
                importlib.reload(module)
                reloaded.append(name)
            except Exception as exc:  # noqa: BLE001 - report, never crash the loop
                raise SelfEditError(f"reload of {name} failed: {exc}") from exc
        self._record({"action": "reload", "modules": reloaded})
        return reloaded

    def _record(self, record: dict) -> None:
        with self.history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": now_iso(), **record}, ensure_ascii=False) + "\n")
