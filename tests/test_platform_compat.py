"""Windows-specific behaviour, verified on Linux by forcing the platform flag."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.platform_compat import (
    InvalidPath,
    atomic_write,
    detect_newline,
    is_within,
    normalize_newlines,
    read_text_normalized,
    run_command,
    select_shell,
    slugify,
    to_newline,
    validate_windows_path,
)


@pytest.mark.parametrize(
    "bad",
    [
        "CON",
        "con.txt",
        "dir/NUL",
        "LPT1.log",
        "a<b.txt",
        "what?.md",
        'quote".txt',
        "trailing.",
        "trailing ",
        "pipe|name",
    ],
)
def test_windows_rejects_invalid_components(bad: str):
    with pytest.raises(InvalidPath):
        validate_windows_path(bad)


@pytest.mark.parametrize(
    "good",
    ["agent/tools/files.py", "C:/Users/dev/project/README.md", "notes-2026.md", "./a/b/c.txt"],
)
def test_windows_accepts_valid_paths(good: str):
    validate_windows_path(good)


def test_windows_max_path_limit():
    with pytest.raises(InvalidPath, match="MAX_PATH"):
        validate_windows_path("a/" + "b" * 300)


def test_reserved_name_check_is_case_insensitive():
    with pytest.raises(InvalidPath):
        validate_windows_path("CoM3.txt")


def test_newline_detection_and_round_trip():
    crlf = "line1\r\nline2\r\nline3\r\n"
    assert detect_newline(crlf) == "\r\n"
    assert detect_newline("a\nb\n") == "\n"
    assert normalize_newlines(crlf) == "line1\nline2\nline3\n"
    assert to_newline("a\nb\n", "\r\n") == "a\r\nb\r\n"


def test_read_text_normalized_preserves_style(tmp_path: Path):
    target = tmp_path / "crlf.py"
    target.write_bytes(b"x = 1\r\ny = 2\r\n")
    text, newline = read_text_normalized(target)
    assert text == "x = 1\ny = 2\n"
    assert newline == "\r\n"


def test_atomic_write_restores_crlf(tmp_path: Path):
    target = tmp_path / "file.txt"
    atomic_write(target, "a\nb\n", newline="\r\n")
    assert target.read_bytes() == b"a\r\nb\r\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_survives_transient_permission_error(tmp_path: Path, monkeypatch):
    target = tmp_path / "locked.txt"
    atomic_write(target, "first")
    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(src, dst):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    atomic_write(target, "second", backoff=0.001)
    assert target.read_text() == "second"
    assert attempts["count"] == 3


def test_shell_selection_prefers_pwsh_on_windows():
    spec = select_shell("windows", which=lambda name: f"/fake/{name}" if name == "pwsh" else None)
    assert spec.name == "pwsh"
    argv = spec.argv("Get-ChildItem")
    assert "utf8" in " ".join(argv).lower().replace("utf-8", "utf8")
    assert argv[-1].endswith("Get-ChildItem")


def test_shell_selection_falls_back_to_cmd():
    spec = select_shell("windows", which=lambda name: None)
    assert spec.name == "cmd"
    argv = spec.argv("dir")
    assert "chcp 65001" in argv[-1]
    assert argv[-1].endswith("dir")


def test_shell_selection_on_posix():
    spec = select_shell("posix", which=lambda name: f"/bin/{name}")
    assert spec.name == "bash"
    assert spec.argv("echo hi") == ["/bin/bash", "-lc", "echo hi"]


def test_run_command_captures_exit_and_output():
    result = run_command("echo hello", timeout=10)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


def test_run_command_reports_failure_exit_code():
    result = run_command("exit 3", timeout=10)
    assert result.exit_code == 3
    assert result.ok is False


def test_run_command_timeout_kills_process():
    result = run_command("sleep 10", timeout=1, platform="posix")
    assert result.timed_out is True
    assert result.exit_code != 0


def test_is_within_rejects_sibling_prefix(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "sub").mkdir(parents=True)
    assert is_within(workspace / "sub" / "a.txt", workspace)
    assert not is_within(tmp_path / "ws-evil" / "a.txt", workspace)


def test_slugify_avoids_reserved_names():
    assert slugify("CON") == "_con"
    assert slugify("Hello World!!") == "hello-world"
    assert slugify("") == "untitled"
