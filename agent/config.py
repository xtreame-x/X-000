"""Configuration for the agent runtime.

All settings come from environment variables (prefix ``AGENT_``) so nothing
secret is ever written into the repository.  Secrets themselves are read from
env on demand and never persisted or logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from agent.platform_compat import IS_WINDOWS, default_shell


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    workspace: Path
    state_dir: Path
    artifacts_dir: Path
    #: Shell used by ``bash_tool``.  On Windows this is pwsh/powershell/cmd.
    shell: str = field(default_factory=lambda: _env("AGENT_SHELL") or default_shell())
    command_timeout: int = 120
    #: Refuse path-traversal outside the workspace unless explicitly allowed.
    restrict_to_workspace: bool = True
    allow_destructive_commands: bool = False
    #: Windows-specific path validation; forced on Windows, overridable for tests.
    windows_path_rules: bool = field(
        default_factory=lambda: _env_bool("AGENT_WINDOWS_PATH_RULES", IS_WINDOWS)
    )
    model: str = field(
        default_factory=lambda: _env("AGENT_MODEL") or "claude-sonnet-4-5-20250929"
    )
    max_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_STEPS", 40))
    request_timeout: int = field(default_factory=lambda: _env_int("AGENT_HTTP_TIMEOUT", 30))

    @classmethod
    def load(cls, workspace: str | Path | None = None) -> "Settings":
        root = Path(workspace or _env("AGENT_WORKSPACE") or os.getcwd()).expanduser().resolve()
        state = Path(_env("AGENT_STATE_DIR") or (root / "state")).resolve()
        artifacts = Path(_env("AGENT_ARTIFACTS_DIR") or (root / "artifacts")).resolve()
        return cls(
            workspace=root,
            state_dir=state,
            artifacts_dir=artifacts,
            command_timeout=_env_int("AGENT_COMMAND_TIMEOUT", 120),
        )

    # --- secret access (never persisted) -------------------------------------
    @staticmethod
    def secret(name: str) -> str | None:
        return _env(name)

    def require_secret(self, name: str, provider: str) -> str:
        value = self.secret(name)
        if not value:
            raise NotConfigured(
                f"{provider} is not configured: set the {name} environment variable.",
                provider=provider,
                env_var=name,
            )
        return value

    # --- derived paths -------------------------------------------------------
    @property
    def snapshots_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @property
    def memory_dir(self) -> Path:
        return self.state_dir / "memory"

    @property
    def audit_log(self) -> Path:
        return self.state_dir / "audit.jsonl"


class NotConfigured(RuntimeError):
    """Raised when a tool needs a credential or service the user has not set up."""

    def __init__(self, message: str, *, provider: str, env_var: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.env_var = env_var

    def to_dict(self) -> dict[str, str]:
        return {
            "error": "not_configured",
            "message": str(self),
            "provider": self.provider,
            "envVar": self.env_var,
        }
