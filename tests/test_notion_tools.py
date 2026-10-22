"""Notion tool handlers: request shaping, summaries and error surfacing."""
from __future__ import annotations

import json

import pytest

from agent.config import NotConfigured
from agent.tools import notion as notion_tools
from agent.tools.base import REGISTRY
from agent.tools.notion import (
    NOTION_VERSION,
    mcp__Notion__append_blocks,
    mcp__Notion__create_database,
    mcp__Notion__create_page,
    mcp__Notion__delete_block,
    mcp__Notion__get_database,
    mcp__Notion__query_database,
    mcp__Notion__read_page,
    mcp__Notion__search,
    mcp__Notion__update_block,
    mcp__Notion__update_page,
)


class FakeResponse:
    def __init__(self, payload: dict | None = None, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Minimal httpx.Client stand-in returning queued responses."""

    def __init__(self, responses: dict[tuple[str, str], list[FakeResponse]]) -> None:
        self.queues = {key: list(value) for key, value in responses.items()}
        self.calls: list[dict] = []

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def _call(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        for (candidate_method, suffix), queue in self.queues.items():
            if candidate_method == method and url.endswith(suffix):
                if not queue:
                    raise AssertionError(f"no canned response left for {method} {suffix}")
                return queue.pop(0)
        raise AssertionError(f"unexpected request {method} {url}")

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._call("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        return self._call("POST", url, **kwargs)

    def patch(self, url: str, **kwargs) -> FakeResponse:
        return self._call("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs) -> FakeResponse:
        return self._call("DELETE", url, **kwargs)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test_token")
    return "secret_test_token"


def _use(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(notion_tools, "_client", lambda ctx: client)


def _text(result) -> str:
    return result.text_content()


def test_missing_token_points_at_the_login_command(monkeypatch, ctx):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(NotConfigured) as excinfo:
        mcp__Notion__search(ctx)
    assert excinfo.value.env_var == "NOTION_TOKEN"
    assert "auth notion" in str(excinfo.value)


def test_every_notion_tool_is_deferred_and_needs_a_token():
    for name in notion_tools.__all__:
        tool_obj = REGISTRY.get(name)
        assert tool_obj.requires_tool_search
        assert "NOTION_TOKEN" in tool_obj.needs_configuration
        assert tool_obj.group == "Notion"


def test_search_sends_version_header_and_summarizes(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/search"): [FakeResponse({"results": [
            {"object": "page", "id": "page-1",
             "properties": {"title": {"type": "title",
                                      "title": [{"plain_text": "Meeting notes"}]}}},
            {"object": "database", "id": "db-1",
             "title": [{"plain_text": "Tasks"}]},
        ]})],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__search(ctx, query="notes", object_type="page", limit=5)

    assert not result.is_error
    text = _text(result)
    assert "- Meeting notes (page) [page-1]" in text
    assert "- Tasks (database) [db-1]" in text
    call = client.calls[0]
    assert call["url"] == "https://api.notion.com/v1/search"
    assert call["headers"]["Notion-Version"] == NOTION_VERSION
    assert call["headers"]["Authorization"] == "Bearer secret_test_token"
    assert call["json"] == {
        "page_size": 5,
        "query": "notes",
        "filter": {"property": "object", "value": "page"},
    }


def test_read_page_renders_block_text(monkeypatch, ctx, token):
    client = FakeClient({
        ("GET", "/v1/pages/page-1"): [FakeResponse({
            "object": "page", "id": "page-1", "url": "https://notion.so/page-1",
            "properties": {"title": {"type": "title",
                                     "title": [{"plain_text": "Notes"}]}},
        })],
        ("GET", "/v1/blocks/page-1/children"): [FakeResponse({"results": [
            {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Plan"}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body text"}]}},
            {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "Ship it"}],
                                        "checked": True}},
            {"type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": [{"plain_text": "item"}]}},
        ], "has_more": False})],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__read_page(ctx, page_id="page-1")

    text = _text(result)
    assert text.startswith("Notes")
    assert "# Plan" in text
    assert "Body text" in text
    assert "[x] Ship it" in text
    assert "- item" in text
    assert result.structured["truncated"] is False


def test_read_page_reports_truncation(monkeypatch, ctx, token):
    client = FakeClient({
        ("GET", "/v1/pages/page-1"): [FakeResponse({
            "object": "page", "id": "page-1",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "T"}]}},
        })],
        ("GET", "/v1/blocks/page-1/children"): [FakeResponse({
            "results": [{"type": "paragraph",
                         "paragraph": {"rich_text": [{"plain_text": "one"}]}}],
            "has_more": True,
            "next_cursor": "cursor-2",
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__read_page(ctx, page_id="page-1", max_blocks=1)

    assert result.structured["truncated"] is True
    assert "[truncated at max_blocks]" in _text(result)


def test_create_page_as_database_row(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/pages"): [FakeResponse({
            "object": "page", "id": "row-1", "url": "https://notion.so/row-1",
            "properties": {"Name": {"type": "title",
                                    "title": [{"plain_text": "New row"}]}},
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__create_page(
        ctx, title="New row", parent_id="db-1", parent_type="database",
        content="First paragraph.\n\nSecond paragraph.", title_property="Name",
    )

    assert not result.is_error
    body = client.calls[0]["json"]
    assert body["parent"] == {"database_id": "db-1"}
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "New row"
    assert [child["paragraph"]["rich_text"][0]["text"]["content"]
            for child in body["children"]] == ["First paragraph.", "Second paragraph."]
    assert "created page New row [row-1]" in _text(result)


def test_create_page_under_a_page_uses_title_property(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/pages"): [FakeResponse({"id": "page-2", "properties": {}})],
    })
    _use(monkeypatch, client)

    mcp__Notion__create_page(ctx, title="Child", parent_id="page-1")

    body = client.calls[0]["json"]
    assert body["parent"] == {"page_id": "page-1"}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == "Child"
    assert "children" not in body


def test_append_blocks_builds_typed_children(monkeypatch, ctx, token):
    client = FakeClient({
        ("PATCH", "/v1/blocks/page-1/children"): [FakeResponse({
            "results": [{"id": "b1"}, {"id": "b2"}],
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__append_blocks(
        ctx, block_id="page-1", texts=["Steps", "Do it"], block_type="to_do"
    )

    children = client.calls[0]["json"]["children"]
    assert children[0]["to_do"]["rich_text"][0]["text"]["content"] == "Steps"
    assert children[0]["to_do"]["checked"] is False
    assert children[1]["to_do"]["rich_text"][0]["text"]["content"] == "Do it"
    assert result.structured["blockIds"] == ["b1", "b2"]


def test_append_blocks_rejects_empty_input(ctx):
    result = mcp__Notion__append_blocks(ctx, block_id="page-1", texts=[])
    assert result.is_error
    assert result.structured["error"] == "invalid_arguments"


def test_update_block_detects_the_current_type(monkeypatch, ctx, token):
    client = FakeClient({
        ("GET", "/v1/blocks/block-1"): [FakeResponse({"id": "block-1", "type": "heading_2"})],
        ("PATCH", "/v1/blocks/block-1"): [FakeResponse({"id": "block-1"})],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__update_block(ctx, block_id="block-1", text="New heading")

    assert not result.is_error
    assert client.calls[0]["method"] == "GET"
    patched = client.calls[1]["json"]
    assert patched["heading_2"]["rich_text"][0]["text"]["content"] == "New heading"


def test_update_block_refuses_non_text_blocks(monkeypatch, ctx, token):
    client = FakeClient({
        ("GET", "/v1/blocks/img-1"): [FakeResponse({"id": "img-1", "type": "image"})],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__update_block(ctx, block_id="img-1", text="nope")

    assert result.is_error
    assert result.structured["error"] == "unsupported_block_type"
    assert len(client.calls) == 1


def test_delete_block_trashes(monkeypatch, ctx, token):
    client = FakeClient({
        ("DELETE", "/v1/blocks/block-1"): [FakeResponse({
            "id": "block-1", "type": "paragraph", "archived": True,
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__delete_block(ctx, block_id="block-1")

    assert "trashed paragraph" in _text(result)
    assert client.calls[0]["method"] == "DELETE"


def test_query_database_passes_filter_and_sorts(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/databases/db-1/query"): [FakeResponse({
            "results": [
                {"id": "row-1", "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Task A"}]}}},
            ],
            "has_more": False,
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__query_database(
        ctx, database_id="db-1",
        filter={"property": "Done", "checkbox": {"equals": False}},
        sorts=[{"property": "Name", "direction": "ascending"}],
        limit=10,
    )

    body = client.calls[0]["json"]
    assert body["page_size"] == 10
    assert body["filter"]["property"] == "Done"
    assert body["sorts"][0]["direction"] == "ascending"
    assert "- Task A [row-1]" in _text(result)
    assert result.structured["hasMore"] is False


def test_update_page_merges_title_and_properties(monkeypatch, ctx, token):
    client = FakeClient({
        ("PATCH", "/v1/pages/row-1"): [FakeResponse({
            "id": "row-1",
            "properties": {"Name": {"type": "title",
                                    "title": [{"plain_text": "Renamed"}]}},
        })],
    })
    _use(monkeypatch, client)

    mcp__Notion__update_page(
        ctx, page_id="row-1", title="Renamed",
        properties={"Status": {"select": {"name": "Done"}}}, title_property="Name",
    )

    body = client.calls[0]["json"]["properties"]
    assert body["Name"]["title"][0]["text"]["content"] == "Renamed"
    assert body["Status"] == {"select": {"name": "Done"}}


def test_update_page_requires_a_change(ctx):
    result = mcp__Notion__update_page(ctx, page_id="row-1")
    assert result.is_error
    assert result.structured["error"] == "invalid_arguments"


def test_create_database_adds_a_title_property(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/databases"): [FakeResponse({
            "id": "db-9", "title": [{"plain_text": "Projects"}],
            "url": "https://notion.so/db-9",
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__create_database(
        ctx, parent_page_id="page-1", title="Projects",
        properties={"Status": {"select": {}}},
    )

    body = client.calls[0]["json"]
    assert body["parent"] == {"page_id": "page-1"}
    assert body["properties"]["Status"] == {"select": {}}
    assert body["properties"]["Name"] == {"title": {}}
    assert "created database Projects [db-9]" in _text(result)


def test_get_database_lists_property_types(monkeypatch, ctx, token):
    client = FakeClient({
        ("GET", "/v1/databases/db-1"): [FakeResponse({
            "object": "database", "id": "db-1",
            "title": [{"plain_text": "Tasks"}],
            "properties": {"Name": {"type": "title"}, "Done": {"type": "checkbox"}},
        })],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__get_database(ctx, database_id="db-1")

    text = _text(result)
    assert "Tasks" in text
    assert "Name: title" in text
    assert "Done: checkbox" in text


def test_api_errors_are_reported_with_status(monkeypatch, ctx, token):
    client = FakeClient({
        ("POST", "/v1/search"): [FakeResponse({
            "object": "error", "status": 401, "code": "unauthorized",
            "message": "API token is invalid.",
        }, status=401)],
    })
    _use(monkeypatch, client)

    result = mcp__Notion__search(ctx, query="x")

    assert result.is_error
    assert result.structured["status"] == 401
    assert "HTTP 401 - API token is invalid." in _text(result)
