"""Notion tools (10), all deferred behind ``tool_search``.

Authentication uses ``NOTION_TOKEN``, written by Oryn's 1-click Notion login
(``auth notion`` / ``auth_notion.bat``). Missing credentials raise
``NotConfigured`` with the exact env var, which the dispatcher turns into a
structured error the model can act on.

The client pins ``Notion-Version: 2022-06-28`` so the legacy database endpoints
(``/v1/databases/{id}/query`` and ``database_id`` parents) stay available; a
database with multiple data sources needs ``2025-09-03`` and answers with
``minimum_api_version``, which ``_fail`` surfaces verbatim.
"""
from __future__ import annotations

from typing import Any, Literal

import httpx

from agent.config import NotConfigured
from agent.tools.base import ToolContext, ToolResult, tool

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MISSING = "NOTION_TOKEN"

#: Block types this module can create, edit and read as plain text.
BlockType = Literal[
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "code",
]

_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
}
_TEXT_LIMIT = 1900


def _token(ctx: ToolContext) -> str:
    token = ctx.settings.secret(MISSING)
    if token:
        return token
    raise NotConfigured(
        "Notion is not configured: run the 1-click browser login "
        "('auth notion' in Oryn, or double-click auth_notion.bat), which saves "
        "NOTION_TOKEN to .env",
        provider="Notion",
        env_var=MISSING,
    )


def _headers(ctx: ToolContext) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token(ctx)}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _client(ctx: ToolContext) -> httpx.Client:
    return httpx.Client(timeout=ctx.settings.request_timeout)


def _fail(action: str, response: httpx.Response) -> ToolResult:
    try:
        payload = response.json()
    except ValueError:
        payload = {"body": response.text[:500]}
    message = payload.get("message") if isinstance(payload, dict) else None
    return ToolResult.error(
        f"Notion {action} failed: HTTP {response.status_code}"
        + (f" - {message}" if message else ""),
        error="notion_api_error",
        status=response.status_code,
        details=payload,
    )


def _rich_text(text: str) -> list[dict]:
    """Split ``text`` into Notion's 2000-character rich-text chunks."""
    chunks = [text[i:i + _TEXT_LIMIT] for i in range(0, len(text), _TEXT_LIMIT)] or [""]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _block(block_type: str, text: str) -> dict:
    payload: dict[str, Any] = {"rich_text": _rich_text(text)}
    if block_type == "to_do":
        payload["checked"] = False
    return {block_type: payload}


def _paragraphs(content: str) -> list[dict]:
    """Turn free text into paragraph blocks, one per blank-line-separated chunk."""
    chunks = [chunk.strip() for chunk in content.split("\n\n") if chunk.strip()]
    return [_block("paragraph", chunk) for chunk in chunks]


def _plain_text(block: dict) -> str:
    kind = block.get("type", "")
    payload = block.get(kind) or {}
    text = "".join(part.get("plain_text", "") for part in payload.get("rich_text") or [])
    if not text:
        return ""
    if kind == "to_do":
        return ("[x] " if payload.get("checked") else "[ ] ") + text
    return _PREFIX.get(kind, "") + text


def _title(obj: dict) -> str:
    """Title text of a page or database, whichever shape the payload uses."""
    for value in (obj.get("properties") or {}).values():
        if value.get("type") == "title":
            text = "".join(part.get("plain_text", "") for part in value.get("title") or [])
            if text:
                return text
    rich = obj.get("title") or []
    if rich:
        return "".join(part.get("plain_text", "") for part in rich)
    return obj.get("id", "(untitled)")


def _page_body(page_id: str, *, title: str | None = None,
               properties: dict | None = None, title_property: str = "title") -> dict:
    merged: dict[str, Any] = {}
    if properties:
        merged.update(properties)
    if title is not None:
        merged[title_property] = {"title": _rich_text(title)}
    if not merged:
        raise ValueError("pass title, properties or both")
    return {"properties": merged}


def _block_children(block_id: str, ctx: ToolContext, *, max_blocks: int,
                    client: httpx.Client) -> tuple[list[dict], bool]:
    """Read up to ``max_blocks`` children, following Notion's cursors."""
    blocks: list[dict] = []
    cursor: str | None = None
    while len(blocks) < max_blocks:
        params: dict[str, Any] = {"page_size": min(100, max_blocks - len(blocks))}
        if cursor:
            params["start_cursor"] = cursor
        response = client.get(f"{API}/blocks/{block_id}/children", params=params, headers=_headers(ctx))
        if response.status_code >= 400:
            raise _NotionRequestError(response)
        payload = response.json()
        blocks.extend(payload.get("results") or [])
        if not payload.get("has_more"):
            return blocks, False
        cursor = payload.get("next_cursor")
        if not cursor:
            return blocks, False
    return blocks, True


class _NotionRequestError(RuntimeError):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"Notion request failed: HTTP {response.status_code}")
        self.response = response


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@tool(group="Notion", name="mcp__Notion__search", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "search", "pages", "databases"))
def mcp__Notion__search(
    ctx: ToolContext,
    query: str = "",
    object_type: Literal["page", "database"] | None = None,
    limit: int = 20,
) -> ToolResult:
    """Search Notion pages and databases by title.

    Args:
        query: Text to match against titles; empty lists everything shared.
        object_type: Restrict results to pages or databases.
        limit: Maximum results to return (1-100).
    """
    body: dict[str, Any] = {"page_size": max(1, min(int(limit), 100))}
    if query:
        body["query"] = query
    if object_type:
        body["filter"] = {"property": "object", "value": object_type}
    with _client(ctx) as client:
        response = client.post(f"{API}/search", headers=_headers(ctx), json=body)
    if response.status_code >= 400:
        return _fail("search", response)

    results = response.json().get("results") or []
    lines = []
    for item in results:
        kind = item.get("object", "page")
        label = "database" if kind == "database" else "page"
        lines.append(f"- {_title(item)} ({label}) [{item.get('id')}]")
    return ToolResult.text(
        "\n".join(lines) or "Nothing matched that search.",
        results=[{"id": item.get("id"), "object": item.get("object"), "title": _title(item)}
                 for item in results],
        count=len(results),
    )


@tool(group="Notion", name="mcp__Notion__read_page", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "read", "page", "content", "blocks"))
def mcp__Notion__read_page(
    ctx: ToolContext,
    page_id: str,
    include_blocks: bool = True,
    max_blocks: int = 100,
) -> ToolResult:
    """Read a page's properties and body text.

    Args:
        page_id: Notion page id.
        include_blocks: Include the page body as plain text.
        max_blocks: Maximum child blocks to read (1-500).
    """
    with _client(ctx) as client:
        response = client.get(f"{API}/pages/{page_id}", headers=_headers(ctx))
        if response.status_code >= 400:
            return _fail("read_page", response)
        page = response.json()

        text = ""
        truncated = False
        if include_blocks:
            try:
                blocks, truncated = _block_children(
                    page_id, ctx, max_blocks=max(1, min(int(max_blocks), 500)), client=client
                )
            except _NotionRequestError as exc:
                return _fail("read_page", exc.response)
            text = "\n".join(filter(None, (_plain_text(block) for block in blocks)))

    title = _title(page)
    url = page.get("url", "")
    body = f"{title}\nid: {page.get('id')}\nurl: {url}"
    if include_blocks:
        body += "\n\n" + (text or "(page has no text content)")
        if truncated:
            body += "\n[truncated at max_blocks]"
    return ToolResult.text(
        body,
        pageId=page.get("id"),
        title=title,
        url=url,
        truncated=truncated,
    )


@tool(group="Notion", name="mcp__Notion__get_database", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "database", "schema", "properties"))
def mcp__Notion__get_database(ctx: ToolContext, database_id: str) -> ToolResult:
    """Read a database's schema, including its title property name.

    Args:
        database_id: Notion database id.
    """
    with _client(ctx) as client:
        response = client.get(f"{API}/databases/{database_id}", headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("get_database", response)

    database = response.json()
    properties = database.get("properties") or {}
    lines = [f"{name}: {meta.get('type', 'unknown')}" for name, meta in properties.items()]
    return ToolResult.text(
        f"{_title(database)}\nid: {database.get('id')}\n\nproperties:\n" + "\n".join(lines),
        databaseId=database.get("id"),
        title=_title(database),
        properties={name: meta.get("type") for name, meta in properties.items()},
    )


@tool(group="Notion", name="mcp__Notion__query_database", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "database", "query", "rows", "filter"))
def mcp__Notion__query_database(
    ctx: ToolContext,
    database_id: str,
    filter: dict | None = None,
    sorts: list[dict] | None = None,
    limit: int = 25,
) -> ToolResult:
    """Query the rows of a database.

    Args:
        database_id: Notion database id.
        filter: Raw Notion filter object, e.g. {"property": "Done", "checkbox": {"equals": false}}.
        sorts: Raw Notion sorts list, e.g. [{"property": "Name", "direction": "ascending"}].
        limit: Maximum rows to return (1-100).
    """
    body: dict[str, Any] = {"page_size": max(1, min(int(limit), 100))}
    if filter:
        body["filter"] = filter
    if sorts:
        body["sorts"] = sorts
    with _client(ctx) as client:
        response = client.post(
            f"{API}/databases/{database_id}/query", headers=_headers(ctx), json=body
        )
    if response.status_code >= 400:
        return _fail("query_database", response)

    payload = response.json()
    rows = payload.get("results") or []
    lines = [f"- {_title(row)} [{row.get('id')}]" for row in rows]
    return ToolResult.text(
        "\n".join(lines) or "No rows matched that query.",
        rows=[{"id": row.get("id"), "title": _title(row)} for row in rows],
        count=len(rows),
        hasMore=bool(payload.get("has_more")),
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
@tool(group="Notion", name="mcp__Notion__create_page", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "create", "page", "new", "write"))
def mcp__Notion__create_page(
    ctx: ToolContext,
    title: str,
    parent_id: str,
    parent_type: Literal["page", "database"] = "page",
    content: str | None = None,
    title_property: str = "title",
) -> ToolResult:
    """Create a page inside a page or as a row in a database.

    Args:
        title: Page or row title.
        parent_id: Parent page id, or database id when parent_type is database.
        parent_type: Whether parent_id is a page or a database.
        content: Optional body text; blank lines separate paragraphs.
        title_property: Database title property name (ignored for page parents).
    """
    if parent_type == "database":
        parent = {"database_id": parent_id}
        properties = {title_property: {"title": _rich_text(title)}}
    else:
        parent = {"page_id": parent_id}
        properties = {"title": {"title": _rich_text(title)}}

    body: dict[str, Any] = {"parent": parent, "properties": properties}
    if content:
        body["children"] = _paragraphs(content)

    with _client(ctx) as client:
        response = client.post(f"{API}/pages", headers=_headers(ctx), json=body)
    if response.status_code >= 400:
        return _fail("create_page", response)

    created = response.json()
    return ToolResult.text(
        f"created page {_title(created)} [{created.get('id')}]\n{created.get('url', '')}",
        pageId=created.get("id"),
        url=created.get("url"),
        title=_title(created),
    )


@tool(group="Notion", name="mcp__Notion__append_blocks", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "append", "blocks", "content", "write"))
def mcp__Notion__append_blocks(
    ctx: ToolContext,
    block_id: str,
    texts: list[str],
    block_type: BlockType = "paragraph",
) -> ToolResult:
    """Append text blocks to a page or block.

    Args:
        block_id: Page or block id whose children receive the new blocks.
        texts: One block per entry, appended in order.
        block_type: Block type for every entry.
    """
    if not texts:
        return ToolResult.error("pass at least one text block", error="invalid_arguments")
    children = [_block(block_type, text) for text in texts]
    with _client(ctx) as client:
        response = client.patch(
            f"{API}/blocks/{block_id}/children",
            headers=_headers(ctx),
            json={"children": children},
        )
    if response.status_code >= 400:
        return _fail("append_blocks", response)

    created = response.json().get("results") or []
    return ToolResult.text(
        f"appended {len(created)} block(s) to {block_id}",
        blockIds=[block.get("id") for block in created],
        count=len(created),
    )


@tool(group="Notion", name="mcp__Notion__update_block", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "update", "edit", "block", "write"))
def mcp__Notion__update_block(
    ctx: ToolContext,
    block_id: str,
    text: str,
    block_type: BlockType | None = None,
) -> ToolResult:
    """Replace a block's text, keeping its type unless one is given.

    Args:
        block_id: Block to edit.
        text: New plain text content.
        block_type: Force a block type; defaults to the block's current type.
    """
    with _client(ctx) as client:
        kind = block_type
        if kind is None:
            current = client.get(f"{API}/blocks/{block_id}", headers=_headers(ctx))
            if current.status_code >= 400:
                return _fail("update_block", current)
            kind = current.json().get("type")
            if kind not in _PREFIX and kind not in {"paragraph", "to_do", "code"}:
                return ToolResult.error(
                    f"block type {kind!r} cannot be edited as text; pass block_type",
                    error="unsupported_block_type",
                    blockType=kind,
                )
        response = client.patch(
            f"{API}/blocks/{block_id}", headers=_headers(ctx), json=_block(kind, text)
        )
    if response.status_code >= 400:
        return _fail("update_block", response)

    updated = response.json()
    return ToolResult.text(f"updated {kind} block [{block_id}]", blockId=updated.get("id"))


@tool(group="Notion", name="mcp__Notion__delete_block", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "delete", "trash", "block"))
def mcp__Notion__delete_block(ctx: ToolContext, block_id: str) -> ToolResult:
    """Move a block (or page) to the Notion trash.

    Args:
        block_id: Block or page id to trash.
    """
    with _client(ctx) as client:
        response = client.delete(f"{API}/blocks/{block_id}", headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("delete_block", response)

    trashed = response.json()
    return ToolResult.text(
        f"trashed {trashed.get('type', 'block')} [{block_id}]",
        blockId=trashed.get("id"),
        archived=bool(trashed.get("archived")),
    )


@tool(group="Notion", name="mcp__Notion__update_page", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "update", "page", "title", "properties"))
def mcp__Notion__update_page(
    ctx: ToolContext,
    page_id: str,
    title: str | None = None,
    properties: dict | None = None,
    title_property: str = "title",
) -> ToolResult:
    """Rename a page or update its property values.

    Args:
        page_id: Page id to update.
        title: New title.
        properties: Raw Notion properties object merged into the update.
        title_property: Title property name for database rows.
    """
    if title is None and not properties:
        return ToolResult.error("pass title, properties or both", error="invalid_arguments")
    try:
        body = _page_body(page_id, title=title, properties=properties,
                          title_property=title_property)
    except ValueError as exc:
        return ToolResult.error(str(exc), error="invalid_arguments")

    with _client(ctx) as client:
        response = client.patch(f"{API}/pages/{page_id}", headers=_headers(ctx), json=body)
    if response.status_code >= 400:
        return _fail("update_page", response)

    updated = response.json()
    return ToolResult.text(f"updated page {_title(updated)} [{page_id}]", pageId=updated.get("id"))


@tool(group="Notion", name="mcp__Notion__create_database", requires_tool_search=True,
      needs_configuration=(MISSING,), tags=("notion", "create", "database", "schema"))
def mcp__Notion__create_database(
    ctx: ToolContext,
    parent_page_id: str,
    title: str,
    properties: dict | None = None,
) -> ToolResult:
    """Create a database inside a page.

    Args:
        parent_page_id: Page that will contain the database.
        title: Database title.
        properties: Schema mapping name to a Notion property object, e.g.
            {"Status": {"select": {}}}. Defaults to a single "Name" title property.
    """
    schema = dict(properties or {})
    if not any("title" in meta for meta in schema.values()):
        schema["Name"] = {"title": {}}
    body = {
        "parent": {"page_id": parent_page_id},
        "title": _rich_text(title),
        "properties": schema,
    }
    with _client(ctx) as client:
        response = client.post(f"{API}/databases", headers=_headers(ctx), json=body)
    if response.status_code >= 400:
        return _fail("create_database", response)

    created = response.json()
    return ToolResult.text(
        f"created database {_title(created)} [{created.get('id')}]",
        databaseId=created.get("id"),
        title=_title(created),
        url=created.get("url"),
    )


__all__ = [
    "mcp__Notion__append_blocks",
    "mcp__Notion__create_database",
    "mcp__Notion__create_page",
    "mcp__Notion__delete_block",
    "mcp__Notion__get_database",
    "mcp__Notion__query_database",
    "mcp__Notion__read_page",
    "mcp__Notion__search",
    "mcp__Notion__update_block",
    "mcp__Notion__update_page",
]
