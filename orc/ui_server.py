"""
Oryn Web & Desktop UI Layer.
Provides a modern, lightweight control center with real-time system stats,
1-click OAuth connectors for Google Drive & Notion, and full chat with tool execution.
Runs with standard library Python (zero extra web framework dependencies).
"""

import os
import sys
import json
import time
import threading
import webbrowser
import subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

ORYN_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ORYN_ROOT / ".env"
load_dotenv(ENV_FILE, override=True)

# Ensure root is in sys.path
if str(ORYN_ROOT) not in sys.path:
    sys.path.insert(0, str(ORYN_ROOT))

from orc.logger import get_logger
from orc.sysbridge import get_sysinfo
from orc.config import load_config
from orc.providers import ProviderManager
from orc.providers.base import ChatMessage
from orc.tools import get_native_tools
from orc.shell import build_system_prompt, is_coding_task, parse_response, parse_all_actions, execute_action

logger = get_logger("ui_server")

UI_PORT = 4242

# Global state for server
_server_instance = None
_provider_manager = None
_conversation_history = []
_history_lock = threading.Lock()


def get_pm():
    global _provider_manager
    if _provider_manager is None:
        cfg = load_config()
        _provider_manager = ProviderManager(cfg)
    return _provider_manager


def get_auth_statuses():
    load_dotenv(ENV_FILE, override=True)
    
    # Drive
    has_drive_refresh = bool((os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip())
    has_drive_access = bool((os.environ.get("GOOGLE_ACCESS_TOKEN") or "").strip())
    if has_drive_refresh:
        drive_status = "connected_permanent"
        drive_label = "Permanent OAuth Connected"
    elif has_drive_access:
        drive_status = "connected_temporary"
        drive_label = "Temporary Token"
    else:
        drive_status = "disconnected"
        drive_label = "Not Connected"

    # Notion
    has_notion = bool((os.environ.get("NOTION_TOKEN") or "").strip())
    notion_status = "connected" if has_notion else "disconnected"
    notion_label = "Connected" if has_notion else "Not Connected"

    # SMTP / Email
    has_smtp = bool((os.environ.get("AGENT_SMTP_USER") or "").strip()) and bool((os.environ.get("AGENT_SMTP_PASSWORD") or "").strip())
    smtp_user = os.environ.get("AGENT_SMTP_USER", "")
    smtp_status = "configured" if has_smtp else "unconfigured"
    smtp_label = f"Configured ({smtp_user})" if has_smtp else "Not Configured"

    # Gemini
    has_gemini = bool((os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip())
    gemini_status = "active" if has_gemini else "missing"
    gemini_label = "Active (gemini-3.5-flash-lite)" if has_gemini else "Missing Key"

    return {
        "drive": {"status": drive_status, "label": drive_label},
        "notion": {"status": notion_status, "label": notion_label},
        "smtp": {"status": smtp_status, "label": smtp_label, "user": smtp_user},
        "gemini": {"status": gemini_status, "label": gemini_label},
    }


def handle_chat_message(user_text: str):
    global _conversation_history
    with _history_lock:
        _conversation_history.append({"role": "user", "content": user_text})
        if len(_conversation_history) > 20:
            _conversation_history = _conversation_history[-20:]
        history_copy = list(_conversation_history)

    pm = get_pm()
    system_prompt = build_system_prompt()
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(ChatMessage(role=m["role"], content=m["content"]) for m in history_copy)

    native_tools = get_native_tools()
    use_coding = is_coding_task(user_text)

    try:
        response = pm.chat(messages, coding=use_coding, tools=native_tools)
        raw = response.content if response else ""
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        return {
            "reply": f"An error occurred while connecting to AI: {e}",
            "actions": [],
        }

    msg, _ = parse_response(raw)
    acts = parse_all_actions(raw)
    action_results = []

    if acts:
        for act in acts:
            result, ok = execute_action(act)
            action_results.append({
                "action": act,
                "ok": ok,
                "result": result[:200] + ("..." if len(result) > 200 else "")
            })

    final_text = msg or raw
    with _history_lock:
        _conversation_history.append({"role": "assistant", "content": final_text})

    return {
        "reply": final_text,
        "actions": action_results,
    }


class OrynUIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        return

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_str, status=200):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_html(INDEX_HTML)
        elif path == "/api/status":
            sysinfo = get_sysinfo() or {
                "cpu_usage": 0, "ram_percent": 0, "ram_used_mb": 0, "ram_total_mb": 0,
                "disk_percent": 0, "disk_used_gb": 0, "disk_total_gb": 0, "processes": []
            }
            auth_info = get_auth_statuses()
            self._send_json({
                "system": sysinfo,
                "auth": auth_info,
                "time": time.strftime("%H:%M:%S"),
            })
        elif path == "/api/history":
            with _history_lock:
                self._send_json({"history": _conversation_history})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(length) if length > 0 else b"{}"

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        if path == "/api/chat":
            msg = body.get("message", "").strip()
            if not msg:
                self._send_json({"error": "Empty message"}, status=400)
                return
            res = handle_chat_message(msg)
            self._send_json(res)

        elif path == "/api/auth/drive":
            # Launch 1-click Drive login in background
            def run_auth():
                try:
                    from orc.drive_auth import login
                    login(open_browser=True)
                except Exception as e:
                    logger.error(f"Drive login error: {e}")

            threading.Thread(target=run_auth, daemon=True).start()
            self._send_json({"status": "started", "message": "Browser opened for Google Drive authorization"})

        elif path == "/api/auth/notion":
            client_id = (os.environ.get("NOTION_CLIENT_ID") or "").strip()
            client_secret = (os.environ.get("NOTION_CLIENT_SECRET") or "").strip()
            if not client_id or not client_secret:
                self._send_json({
                    "status": "missing_credentials",
                    "message": "Notion OAuth requires NOTION_CLIENT_ID and NOTION_CLIENT_SECRET. You can also paste an Internal Integration Token (NOTION_TOKEN)."
                })
                return

            def run_auth_notion():
                try:
                    from orc.notion_auth import login
                    login(open_browser=True)
                except Exception as e:
                    logger.error(f"Notion login error: {e}")

            threading.Thread(target=run_auth_notion, daemon=True).start()
            self._send_json({"status": "started", "message": "Browser opened for Notion authorization"})

        elif path == "/api/save_token":
            key = body.get("key", "").strip()
            val = body.get("value", "").strip()
            allowed = ("NOTION_TOKEN", "GOOGLE_ACCESS_TOKEN", "GOOGLE_REFRESH_TOKEN", "GEMINI_API_KEY")
            if key in allowed and val:
                from orc.oauth import persist_env
                persist_env({key: val})
                self._send_json({"ok": True, "message": f"{key} saved to .env"})
            else:
                self._send_json({"ok": False, "error": "Invalid key or empty value"}, status=400)

        elif path == "/api/test/drive":
            from orc.openagent_bridge import google_drive_action
            result = google_drive_action("list_recent")
            ok = "HTTP 401" not in str(result) and "not configured" not in str(result).lower()
            self._send_json({"ok": ok, "details": str(result)[:300]})

        elif path == "/api/test/smtp":
            from orc.openagent_bridge import send_email
            to_addr = os.environ.get("AGENT_SMTP_USER") or "test@example.com"
            result = send_email(to_addr, "Oryn UI Test Email", "This is an automated test from the Oryn UI.")
            ok = "sent" in str(result).lower()
            self._send_json({"ok": ok, "details": str(result)[:300]})

        elif path == "/api/clear":
            global _conversation_history
            with _history_lock:
                _conversation_history.clear()
            self._send_json({"ok": True})

        else:
            self.send_error(404, "Not Found")


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Oryn - AI System Agent</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f293d;
      --card-hover: #162032;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.15);
      --green: #10b981;
      --green-glow: rgba(16, 185, 129, 0.15);
      --yellow: #f59e0b;
      --red: #ef4444;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text-main);
      font-family: var(--font);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    /* Header */
    header {
      background: rgba(17, 24, 39, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--card-border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      z-index: 10;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-logo {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, #0284c7, #38bdf8);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 900;
      font-size: 18px;
      color: #fff;
      box-shadow: 0 0 15px rgba(56, 189, 248, 0.4);
    }
    .brand-title {
      font-weight: 700;
      font-size: 18px;
      letter-spacing: -0.5px;
    }
    .badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #1e293b;
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.3);
      text-transform: uppercase;
      font-weight: 600;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 8px var(--green);
      display: inline-block;
      margin-right: 6px;
    }

    /* Main Layout */
    .container {
      display: flex;
      flex: 1;
      height: calc(100vh - 61px);
      overflow: hidden;
    }

    /* Sidebar */
    .sidebar {
      width: 340px;
      background: #0d131f;
      border-right: 1px solid var(--card-border);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 20px;
      overflow-y: auto;
    }
    .section-title {
      font-size: 11px;
      font-weight: 700;
      color: var(--text-muted);
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 10px;
    }

    /* Service Cards */
    .service-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 14px;
      margin-bottom: 10px;
      transition: all 0.2s ease;
    }
    .service-card:hover {
      border-color: #2e3e56;
      background: var(--card-hover);
    }
    .service-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .service-name {
      font-size: 14px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .service-status {
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 6px;
      font-weight: 600;
    }
    .status-ok { background: var(--green-glow); color: var(--green); }
    .status-warn { background: rgba(245, 158, 11, 0.15); color: var(--yellow); }
    .status-err { background: rgba(239, 68, 68, 0.15); color: var(--red); }
    .service-desc {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 10px;
    }
    .btn-group {
      display: flex;
      gap: 6px;
    }
    button {
      cursor: pointer;
      font-family: inherit;
      border: none;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 600;
      transition: all 0.15s ease;
    }
    .btn-primary {
      background: #0284c7;
      color: #fff;
    }
    .btn-primary:hover { background: #0369a1; }
    .btn-outline {
      background: transparent;
      border: 1px solid var(--card-border);
      color: var(--text-main);
    }
    .btn-outline:hover { background: #1e293b; }

    /* Telemetry Meters */
    .meter-box {
      margin-bottom: 12px;
    }
    .meter-label {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      margin-bottom: 4px;
      color: var(--text-muted);
    }
    .meter-bar-bg {
      height: 6px;
      background: #1e293b;
      border-radius: 999px;
      overflow: hidden;
    }
    .meter-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, #0284c7, #38bdf8);
      border-radius: 999px;
      transition: width 0.4s ease;
    }

    /* Chat Area */
    .chat-container {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: var(--bg);
      position: relative;
    }
    .chat-messages {
      flex: 1;
      padding: 24px 32px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .message {
      display: flex;
      gap: 12px;
      max-width: 80%;
      animation: fadeIn 0.2s ease;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .msg-user {
      align-self: flex-end;
      flex-direction: row-reverse;
    }
    .msg-assistant {
      align-self: flex-start;
    }
    .msg-avatar {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 13px;
      flex-shrink: 0;
    }
    .avatar-oryn {
      background: #0369a1;
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.4);
    }
    .avatar-user {
      background: #334155;
      color: #cbd5e1;
    }
    .msg-bubble {
      padding: 12px 16px;
      border-radius: 14px;
      font-size: 14px;
      line-height: 1.5;
    }
    .bubble-user {
      background: #0284c7;
      color: #fff;
      border-bottom-right-radius: 4px;
    }
    .bubble-assistant {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      border-bottom-left-radius: 4px;
    }
    .tool-badge {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.2);
      border-radius: 4px;
      padding: 2px 6px;
      margin-bottom: 6px;
    }

    /* Quick Suggestions */
    .suggestions {
      display: flex;
      gap: 8px;
      padding: 8px 32px;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .chip {
      background: #111827;
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      border-radius: 999px;
      padding: 6px 14px;
      font-size: 12px;
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.15s ease;
    }
    .chip:hover {
      background: #1e293b;
      color: var(--text-main);
      border-color: #38bdf8;
    }

    /* Input Footer */
    .chat-input-area {
      padding: 16px 32px 24px;
      background: rgba(9, 13, 22, 0.95);
      border-top: 1px solid var(--card-border);
    }
    .input-wrapper {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      display: flex;
      align-items: center;
      padding: 6px 12px;
      transition: border-color 0.2s ease;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }
    .input-wrapper:focus-within {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-glow);
    }
    input[type="text"] {
      flex: 1;
      background: transparent;
      border: none;
      outline: none;
      color: var(--text-main);
      font-size: 14px;
      padding: 8px;
    }
    .icon-btn {
      background: transparent;
      border: none;
      cursor: pointer;
      color: var(--text-muted);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
    }
    .icon-btn:hover {
      color: var(--accent);
      background: rgba(56, 189, 248, 0.1);
    }
    .send-btn {
      background: #0284c7;
      color: #fff;
      border-radius: 8px;
      padding: 6px 14px;
      font-size: 13px;
      font-weight: 600;
      margin-left: 8px;
    }
    .send-btn:hover { background: #0369a1; }
  </style>
</head>
<body>

  <!-- Header -->
  <header>
    <div class="brand">
      <div class="brand-logo">O</div>
      <div class="brand-title">ORYN</div>
      <span class="badge">v0.2.0 • 75 Tools</span>
    </div>
    <div class="header-actions">
      <div><span class="status-dot"></span><span style="font-size: 13px; font-weight: 600;">System Online</span></div>
      <button class="btn-outline" onclick="clearChat()">Clear Chat</button>
    </div>
  </header>

  <!-- Layout Container -->
  <div class="container">
    
    <!-- Sidebar: Connections & Hardware Telemetry -->
    <div class="sidebar">
      
      <!-- Connectors -->
      <div>
        <div class="section-title">1-Click Service Connections</div>

        <!-- Google Drive Card -->
        <div class="service-card" id="card-drive">
          <div class="service-head">
            <div class="service-name">
              <span>📁 Google Drive</span>
            </div>
            <span class="service-status status-warn" id="status-drive">Checking...</span>
          </div>
          <div class="service-desc" id="desc-drive">Sync and manage cloud files.</div>
          <div class="btn-group">
            <button class="btn-primary" onclick="connectDrive()">1-Click Connect</button>
            <button class="btn-outline" onclick="testDrive()">Test</button>
          </div>
        </div>

        <!-- Notion Card -->
        <div class="service-card" id="card-notion">
          <div class="service-head">
            <div class="service-name">
              <span>📓 Notion</span>
            </div>
            <span class="service-status status-warn" id="status-notion">Checking...</span>
          </div>
          <div class="service-desc" id="desc-notion">Read and write pages & databases.</div>
          <div class="btn-group">
            <button class="btn-primary" onclick="connectNotion()">1-Click Connect</button>
            <button class="btn-outline" onclick="testNotion()">Test</button>
          </div>
        </div>

        <!-- Email / SMTP Card -->
        <div class="service-card" id="card-smtp">
          <div class="service-head">
            <div class="service-name">
              <span>✉️ Gmail SMTP</span>
            </div>
            <span class="service-status status-warn" id="status-smtp">Checking...</span>
          </div>
          <div class="service-desc" id="desc-smtp">Send direct emails via TLS.</div>
          <div class="btn-group">
            <button class="btn-outline" onclick="testSMTP()">Send Test Email</button>
          </div>
        </div>

        <!-- Gemini AI Engine -->
        <div class="service-card" id="card-gemini">
          <div class="service-head">
            <div class="service-name">
              <span>🧠 Gemini AI</span>
            </div>
            <span class="service-status status-ok" id="status-gemini">Active</span>
          </div>
          <div class="service-desc">gemini-3.5-flash-lite (Free Tier)</div>
        </div>

      </div>

      <!-- Live Hardware Telemetry -->
      <div>
        <div class="section-title">Hardware Telemetry</div>
        
        <div class="meter-box">
          <div class="meter-label">
            <span>CPU Load</span>
            <span id="txt-cpu">0%</span>
          </div>
          <div class="meter-bar-bg"><div class="meter-bar-fill" id="bar-cpu" style="width: 0%;"></div></div>
        </div>

        <div class="meter-box">
          <div class="meter-label">
            <span>RAM Usage</span>
            <span id="txt-ram">0% (0MB / 0MB)</span>
          </div>
          <div class="meter-bar-bg"><div class="meter-bar-fill" id="bar-ram" style="width: 0%;"></div></div>
        </div>

        <div class="meter-box">
          <div class="meter-label">
            <span>Storage (C:)</span>
            <span id="txt-disk">0% (0GB / 0GB)</span>
          </div>
          <div class="meter-bar-bg"><div class="meter-bar-fill" id="bar-disk" style="width: 0%;"></div></div>
        </div>

      </div>

    </div>

    <!-- Main Chat Workspace -->
    <div class="chat-container">
      
      <!-- Messages List -->
      <div class="chat-messages" id="messages-list">
        <div class="message msg-assistant">
          <div class="msg-avatar avatar-oryn">O</div>
          <div class="msg-bubble bubble-assistant">
            Hello Jayap. I am Oryn, your autonomous system agent. What needs doing today?
          </div>
        </div>
      </div>

      <!-- Quick Chips -->
      <div class="suggestions">
        <div class="chip" onclick="quickSend('Check my live system hardware status')">⚡ Check Hardware</div>
        <div class="chip" onclick="quickSend('Set a timer for 1 minute')">⏱️ Set 1m Timer</div>
        <div class="chip" onclick="quickSend('Open Windows Clock app')">⏰ Open Clock</div>
        <div class="chip" onclick="quickSend('List files on my Google Drive')">📁 Search Google Drive</div>
        <div class="chip" onclick="quickSend('Search recent tech news')">🌐 Search Web</div>
      </div>

      <!-- Chat Input Area -->
      <div class="chat-input-area">
        <div class="input-wrapper">
          <input type="text" id="user-input" placeholder="Ask Oryn anything, control your laptop, or run system tasks..." onkeydown="handleKey(event)" autofocus>
          <button class="icon-btn" title="Voice Input" onclick="toggleVoiceInput()" id="mic-btn">🎙️</button>
          <button class="send-btn" onclick="sendMessage()">Send</button>
        </div>
      </div>

    </div>

  </div>

  <script>
    // Poll status on load
    async function updateStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Update hardware
        if (data.system) {
          const s = data.system;
          document.getElementById('txt-cpu').innerText = `${s.cpu_usage || 0}%`;
          document.getElementById('bar-cpu').style.width = `${Math.min(100, s.cpu_usage || 0)}%`;

          document.getElementById('txt-ram').innerText = `${s.ram_percent || 0}% (${Math.round(s.ram_used_mb || 0)}MB / ${Math.round(s.ram_total_mb || 0)}MB)`;
          document.getElementById('bar-ram').style.width = `${Math.min(100, s.ram_percent || 0)}%`;

          document.getElementById('txt-disk').innerText = `${s.disk_percent || 0}% (${Math.round(s.disk_used_gb || 0)}GB / ${Math.round(s.disk_total_gb || 0)}GB)`;
          document.getElementById('bar-disk').style.width = `${Math.min(100, s.disk_percent || 0)}%`;
        }

        // Update auth
        if (data.auth) {
          const a = data.auth;
          
          // Drive
          const driveBadge = document.getElementById('status-drive');
          if (a.drive.status === 'connected_permanent') {
            driveBadge.className = 'service-status status-ok';
            driveBadge.innerText = 'Connected';
            document.getElementById('desc-drive').innerText = 'Permanent OAuth Active';
          } else if (a.drive.status === 'connected_temporary') {
            driveBadge.className = 'service-status status-warn';
            driveBadge.innerText = 'Temporary';
            document.getElementById('desc-drive').innerText = 'Token expires soon';
          } else {
            driveBadge.className = 'service-status status-err';
            driveBadge.innerText = 'Disconnected';
            document.getElementById('desc-drive').innerText = 'Click 1-Click Connect to link Drive.';
          }

          // Notion
          const notionBadge = document.getElementById('status-notion');
          if (a.notion.status === 'connected') {
            notionBadge.className = 'service-status status-ok';
            notionBadge.innerText = 'Connected';
          } else {
            notionBadge.className = 'service-status status-err';
            notionBadge.innerText = 'Disconnected';
          }

          // SMTP
          const smtpBadge = document.getElementById('status-smtp');
          if (a.smtp.status === 'configured') {
            smtpBadge.className = 'service-status status-ok';
            smtpBadge.innerText = 'Active';
            document.getElementById('desc-smtp').innerText = a.smtp.user;
          } else {
            smtpBadge.className = 'service-status status-warn';
            smtpBadge.innerText = 'Unset';
          }
        }
      } catch (e) {
        console.error("Status poll error:", e);
      }
    }

    setInterval(updateStatus, 3000);
    updateStatus();

    // 1-Click Connect Google Drive
    async function connectDrive() {
      alert("Opening Google sign-in in your browser. Click Allow to connect Google Drive.");
      await fetch('/api/auth/drive', { method: 'POST' });
    }

    // 1-Click Connect Notion
    async function connectNotion() {
      const res = await fetch('/api/auth/notion', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'missing_credentials') {
        const token = prompt("To connect Notion, enter your Notion Token (secret_... or ntn_... from https://www.notion.so/my-integrations):");
        if (token && token.trim()) {
          const saveRes = await fetch('/api/save_token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'NOTION_TOKEN', value: token.trim() })
          });
          const saveData = await saveRes.json();
          alert(saveData.ok ? "✅ Notion connected successfully!" : "Error: " + saveData.error);
          updateStatus();
        }
      } else {
        alert("Opening Notion sign-in in your browser. Select your workspace and click Allow.");
      }
    }

    // Test Google Drive
    async function testDrive() {
      const res = await fetch('/api/test/drive', { method: 'POST' });
      const data = await res.json();
      alert(data.ok ? "✅ Google Drive is fully working!" : "⚠️ Drive error: " + data.details);
      updateStatus();
    }

    // Test SMTP
    async function testSMTP() {
      alert("Sending test email to your configured address...");
      const res = await fetch('/api/test/smtp', { method: 'POST' });
      const data = await res.json();
      alert(data.ok ? "✅ Test email sent successfully!" : "⚠️ SMTP error: " + data.details);
    }

    // Chat handling
    function appendMessage(role, text, actions = []) {
      const list = document.getElementById('messages-list');
      const msgDiv = document.createElement('div');
      msgDiv.className = `message msg-${role}`;
      
      const avatar = document.createElement('div');
      avatar.className = `msg-avatar avatar-${role === 'user' ? 'user' : 'oryn'}`;
      avatar.innerText = role === 'user' ? 'U' : 'O';

      const bubble = document.createElement('div');
      bubble.className = `msg-bubble bubble-${role}`;

      if (actions && actions.length > 0) {
        actions.forEach(a => {
          const badge = document.createElement('div');
          badge.className = 'tool-badge';
          badge.innerText = `⚡ Action: ${a.action}`;
          bubble.appendChild(badge);
        });
      }

      const content = document.createElement('div');
      content.innerText = text;
      bubble.appendChild(content);

      msgDiv.appendChild(avatar);
      msgDiv.appendChild(bubble);
      list.appendChild(msgDiv);
      list.scrollTop = list.scrollHeight;
    }

    async function sendMessage() {
      const input = document.getElementById('user-input');
      const text = input.value.trim();
      if (!text) return;

      appendMessage('user', text);
      input.value = '';

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        appendMessage('assistant', data.reply || 'No response', data.actions);
      } catch (e) {
        appendMessage('assistant', 'Error sending message: ' + e);
      }
    }

    function quickSend(text) {
      document.getElementById('user-input').value = text;
      sendMessage();
    }

    function handleKey(e) {
      if (e.key === 'Enter') {
        sendMessage();
      }
    }

    async function clearChat() {
      await fetch('/api/clear', { method: 'POST' });
      const list = document.getElementById('messages-list');
      list.innerHTML = `
        <div class="message msg-assistant">
          <div class="msg-avatar avatar-oryn">O</div>
          <div class="msg-bubble bubble-assistant">
            Conversation cleared. How can I help you?
          </div>
        </div>
      `;
    }

    // Simple Web Speech API voice input
    function toggleVoiceInput() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        alert("Speech recognition is not supported in this browser.");
        return;
      }
      const recognition = new SpeechRecognition();
      recognition.onstart = () => {
        document.getElementById('mic-btn').innerText = '🔴';
      };
      recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        document.getElementById('user-input').value = transcript;
        sendMessage();
      };
      recognition.onend = () => {
        document.getElementById('mic-btn').innerText = '🎙️';
      };
      recognition.start();
    }
  </script>
</body>
</html>
"""


def start_ui_server(port=UI_PORT, open_browser=True):
    """Start the UI HTTP server and optionally open the dashboard."""
    server = ThreadingHTTPServer(("localhost", port), OrynUIHandler)
    url = f"http://localhost:{port}"
    print(f"\n\033[34m[oryn-ui]\033[0m live at {url}")

    if open_browser:
        # Check for Edge app mode for native desktop experience
        edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if os.path.exists(edge_path):
            try:
                subprocess.Popen([edge_path, f"--app={url}"])
            except Exception:
                webbrowser.open(url)
        else:
            webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\033[90m[oryn-ui] shutting down server...\033[0m")
        server.server_close()


if __name__ == "__main__":
    start_ui_server()
