"""Claude Docs tools (8).

A local, durable document store: three tools are always available (guide, batch,
update) and the other five (create, delete, export, query, read) are deferred
behind ``tool_search`` exactly as the tool catalog specifies.

Documents live in ``<state>/docs/documents.json`` so the store survives restarts
and works offline.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Literal

from agent.state import now_iso
from agent.tools.base import ToolContext, ToolResult, tool

STORE = "docs/documents.json"
MAX_BATCH = 20


def _load(ctx: ToolContext) -> list[dict]:
    return ctx.state.collection(STORE)


def _save(ctx: ToolContext, docs: list[dict]) -> None:
    ctx.state.save_collection(STORE, docs)


def _find(docs: list[dict], doc_id: str) -> dict | None:
    return next((doc for doc in docs if doc["id"] == doc_id), None)


def _snippet(content: str, needle: str, width: int = 160) -> str:
    index = content.lower().find(needle.lower())
    if index < 0:
        return content[:width].replace("\n", " ")
    start = max(index - width // 3, 0)
    return content[start : start + width].replace("\n", " ")


def _slug(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return value or "document"


@tool(group="Claude Docs", name="mcp__Claude_Docs__guide", tags=("docs", "guide", "help", "overview"))
def mcp__Claude_Docs__guide(ctx: ToolContext, topic: str = "") -> ToolResult:
    """Explain the document store and how to use these tools.

    Args:
        topic: Optional filter; when set, matching documents are listed too.
    """
    docs = _load(ctx)
    overview = (
        "# Document store guide\n\n"
        "Tools: create, read, update, delete, query, export, batch, guide.\n\n"
        "- create(title, content, tags) -> new doc id (deferred: run tool_search first)\n"
        "- read(doc_id) -> full document\n"
        "- update(doc_id, title?, content?, tags?) -> revised document\n"
        "- delete(doc_id) -> removes one document\n"
        "- query(query) -> ranked matches with snippets\n"
        "- export(doc_id, format) -> markdown/json/text artifact\n"
        f"- batch(operations) -> up to {MAX_BATCH} operations in one call\n\n"
        f"Store location: {ctx.state.path(STORE)}\nDocuments: {len(docs)}\n"
    )
    if topic.strip():
        matches = [doc for doc in docs if topic.lower() in json.dumps(doc).lower()]
        overview += "\nMatching documents:\n" + (
            "\n".join(f"- {doc['id']}: {doc['title']}" for doc in matches) or "(none)"
        )
    return ToolResult.text(overview, documents=len(docs), topic=topic or None)


@tool(group="Claude Docs", name="mcp__Claude_Docs__create", requires_tool_search=True,
      tags=("docs", "create", "write", "new"))
def mcp__Claude_Docs__create(
    ctx: ToolContext,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> ToolResult:
    """Create a document.

    Args:
        title: Document title.
        content: Markdown or plain-text body.
        tags: Optional labels for later filtering.
    """
    if not title.strip():
        return ToolResult.error("title must not be empty", error="invalid_arguments")
    docs = _load(ctx)
    document = {
        "id": f"doc_{uuid.uuid4().hex[:10]}",
        "title": title.strip(),
        "content": content,
        "tags": [str(tag) for tag in (tags or [])],
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "revision": 1,
    }
    docs.append(document)
    _save(ctx, docs)
    return ToolResult.text(f"created {document['id']}: {document['title']}", **document)


@tool(group="Claude Docs", name="mcp__Claude_Docs__read", requires_tool_search=True,
      tags=("docs", "read", "get", "open"))
def mcp__Claude_Docs__read(ctx: ToolContext, doc_id: str) -> ToolResult:
    """Read a document by id.

    Args:
        doc_id: Identifier returned by create or query.
    """
    document = _find(_load(ctx), doc_id)
    if document is None:
        return ToolResult.error(f"no document {doc_id!r}", error="not_found", docId=doc_id)
    return ToolResult.text(
        f"# {document['title']}\n\n{document['content']}",
        **document,
    )


@tool(group="Claude Docs", name="mcp__Claude_Docs__update", tags=("docs", "update", "edit", "revise"))
def mcp__Claude_Docs__update(
    ctx: ToolContext,
    doc_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
) -> ToolResult:
    """Update a document's title, body or tags.

    Args:
        doc_id: Document to modify.
        title: New title.
        content: New body.
        tags: Replacement tag list.
    """
    docs = _load(ctx)
    document = _find(docs, doc_id)
    if document is None:
        return ToolResult.error(f"no document {doc_id!r}", error="not_found", docId=doc_id)
    if title is None and content is None and tags is None:
        return ToolResult.error("nothing to update; pass title, content or tags", error="invalid_arguments")
    if title is not None:
        if not title.strip():
            return ToolResult.error("title must not be empty", error="invalid_arguments")
        document["title"] = title.strip()
    if content is not None:
        document["content"] = content
    if tags is not None:
        document["tags"] = [str(tag) for tag in tags]
    document["updatedAt"] = now_iso()
    document["revision"] = int(document.get("revision", 1)) + 1
    _save(ctx, docs)
    return ToolResult.text(f"updated {doc_id} (revision {document['revision']})", **document)


@tool(group="Claude Docs", name="mcp__Claude_Docs__delete", requires_tool_search=True,
      tags=("docs", "delete", "remove"))
def mcp__Claude_Docs__delete(ctx: ToolContext, doc_id: str) -> ToolResult:
    """Delete a document.

    Args:
        doc_id: Document to remove.
    """
    docs = _load(ctx)
    remaining = [doc for doc in docs if doc["id"] != doc_id]
    if len(remaining) == len(docs):
        return ToolResult.error(f"no document {doc_id!r}", error="not_found", docId=doc_id)
    _save(ctx, remaining)
    return ToolResult.text(f"deleted {doc_id}", docId=doc_id, deleted=True)


@tool(group="Claude Docs", name="mcp__Claude_Docs__query", requires_tool_search=True,
      tags=("docs", "search", "query", "find"))
def mcp__Claude_Docs__query(ctx: ToolContext, query: str, limit: int = 5) -> ToolResult:
    """Search documents by text.

    Args:
        query: Words to look for in titles, bodies and tags.
        limit: Maximum matches (1-20).
    """
    needle = query.strip().lower()
    if not needle:
        return ToolResult.error("query must not be empty", error="invalid_arguments")
    terms = [term for term in re.split(r"\W+", needle) if term]
    scored = []
    for document in _load(ctx):
        haystack = " ".join(
            [document["title"], document["content"], " ".join(document.get("tags", []))]
        ).lower()
        score = sum(3 if term in document["title"].lower() else 1 for term in terms if term in haystack)
        if score:
            scored.append((score, document))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["title"]))
    results = [
        {
            "id": document["id"],
            "title": document["title"],
            "tags": document.get("tags", []),
            "snippet": _snippet(document["content"], terms[0] if terms else needle),
            "score": score,
        }
        for score, document in scored[: max(1, min(int(limit), 20))]
    ]
    if not results:
        return ToolResult.text(f"No documents matched {query!r}.", results=[])
    body = "\n".join(f"- {r['id']} ({r['score']}): {r['title']}\n  {r['snippet']}" for r in results)
    return ToolResult.text(body, results=results, query=query)


@tool(group="Claude Docs", name="mcp__Claude_Docs__export", requires_tool_search=True,
      tags=("docs", "export", "download", "artifact"))
def mcp__Claude_Docs__export(
    ctx: ToolContext,
    doc_id: str,
    format: Literal["markdown", "json", "text"] = "markdown",
) -> ToolResult:
    """Export a document to a file artifact.

    Args:
        doc_id: Document to export.
        format: Output format.
    """
    document = _find(_load(ctx), doc_id)
    if document is None:
        return ToolResult.error(f"no document {doc_id!r}", error="not_found", docId=doc_id)
    if format == "markdown":
        payload, ext, mime = (
            f"# {document['title']}\n\n{document['content']}\n",
            "md",
            "text/markdown",
        )
    elif format == "json":
        payload, ext, mime = json.dumps(document, indent=2, ensure_ascii=False), "json", "application/json"
    else:
        payload, ext, mime = f"{document['title']}\n\n{document['content']}\n", "txt", "text/plain"
    info = ctx.save_artifact(payload, name=_slug(document["title"]), ext=ext, mime=mime)
    return ToolResult.text(f"exported {doc_id} to {info['path']}", **info, docId=doc_id, format=format)


@tool(group="Claude Docs", name="mcp__Claude_Docs__batch", tags=("docs", "batch", "bulk", "operations"))
def mcp__Claude_Docs__batch(ctx: ToolContext, operations: list[dict]) -> ToolResult:
    """Run several document operations in one call.

    Each operation is an object with 'op' plus that tool's arguments, e.g.
    {"op": "create", "title": "Notes", "content": "..."} or
    {"op": "update", "doc_id": "doc_ab12", "content": "..."}.
    Operations run in order; a failure is reported for that entry without
    aborting the rest.

    Args:
        operations: Up to 20 operations, each with an 'op' of create, read, update, delete or query.
    """
    if not operations:
        return ToolResult.error("operations must not be empty", error="invalid_arguments")
    if len(operations) > MAX_BATCH:
        return ToolResult.error(
            f"at most {MAX_BATCH} operations per batch, got {len(operations)}",
            error="too_many_operations",
        )

    handlers = {
        "create": mcp__Claude_Docs__create,
        "read": mcp__Claude_Docs__read,
        "update": mcp__Claude_Docs__update,
        "delete": mcp__Claude_Docs__delete,
        "query": mcp__Claude_Docs__query,
    }
    results = []
    for index, operation in enumerate(operations, 1):
        name = str(operation.get("op", "")).lower()
        handler = handlers.get(name)
        if handler is None:
            results.append(
                {"index": index, "op": name, "ok": False,
                 "error": f"unknown op {name!r}; use create, read, update, delete or query"}
            )
            continue
        arguments = {key: value for key, value in operation.items() if key != "op"}
        try:
            outcome = handler(ctx=ctx, **arguments)
        except TypeError as exc:
            results.append({"index": index, "op": name, "ok": False, "error": f"bad arguments: {exc}"})
            continue
        results.append(
            {
                "index": index,
                "op": name,
                "ok": not outcome.is_error,
                "error": outcome.structured.get("error"),
                "result": outcome.structured,
            }
        )

    succeeded = sum(1 for item in results if item["ok"])
    return ToolResult(
        blocks=[
            {
                "type": "text",
                "text": f"batch: {succeeded}/{len(results)} operations succeeded\n"
                + "\n".join(
                    f"{item['index']}. {item['op']}: {'ok' if item['ok'] else item.get('error')}"
                    for item in results
                ),
            }
        ],
        structured={"results": results, "succeeded": succeeded, "total": len(results)},
        is_error=succeeded != len(results),
    )


__all__ = [
    "mcp__Claude_Docs__guide",
    "mcp__Claude_Docs__create",
    "mcp__Claude_Docs__read",
    "mcp__Claude_Docs__update",
    "mcp__Claude_Docs__delete",
    "mcp__Claude_Docs__query",
    "mcp__Claude_Docs__export",
    "mcp__Claude_Docs__batch",
]
