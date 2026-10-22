"""1-click Notion OAuth for Oryn's Notion tools.

Notion's public connection flow is a single browser round-trip, so it reuses the
loopback callback server in :mod:`orc.oauth`:

1. Open ``https://api.notion.com/v1/oauth/authorize`` with ``owner=user`` and a
   random ``state``.
2. Catch ``http://localhost:8085/oauth2callback``, validate ``state`` and show
   the confirmation page.
3. Exchange the code at ``https://api.notion.com/v1/oauth/token`` using HTTP
   Basic auth (``client_id:client_secret``) and a JSON body.
4. Save ``NOTION_TOKEN`` (plus workspace metadata) to ``.env`` and reload the
   running process.

Notion access tokens are long-lived; if one is revoked or rotated, re-running
the login replaces it. A refresh token is stored when Notion returns one.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import httpx
from dotenv import load_dotenv

from orc import oauth
from orc.oauth import (
    DEFAULT_TIMEOUT,
    ENV_FILE,
    CallbackServer,
    OAuthError,
    PageBrand,
)

NotionAuthError = OAuthError
load_dotenv(ENV_FILE, override=True)

AUTHORIZE_ENDPOINT = "https://api.notion.com/v1/oauth/authorize"
TOKEN_ENDPOINT = "https://api.notion.com/v1/oauth/token"
REVOKE_ENDPOINT = "https://api.notion.com/v1/oauth/revoke"

#: Pinned API version.  ``2022-06-28`` keeps the database endpoints
#: (``/v1/databases/{id}/query``, database-id parents) stable; a database with
#: multiple data sources requires ``2025-09-03``+ and answers with
#: ``minimum_api_version`` if it needs one.
NOTION_VERSION = "2022-06-28"

DEFAULT_CLIENT_ID = os.environ.get("ORYN_NOTION_CLIENT_ID", "")
DEFAULT_CLIENT_SECRET = os.environ.get("ORYN_NOTION_CLIENT_SECRET", "")

NOTION_PAGES = PageBrand(
    success_title="\u2705 Oryn Notion Connected",
    success_subtitle="Authentication successful! You can safely close this browser tab.",
    success_hint="Oryn saved your Notion token and can now read and edit the pages you shared.",
    cancelled_title="\u26a0\ufe0f Notion Connection Cancelled",
    cancelled_subtitle="No workspace was connected, so nothing was saved. You can close this tab.",
    cancelled_hint="Run the login again whenever you are ready.",
    failed_title="\u26a0\ufe0f Oryn Could Not Connect to Notion",
    failed_subtitle="The sign-in response was rejected. You can close this tab and try again.",
)


@dataclass
class NotionLogin:
    """What Notion returned from the token exchange."""

    access_token: str
    workspace_id: str = ""
    workspace_name: str = ""
    bot_id: str = ""
    refresh_token: str | None = None

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token)


# ---------------------------------------------------------------------------
# Request construction and token exchange
# ---------------------------------------------------------------------------
def build_auth_url(state: str, *, client_id: str,
                   redirect_uri: str = oauth.redirect_uri()) -> str:
    """Notion authorization URL for a user-owned public connection."""
    return oauth.build_url(
        AUTHORIZE_ENDPOINT,
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": state,
        },
    )


def basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def exchange_code(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str = oauth.redirect_uri(),
    timeout: float = 30.0,
    http_client: httpx.Client | None = None,
) -> dict:
    """Swap the authorization code for a Notion access token."""
    headers = {
        "Authorization": basic_auth_header(client_id, client_secret),
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
        "Accept": "application/json",
    }
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        if http_client is not None:
            response = http_client.post(TOKEN_ENDPOINT, json=payload, headers=headers)
        else:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(TOKEN_ENDPOINT, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise NotionAuthError(
            "network",
            f"could not reach Notion's token endpoint: {type(exc).__name__}: {exc}",
        ) from exc

    if response.status_code >= 400:
        code_field = detail = ""
        try:
            body = response.json()
            code_field = str(body.get("error") or body.get("code") or "")
            detail = str(body.get("error_description") or body.get("message") or "")
        except ValueError:
            detail = response.text[:300]
        reason = " - ".join(part for part in (code_field, detail) if part)
        raise NotionAuthError(
            "exchange_failed",
            f"Notion rejected the token exchange (HTTP {response.status_code})"
            + (f": {reason}" if reason else ""),
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise NotionAuthError("exchange_failed", "Notion returned a malformed token response") from exc

    if not data.get("access_token"):
        raise NotionAuthError("exchange_failed", "Notion did not return an access token")
    return data


def resolve_client_credentials(
    client_id: str | None = None,
    client_secret: str | None = None,
) -> tuple[str, str]:
    """Client credentials from arguments, then the environment, then defaults."""
    resolved_id = (client_id or os.environ.get("NOTION_CLIENT_ID") or DEFAULT_CLIENT_ID).strip()
    resolved_secret = (
        client_secret or os.environ.get("NOTION_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET
    ).strip()
    if not resolved_id or not resolved_secret:
        raise NotionAuthError(
            "missing_client",
            "NOTION_CLIENT_ID and NOTION_CLIENT_SECRET are not set. Create a public "
            "integration at https://www.notion.so/my-integrations, add "
            f"{oauth.redirect_uri()} as a redirect URI, then run "
            "'auth notion' in Oryn (or python auth_notion.py menu).",
        )
    return resolved_id, resolved_secret


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _env_value(text: str) -> str:
    """Quote a value dotenv can read back verbatim (workspace names have spaces)."""
    if not text or any(char in text for char in ' #"\'\t') or text != text.strip():
        return json.dumps(text)
    return text


def save_tokens(payload: Mapping[str, object], *, env_file: str | Path = ENV_FILE,
                reload_env: bool = True) -> NotionLogin:
    """Write the Notion token and workspace metadata to ``.env``."""
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise NotionAuthError("exchange_failed", "no access token to save")

    values = {"NOTION_TOKEN": access_token}
    workspace_id = str(payload.get("workspace_id") or "")
    workspace_name = str(payload.get("workspace_name") or "")
    bot_id = str(payload.get("bot_id") or "")
    refresh_token = str(payload.get("refresh_token") or "") or None
    if workspace_id:
        values["NOTION_WORKSPACE_ID"] = workspace_id
    if workspace_name:
        values["NOTION_WORKSPACE_NAME"] = _env_value(workspace_name)
    if bot_id:
        values["NOTION_BOT_ID"] = bot_id
    if refresh_token:
        values["NOTION_REFRESH_TOKEN"] = refresh_token

    oauth.persist_env(values, env_file=env_file, reload_env=reload_env)
    return NotionLogin(
        access_token=access_token,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        bot_id=bot_id,
        refresh_token=refresh_token,
    )


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def is_configured() -> bool:
    return bool((os.environ.get("NOTION_TOKEN") or "").strip())


def workspace_name() -> str:
    return (os.environ.get("NOTION_WORKSPACE_NAME") or "").strip()


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------
def login(
    *,
    env_file: str | Path = ENV_FILE,
    client_id: str | None = None,
    client_secret: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    open_browser: bool = True,
    port: int = oauth.CALLBACK_PORT,
    announce: Callable[[str], None] = print,
    http_client: httpx.Client | None = None,
) -> NotionLogin:
    """Run the 1-click Notion login and persist the resulting token."""
    client_id, client_secret = resolve_client_credentials(client_id, client_secret)
    state = oauth.new_state()

    with CallbackServer(state, brand=NOTION_PAGES, port=port) as server:
        redirect = server.redirect_uri
        url = build_auth_url(state, client_id=client_id, redirect_uri=redirect)
        announce("[notion] opening Notion sign-in in your default browser...")
        announce(f"[notion] waiting for approval (up to {int(timeout)}s)")
        oauth.open_in_browser(url, open_browser=open_browser, announce=announce)

        try:
            outcome = server.wait(timeout)
        except KeyboardInterrupt as exc:
            raise NotionAuthError("cancelled", "login cancelled") from exc

    code = oauth.validate_outcome(outcome, state)

    announce("[notion] authorization received, exchanging it for a token...")
    payload = exchange_code(
        code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect,
        http_client=http_client,
    )
    result = save_tokens(payload, env_file=env_file)
    label = result.workspace_name or "your workspace"
    announce(f"[notion] saved NOTION_TOKEN for {label} to .env")
    return result


def offer_login(
    *,
    ask: Callable[[str], str] = input,
    announce: Callable[[str], None] = print,
    timeout: float = DEFAULT_TIMEOUT,
    env_file: str | Path = ENV_FILE,
) -> bool:
    """Ask the user to connect Notion from a tool call; True when connected."""
    if not oauth.stdin_is_interactive():
        return False
    try:
        answer = ask("Notion needs you to sign in. Open the browser login now? [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        return False
    if answer.strip().lower() not in {"", "y", "yes"}:
        return False
    try:
        login(timeout=timeout, env_file=env_file, announce=announce)
    except NotionAuthError as exc:
        announce(f"[notion] login failed: {exc}")
        return False
    return is_configured()


def main(argv: list[str] | None = None) -> int:
    """``python -m orc.notion_auth`` entry point."""
    load_dotenv(dotenv_path=ENV_FILE)
    args = list(sys.argv[1:] if argv is None else argv)
    timeout = DEFAULT_TIMEOUT
    for arg in args:
        if arg.startswith("--timeout="):
            try:
                timeout = float(arg.split("=", 1)[1])
            except ValueError:
                print(f"invalid --timeout value: {arg}", file=sys.stderr)
                return 2
    try:
        result = login(timeout=timeout)
    except NotionAuthError as exc:
        print(f"\n[notion] {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n[notion] cancelled")
        return 130
    if result.workspace_name:
        print(f"[notion] done. Connected to {result.workspace_name}.")
    else:
        print("[notion] done. Notion tools are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
