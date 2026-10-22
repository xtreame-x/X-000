"""A self-modifying agentic runtime with a 313-tool registry.

Public surface::

    from agent import Settings, ToolContext, registry, Dispatcher

The tool catalog is split into two checkpoints: the first 75 tools (everything
except Vercel) and the final 238 Vercel tools.
"""
from __future__ import annotations

__version__ = "0.1.0"

from agent.config import NotConfigured, Settings  # noqa: E402
from agent.state import StateStore  # noqa: E402
from agent.tools.base import REGISTRY, Registry, Tool, ToolContext, ToolResult, tool  # noqa: E402

registry: Registry = REGISTRY

__all__ = [
    "NotConfigured",
    "REGISTRY",
    "Registry",
    "Settings",
    "StateStore",
    "Tool",
    "ToolContext",
    "ToolResult",
    "registry",
    "tool",
    "__version__",
]
