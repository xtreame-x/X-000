"""Agent loop behaviour with a scripted provider (no network, no API key)."""
from __future__ import annotations

from pathlib import Path

from agent.llm import LLMResponse, ScriptedProvider
from agent.loop import Agent


def _tool_use(call_id: str, name: str, arguments: dict) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


def test_loop_executes_tool_then_answers(ctx, settings):
    provider = ScriptedProvider(
        [
            LLMResponse(blocks=[_tool_use("1", "create_file",
                                          {"path": "made.txt", "content": "hello"})]),
            LLMResponse(blocks=[{"type": "text", "text": "done"}], stop_reason="end_turn"),
        ]
    )
    run = Agent(ctx, provider).run("create a file")
    assert run.text == "done"
    assert (settings.workspace / "made.txt").read_text() == "hello"
    assert [record.name for record in run.tool_calls] == ["create_file"]
    assert run.stop_reason == "end_turn"


def test_loop_advertises_only_non_deferred_tools_first(ctx):
    provider = ScriptedProvider([LLMResponse(blocks=[{"type": "text", "text": "ok"}])])
    Agent(ctx, provider).run("hi")
    advertised = provider.calls[0]["tools"]
    assert "bash_tool" in advertised
    assert "mcp__Google_Drive__list_recent_files" not in advertised


def test_loop_advertises_unlocked_tools_on_the_next_turn(ctx):
    provider = ScriptedProvider(
        [
            LLMResponse(blocks=[_tool_use("1", "tool_search", {"query": "drive"})]),
            LLMResponse(blocks=[{"type": "text", "text": "found"}], stop_reason="end_turn"),
        ]
    )
    Agent(ctx, provider).run("what drive tools exist?")
    first, second = provider.calls[0]["tools"], provider.calls[1]["tools"]
    assert "mcp__Google_Drive__list_recent_files" not in first
    assert "mcp__Google_Drive__list_recent_files" in second


def test_loop_feeds_tool_errors_back_instead_of_crashing(ctx):
    provider = ScriptedProvider(
        [
            LLMResponse(blocks=[_tool_use("1", "view", {"path": "nope.txt"})]),
            LLMResponse(blocks=[{"type": "text", "text": "file is missing"}], stop_reason="end_turn"),
        ]
    )
    run = Agent(ctx, provider).run("read nope.txt")
    assert run.text == "file is missing"
    assert run.tool_calls[0].is_error is True
    # The failed result must be visible to the model on the next turn.
    tool_result = provider.calls[1]["messages"][-1]["blocks"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is True


def test_loop_stops_on_end_conversation(ctx):
    provider = ScriptedProvider(
        [LLMResponse(blocks=[_tool_use("1", "end_conversation",
                                        {"reason": "done", "summary": "finished"})])]
    )
    run = Agent(ctx, provider).run("finish")
    assert run.stop_reason == "done"
    assert run.text == "finished"
    assert ctx.stop_requested is True


def test_loop_stops_at_max_steps(ctx):
    loop_response = LLMResponse(blocks=[_tool_use("1", "user_time_v0", {})])
    provider = ScriptedProvider([loop_response, loop_response, loop_response])
    run = Agent(ctx, provider).run("loop forever", max_steps=2)
    assert run.stop_reason == "max_steps"
    assert run.steps == 2


def test_loop_reports_llm_errors_without_raising(ctx):
    class Broken:
        name = "broken"
        model = "broken"

        def complete(self, **kwargs):
            from agent.llm import LLMError

            raise LLMError("provider down", status=500, body="upstream failure")

    run = Agent(ctx, Broken()).run("hi")
    assert run.stop_reason == "llm_error"
    assert "provider down" in run.text
    assert run.error


def test_self_edit_via_loop_persists_and_is_snapshotted(ctx, settings):
    target = settings.workspace / "app.py"
    target.write_text("def add(a, b):\n    return a - b\n")
    provider = ScriptedProvider(
        [
            LLMResponse(blocks=[_tool_use("1", "str_replace", {
                "path": "app.py",
                "replacements": [{"old": "return a - b", "new": "return a + b"}],
            })]),
            LLMResponse(blocks=[{"type": "text", "text": "fixed"}], stop_reason="end_turn"),
        ]
    )
    run = Agent(ctx, provider).run("fix the bug")
    assert run.text == "fixed"
    assert "a + b" in target.read_text()
    snapshots = list((settings.state_dir / "snapshots").iterdir())
    assert snapshots, "expected a pre-edit snapshot"
    assert Path(snapshots[0]).is_dir()


def test_system_prompt_mentions_platform_shell(settings, ctx):
    provider = ScriptedProvider([LLMResponse(blocks=[{"type": "text", "text": "ok"}])])
    Agent(ctx, provider).run("hi")
    system = provider.calls[0]["system"]
    assert str(settings.workspace) in system
    assert "bash" in system.lower()
