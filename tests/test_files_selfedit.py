"""File tools and the self-edit engine, including Windows-relevant behaviour."""
from __future__ import annotations

from pathlib import Path

from agent.self_edit import SelfEditor


def test_create_file_then_view_numbers_lines(dispatcher, settings):
    created = dispatcher.call("create_file", {"path": "hello.txt", "content": "alpha\nbeta\n"})
    assert not created.is_error
    assert (settings.workspace / "hello.txt").read_text() == "alpha\nbeta\n"

    viewed = dispatcher.call("view", {"path": "hello.txt"})
    assert not viewed.is_error
    assert "1\talpha" in viewed.text_content()
    assert viewed.structured["totalLines"] == 3


def test_create_file_refuses_to_clobber_without_overwrite(dispatcher):
    dispatcher.call("create_file", {"path": "a.txt", "content": "one"})
    second = dispatcher.call("create_file", {"path": "a.txt", "content": "two"})
    assert second.is_error
    assert second.structured["error"] == "bad_request"


def test_create_file_syntax_gate_rejects_broken_python(dispatcher, settings):
    result = dispatcher.call("create_file", {"path": "broken.py", "content": "def f(:\n"})
    assert result.is_error
    assert "syntax error" in result.text_content().lower()
    assert not (settings.workspace / "broken.py").exists()


def test_create_file_syntax_gate_rejects_broken_json(dispatcher, settings):
    result = dispatcher.call("create_file", {"path": "bad.json", "content": "{oops}"})
    assert result.is_error
    assert not (settings.workspace / "bad.json").exists()


def test_str_replace_preserves_crlf(dispatcher, settings):
    target = settings.workspace / "crlf.txt"
    target.write_bytes(b"first line\r\nsecond line\r\nthird line\r\n")

    result = dispatcher.call(
        "str_replace",
        {"path": "crlf.txt", "replacements": [{"old": "second line", "new": "SECOND"}]},
    )
    assert not result.is_error
    assert target.read_bytes() == b"first line\r\nSECOND\r\nthird line\r\n"
    assert result.structured["newline"] == "CRLF"


def test_str_replace_rejects_ambiguous_match(dispatcher, settings):
    (settings.workspace / "dup.txt").write_text("x\nx\n")
    result = dispatcher.call(
        "str_replace", {"path": "dup.txt", "replacements": [{"old": "x", "new": "y"}]}
    )
    assert result.is_error
    assert "matched 2 times" in result.text_content()


def test_str_replace_allows_multiple_when_asked(dispatcher, settings):
    (settings.workspace / "dup.txt").write_text("x\nx\n")
    result = dispatcher.call(
        "str_replace",
        {"path": "dup.txt", "replacements": [{"old": "x", "new": "y", "allow_multiple": True}]},
    )
    assert not result.is_error
    assert (settings.workspace / "dup.txt").read_text() == "y\ny\n"


def test_str_replace_reports_missing_text_without_writing(dispatcher, settings):
    target = settings.workspace / "keep.txt"
    target.write_text("untouched\n")
    result = dispatcher.call(
        "str_replace", {"path": "keep.txt", "replacements": [{"old": "absent", "new": "x"}]}
    )
    assert result.is_error
    assert target.read_text() == "untouched\n"


def test_str_replace_rejects_invalid_result(dispatcher, settings):
    target = settings.workspace / "mod.py"
    target.write_text("x = 1\n")
    result = dispatcher.call(
        "str_replace", {"path": "mod.py", "replacements": [{"old": "x = 1", "new": "x = ("}]}
    )
    assert result.is_error
    assert target.read_text() == "x = 1\n"


def test_view_directory_lists_entries(dispatcher, settings):
    (settings.workspace / "one.txt").write_text("1")
    (settings.workspace / "sub").mkdir()
    result = dispatcher.call("view", {"path": "."})
    assert not result.is_error
    assert "one.txt" in result.text_content()
    assert "sub" in result.text_content()


def test_path_escape_is_blocked(dispatcher):
    result = dispatcher.call("view", {"path": "../../etc/passwd"})
    assert result.is_error
    assert result.structured["error"] == "bad_request"


def test_secrets_are_not_writable(dispatcher, settings):
    result = dispatcher.call("create_file", {"path": ".env", "content": "TOKEN=1"})
    assert result.is_error
    assert not (settings.workspace / ".env").exists()


def test_state_directory_is_protected(dispatcher):
    result = dispatcher.call("create_file", {"path": "state/evil.json", "content": "{}"})
    assert result.is_error


def test_artifact_writes_into_artifacts_dir(dispatcher, settings):
    result = dispatcher.call(
        "Artifact", {"content": "# Report\n", "title": "Weekly Report", "mime_type": "text/markdown"}
    )
    assert not result.is_error
    path = Path(result.structured["path"])
    assert path.parent == settings.artifacts_dir
    assert path.read_text() == "# Report\n"


def test_present_files_records_and_reports_missing(dispatcher, settings):
    (settings.workspace / "a.txt").write_text("a")
    result = dispatcher.call("present_files", {"paths": ["a.txt", "missing.txt"]})
    assert not result.is_error
    assert result.structured["missing"] == ["missing.txt"]
    assert len(result.structured["files"]) == 1


def test_bash_tool_runs_and_reports_exit(dispatcher):
    ok = dispatcher.call("bash_tool", {"command": "echo hi"})
    assert not ok.is_error
    assert "hi" in ok.text_content()
    assert ok.structured["exitCode"] == 0

    failed = dispatcher.call("bash_tool", {"command": "exit 7"})
    assert failed.is_error
    assert failed.structured["exitCode"] == 7


def test_bash_tool_refuses_destructive_command(dispatcher):
    result = dispatcher.call("bash_tool", {"command": "rm -rf /"})
    assert result.is_error
    assert result.structured["error"] == "destructive_command_refused"


def test_bash_tool_respects_cwd(dispatcher, settings):
    (settings.workspace / "nested").mkdir()
    (settings.workspace / "nested" / "marker.txt").write_text("x")
    result = dispatcher.call("bash_tool", {"command": "ls", "cwd": "nested"})
    assert "marker.txt" in result.text_content()


def test_snapshot_and_restore_round_trip(settings, ctx):
    target = settings.workspace / "code.py"
    target.write_text("value = 1\n")
    editor = SelfEditor(settings.workspace, ctx.state)
    snapshot = editor.snapshot("before", paths=["code.py"])

    editor.write_file("code.py", "value = 2\n")
    assert target.read_text() == "value = 2\n"

    restored = editor.restore(snapshot)
    assert "code.py" in restored
    assert target.read_text() == "value = 1\n"


def test_self_edit_records_history(settings, ctx):
    editor = SelfEditor(settings.workspace, ctx.state)
    editor.write_file("notes.md", "hello\n")
    log = (ctx.state.root / "self_edits.jsonl").read_text().strip().splitlines()
    assert any('"action": "write"' in line for line in log)
