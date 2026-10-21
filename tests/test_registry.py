"""Registry, schema derivation, deferred gating and catalog parity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.tools import CHECKPOINT_1_COUNT, checkpoint_counts, verify_checkpoints
from agent.tools.base import REGISTRY, split_docstring

CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "checkpoint-1.json"


def test_registry_has_exactly_65_checked_in_tools():
    assert len(REGISTRY) == CHECKPOINT_1_COUNT == 65
    assert checkpoint_counts()["checkpoint1"] == 65


def test_names_match_the_catalog_exactly():
    catalog = json.loads(CATALOG.read_text())
    expected = [entry["name"] for entry in catalog]
    assert sorted(expected) == sorted(REGISTRY.names())


def test_deferred_flags_match_the_catalog():
    catalog = json.loads(CATALOG.read_text())
    expected = {entry["name"] for entry in catalog if entry["requiresToolSearch"]}
    actual = {name for name in REGISTRY.names() if REGISTRY.get(name).requires_tool_search}
    assert actual == expected
    assert len(actual) == 23


def test_group_counts_match_the_source_document():
    counts = {group: len(items) for group, items in REGISTRY.by_group().items()}
    assert counts == {
        "Files and code": 6,
        "Web and search": 3,
        "Memory": 6,
        "Phone and personal": 11,
        "Places and sports": 3,
        "UI and display": 6,
        "Connectors": 3,
        "Claude Docs": 8,
        "Other": 1,
        "Google Drive": 11,
        "Three.js 3D Viewer": 2,
        "Trimble SketchUp": 5,
    }


def test_checkpoint_verification_reports_checkpoint1_complete():
    report = verify_checkpoints()
    assert report["checkpoint1"] is True
    # Checkpoint 2 is a separate milestone; it is reported, not assumed.
    assert report["checkpoint2"] is False
    assert report["total"] is False


def test_schema_derived_from_signature_has_descriptions():
    schema = REGISTRY.get("view").schema()
    props = schema["input_schema"]["properties"]
    assert props["path"]["description"]
    assert props["offset"]["description"]
    assert "path" in schema["input_schema"]["required"]
    assert "offset" not in schema["input_schema"]["required"]


def test_every_tool_has_a_description_and_object_schema():
    for tool_obj in REGISTRY.all():
        schema = tool_obj.schema()
        assert schema["description"].strip(), tool_obj.name
        assert schema["input_schema"]["type"] == "object", tool_obj.name
        assert schema["input_schema"].get("additionalProperties") is False, tool_obj.name


def test_docstring_argument_parsing():
    def handler(path: str, limit: int = 10) -> None:
        """Read something.

        Args:
            path: Where to read from.
            limit: How many rows.

        Returns:
            Nothing.
        """

    description, params = split_docstring(handler.__doc__)
    assert description == "Read something."
    assert params == {"path": "Where to read from.", "limit": "How many rows."}


def test_params_model_forbids_unknown_arguments():
    model = REGISTRY.get("view").params
    with pytest.raises(Exception):
        model.model_validate({"path": "x", "bogus": 1})


def test_dispatcher_reports_invalid_arguments(dispatcher):
    result = dispatcher.call("view", {"nope": 1})
    assert result.is_error
    assert result.structured["error"] == "invalid_arguments"


def test_dispatcher_reports_unknown_tool_with_suggestions(dispatcher):
    result = dispatcher.call("webfetch", {})
    assert result.is_error
    assert result.structured["error"] == "unknown_tool"
    assert result.structured["suggestions"]


def test_deferred_tool_is_gated_until_tool_search(dispatcher, ctx):
    gated = dispatcher.call("mcp__Google_Drive__list_recent_files", {})
    assert gated.is_error
    assert gated.structured["error"] == "tool_not_loaded"

    search = dispatcher.call("tool_search", {"query": "drive files", "limit": 25})
    assert not search.is_error
    assert "mcp__Google_Drive__list_recent_files" in ctx.loaded_tools

    after = dispatcher.call("mcp__Google_Drive__list_recent_files", {})
    # Unlocked now: it fails on credentials, not on gating.
    assert after.structured.get("error") == "not_configured"


def test_deferred_tools_are_hidden_from_initial_schema_list():
    names = {schema["name"] for schema in REGISTRY.schemas()}
    assert "mcp__Google_Drive__list_recent_files" not in names
    assert "bash_tool" in names
    assert len(names) == 65 - 23


def test_tool_search_returns_callable_schemas(dispatcher):
    result = dispatcher.call("tool_search", {"query": "drive"})
    schemas = result.structured["data"]
    assert isinstance(schemas, list) and schemas
    assert all({"name", "description", "input_schema"} <= set(item) for item in schemas)
    assert result.is_error is False


def test_tool_search_with_no_matches_degrades_gracefully(dispatcher):
    result = dispatcher.call("tool_search", {"query": "zzzz"})
    assert result.is_error is False
    assert result.structured["matches"] == []


def test_duplicate_registration_is_rejected():
    from agent.tools.base import Registry

    registry = Registry()
    tool_obj = REGISTRY.get("view")
    registry.add(tool_obj)
    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.add(tool_obj)
