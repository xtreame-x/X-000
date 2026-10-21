"""Behavioural tests for the checkpoint-1 tool families (no network required)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.tools.web import html_to_text


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
def test_memory_full_lifecycle(dispatcher):
    assert not dispatcher.call("memory_write", {"name": "Project", "content": "uses pytest\n"}).is_error
    assert dispatcher.call("memory_read", {"name": "project"}).text_content() == "uses pytest\n"

    dispatcher.call("memory_append", {"name": "project", "content": "python 3.12"})
    body = dispatcher.call("memory_read", {"name": "project"}).text_content()
    assert body == "uses pytest\npython 3.12"

    replaced = dispatcher.call(
        "memory_str_replace", {"name": "project", "old": "pytest", "new": "unittest"}
    )
    assert not replaced.is_error
    assert "unittest" in dispatcher.call("memory_read", {"name": "project"}).text_content()

    listing = dispatcher.call("memory_list", {})
    assert "project" in listing.text_content()

    assert not dispatcher.call("memory_delete", {"name": "project"}).is_error
    assert dispatcher.call("memory_read", {"name": "project"}).is_error


def test_memory_name_cannot_traverse(dispatcher, settings):
    dispatcher.call("memory_write", {"name": "../../evil", "content": "x"})
    files = list((settings.state_dir / "memory").glob("*.md"))
    assert [f.name for f in files] == ["evil.md"]


def test_memory_replace_requires_unambiguous_match(dispatcher):
    dispatcher.call("memory_write", {"name": "dupes", "content": "a a a"})
    result = dispatcher.call("memory_str_replace", {"name": "dupes", "old": "a", "new": "b"})
    assert result.is_error
    assert result.structured["error"] == "ambiguous_match"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def test_docs_create_query_update_delete(dispatcher, ctx):
    ctx.loaded_tools.update(
        {"mcp__Claude_Docs__create", "mcp__Claude_Docs__read",
         "mcp__Claude_Docs__query", "mcp__Claude_Docs__delete",
         "mcp__Claude_Docs__export"}
    )
    created = dispatcher.call(
        "mcp__Claude_Docs__create",
        {"title": "Deploy notes", "content": "Run the migrations first.", "tags": ["ops"]},
    )
    assert not created.is_error
    doc_id = created.structured["id"]

    read = dispatcher.call("mcp__Claude_Docs__read", {"doc_id": doc_id})
    assert "migrations" in read.text_content()

    found = dispatcher.call("mcp__Claude_Docs__query", {"query": "migrations"})
    assert doc_id in found.text_content()

    updated = dispatcher.call(
        "mcp__Claude_Docs__update", {"doc_id": doc_id, "content": "Run migrations, then smoke test."}
    )
    assert updated.structured["revision"] == 2

    exported = dispatcher.call("mcp__Claude_Docs__export", {"doc_id": doc_id, "format": "markdown"})
    assert Path(exported.structured["path"]).exists()

    assert not dispatcher.call("mcp__Claude_Docs__delete", {"doc_id": doc_id}).is_error
    assert dispatcher.call("mcp__Claude_Docs__read", {"doc_id": doc_id}).is_error


def test_docs_batch_reports_per_operation_results(dispatcher):
    result = dispatcher.call(
        "mcp__Claude_Docs__batch",
        {
            "operations": [
                {"op": "create", "title": "One", "content": "alpha"},
                {"op": "nonsense"},
                {"op": "query", "query": "alpha"},
            ]
        },
    )
    assert result.is_error  # one operation failed
    assert result.structured["total"] == 3
    assert result.structured["succeeded"] == 2


def test_docs_batch_caps_operation_count(dispatcher):
    result = dispatcher.call(
        "mcp__Claude_Docs__batch", {"operations": [{"op": "query", "query": "x"}] * 21}
    )
    assert result.is_error
    assert result.structured["error"] == "too_many_operations"


# ---------------------------------------------------------------------------
# Personal
# ---------------------------------------------------------------------------
def test_event_lifecycle(dispatcher):
    created = dispatcher.call(
        "event_create_v1",
        {
            "title": "Standup",
            "start": "2026-11-02T09:00:00Z",
            "end": "2026-11-02T09:15:00Z",
            "attendees": ["a@example.com"],
            "reminders_minutes": [5, 1],
            "timezone_name": "UTC",
        },
    )
    assert not created.is_error
    event_id = created.structured["id"]
    assert created.structured["remindersMinutes"] == [1, 5]

    found = dispatcher.call("event_search_v0", {"query": "standup", "timezone_name": "UTC"})
    assert event_id in found.text_content()

    updated = dispatcher.call(
        "event_update_v0", {"event_id": event_id, "title": "Daily standup", "timezone_name": "UTC"}
    )
    assert updated.structured["title"] == "Daily standup"

    deleted = dispatcher.call("event_delete_v0", {"event_id": event_id})
    assert not deleted.is_error
    assert dispatcher.call("event_delete_v0", {"event_id": event_id}).is_error


def test_event_rejects_end_before_start(dispatcher):
    result = dispatcher.call(
        "event_create_v0",
        {"title": "Bad", "start": "2026-11-02T10:00:00Z", "end": "2026-11-02T09:00:00Z"},
    )
    assert result.is_error


def test_alarm_and_timer_are_persisted(dispatcher, ctx):
    alarm = dispatcher.call("alarm_create_v0", {"at": "+15m", "label": "stretch"})
    assert not alarm.is_error
    assert alarm.structured["scheduledAt"]

    timer = dispatcher.call("timer_create_v0", {"seconds": 90, "label": "tea"})
    assert not timer.is_error
    assert ctx.state.collection("personal/timers.json")[0]["seconds"] == 90

    repeats = dispatcher.call("alarm_create_v0", {"at": "07:30", "repeat": "weekly"})
    assert repeats.structured["repeat"] == "weekly"


def test_user_time_reports_timezone(dispatcher):
    result = dispatcher.call("user_time_v0", {"timezone_name": "Europe/Berlin"})
    assert not result.is_error
    assert result.structured["timezone"] == "Europe/Berlin"
    assert result.structured["iso"].endswith("+01:00") or result.structured["iso"].endswith("+02:00")


def test_user_time_rejects_unknown_timezone(dispatcher):
    result = dispatcher.call("user_time_v0", {"timezone_name": "Mars/Olympus"})
    assert result.is_error


def test_message_compose_defaults_to_draft(dispatcher):
    result = dispatcher.call(
        "message_compose_v1",
        {"to": ["dev@example.com"], "subject": "Hi", "body": "Draft only"},
    )
    assert not result.is_error
    assert result.structured["status"] == "draft"
    assert result.structured["mailtoLink"].startswith("mailto:")


def test_message_send_requires_smtp_configuration(dispatcher):
    result = dispatcher.call(
        "message_compose_v1",
        {"to": ["dev@example.com"], "subject": "Hi", "body": "Send", "send": True},
    )
    assert result.is_error
    assert result.structured["error"] == "not_configured"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def test_chart_writes_artifact_and_reports_points(dispatcher, settings):
    result = dispatcher.call(
        "chart_display_v0",
        {
            "title": "Revenue",
            "data": [{"month": "Jan", "value": 10}, {"month": "Feb", "value": 25}],
            "x": "month",
            "y": "value",
            "chart_type": "bar",
        },
    )
    assert not result.is_error
    assert result.structured["points"] == 2
    assert Path(result.structured["path"]).parent == settings.artifacts_dir
    assert "<svg" in Path(result.structured["path"]).read_text()


@pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
def test_all_chart_types_render(dispatcher, chart_type):
    result = dispatcher.call(
        "chart_display_v0",
        {
            "title": "T",
            "data": [{"k": "a", "v": 1}, {"k": "b", "v": 2}],
            "x": "k",
            "y": "v",
            "chart_type": chart_type,
        },
    )
    assert not result.is_error


def test_chart_rejects_non_numeric_values(dispatcher):
    result = dispatcher.call(
        "chart_display_v0",
        {"title": "T", "data": [{"k": "a", "v": "lots"}], "x": "k", "y": "v"},
    )
    assert result.is_error
    assert "not numeric" in result.text_content()


def test_widget_rejects_scripts(dispatcher):
    result = dispatcher.call(
        "mcp__visualize__show_widget",
        {"widget_type": "html", "content": "<script>alert(1)</script>", "title": "bad"},
    )
    assert result.is_error
    assert result.structured["error"] == "unsafe_content"


def test_visualize_read_me_documents_widgets(dispatcher):
    result = dispatcher.call("mcp__visualize__read_me", {})
    assert "show_widget" in result.text_content()


def test_recipe_and_cards_render(dispatcher):
    recipe = dispatcher.call(
        "recipe_display_v0",
        {"title": "Tea", "ingredients": ["water", "tea"], "steps": ["boil", "steep"], "servings": 1},
    )
    assert not recipe.is_error
    assert "# Tea" in Path(recipe.structured["path"]).read_text()

    cards = dispatcher.call(
        "show_recommendation_cards",
        {"title": "Picks", "cards": [{"title": "A", "description": "first"}]},
    )
    assert not cards.is_error
    assert "A" in Path(cards.structured["path"]).read_text()


def test_ask_user_input_without_channel_reports_error(dispatcher):
    result = dispatcher.call("ask_user_input_v0", {"question": "Deploy?", "options": ["yes", "no"]})
    assert result.is_error
    assert result.structured["error"] == "no_interactive_channel"


def test_ask_user_input_uses_channel(ctx):
    from agent.runtime import Dispatcher

    ctx.ask = lambda question, options: options[1]
    dispatcher = Dispatcher(ctx)
    result = dispatcher.call(
        "ask_user_input_v0", {"question": "Deploy?", "options": ["yes", "no"]}
    )
    assert not result.is_error
    assert result.structured["answer"] == "no"


# ---------------------------------------------------------------------------
# Places and web helpers
# ---------------------------------------------------------------------------
def test_map_display_escapes_titles_and_writes_artifact(dispatcher):
    result = dispatcher.call(
        "places_map_display_v0",
        {
            "title": "Cafes <script>",
            "places": [
                {"name": "A <b>", "latitude": 52.5, "longitude": 13.4, "address": "Berlin"},
                {"name": "B", "latitude": 52.51, "longitude": 13.41},
            ],
        },
    )
    assert not result.is_error
    page = Path(result.structured["path"]).read_text()
    assert "&lt;script&gt;" in page
    assert "leaflet" in page


def test_map_display_rejects_missing_coordinates(dispatcher):
    result = dispatcher.call("places_map_display_v0", {"places": [{"name": "nowhere"}]})
    assert result.is_error
    assert result.structured["error"] == "invalid_arguments"


def test_html_to_text_strips_scripts_and_nav():
    markup = (
        "<html><head><title>Doc</title><style>x{}</style></head>"
        "<body><nav>menu</nav><h1>Heading</h1><p>Body text</p>"
        "<script>alert(1)</script><footer>foot</footer></body></html>"
    )
    title, text = html_to_text(markup)
    assert title == "Doc"
    assert "Heading" in text and "Body text" in text
    assert "alert" not in text
    assert "menu" not in text


# ---------------------------------------------------------------------------
# Three.js and SketchUp
# ---------------------------------------------------------------------------
def test_threejs_scene_renders_when_unlocked(dispatcher, ctx):
    ctx.loaded_tools.add("mcp__Three_js_3D_Viewer__show_threejs_scene")
    result = dispatcher.call(
        "mcp__Three_js_3D_Viewer__show_threejs_scene",
        {
            "title": "Cube",
            "scene": {
                "objects": [{"type": "box", "size": [1, 1, 1], "position": [0, 0.5, 0]}],
                "lights": [{"type": "ambient", "intensity": 0.5}],
            },
        },
    )
    assert not result.is_error
    page = Path(result.structured["path"]).read_text()
    assert "THREE.BoxGeometry" in page
    assert result.structured["objects"] == 1


def test_threejs_rejects_unknown_shape(dispatcher, ctx):
    ctx.loaded_tools.add("mcp__Three_js_3D_Viewer__show_threejs_scene")
    result = dispatcher.call(
        "mcp__Three_js_3D_Viewer__show_threejs_scene",
        {"scene": {"objects": [{"type": "dodecahedron"}]}},
    )
    assert result.is_error
    assert result.structured["error"] == "invalid_scene"


def test_threejs_rejects_bad_vector(dispatcher, ctx):
    ctx.loaded_tools.add("mcp__Three_js_3D_Viewer__show_threejs_scene")
    result = dispatcher.call(
        "mcp__Three_js_3D_Viewer__show_threejs_scene",
        {"scene": {"objects": [{"type": "box", "position": [0, 1]}]}},
    )
    assert result.is_error
    assert "position" in result.text_content()


def test_sketchup_builds_ruby_and_counts_usage(dispatcher, ctx):
    ctx.loaded_tools.update(
        {"mcp__Trimble_SketchUp__build_model", "mcp__Trimble_SketchUp__usage_limit_reached"}
    )
    result = dispatcher.call(
        "mcp__Trimble_SketchUp__build_model",
        {
            "title": "Shed",
            "components": [
                {"type": "box", "size": [2000, 1500, 100], "position": [0, 0, 0], "name": "Slab"},
                {"type": "cylinder", "size": [200, 200, 2400], "position": [0, 0, 0]},
            ],
            "units": "mm",
        },
    )
    assert not result.is_error
    assert result.structured["components"] == 2
    ruby = Path(result.structured["rubyPath"]).read_text()
    assert "Sketchup.active_model" in ruby
    assert "openagent_mm(2000" in ruby

    usage = dispatcher.call("mcp__Trimble_SketchUp__usage_limit_reached", {})
    assert usage.structured["used"] == 1


def test_sketchup_rejects_negative_sizes(dispatcher, ctx):
    ctx.loaded_tools.add("mcp__Trimble_SketchUp__build_model")
    result = dispatcher.call(
        "mcp__Trimble_SketchUp__build_model",
        {"title": "Bad", "components": [{"type": "box", "size": [-1, 1, 1]}]},
    )
    assert result.is_error
    assert result.structured["error"] == "invalid_spec"


def test_sketchup_usage_limit_blocks_further_builds(dispatcher, ctx, monkeypatch):
    monkeypatch.setenv("AGENT_SKETCHUP_MONTHLY_LIMIT", "1")
    ctx.loaded_tools.add("mcp__Trimble_SketchUp__build_model")
    spec = {"title": "A", "components": [{"type": "box"}]}
    assert not dispatcher.call("mcp__Trimble_SketchUp__build_model", spec).is_error
    second = dispatcher.call("mcp__Trimble_SketchUp__build_model", {"title": "B",
                                                                    "components": [{"type": "box"}]})
    assert second.is_error
    assert second.structured["error"] == "usage_limit_reached"


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
def test_end_conversation_sets_stop_flag(dispatcher, ctx):
    result = dispatcher.call("end_conversation", {"reason": "done", "summary": "all set"})
    assert not result.is_error
    assert ctx.stop_requested is True
    assert ctx.stop_reason == "done"
    assert ctx.stop_requested


def test_tool_search_unlocks_sketchup_tools(dispatcher, ctx):
    result = dispatcher.call("tool_search", {"query": "sketchup model"})
    assert not result.is_error
    assert any(name.startswith("mcp__Trimble_SketchUp__") for name in ctx.loaded_tools)


def test_connector_catalog_search(dispatcher):
    result = dispatcher.call("search_mcp_registry", {"query": "postgres"})
    assert "postgres" in result.text_content()
    suggested = dispatcher.call("suggest_connectors", {"goal": "deploy my app to production"})
    assert "vercel" in suggested.text_content()


def test_audit_log_records_calls(dispatcher, ctx):
    dispatcher.call("view", {"path": "missing.txt"})
    entries = ctx.state.read_jsonl("audit.jsonl")
    assert entries and entries[-1]["tool"] == "view"
    assert entries[-1]["isError"] is True


def test_structured_results_are_json_serialisable(dispatcher):
    result = dispatcher.call("user_time_v0", {})
    json.dumps(result.to_dict(), default=str)
