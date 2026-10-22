"""1-click Google OAuth 2.0 desktop login for Oryn's Google Drive tools.

The flow, in order:

1. Bind a throwaway callback server on ``http://localhost:8085/oauth2callback``.
2. Open Google's consent screen in the user's default browser with a random
   ``state`` value and ``access_type=offline`` + ``prompt=consent`` so Google
   returns a long-lived refresh token.
3. Capture the ``code`` (or Google's ``error=access_denied``) from the redirect,
   validate ``state``, and hand the browser a dark-mode confirmation page.
4. Exchange the code at Google's token endpoint with ``httpx``.
5. Persist ``GOOGLE_REFRESH_TOKEN`` / ``GOOGLE_ACCESS_TOKEN`` into ``.env``
   atomically (newline style preserved) and reload the live process env.

The callback server and env writer live in :mod:`orc.oauth`; only Google's
protocol details are here.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import httpx
from dotenv import load_dotenv

from orc import oauth
from orc.oauth import (
    CALLBACK_PATH,
    DEFAULT_TIMEOUT,
    ENV_FILE,
    ORYN_ROOT,
    CallbackOutcome,
    CallbackServer,
    OAuthError,
    PageBrand,
    update_env_file,
)

DriveAuthError = OAuthError
load_dotenv(ENV_FILE, override=True)

REDIRECT_URI = oauth.redirect_uri()
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"

#: Fallback app credentials shipped with Oryn.  Kept overridable through the
#: environment so a user's own Google Cloud client can replace them.
DEFAULT_CLIENT_ID = os.environ.get("ORYN_GOOGLE_CLIENT_ID", "")
DEFAULT_CLIENT_SECRET = os.environ.get("ORYN_GOOGLE_CLIENT_SECRET", "")

DRIVE_PAGES = PageBrand(
    success_title="\u2705 Oryn Google Drive Connected",
    success_subtitle="Authentication successful! You can safely close this browser tab.",
    success_hint="Oryn saved your refresh token and will keep Drive connected on its own.",
    cancelled_title="\u26a0\ufe0f Google Drive Connection Cancelled",
    cancelled_subtitle="No access was granted, so nothing was saved. You can close this tab.",
    cancelled_hint="Run the login again whenever you are ready.",
    failed_title="\u26a0\ufe0f Oryn Could Not Connect to Google Drive",
    failed_subtitle="The sign-in response was rejected. You can close this tab and try again.",
)


@dataclass
class OAuthResult:
    """Tokens returned by Google's token endpoint."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int = 0
    scope: str = ""

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token)


# ---------------------------------------------------------------------------
# Browser pages
# ---------------------------------------------------------------------------
def success_page() -> bytes:
    return oauth.success_page(DRIVE_PAGES)


def cancelled_page(description: str | None = None) -> bytes:
    return oauth.cancelled_page(DRIVE_PAGES, description)


def failure_page(description: str) -> bytes:
    return oauth.failure_page(DRIVE_PAGES, description)


# ---------------------------------------------------------------------------
# Request construction and token exchange
# ---------------------------------------------------------------------------
def build_auth_url(state: str, *, client_id: str, redirect_uri: str = REDIRECT_URI) -> str:
    """Google consent URL carrying every parameter the desktop flow needs."""
    return oauth.build_url(
        AUTH_ENDPOINT,
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": DRIVE_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        },
    )


def exchange_code(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str = REDIRECT_URI,
    timeout: float = 30.0,
    http_client: httpx.Client | None = None,
) -> dict:
    """Swap an authorization code for tokens at Google's token endpoint."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        if http_client is not None:
            response = http_client.post(TOKEN_ENDPOINT, data=payload)
        else:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(TOKEN_ENDPOINT, data=payload)
    except httpx.HTTPError as exc:
        raise DriveAuthError(
            "network",
            f"could not reach Google's token endpoint: {type(exc).__name__}: {exc}",
        ) from exc

    if response.status_code >= 400:
        code_field = detail = ""
        try:
            body = response.json()
            code_field = str(body.get("error") or "")
            detail = str(body.get("error_description") or "")
        except ValueError:
            detail = response.text[:300]
        reason = " - ".join(part for part in (code_field, detail) if part)
        raise DriveAuthError(
            "exchange_failed",
            f"Google rejected the token exchange (HTTP {response.status_code})"
            + (f": {reason}" if reason else ""),
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise DriveAuthError("exchange_failed", "Google returned a malformed token response") from exc

    if not data.get("access_token"):
        raise DriveAuthError("exchange_failed", "Google did not return an access token")
    return data


def resolve_client_credentials(
    client_id: str | None = None,
    client_secret: str | None = None,
) -> tuple[str, str]:
    """Client credentials from arguments, then the environment, then defaults."""
    resolved_id = (client_id or os.environ.get("GOOGLE_CLIENT_ID") or DEFAULT_CLIENT_ID).strip()
    resolved_secret = (
        client_secret or os.environ.get("GOOGLE_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET
    ).strip()
    if not resolved_id or not resolved_secret:
        raise DriveAuthError(
            "missing_client",
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. Create a Desktop app "
            "OAuth client at https://console.cloud.google.com/apis/credentials and add both "
            "values to .env (or run: python auth_drive.py menu).",
        )
    return resolved_id, resolved_secret


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_tokens(payload: Mapping[str, object], *, env_file: str | Path = ENV_FILE,
                reload_env: bool = True) -> OAuthResult:
    """Write tokens to ``.env`` and reload them into the running process."""
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise DriveAuthError("exchange_failed", "no access token to save")
    refresh_token = str(payload.get("refresh_token") or "") or None

    values = {"GOOGLE_ACCESS_TOKEN": access_token}
    if refresh_token:
        values["GOOGLE_REFRESH_TOKEN"] = refresh_token
    oauth.persist_env(values, env_file=env_file, reload_env=reload_env)
    if reload_env:
        clear_drive_token_cache()

    return OAuthResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(payload.get("expires_in") or 0),
        scope=str(payload.get("scope") or ""),
    )


def clear_drive_token_cache() -> None:
    """Drop the in-memory bearer token so the next tool call refreshes it."""
    try:
        from agent.tools.drive import clear_token_cache
    except Exception:  # noqa: BLE001 - cache reset is best-effort
        return
    clear_token_cache()


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def has_refresh_token() -> bool:
    return bool((os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip())


def is_configured() -> bool:
    return has_refresh_token() or bool((os.environ.get("GOOGLE_ACCESS_TOKEN") or "").strip())


def missing_drive_credentials() -> bool:
    """True when Drive tools cannot authenticate at all."""
    return not is_configured()


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
) -> OAuthResult:
    """Run the whole 1-click login and persist the resulting tokens."""
    client_id, client_secret = resolve_client_credentials(client_id, client_secret)
    state = oauth.new_state()

    with CallbackServer(state, brand=DRIVE_PAGES, port=port) as server:
        redirect = server.redirect_uri
        url = build_auth_url(state, client_id=client_id, redirect_uri=redirect)
        announce("[drive] opening Google sign-in in your default browser...")
        announce(f"[drive] waiting for approval (up to {int(timeout)}s)")
        oauth.open_in_browser(url, open_browser=open_browser, announce=announce)

        try:
            outcome = server.wait(timeout)
        except KeyboardInterrupt as exc:
            raise DriveAuthError("cancelled", "login cancelled") from exc

    code = oauth.validate_outcome(outcome, state)

    announce("[drive] authorization received, exchanging it for tokens...")
    payload = exchange_code(
        code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect,
        http_client=http_client,
    )
    if not payload.get("refresh_token"):
        announce(
            "[drive] warning: Google returned no refresh token, so this login will expire "
            "in about an hour. Revoke Oryn at https://myaccount.google.com/permissions "
            "and sign in again to get offline access."
        )
    result = save_tokens(payload, env_file=env_file)
    announce("[drive] saved GOOGLE_REFRESH_TOKEN to .env")
    return result


def offer_login(
    *,
    ask: Callable[[str], str] = input,
    announce: Callable[[str], None] = print,
    timeout: float = DEFAULT_TIMEOUT,
    env_file: str | Path = ENV_FILE,
) -> bool:
    """Ask the user to sign in from a tool call; returns True when connected.

    Non-interactive runs (pipes, scheduled jobs) decline immediately instead of
    blocking on a browser nobody will click.
    """
    if not oauth.stdin_is_interactive():
        return False
    try:
        answer = ask("Google Drive needs you to sign in. Open the browser login now? [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        return False
    if answer.strip().lower() not in {"", "y", "yes"}:
        return False
    try:
        login(timeout=timeout, env_file=env_file, announce=announce)
    except DriveAuthError as exc:
        announce(f"[drive] login failed: {exc}")
        return False
    return is_configured()


def main(argv: list[str] | None = None) -> int:
    """``python -m orc.drive_auth`` entry point."""
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
    except DriveAuthError as exc:
        print(f"\n[drive] {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n[drive] cancelled")
        return 130
    if result.has_refresh_token:
        print("[drive] done. Google Drive stays connected permanently.")
    else:
        print("[drive] access granted, but Google returned no refresh token. "
              "Re-run the login and make sure you approve offline access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
