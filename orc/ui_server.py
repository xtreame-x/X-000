"""
Oryn desktop dashboard server.

Serves the static single-page app from ``orc/ui`` and a small JSON API:

  GET  /api/status            telemetry, connections and active model
  GET  /api/history           chat transcript
  GET  /api/settings          schema + current config + masked credentials
  GET  /api/tools             the OpenAgent tool catalog
  GET  /api/providers         available providers and their models
  POST /api/chat              send a message (runs tools like the CLI)
  POST /api/clear             forget the transcript
  POST /api/settings          validate and persist config.yaml settings
  POST /api/settings/secret   save one `.env` credential
  POST /api/settings/secret/clear
  POST /api/auth/{drive,notion}
  POST /api/auth/disconnect   forget stored tokens for a service
  POST /api/test/{drive,smtp}

Standard library only, so the dashboard adds no runtime dependencies.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

ORYN_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ORYN_ROOT / ".env"
UI_DIR = Path(__file__).resolve().parent / "ui"
load_dotenv(ENV_FILE, override=True)

if str(ORYN_ROOT) not in sys.path:
    sys.path.insert(0, str(ORYN_ROOT))

from orc import settings as oryn_settings
from orc.config import load_config
from orc.logger import get_logger
from orc.providers import ProviderManager
from orc.providers.base import ChatMessage
from orc.shell import (
    build_system_prompt,
    execute_action,
    is_coding_task,
    parse_all_actions,
    parse_response,
)
from orc.sysbridge import get_sysinfo
from orc.tools import get_native_tools

logger = get_logger("ui_server")

UI_PORT = 4242
SERVER_VERSION = oryn_settings.current_version()

_server_instance = None
_provider_manager = None
_conversation_history: list[dict] = []
_history_lock = threading.Lock()
_started_at = time.time()
_last_chat: dict = {}

_sysinfo_cache: dict | None = None
_sysinfo_cache_at = 0.0
_sysinfo_lock = threading.Lock()

_catalog_cache: dict | None = None
_catalog_lock = threading.Lock()

# Per-tool previews are useful in the transcript, but don't ship megabytes.
MAX_ACTION_RESULT = 4000


class PortUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Cached data sources
# ---------------------------------------------------------------------------
def get_pm() -> ProviderManager:
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = ProviderManager(load_config())
    return _provider_manager


def _invalidate_provider_manager() -> None:
    global _provider_manager
    _provider_manager = None


oryn_settings.set_provider_invalidator(_invalidate_provider_manager)


def _empty_sysinfo() -> dict:
    return {
        "cpu_usage": 0,
        "ram_percent": 0,
        "ram_used_mb": 0,
        "ram_total_mb": 0,
        "disk_percent": 0,
        "disk_used_gb": 0,
        "disk_total_gb": 0,
        "processes": [],
    }


def get_sysinfo_cached(ttl: float = 2.5) -> dict:
    """sysinfo.exe costs a process spawn, so never call it twice per poll."""
    global _sysinfo_cache, _sysinfo_cache_at
    now = time.time()
    with _sysinfo_lock:
        if _sysinfo_cache is not None and now - _sysinfo_cache_at < ttl:
            return _sysinfo_cache
        data = get_sysinfo()
        if not data:
            data = _sysinfo_cache or _empty_sysinfo()
        _sysinfo_cache = data
        _sysinfo_cache_at = now
        return data


def get_tool_catalog() -> dict:
    global _catalog_cache
    with _catalog_lock:
        if _catalog_cache is None:
            path = ORYN_ROOT / "catalog" / "tools.json"
            try:
                _catalog_cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - catalog is in the repo
                logger.error(f"could not read tool catalog: {exc}")
                _catalog_cache = {"totals": {}, "tools": [], "checkpoints": []}
        return _catalog_cache


def native_tool_names() -> list[str]:
    try:
        tools = get_native_tools()
    except Exception:
        return []
    names = []
    for tool in tools or []:
        name = getattr(tool, "__name__", None) or getattr(tool, "name", None)
        if name:
            names.append(str(name))
    return sorted(set(names))


def get_auth_statuses() -> dict:
    load_dotenv(ENV_FILE, override=True)
    cfg = load_config()
    ai = cfg.get("ai", {}) if isinstance(cfg, dict) else {}

    has_drive_refresh = bool((os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip())
    has_drive_access = bool((os.environ.get("GOOGLE_ACCESS_TOKEN") or "").strip())
    if has_drive_refresh:
        drive = {
            "status": "connected_permanent",
            "label": "Permanent OAuth connected",
            "detail": "Refresh token stored; access renews automatically.",
        }
    elif has_drive_access:
        drive = {
            "status": "connected_temporary",
            "label": "Temporary token",
            "detail": "No refresh token stored, so this expires.",
        }
    else:
        drive = {
            "status": "disconnected",
            "label": "Not connected",
            "detail": "Sign in once to give Oryn Drive access.",
        }

    has_notion = bool((os.environ.get("NOTION_TOKEN") or "").strip())
    notion_workspace = (os.environ.get("NOTION_WORKSPACE_NAME") or "").strip()
    notion = {
        "status": "connected" if has_notion else "disconnected",
        "label": notion_workspace or ("Connected" if has_notion else "Not connected"),
        "detail": "Pages shared with the integration are readable." if has_notion else "Connect a workspace or paste an integration token.",
    }

    smtp_user = (os.environ.get("AGENT_SMTP_USER") or "").strip()
    has_smtp = bool(smtp_user) and bool((os.environ.get("AGENT_SMTP_PASSWORD") or "").strip())
    smtp = {
        "status": "configured" if has_smtp else "unconfigured",
        "label": smtp_user if has_smtp else "Not configured",
        "detail": (os.environ.get("AGENT_SMTP_HOST") or "smtp.gmail.com") if has_smtp else "Add SMTP details in Settings → Credentials.",
        "user": smtp_user,
    }

    gemini_key = bool((os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip())
    gemini = {
        "status": "active" if gemini_key else "missing",
        "label": str(ai.get("model") or "gemini-3.5-flash-lite") if gemini_key else "Missing API key",
        "detail": "Primary engine" if gemini_key else "Add GEMINI_API_KEY to start chatting.",
    }

    return {"drive": drive, "notion": notion, "smtp": smtp, "gemini": gemini}


def get_ai_status() -> dict:
    cfg = load_config()
    ai = cfg.get("ai", {}) if isinstance(cfg, dict) else {}
    try:
        available = get_pm().list_available()
    except Exception:
        available = []
    return {
        "provider": ai.get("provider", "unknown"),
        "model": ai.get("model", "unknown"),
        "coding_provider": ai.get("coding_provider") or "",
        "coding_model": ai.get("coding_model") or "",
        "temperature": ai.get("temperature"),
        "max_tokens": ai.get("max_tokens"),
        "available": available,
        "last": _last_chat,
    }


def get_app_status() -> dict:
    with _history_lock:
        messages = len(_conversation_history)
    return {
        "version": SERVER_VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "started_at": _started_at,
        "uptime_sec": int(time.time() - _started_at),
        "messages": messages,
        "tools": len(get_tool_catalog().get("tools") or []),
        "native_tools": len(native_tool_names()),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "there",
        "port": int(getattr(_server_instance, "server_address", ("", 0))[1]),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def _summarise_action(act: str) -> str:
    text = " ".join(str(act).split())
    if text.startswith("[") and "]" in text:
        end = text.find("]")
        name = text[1:end]
        rest = text[end + 1 :].strip()
        return f"{name} {rest[:80]}".strip()
    return text[:120]


def handle_chat_message(user_text: str) -> dict:
    global _conversation_history, _last_chat

    user_text = (user_text or "").strip()
    if not user_text:
        return {"reply": "", "actions": [], "error": "Empty message"}

    with _history_lock:
        _conversation_history.append({"role": "user", "content": user_text})
        if len(_conversation_history) > 20:
            _conversation_history = _conversation_history[-20:]
        history_copy = list(_conversation_history)

    pm = get_pm()
    messages = [ChatMessage(role="system", content=build_system_prompt())]
    messages.extend(ChatMessage(role=m["role"], content=m["content"]) for m in history_copy)
    native_tools = get_native_tools()
    use_coding = is_coding_task(user_text)

    try:
        response = pm.chat(messages, coding=use_coding, tools=native_tools)
        raw = response.content if response else ""
    except Exception as exc:
        logger.error(f"Chat failed: {exc}")
        with _history_lock:
            _conversation_history.pop()  # the failed turn is not part of history
        return {
            "reply": f"I couldn't reach the model. {exc}",
            "actions": [],
            "error": str(exc),
        }

    msg, _ = parse_response(raw)
    acts = parse_all_actions(raw)
    action_results = []
    for act in acts:
        try:
            result, ok = execute_action(act)
        except Exception as exc:  # a failing tool must not kill the turn
            result, ok = f"tool failed: {exc}", False
        text = str(result)
        action_results.append(
            {
                "label": _summarise_action(act),
                "ok": bool(ok),
                "result": text[:MAX_ACTION_RESULT] + ("…" if len(text) > MAX_ACTION_RESULT else ""),
            }
        )

    final_text = msg or raw
    with _history_lock:
        _conversation_history.append({"role": "assistant", "content": final_text})

    _last_chat = {
        "provider": getattr(response, "provider", pm.active_name),
        "model": getattr(response, "model", pm.active_model),
        "latency_ms": getattr(response, "latency_ms", 0),
        "tokens_in": getattr(response, "tokens_in", 0),
        "tokens_out": getattr(response, "tokens_out", 0),
        "tools_run": len(action_results),
        "at": time.time(),
    }

    return {"reply": final_text, "actions": action_results, "meta": _last_chat}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class OrynUIHandler(BaseHTTPRequestHandler):
    server_version = f"OrynUI/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # keep the console readable
        return

    # -- responses ---------------------------------------------------------
    def _send_bytes(self, body: bytes, content_type: str, status: int = 200,
                    extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _origin(self) -> tuple[bool, str | None]:
        """Accept same-machine origins only; a local agent must not be CSRF-able."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True, None
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in ("localhost", "127.0.0.1", "::1"):
            return False, origin
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port != int(self.server.server_address[1]):
            return False, origin
        return True, origin

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        headers = {"Cache-Control": "no-store"}
        allowed, origin = self._origin()
        if allowed and origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        self._send_bytes(body, "application/json; charset=utf-8", status=status, extra_headers=headers)

    def _send_error_json(self, message: str, status: int = 400, errors=None) -> None:
        payload = {"ok": False, "error": message}
        if errors:
            payload["errors"] = errors
        self._send_json(payload, status=status)

    def _serve_static(self, relative: str) -> None:
        if relative in ("", "/"):
            relative = "index.html"
        relative = relative.lstrip("/")
        candidate = (UI_DIR / unquote(relative)).resolve()
        try:
            candidate.relative_to(UI_DIR.resolve())
        except ValueError:
            self._send_error_json("Not found", status=404)
            return
        if not candidate.is_file():
            self._send_error_json("Not found", status=404)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        self._send_bytes(
            candidate.read_bytes(),
            content_type,
            extra_headers={"Cache-Control": "no-store"},
        )

    def do_OPTIONS(self):  # noqa: N802 - stdlib naming
        allowed, origin = self._origin()
        self.send_response(204)
        if allowed and origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()

    # -- GET ---------------------------------------------------------------
    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path

        if path in ("/favicon.ico", "/favicon.svg"):
            self._serve_static("favicon.svg")
            return
        if path in ("/", "/index.html") or path.startswith("/assets/"):
            self._serve_static(path.replace("/assets/", "/"))
            return
        if path == "/api/status":
            self._send_json(
                {
                    "system": get_sysinfo_cached(),
                    "auth": get_auth_statuses(),
                    "ai": get_ai_status(),
                    "app": get_app_status(),
                    "time": time.strftime("%H:%M:%S"),
                }
            )
        elif path == "/api/settings":
            self._send_json(oryn_settings.load_settings())
        elif path == "/api/history":
            with _history_lock:
                self._send_json({"history": _conversation_history})
        elif path == "/api/tools":
            catalog = get_tool_catalog()
            self._send_json(
                {
                    "source": catalog.get("source", ""),
                    "totals": catalog.get("totals", {}),
                    "checkpoints": catalog.get("checkpoints", []),
                    "tools": catalog.get("tools", []),
                    "native": native_tool_names(),
                }
            )
        elif path == "/api/providers":
            self._send_json({"providers": oryn_settings.provider_catalog()})
        elif path == "/api/health":
            self._send_json({"ok": True, "version": SERVER_VERSION})
        else:
            self._send_error_json("Not found", status=404)

    # -- POST --------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        allowed, origin = self._origin()
        if not allowed:
            self._send_error_json("Cross-origin request blocked", status=403)
            return
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                body = {}
        except Exception:
            self._send_error_json("Request body must be JSON", status=400)
            return

        try:
            self._route_post(path, body)
        except oryn_settings.SettingsError as exc:
            self._send_error_json("Some settings need attention", status=400, errors=exc.errors)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(f"POST {path} failed")
            self._send_error_json(f"{type(exc).__name__}: {exc}", status=500)

    def _route_post(self, path: str, body: dict) -> None:
        if path == "/api/chat":
            message = str(body.get("message", "")).strip()
            if not message:
                self._send_error_json("Message is empty", status=400)
                return
            if len(message) > 8000:
                self._send_error_json("Message is too long (max 8000 characters)", status=400)
                return
            self._send_json(handle_chat_message(message))

        elif path == "/api/clear":
            global _conversation_history
            with _history_lock:
                _conversation_history = []
            self._send_json({"ok": True})

        elif path == "/api/settings":
            values = body.get("values", body)
            if not isinstance(values, dict):
                self._send_error_json("values must be an object", status=400)
                return
            result = oryn_settings.save_settings(values)
            self._send_json({"ok": True, **result})

        elif path == "/api/settings/secret":
            key = str(body.get("key", "")).strip()
            value = str(body.get("value", ""))
            oryn_settings.save_secret(key, value)
            self._send_json(
                {
                    "ok": True,
                    "secrets": oryn_settings.secret_status(),
                    "message": f"{key} saved",
                }
            )

        elif path == "/api/settings/secret/clear":
            key = str(body.get("key", "")).strip()
            oryn_settings.clear_secret(key)
            self._send_json(
                {
                    "ok": True,
                    "secrets": oryn_settings.secret_status(),
                    "message": f"{key} removed",
                }
            )

        elif path == "/api/auth/drive":
            def run_drive_auth() -> None:
                try:
                    from orc.drive_auth import login

                    login(open_browser=True)
                except Exception as exc:
                    logger.error(f"Drive login error: {exc}")

            threading.Thread(target=run_drive_auth, daemon=True).start()
            self._send_json(
                {
                    "ok": True,
                    "status": "started",
                    "message": "Browser opened — approve access to finish connecting Drive.",
                }
            )

        elif path == "/api/auth/notion":
            client_id = (os.environ.get("NOTION_CLIENT_ID") or "").strip()
            client_secret = (os.environ.get("NOTION_CLIENT_SECRET") or "").strip()
            if not client_id or not client_secret:
                self._send_json(
                    {
                        "ok": False,
                        "status": "missing_credentials",
                        "message": "Add NOTION_CLIENT_ID and NOTION_CLIENT_SECRET in Settings → Credentials, or paste an integration token instead.",
                    }
                )
                return

            def run_notion_auth() -> None:
                try:
                    from orc.notion_auth import login

                    login(open_browser=True)
                except Exception as exc:
                    logger.error(f"Notion login error: {exc}")

            threading.Thread(target=run_notion_auth, daemon=True).start()
            self._send_json(
                {
                    "ok": True,
                    "status": "started",
                    "message": "Browser opened — pick a workspace and approve access.",
                }
            )

        elif path == "/api/auth/disconnect":
            service = str(body.get("service", "")).strip()
            removed = oryn_settings.clear_service_credentials(service)
            self._send_json(
                {
                    "ok": True,
                    "removed": removed,
                    "auth": get_auth_statuses(),
                    "message": f"{service} disconnected",
                }
            )

        elif path == "/api/test/drive":
            from orc.openagent_bridge import google_drive_action

            result = str(google_drive_action("list_recent"))
            ok = "HTTP 401" not in result and "not configured" not in result.lower()
            self._send_json({"ok": ok, "details": result[:600]})

        elif path == "/api/test/smtp":
            from orc.openagent_bridge import send_email

            to_addr = (os.environ.get("AGENT_SMTP_USER") or "").strip()
            if not to_addr:
                self._send_json({"ok": False, "details": "No SMTP username configured."})
                return
            result = str(send_email(to_addr, "Oryn dashboard test", "This is an automated test message from the Oryn dashboard."))
            ok = "sent" in result.lower()
            self._send_json({"ok": ok, "details": result[:600]})

        else:
            self._send_error_json("Not found", status=404)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------
class OrynHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"


def create_server(port: int = UI_PORT, host: str = "127.0.0.1") -> OrynHTTPServer:
    """Bind the dashboard, trying nearby ports when one is taken."""
    last_error: Exception | None = None
    for candidate in range(port, port + 10):
        try:
            return OrynHTTPServer((host, candidate), OrynUIHandler)
        except OSError as exc:
            last_error = exc
            continue
    raise PortUnavailable(
        f"could not bind a dashboard port in {port}-{port + 9}: {last_error}"
    )


def start_ui_server(port: int = UI_PORT, open_browser: bool = True,
                    host: str = "127.0.0.1", background: bool = False):
    """Start the UI server. ``background`` returns ``(server, url)`` immediately."""
    global _server_instance
    server = create_server(port, host=host)
    _server_instance = server
    bound_port = int(server.server_address[1])
    url = f"http://localhost:{bound_port}"

    print(f"\033[34m[oryn-ui]\033[0m live at {url}")
    if open_browser:
        webbrowser.open(url)

    if background:
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="oryn-ui")
        thread.start()
        return server, url

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\033[90m[oryn-ui] shutting down server...\033[0m")
    finally:
        server.server_close()
    return server, url


if __name__ == "__main__":
    start_ui_server()
