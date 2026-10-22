"""1-click Google Drive OAuth: URL, callback server, exchange and persistence."""
from __future__ import annotations

import io
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from orc import drive_auth, oauth
from orc.drive_auth import (
    CALLBACK_PATH,
    DRIVE_SCOPE,
    DriveAuthError,
    build_auth_url,
    exchange_code,
    login,
    offer_login,
    update_env_file,
)

ENV_KEYS = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_ACCESS_TOKEN",
    "GOOGLE_REFRESH_TOKEN",
)


@pytest.fixture
def env_guard():
    """Restore the process environment after tests that persist tokens."""
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_browser(response_holder: dict, *, code: str | None = "test-code",
                  state: str | None = None, error: str | None = None,
                  delay: float = 0.05):
    """Return a webbrowser.open replacement that acts like the user's browser."""

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
                    response_holder["status"] = response.status
                    response_holder["body"] = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:  # 400 pages are expected in tests
                response_holder["status"] = exc.code
                response_holder["body"] = exc.read().decode("utf-8")
            except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
                response_holder["error"] = exc

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_url


def _token_client(**overrides) -> httpx.Client:
    payload = {
        "access_token": "ya29.fake-access",
        "refresh_token": "1//fake-refresh",
        "expires_in": 3599,
        "scope": DRIVE_SCOPE,
        "token_type": "Bearer",
    }
    payload.update(overrides)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": request.content.decode()})
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client


def test_auth_url_carries_every_desktop_parameter():
    url = build_auth_url("deadbeef", client_id="client.apps.googleusercontent.com")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["client.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://localhost:8085/oauth2callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [DRIVE_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"] == ["deadbeef"]


def test_login_opens_browser_captures_code_and_persists_tokens(
    tmp_path: Path, monkeypatch, env_guard
):
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"ORYN_UNRELATED_SETTING=keep-me\r\nGOOGLE_ACCESS_TOKEN=stale\r\n")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    response_holder: dict = {}
    monkeypatch.setattr(oauth.webbrowser, "open", _fake_browser(response_holder))
    client = _token_client()
    lines: list[str] = []

    result = login(
        env_file=env_file,
        port=0,
        timeout=15,
        http_client=client,
        announce=lines.append,
    )

    assert result.refresh_token == "1//fake-refresh"
    assert result.access_token == "ya29.fake-access"
    assert response_holder.get("status") == 200, response_holder.get("error")
    body = response_holder["body"]
    assert "Oryn Google Drive Connected" in body
    assert "Authentication successful! You can safely close this browser tab." in body
    assert "window.close()" in body

    written = env_file.read_bytes()
    assert b"ORYN_UNRELATED_SETTING=keep-me\r\n" in written  # newline style preserved
    assert b"GOOGLE_ACCESS_TOKEN=ya29.fake-access" in written
    assert b"GOOGLE_REFRESH_TOKEN=1//fake-refresh" in written
    assert b"GOOGLE_ACCESS_TOKEN=stale" not in written

    assert os.environ["GOOGLE_REFRESH_TOKEN"] == "1//fake-refresh"
    assert os.environ["GOOGLE_ACCESS_TOKEN"] == "ya29.fake-access"

    exchange = client.seen[0]  # type: ignore[attr-defined]
    assert exchange["url"] == "https://oauth2.googleapis.com/token"
    form = parse_qs(exchange["body"])
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["test-code"]
    assert form["client_id"] == ["client-id"]
    assert form["client_secret"] == ["client-secret"]
    assert form["redirect_uri"][0].endswith(CALLBACK_PATH)
    assert len([p for p in env_file.parent.iterdir() if p.name != ".env"]) == 0


def test_state_mismatch_aborts_before_token_exchange(tmp_path: Path, monkeypatch, env_guard):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth.webbrowser, "open", _fake_browser({}, state="attacker"))
    client = _token_client()

    with pytest.raises(DriveAuthError) as excinfo:
        login(env_file=tmp_path / ".env", port=0, timeout=5, http_client=client)

    assert excinfo.value.code == "state_mismatch"
    assert client.seen == []  # type: ignore[attr-defined]
    assert not (tmp_path / ".env").exists()


def test_cancel_in_browser_raises_cancelled_and_saves_nothing(
    tmp_path: Path, monkeypatch, env_guard
):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        oauth.webbrowser, "open", _fake_browser({}, error="access_denied")
    )
    client = _token_client()

    with pytest.raises(DriveAuthError) as excinfo:
        login(env_file=tmp_path / ".env", port=0, timeout=5, http_client=client)

    assert excinfo.value.code == "cancelled"
    assert not (tmp_path / ".env").exists()


def test_timeout_returns_cleanly_and_releases_the_port(tmp_path: Path, monkeypatch, env_guard):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    port = _free_port()
    started = time.monotonic()

    with pytest.raises(DriveAuthError) as excinfo:
        login(env_file=tmp_path / ".env", port=port, timeout=0.7,
              open_browser=False, http_client=_token_client())

    assert excinfo.value.code == "timeout"
    assert time.monotonic() - started < 5
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))  # would raise if the server were still bound


def test_missing_client_credentials_fail_before_opening_a_browser(monkeypatch, env_guard):
    monkeypatch.setattr(drive_auth, "DEFAULT_CLIENT_ID", "")
    monkeypatch.setattr(drive_auth, "DEFAULT_CLIENT_SECRET", "")
    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", opened.append)

    with pytest.raises(DriveAuthError) as excinfo:
        login(open_browser=True, timeout=1)

    assert excinfo.value.code == "missing_client"
    assert "GOOGLE_CLIENT_ID" in str(excinfo.value)
    assert opened == []


def test_exchange_code_surfaces_google_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Bad Request"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(DriveAuthError) as excinfo:
        exchange_code("code", client_id="id", client_secret="secret", http_client=client)

    assert excinfo.value.code == "exchange_failed"
    assert "invalid_grant" in str(excinfo.value)


def test_update_env_file_is_atomic_and_preserves_order(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("# keep this comment\nA=1\nB=2\n", encoding="utf-8")

    update_env_file({"B": "20", "C": "3"}, env_file=env_file)

    assert env_file.read_text(encoding="utf-8") == "# keep this comment\nA=1\nB=20\nC=3\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_update_env_file_refuses_multiline_values(tmp_path: Path):
    with pytest.raises(ValueError):
        update_env_file({"X": "line1\nline2"}, env_file=tmp_path / ".env")


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102 - matches sys.stdin
        return True


def test_offer_login_declines_without_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    def explode() -> None:
        raise AssertionError("interactive login must not start without a tty")

    monkeypatch.setattr(drive_auth, "login", lambda **_: explode())
    assert offer_login() is False


def test_offer_login_runs_when_the_user_agrees(monkeypatch, env_guard):
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    calls: list[float] = []

    def fake_login(**kwargs):
        calls.append(kwargs.get("timeout", 0))
        os.environ["GOOGLE_REFRESH_TOKEN"] = "1//fresh"
        return drive_auth.OAuthResult(access_token="a", refresh_token="1//fresh")

    monkeypatch.setattr(drive_auth, "login", fake_login)
    assert offer_login(ask=lambda _: "y") is True
    assert len(calls) == 1


def test_offer_login_respects_a_no(monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    monkeypatch.setattr(
        drive_auth, "login", lambda **_: pytest.fail("login must not run when declined")
    )
    assert offer_login(ask=lambda _: "n") is False
