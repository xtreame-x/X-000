"""Durable state for the agent: JSON documents, JSONL event logs, audit trail.

All writes go through :func:`agent.platform_compat.atomic_write`, which retries
the Windows rename race, and paths are confined to the state directory.

Concurrency model: per-file ``threading.RLock`` (single-process agent).  Cross
process safety comes from the atomic rename, not from advisory locks, because
``fcntl`` does not exist on Windows.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

from agent.platform_compat import atomic_write, normalize_newlines, safe_join


class StateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # --- paths ---------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        rel = "/".join(str(p).strip("/") for p in parts if p)
        target = safe_join(self.root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _lock(self, key: str) -> threading.RLock:
        with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    # --- JSON documents ------------------------------------------------------
    def read_json(self, name: str, default: Any = None) -> Any:
        target = self.path(name)
        if not target.exists():
            return default
        with self._lock(name):
            text = normalize_newlines(target.read_text(encoding="utf-8-sig"))
        if not text.strip():
            return default
        return json.loads(text)

    def write_json(self, name: str, data: Any) -> Path:
        target = self.path(name)
        with self._lock(name):
            atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return target

    # --- JSONL logs ----------------------------------------------------------
    def append_jsonl(self, name: str, record: dict) -> Path:
        target = self.path(name)
        with self._lock(name):
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            if target.exists():
                previous = normalize_newlines(target.read_text(encoding="utf-8-sig"))
                atomic_write(target, previous + line)
            else:
                atomic_write(target, line)
        return target

    def read_jsonl(self, name: str, *, limit: int | None = None) -> list[dict]:
        target = self.path(name)
        if not target.exists():
            return []
        with self._lock(name):
            lines = normalize_newlines(target.read_text(encoding="utf-8-sig")).splitlines()
        records: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records[-limit:] if limit else records

    # --- text documents ------------------------------------------------------
    def read_text(self, name: str, default: str = "") -> str:
        target = self.path(name)
        if not target.exists():
            return default
        with self._lock(name):
            return normalize_newlines(target.read_text(encoding="utf-8-sig"))

    def write_text(self, name: str, text: str) -> Path:
        target = self.path(name)
        with self._lock(name):
            atomic_write(target, text)
        return target

    def delete(self, name: str) -> bool:
        target = self.path(name)
        with self._lock(name):
            if target.exists():
                target.unlink()
                return True
        return False

    def list(self, folder: str, *, suffix: str | None = None) -> list[str]:
        base = self.path(folder)
        if not base.exists():
            return []
        names = []
        for entry in sorted(base.iterdir()):
            if not entry.is_file():
                continue
            if suffix and entry.suffix != suffix:
                continue
            names.append(entry.name)
        return names

    # --- collections ---------------------------------------------------------
    def collection(self, name: str) -> list[dict]:
        data = self.read_json(name, default=[])
        return data if isinstance(data, list) else []

    def save_collection(self, name: str, items: Iterable[dict]) -> Path:
        return self.write_json(name, list(items))


def now_iso() -> str:
    """UTC timestamp with timezone offset, sortable and unambiguous."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
