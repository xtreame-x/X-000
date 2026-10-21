"""CLI surface: exit codes, bare-prompt rewriting, direct tool calls."""
from __future__ import annotations

from pathlib import Path

from agent.cli import main


def test_help_exits_zero(capsys):
    assert main([]) == 0
    assert "usage: openagent" in capsys.readouterr().out


def test_tools_listing(capsys):
    assert main(["tools", "--group", "Files and code"]) == 0
    out = capsys.readouterr().out
    assert "bash_tool" in out
    assert "str_replace" in out


def test_call_tool_succeeds(tmp_path: Path, capsys):
    code = main(["--workspace", str(tmp_path), "call", "user_time_v0", "--args", "{}"])
    assert code == 0
    assert "UTC" in capsys.readouterr().out


def test_call_tool_failure_returns_one(tmp_path: Path, capsys):
    code = main(
        ["--workspace", str(tmp_path), "call", "view", "--args", '{"path": "nope.txt"}']
    )
    assert code == 1
    assert "no such file" in capsys.readouterr().out


def test_invalid_json_arguments_returns_two(tmp_path: Path, capsys):
    code = main(["--workspace", str(tmp_path), "call", "view", "--args", "{oops"])
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_global_flag_before_subcommand_is_not_rewritten(tmp_path: Path, capsys):
    code = main(["--workspace", str(tmp_path), "tools"])
    assert code == 0
    assert "Tools" not in capsys.readouterr().err


def test_bare_prompt_is_rewritten_to_run(monkeypatch, tmp_path: Path, capsys):
    # No credentials -> the run command reports a clean configuration error.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    code = main(["--workspace", str(tmp_path), "explain this repo"])
    assert code == 2
    assert "no LLM credentials" in capsys.readouterr().err


def test_scripted_run_completes(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    code = main(["--workspace", str(tmp_path), "run", "hello"])
    assert code == 0
    assert "scripted provider exhausted" in capsys.readouterr().out


def test_strict_verify_fails_until_checkpoint2(monkeypatch, capsys):
    code = main(["--workspace", str(Path.cwd()), "verify", "--strict"])
    assert code == 1
    assert '"checkpoint1": true' in capsys.readouterr().out
