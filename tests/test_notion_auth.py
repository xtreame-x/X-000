"""1-click Notion OAuth: URL, Basic-auth exchange, pages and env persistence."""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from dotenv import dotenv_values

from orc import notion_auth, oauth
from orc.notion_auth import (
    NOTION_VERSION,
    NotionAuthError,
    build_auth_url,
    exchange_code,
    login,
    offer_login,
)

ENV_KEYS = (
    "NOTION_CLIENT_ID",
    "NOTION_CLIENT_SECRET",
    "NOTION_TOKEN",
    "NOTION_WORKSPACE_ID",
    "NOTION_WORKSPACE_NAME",
    "NOTION_BOT_ID",
    "NOTION_REFRESH_TOKEN",
)


@pytest.fixture
def env_guard():
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _fake_browser(holder: dict, *, code: str | None = "notion-code",
                  state: str | None = None, error: str | None = None,
                  delay: float = 0.05):
    """Act like the user's browser: follow the redirect once webbrowser opens."""

    def open_url(url: str) -> bool:
        query = parse_qs(urlsplit(url).query)
        redirect = query["redirect_uri"][0]
        returned_state = state if state is not None else query["state"][0]
        if error:
            target = f"{redirect}?error={error}&state={returned_state}"
        else:
            target = f"{redirect}?code={code}&state={returned_state}"

        def hit() -> None:
            time.sleep(delay)
            try:
                with urllib.request.urlopen(target, timeout=5) as response:
                    holder["status"] = response.status
                    holder["body"] = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                holder["status"] = exc.code
                holder["body"] = exc.read().decode("utf-8")
            except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
                holder["error"] = exc

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_url


def _token_client(captured: list[dict] | None = None) -> httpx.Client:
    payload = {
        "access_token": "secret_notion_access",
        "token_type": "bearer",
        "refresh_token": "notion-refresh",
        "bot_id": "bot-123",
        "workspace_name": "Oryn Workspace",
        "workspace_id": "ws-456",
        "workspace_icon": None,
        "duplicated_template_id": None,
        "owner": {"type": "user", "user": {"id": "user-1"}},
        "request_id": "req-1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(
                {
                    "url": str(request.url),
                    "authorization": request.headers.get("authorization"),
                    "notion_version": request.headers.get("notion-version"),
                    "body": json.loads(request.content.decode()),
                }
            )
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_auth_url_uses_owner_user_and_state():
    url = build_auth_url("abc123", client_id="client-uuid")
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["client-uuid"]
    assert query["redirect_uri"] == ["http://localhost:8085/oauth2callback"]
    assert query["response_type"] == ["code"]
    assert query["owner"] == ["user"]
    assert query["state"] == ["abc123"]


def test_login_persists_token_and_workspace(tmp_path: Path, monkeypatch, env_guard):
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"GEMINI_API_KEY=keep-me\r\nNOTION_TOKEN=stale\r\n")
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-uuid")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "client-secret")
    holder: dict = {}
    monkeypatch.setattr(oauth.webbrowser, "open", _fake_browser(holder))
    captured: list[dict] = []

    result = login(
        env_file=env_file,
        port=0,
        timeout=15,
        http_client=_token_client(captured),
        announce=lambda _line: None,
    )

    assert result.access_token == "secret_notion_access"
    assert result.workspace_name == "Oryn Workspace"
    assert result.bot_id == "bot-123"

    assert holder.get("status") == 200, holder.get("error")
    body = holder["body"]
    assert "Oryn Notion Connected" in body
    assert "Authentication successful! You can safely close this browser tab." in body
    assert "window.close()" in body

    written = env_file.read_bytes()
    assert b"GEMINI_API_KEY=keep-me\r\n" in written
    assert b"NOTION_TOKEN=secret_notion_access" in written
    assert b"NOTION_TOKEN=stale" not in written
    assert b"NOTION_WORKSPACE_ID=ws-456" in written
    assert b"NOTION_BOT_ID=bot-123" in written
    assert b"NOTION_REFRESH_TOKEN=notion-refresh" in written

    # Quoted so dotenv reads the workspace name back with its space intact.
    values = dotenv_values(env_file)
    assert values["NOTION_WORKSPACE_NAME"] == "Oryn Workspace"

    assert os.environ["NOTION_TOKEN"] == "secret_notion_access"
    assert len([p for p in tmp_path.iterdir() if p.name != ".env"]) == 0

    exchange = captured[0]
    assert exchange["url"] == "https://api.notion.com/v1/oauth/token"
    expected_basic = base64.b64encode(b"client-uuid:client-secret").decode()
    assert exchange["authorization"] == f"Basic {expected_basic}"
    assert exchange["notion_version"] == NOTION_VERSION
    assert exchange["body"]["grant_type"] == "authorization_code"
    assert exchange["body"]["code"] == "notion-code"
    assert exchange["body"]["redirect_uri"].endswith("/oauth2callback")


def test_cancel_in_browser_raises_cancelled(tmp_path: Path, monkeypatch, env_guard):
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-uuid")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth.webbrowser, "open", _fake_browser({}, error="access_denied"))
    captured: list[dict] = []

    with pytest.raises(NotionAuthError) as excinfo:
        login(env_file=tmp_path / ".env", port=0, timeout=5,
              http_client=_token_client(captured))

    assert excinfo.value.code == "cancelled"
    assert captured == []
    assert not (tmp_path / ".env").exists()


def test_state_mismatch_never_reaches_notion(tmp_path: Path, monkeypatch, env_guard):
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-uuid")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth.webbrowser, "open", _fake_browser({}, state="attacker"))
    captured: list[dict] = []

    with pytest.raises(NotionAuthError) as excinfo:
        login(env_file=tmp_path / ".env", port=0, timeout=5,
              http_client=_token_client(captured))

    assert excinfo.value.code == "state_mismatch"
    assert captured == []


def test_missing_client_credentials_fail_before_browser(monkeypatch, env_guard):
    monkeypatch.setattr(notion_auth, "DEFAULT_CLIENT_ID", "")
    monkeypatch.setattr(notion_auth, "DEFAULT_CLIENT_SECRET", "")
    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", opened.append)

    with pytest.raises(NotionAuthError) as excinfo:
        login(open_browser=True, timeout=1)

    assert excinfo.value.code == "missing_client"
    assert "NOTION_CLIENT_ID" in str(excinfo.value)
    assert "localhost:8085" in str(excinfo.value)
    assert opened == []


def test_exchange_reports_notion_error_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"object": "error", "status": 401, "code": "unauthorized",
                  "message": "API token is invalid."},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(NotionAuthError) as excinfo:
        exchange_code("code", client_id="id", client_secret="secret", http_client=client)

    assert excinfo.value.code == "exchange_failed"
    assert "unauthorized" in str(excinfo.value)
    assert "API token is invalid." in str(excinfo.value)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_offer_login_declines_without_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr(
        notion_auth, "login", lambda **_: pytest.fail("login must not run without a tty")
    )
    assert offer_login() is False


def test_offer_login_runs_when_the_user_agrees(monkeypatch, env_guard):
    monkeypatch.setattr("sys.stdin", _FakeTTY())

    def fake_login(**kwargs):
        os.environ["NOTION_TOKEN"] = "from-login"
        return notion_auth.NotionLogin(access_token="from-login")

    monkeypatch.setattr(notion_auth, "login", fake_login)
    assert offer_login(ask=lambda _: "y") is True
    assert offer_login(ask=lambda _: "") is True


def test_offer_login_respects_a_no(monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    monkeypatch.setattr(
        notion_auth, "login", lambda **_: pytest.fail("login must not run when declined")
    )
    assert offer_login(ask=lambda _: "n") is False
