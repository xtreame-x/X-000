"""Google Drive tools (11), all deferred behind ``tool_search``.

Authentication, in order of preference:

1. ``GOOGLE_ACCESS_TOKEN`` — a ready bearer token.
2. ``GOOGLE_CLIENT_ID`` + ``GOOGLE_CLIENT_SECRET`` + ``GOOGLE_REFRESH_TOKEN`` —
   refreshed on demand against Google's OAuth endpoint.

Missing credentials raise ``NotConfigured`` with the exact env var to set, which
the dispatcher converts into a structured error the model can act on.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal

import httpx

from agent.config import NotConfigured
from agent.tools.base import ToolContext, ToolResult, tool

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,webViewLink,parents,owners(emailAddress)"
GOOGLE_DOC = "application/vnd.google-apps"
EXPORTABLE = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/png",
}
MISSING = "GOOGLE_ACCESS_TOKEN"


def _access_token(ctx: ToolContext) -> str:
    token = ctx.settings.secret(MISSING)
    if token:
        return token
    client_id = ctx.settings.secret("GOOGLE_CLIENT_ID")
    client_secret = ctx.settings.secret("GOOGLE_CLIENT_SECRET")
    refresh = ctx.settings.secret("GOOGLE_REFRESH_TOKEN")
    if client_id and client_secret and refresh:
        try:
            with httpx.Client(timeout=ctx.settings.request_timeout) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                return response.json()["access_token"]
        except Exception as exc:  # noqa: BLE001
            raise NotConfigured(
                f"Google OAuth refresh failed: {type(exc).__name__}: {exc}",
                provider="Google Drive",
                env_var="GOOGLE_REFRESH_TOKEN",
            ) from exc
    raise NotConfigured(
        "Google Drive is not configured: set GOOGLE_ACCESS_TOKEN, or "
        "GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN",
        provider="Google Drive",
        env_var=MISSING,
    )


def _headers(ctx: ToolContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(ctx)}"}


def _client(ctx: ToolContext) -> httpx.Client:
    return httpx.Client(timeout=ctx.settings.request_timeout)


def _fail(action: str, response: httpx.Response) -> ToolResult:
    try:
        payload = response.json()
    except ValueError:
        payload = {"body": response.text[:500]}
    return ToolResult.error(
        f"Drive {action} failed: HTTP {response.status_code}",
        error="drive_api_error",
        status=response.status_code,
        details=payload,
    )


def _multipart(metadata: dict, content: bytes, mime_type: str) -> tuple[bytes, str]:
    boundary = f"openagent-{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(metadata).encode(),
            f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/related; boundary={boundary}"


@tool(group="Google Drive", name="mcp__Google_Drive__list_recent_files",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "files", "list", "recent"))
def mcp__Google_Drive__list_recent_files(
    ctx: ToolContext,
    limit: int = 20,
    folder_id: str | None = None,
) -> ToolResult:
    """List recently modified Drive files.

    Args:
        limit: Maximum files to return (1-100).
        folder_id: Restrict to a folder.
    """
    params: dict[str, Any] = {
        "pageSize": max(1, min(int(limit), 100)),
        "orderBy": "modifiedTime desc",
        "fields": f"files({FIELDS})",
        "q": "trashed = false",
    }
    if folder_id:
        params["q"] = f"'{folder_id}' in parents and trashed = false"
    with _client(ctx) as client:
        response = client.get(f"{API}/files", params=params, headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("list_recent_files", response)
    files = response.json().get("files", [])
    body = "\n".join(f"- {f['name']} ({f.get('mimeType', '')}) [{f['id']}]" for f in files)
    return ToolResult.text(body or "No files found.", files=files, count=len(files))


@tool(group="Google Drive", name="mcp__Google_Drive__search_files",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "search", "files", "query"))
def mcp__Google_Drive__search_files(
    ctx: ToolContext,
    query: str,
    limit: int = 20,
    full_text: bool = False,
) -> ToolResult:
    """Search Drive files by name or content.

    Args:
        query: Text to search for.
        limit: Maximum results (1-100).
        full_text: Search file contents instead of names.
    """
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    clause = f"fullText contains '{escaped}'" if full_text else f"name contains '{escaped}'"
    params = {
        "q": f"{clause} and trashed = false",
        "pageSize": max(1, min(int(limit), 100)),
        "fields": f"files({FIELDS})",
    }
    with _client(ctx) as client:
        response = client.get(f"{API}/files", params=params, headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("search_files", response)
    files = response.json().get("files", [])
    body = "\n".join(f"- {f['name']} [{f['id']}]" for f in files)
    return ToolResult.text(body or f"No files matched {query!r}.", files=files, query=query)


@tool(group="Google Drive", name="mcp__Google_Drive__get_file_metadata",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "metadata", "file", "info"))
def mcp__Google_Drive__get_file_metadata(ctx: ToolContext, file_id: str) -> ToolResult:
    """Get a file's metadata.

    Args:
        file_id: Drive file id.
    """
    with _client(ctx) as client:
        response = client.get(f"{API}/files/{file_id}", params={"fields": FIELDS}, headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("get_file_metadata", response)
    metadata = response.json()
    return ToolResult.text(
        f"{metadata.get('name')} ({metadata.get('mimeType')})\n"
        f"id: {metadata.get('id')}\nmodified: {metadata.get('modifiedTime')}\n"
        f"size: {metadata.get('size', 'unknown')}\nlink: {metadata.get('webViewLink', '')}",
        **metadata,
    )


@tool(group="Google Drive", name="mcp__Google_Drive__get_file_permissions",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "permissions", "sharing", "access"))
def mcp__Google_Drive__get_file_permissions(ctx: ToolContext, file_id: str) -> ToolResult:
    """List who can access a file.

    Args:
        file_id: Drive file id.
    """
    fields = "permissions(id,type,role,emailAddress,domain,displayName,deleted)"
    with _client(ctx) as client:
        response = client.get(f"{API}/files/{file_id}/permissions", params={"fields": fields}, headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("get_file_permissions", response)
    permissions = response.json().get("permissions", [])
    body = "\n".join(
        f"- {p.get('emailAddress') or p.get('domain') or p.get('type')}: {p.get('role')}"
        for p in permissions
    )
    return ToolResult.text(body or "No explicit permissions.", permissions=permissions)


@tool(group="Google Drive", name="mcp__Google_Drive__read_file_content",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "read", "content", "download", "docs"))
def mcp__Google_Drive__read_file_content(
    ctx: ToolContext,
    file_id: str,
    max_chars: int = 50000,
) -> ToolResult:
    """Read a file's text content, exporting Google Docs/Sheets to text.

    Args:
        file_id: Drive file id.
        max_chars: Maximum characters to return.
    """
    with _client(ctx) as client:
        metadata = client.get(
            f"{API}/files/{file_id}", params={"fields": "id,name,mimeType,size"}, headers=_headers(ctx)
        )
        if metadata.status_code >= 400:
            return _fail("read_file_content", metadata)
        meta = metadata.json()
        mime = meta.get("mimeType", "")
        if mime.startswith(GOOGLE_DOC):
            export_as = EXPORTABLE.get(mime, "text/plain")
            response = client.get(
                f"{API}/files/{file_id}/export",
                params={"mimeType": export_as},
                headers=_headers(ctx),
            )
        else:
            response = client.get(
                f"{API}/files/{file_id}", params={"alt": "media"}, headers=_headers(ctx)
            )
    if response.status_code >= 400:
        return _fail("read_file_content", response)

    raw = response.content
    if b"\x00" in raw[:8192]:
        return ToolResult.error(
            "file is binary; use download_file_content instead",
            error="binary_file",
            fileId=file_id,
            mimeType=meta.get("mimeType"),
        )
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > max_chars
    return ToolResult.text(
        text[:max_chars] + ("\n[truncated]" if truncated else ""),
        fileId=file_id,
        name=meta.get("name"),
        mimeType=meta.get("mimeType"),
        characters=len(text),
        truncated=truncated,
    )


@tool(group="Google Drive", name="mcp__Google_Drive__download_file_content",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "download", "save", "export"))
def mcp__Google_Drive__download_file_content(
    ctx: ToolContext,
    file_id: str,
    destination: str,
) -> ToolResult:
    """Download a file into the workspace.

    Args:
        file_id: Drive file id.
        destination: Workspace-relative path to write.
    """
    target = ctx.resolve_path(destination)
    with _client(ctx) as client:
        metadata = client.get(
            f"{API}/files/{file_id}", params={"fields": "name,mimeType"}, headers=_headers(ctx)
        )
        if metadata.status_code >= 400:
            return _fail("download_file_content", metadata)
        meta = metadata.json()
        if meta.get("mimeType", "").startswith(GOOGLE_DOC):
            return ToolResult.error(
                "Google-native files cannot be downloaded raw; use read_file_content",
                error="unsupported_content",
                mimeType=meta.get("mimeType"),
            )
        response = client.get(f"{API}/files/{file_id}", params={"alt": "media"}, headers=_headers(ctx))
    if response.status_code >= 400:
        return _fail("download_file_content", response)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return ToolResult.text(
        f"downloaded {len(response.content)} bytes to {target}",
        path=str(target),
        bytes=len(response.content),
        name=meta.get("name"),
    )


@tool(group="Google Drive", name="mcp__Google_Drive__create_file",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "create", "upload", "new"))
def mcp__Google_Drive__create_file(
    ctx: ToolContext,
    name: str,
    content: str,
    mime_type: str = "text/plain",
    folder_id: str | None = None,
) -> ToolResult:
    """Create a text file in Drive.

    Args:
        name: File name.
        content: UTF-8 text content.
        mime_type: MIME type stored with the file.
        folder_id: Parent folder id; defaults to My Drive root.
    """
    metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
    if folder_id:
        metadata["parents"] = [folder_id]
    body, content_type = _multipart(metadata, content.encode("utf-8"), mime_type)
    with _client(ctx) as client:
        response = client.post(
            f"{UPLOAD}/files",
            params={"uploadType": "multipart", "fields": FIELDS},
            headers={**_headers(ctx), "Content-Type": content_type},
            content=body,
        )
    if response.status_code >= 400:
        return _fail("create_file", response)
    created = response.json()
    return ToolResult.text(f"created {created['name']} [{created['id']}]", **created)


@tool(group="Google Drive", name="mcp__Google_Drive__update_file",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "update", "edit", "rename"))
def mcp__Google_Drive__update_file(
    ctx: ToolContext,
    file_id: str,
    content: str | None = None,
    name: str | None = None,
    mime_type: str = "text/plain",
) -> ToolResult:
    """Update a file's content and/or name.

    Args:
        file_id: Drive file id.
        content: New text content.
        name: New file name.
        mime_type: MIME type used when uploading new content.
    """
    if content is None and name is None:
        return ToolResult.error("pass content, name or both", error="invalid_arguments")
    metadata: dict[str, Any] = {}
    if name is not None:
        metadata["name"] = name
    if content is not None:
        metadata["mimeType"] = mime_type

    with _client(ctx) as client:
        if content is not None:
            body, content_type = _multipart(metadata or {}, content.encode("utf-8"), mime_type)
            response = client.patch(
                f"{UPLOAD}/files/{file_id}",
                params={"uploadType": "multipart", "fields": FIELDS},
                headers={**_headers(ctx), "Content-Type": content_type},
                content=body,
            )
        else:
            response = client.patch(
                f"{API}/files/{file_id}",
                params={"fields": FIELDS},
                headers={**_headers(ctx), "Content-Type": "application/json"},
                content=json.dumps(metadata),
            )
    if response.status_code >= 400:
        return _fail("update_file", response)
    updated = response.json()
    return ToolResult.text(f"updated {updated.get('name')} [{file_id}]", **updated)


@tool(group="Google Drive", name="mcp__Google_Drive__copy_file",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "copy", "duplicate", "clone"))
def mcp__Google_Drive__copy_file(
    ctx: ToolContext,
    file_id: str,
    name: str | None = None,
    folder_id: str | None = None,
) -> ToolResult:
    """Copy a file.

    Args:
        file_id: Source file id.
        name: Name for the copy; defaults to 'Copy of <original>'.
        folder_id: Destination folder id.
    """
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if folder_id:
        payload["parents"] = [folder_id]
    with _client(ctx) as client:
        response = client.post(
            f"{API}/files/{file_id}/copy",
            params={"fields": FIELDS},
            headers={**_headers(ctx), "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
    if response.status_code >= 400:
        return _fail("copy_file", response)
    copy = response.json()
    return ToolResult.text(f"copied to {copy.get('name')} [{copy.get('id')}]", **copy)


@tool(group="Google Drive", name="mcp__Google_Drive__share_file",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "share", "permissions", "invite"))
def mcp__Google_Drive__share_file(
    ctx: ToolContext,
    file_id: str,
    email: str,
    role: Literal["reader", "commenter", "writer"] = "reader",
    notify: bool = True,
) -> ToolResult:
    """Share a file with a person.

    Args:
        file_id: Drive file id.
        email: Recipient email address.
        role: Access level to grant.
        notify: Send Google's notification email.
    """
    payload = {"type": "user", "role": role, "emailAddress": email}
    with _client(ctx) as client:
        response = client.post(
            f"{API}/files/{file_id}/permissions",
            params={"sendNotificationEmail": str(bool(notify)).lower(), "fields": "id,role,type,emailAddress"},
            headers={**_headers(ctx), "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
    if response.status_code >= 400:
        return _fail("share_file", response)
    permission = response.json()
    return ToolResult.text(f"shared {file_id} with {email} as {role}", permission=permission)


@tool(group="Google Drive", name="mcp__Google_Drive__trash_file",
      requires_tool_search=True, needs_configuration=(MISSING,),
      tags=("drive", "trash", "delete", "remove"))
def mcp__Google_Drive__trash_file(ctx: ToolContext, file_id: str) -> ToolResult:
    """Move a file to the Drive trash.

    Args:
        file_id: Drive file id.
    """
    with _client(ctx) as client:
        response = client.patch(
            f"{API}/files/{file_id}",
            params={"fields": "id,name,trashed"},
            headers={**_headers(ctx), "Content-Type": "application/json"},
            content=json.dumps({"trashed": True}),
        )
    if response.status_code >= 400:
        return _fail("trash_file", response)
    payload = response.json()
    return ToolResult.text(f"trashed {payload.get('name', file_id)}", **payload)


__all__ = [
    "mcp__Google_Drive__copy_file",
    "mcp__Google_Drive__create_file",
    "mcp__Google_Drive__download_file_content",
    "mcp__Google_Drive__get_file_metadata",
    "mcp__Google_Drive__get_file_permissions",
    "mcp__Google_Drive__list_recent_files",
    "mcp__Google_Drive__read_file_content",
    "mcp__Google_Drive__search_files",
    "mcp__Google_Drive__share_file",
    "mcp__Google_Drive__trash_file",
    "mcp__Google_Drive__update_file",
]
