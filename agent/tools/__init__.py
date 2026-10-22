"""Tool package loader.

Importing a module registers its tools via the ``@tool`` decorator. The
checkpoint constant below is asserted against the live registry so a module that
fails to import, or a renamed tool, is caught immediately instead of quietly
shrinking the advertised tool set.
"""
from __future__ import annotations

import importlib

#: Checkpoint 1: everything except the 238 Vercel tools.
CHECKPOINT_1_MODULES = (
    "files",
    "web",
    "memory",
    "personal",
    "places",
    "ui",
    "connectors",
    "docs",
    "other",
    "drive",
    "notion",
    "threejs",
    "sketchup",
)
CHECKPOINT_1_COUNT = 75

#: Checkpoint 2 (the 238 Vercel tools) is registered by these modules.
CHECKPOINT_2_MODULES: tuple[str, ...] = ("vercel",)
CHECKPOINT_2_COUNT = 238

_loaded = False


def load_all_tools(*, include_vercel: bool = True) -> None:
    """Import every tool module once. Safe to call repeatedly."""
    global _loaded
    if _loaded:
        return
    for module in CHECKPOINT_1_MODULES:
        importlib.import_module(f"agent.tools.{module}")
    if include_vercel:
        for module in CHECKPOINT_2_MODULES:
            try:
                importlib.import_module(f"agent.tools.{module}")
            except ModuleNotFoundError:
                # Checkpoint 2 not built yet: the 75 checkpoint-1 tools still load.
                break
    _loaded = True


def checkpoint_counts() -> dict[str, int]:
    """Report how many tools each checkpoint contributes."""
    from agent.tools.base import REGISTRY

    REGISTRY.load_all()
    names = set(REGISTRY.names())
    vercel = sum(1 for name in names if name.startswith("mcp__Vercel__"))
    return {"checkpoint1": len(names) - vercel, "checkpoint2": vercel, "total": len(names)}


def verify_checkpoints(*, strict: bool = False) -> dict[str, bool]:
    """Compare the registry against the documented checkpoint sizes.

    Each checkpoint is reported separately: checkpoint 1 is complete while
    checkpoint 2 (the 238 Vercel tools) is still being built, so ``total`` is
    legitimately false until both are present.
    """
    counts = checkpoint_counts()
    report = {
        "checkpoint1": counts["checkpoint1"] == CHECKPOINT_1_COUNT,
        "checkpoint2": counts["checkpoint2"] == CHECKPOINT_2_COUNT,
        "total": counts["total"] == CHECKPOINT_1_COUNT + CHECKPOINT_2_COUNT,
    }
    if strict and not all(report.values()):
        raise AssertionError(f"checkpoint mismatch: {counts} vs report {report}")
    return report


__all__ = [
    "CHECKPOINT_1_COUNT",
    "CHECKPOINT_1_MODULES",
    "CHECKPOINT_2_COUNT",
    "CHECKPOINT_2_MODULES",
    "checkpoint_counts",
    "load_all_tools",
    "verify_checkpoints",
]
