"""Shared desktop OAuth plumbing: localhost callback page, state, `.env` writes.

Google Drive and Notion both authorize through a redirect to a loopback server
that this module provides. Providers only supply:

* a :class:`PageBrand` with the browser copy,
* their authorization URL and token exchange,
* the env keys to persist.

The server itself is stdlib-only (``http.server``) and answers exactly one
redirect, which is why there is no heavy OAuth SDK in Oryn's dependency list.
"""
from __future__ import annotations

import html
import os
import re
import secrets
import sys
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from dotenv import load_dotenv

from agent.platform_compat import IS_WINDOWS, atomic_write, read_text_normalized

ORYN_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ORYN_ROOT / ".env"
load_dotenv(ENV_FILE, override=True)

CALLBACK_HOST = "localhost"
CALLBACK_PORT = 8085
CALLBACK_PATH = "/oauth2callback"
DEFAULT_TIMEOUT = 120.0

_ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def redirect_uri(port: int = CALLBACK_PORT) -> str:
    """The loopback redirect URI registered with the provider."""
    return f"http://localhost:{port}{CALLBACK_PATH}"


class OAuthError(RuntimeError):
    """A login attempt failed; ``code`` is stable and safe to branch on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class CallbackOutcome:
    """What the browser delivered to the callback server."""

    code: str | None = None
    error: str | None = None
    error_description: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class PageBrand:
    """Provider-specific copy for the three browser pages."""

    success_title: str
    success_subtitle: str
    success_hint: str
    cancelled_title: str
    cancelled_subtitle: str
    cancelled_hint: str
    failed_title: str
    failed_subtitle: str


# ---------------------------------------------------------------------------
# Browser pages
# ---------------------------------------------------------------------------
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
    background: radial-gradient(900px 480px at 50% -12%, {glow}, transparent 70%), #0a0e13;
    color: #e6edf3; font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  }}
  .card {{
    width: min(100%, 470px); padding: 46px 38px 38px; text-align: center;
    background: #141a22; border: 1px solid #222c38; border-radius: 18px;
    box-shadow: 0 26px 70px rgba(0, 0, 0, .5), inset 0 1px 0 rgba(255, 255, 255, .04);
  }}
  .mark {{
    width: 58px; height: 58px; margin: 0 auto 22px; display: grid; place-items: center;
    border-radius: 50%; background: {badge}; color: {accent}; font-size: 27px;
    line-height: 1; font-weight: 600;
  }}
  h1 {{ margin: 0 0 12px; font-size: 20.5px; line-height: 1.35; color: #f3f7fb; }}
  p {{ margin: 0; font-size: 14.5px; line-height: 1.65; color: #9aa7b4; }}
  .hint {{ margin-top: 20px; font-size: 12px; color: #6b7885; }}
  button {{
    margin-top: 26px; padding: 10px 22px; font: inherit; font-size: 13.5px;
    color: {accent}; background: {badge}; border: 1px solid {border};
    border-radius: 9px; cursor: pointer;
  }}
  button:hover {{ filter: brightness(1.15); }}
</style>
</head>
<body>
  <main class="card">
    <div class="mark">{mark}</div>
    <h1>{title}</h1>
    <p>{subtitle}</p>
    <p class="hint">{hint}</p>
    <button type="button" onclick="window.close()">Close tab</button>
  </main>
  <script>{script}</script>
</body>
</html>
"""

_SUCCESS_STYLE = {
    "mark": "\u2713",
    "accent": "#34d399",
    "badge": "rgba(52, 211, 153, .13)",
    "border": "rgba(52, 211, 153, .35)",
    "glow": "rgba(52, 211, 153, .14)",
    "script": "setTimeout(function () { window.close(); }, 1500);",
}

_CANCELLED_STYLE = {
    "mark": "\u2715",
    "accent": "#fbbf24",
    "badge": "rgba(251, 191, 36, .12)",
    "border": "rgba(251, 191, 36, .32)",
    "glow": "rgba(251, 191, 36, .12)",
    "script": "",
}

_FAILED_STYLE = {
    "mark": "\u2715",
    "accent": "#f87171",
    "badge": "rgba(248, 113, 113, .12)",
    "border": "rgba(248, 113, 113, .32)",
    "glow": "rgba(248, 113, 113, .12)",
    "script": "",
}


def _render(title: str, subtitle: str, hint: str, style: Mapping[str, str]) -> bytes:
    return _PAGE.format(
        title=html.escape(title),
        subtitle=subtitle,
        hint=hint,
        **style,
    ).encode("utf-8")


def success_page(brand: PageBrand) -> bytes:
    return _render(brand.success_title, brand.success_subtitle, brand.success_hint, _SUCCESS_STYLE)


def cancelled_page(brand: PageBrand, description: str | None = None) -> bytes:
    subtitle = html.escape(description) if description else brand.cancelled_subtitle
    return _render(brand.cancelled_title, subtitle, brand.cancelled_hint, _CANCELLED_STYLE)


def failure_page(brand: PageBrand, description: str) -> bytes:
    return _render(
        brand.failed_title,
        html.escape(description) or brand.failed_subtitle,
        "",
        _FAILED_STYLE,
    )


# ---------------------------------------------------------------------------
# Callback server
# ---------------------------------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles exactly the redirect the provider sends back to localhost."""

    server_version = "OrynOAuth/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parts = urlsplit(self.path)
        if parts.path == "/favicon.ico":
            self._respond(204, b"", "text/plain; charset=utf-8")
            return
        if parts.path != CALLBACK_PATH:
            self._respond(
                404,
                failure_page(
                    self.server.brand,
                    f"Nothing is served at {parts.path}. Start the login from Oryn "
                    "and let the browser follow the redirect.",
                ),
                "text/html; charset=utf-8",
            )
            return

        params = parse_qs(parts.query, keep_blank_values=True)

        def first(name: str) -> str:
            values = params.get(name) or [""]
            return values[0]

        state = first("state")
        error = first("error")

        if error:
            description = first("error_description")
            self.server.outcome = CallbackOutcome(
                error=error, error_description=description, state=state
            )
            self._respond(
                200,
                cancelled_page(self.server.brand, description or None),
                "text/html; charset=utf-8",
            )
            return

        code = first("code")
        if not secrets.compare_digest(state, self.server.expected_state):
            self.server.outcome = CallbackOutcome(error="state_mismatch", state=state)
            self._respond(
                400,
                failure_page(
                    self.server.brand,
                    "The security check for this sign-in did not match. "
                    "Start the login again from Oryn.",
                ),
                "text/html; charset=utf-8",
            )
            return

        if not code:
            self.server.outcome = CallbackOutcome(error="missing_code", state=state)
            self._respond(
                400,
                failure_page(self.server.brand, "The provider did not return an authorization code."),
                "text/html; charset=utf-8",
            )
            return

        self.server.outcome = CallbackOutcome(code=code, state=state)
        self._respond(200, success_page(self.server.brand), "text/html; charset=utf-8")

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the terminal clean; the entry points print their own status."""


class _CallbackHTTPServer(HTTPServer):
    """HTTPServer carrying the expected state, brand and captured outcome."""

    #: SO_REUSEADDR lets a second local process steal the port on Windows, so it
    #: stays off there and we surface a clear "port busy" error instead.
    allow_reuse_address = not IS_WINDOWS
    expected_state: str
    outcome: CallbackOutcome | None
    brand: PageBrand
    timeout = 0.5


class CallbackServer:
    """Single-shot localhost listener for a provider's authorization redirect."""

    def __init__(self, expected_state: str, *, brand: PageBrand,
                 host: str = CALLBACK_HOST, port: int = CALLBACK_PORT) -> None:
        try:
            self._httpd = _CallbackHTTPServer((host, port), _CallbackHandler)
        except OSError as exc:
            raise OAuthError(
                "port_busy",
                f"cannot listen on http://localhost:{port}{CALLBACK_PATH} "
                f"({exc}); close the other Oryn sign-in window and retry",
            ) from exc
        self._httpd.expected_state = expected_state
        self._httpd.outcome = None
        self._httpd.brand = brand
        self.port = int(self._httpd.server_address[1])
        self.redirect_uri = redirect_uri(self.port)

    @property
    def outcome(self) -> CallbackOutcome | None:
        return self._httpd.outcome

    def wait(self, timeout: float = DEFAULT_TIMEOUT) -> CallbackOutcome:
        """Serve redirects until the browser lands or ``timeout`` elapses."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            self._httpd.handle_request()
            if self._httpd.outcome is not None:
                return self._httpd.outcome
        raise OAuthError(
            "timeout",
            f"no response from the browser within {int(timeout)}s; nothing was changed. "
            f"If the provider showed a redirect URI mismatch, register {self.redirect_uri} "
            "as an allowed redirect URI and retry.",
        )

    def close(self) -> None:
        self._httpd.server_close()

    def __enter__(self) -> "CallbackServer":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def build_url(endpoint: str, params: Mapping[str, str]) -> str:
    return f"{endpoint}?{urlencode(dict(params))}"


def new_state() -> str:
    """Cryptographically secure CSRF state for one login attempt."""
    return secrets.token_hex(32)


def validate_outcome(outcome: CallbackOutcome, state: str) -> str:
    """Return the authorization code, or raise for every failure shape."""
    if outcome.error == "access_denied":
        raise OAuthError("cancelled", "access was denied in the browser; nothing was saved")
    if outcome.error:
        raise OAuthError(
            outcome.error,
            f"sign-in did not complete ({outcome.error}); nothing was saved",
        )
    if not outcome.state or not secrets.compare_digest(outcome.state, state):
        raise OAuthError("state_mismatch", "state validation failed; nothing was saved")
    if not outcome.code:
        raise OAuthError("missing_code", "the provider did not return an authorization code")
    return outcome.code


# ---------------------------------------------------------------------------
# Environment persistence
# ---------------------------------------------------------------------------
def update_env_file(values: Mapping[str, str], *, env_file: str | Path = ENV_FILE) -> Path:
    """Atomically set ``values`` in ``env_file``, preserving comments and order."""
    target = Path(env_file)
    for key, value in values.items():
        if any(char in value for char in "\r\n\0"):
            raise ValueError(f"refusing to write a multi-line value for {key}")

    if target.exists():
        text, newline = read_text_normalized(target)
    else:
        text, newline = "", "\n"

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    pending = dict(values)
    updated: list[str] = []
    for line in lines:
        match = _ENV_KEY.match(line)
        key = match.group(1) if match else None
        if key and key in pending:
            updated.append(f"{key}={pending.pop(key)}")
        else:
            updated.append(line)
    for key, value in pending.items():
        updated.append(f"{key}={value}")

    atomic_write(target, "\n".join(updated) + "\n", newline=newline)
    return target


def persist_env(values: Mapping[str, str], *, env_file: str | Path = ENV_FILE,
                reload_env: bool = True) -> None:
    """Write values to ``.env`` and, optionally, into this process's environment."""
    update_env_file(values, env_file=env_file)
    if reload_env:
        os.environ.update(values)
        load_dotenv(dotenv_path=env_file, override=True)


def open_in_browser(url: str, *, open_browser: bool = True,
                    announce: Callable[[str], None] = print) -> None:
    """Open ``url`` in the default browser, printing it when that cannot work."""
    if open_browser:
        opened = webbrowser.open(url)
        if not opened:
            announce("[auth] could not launch a browser automatically; open this URL:")
            announce(url)
    else:
        announce(url)


def stdin_is_interactive() -> bool:
    stream = sys.stdin
    try:
        return bool(stream) and stream.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = [
    "CALLBACK_HOST",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "DEFAULT_TIMEOUT",
    "ENV_FILE",
    "ORYN_ROOT",
    "CallbackOutcome",
    "CallbackServer",
    "OAuthError",
    "PageBrand",
    "build_url",
    "cancelled_page",
    "failure_page",
    "new_state",
    "open_in_browser",
    "persist_env",
    "redirect_uri",
    "stdin_is_interactive",
    "success_page",
    "update_env_file",
    "validate_outcome",
]
