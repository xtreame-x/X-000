from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Settings  # noqa: E402
from agent.runtime import Dispatcher, build_context  # noqa: E402
from agent.tools import load_all_tools  # noqa: E402


@pytest.fixture(autouse=True)
def _load_tools():
    load_all_tools()
    yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Settings(
        workspace=workspace,
        state_dir=workspace / "state",
        artifacts_dir=workspace / "artifacts",
        shell="bash",
        command_timeout=20,
        windows_path_rules=False,
        max_steps=5,
    )


@pytest.fixture
def ctx(settings: Settings):
    return build_context(settings)


@pytest.fixture
def dispatcher(ctx) -> Dispatcher:
    return Dispatcher(ctx)
