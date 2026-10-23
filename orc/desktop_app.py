"""Launch the Oryn dashboard as a desktop application.

Preference order:

1. **pywebview** — a real native window with its own taskbar entry (installed
   with ``requirements.txt``; uses the Edge WebView2 runtime on Windows).
2. **Edge / Chrome app mode** — a chromeless window from the browser that is
   already on the machine.
3. **Default browser** — always works.

``python oryn_ui.py --web`` skips straight to the browser. The HTTP server is
always started here, so every path shows the same live dashboard.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ORYN_ROOT = Path(__file__).resolve().parent.parent
WINDOW_SIZE = (1360, 880)


class _Color:
    BLUE = "\033[34m"
    DIM = "\033[90m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RESET = "\033[0m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    stream = sys.stdout
    try:
        return bool(stream) and stream.isatty()
    except (AttributeError, ValueError):
        return False


def _paint(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{_Color.RESET}"


def _edge_candidates() -> list[str]:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    found = shutil.which("msedge")
    if found:
        candidates.insert(0, found)
    return [path for path in candidates if os.path.exists(path)]


def _chrome_candidates() -> list[str]:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        candidates.insert(0, found)
    return [path for path in candidates if path and os.path.exists(path)]


def _app_mode_command(executable: str, url: str) -> list[str]:
    return [
        executable,
        f"--app={url}",
        f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def _launch_app_window(url: str) -> bool:
    """Open a chromeless Edge/Chrome window. Returns True if one started."""
    for executable in _edge_candidates() + _chrome_candidates():
        try:
            subprocess.Popen(
                _app_mode_command(executable, url),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            continue
    return False


def _launch_webview(url: str) -> bool:
    """Open the dashboard in a native pywebview window, if available."""
    try:
        import webview  # type: ignore
    except Exception:
        return False

    try:
        webview.create_window(
            "Oryn",
            url,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=(980, 640),
            confirm_close=False,
            background_color="#080b10",
            text_select=True,
        )
        webview.start()
        return True
    except Exception as exc:  # missing WebView2 runtime, GTK, etc.
        print(_paint(f"[oryn-ui] native window unavailable: {exc}", _Color.YELLOW))
        return False


def _serve_in_background(port: int):
    from orc.ui_server import create_server

    server = create_server(port)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="oryn-ui-http")
    thread.start()
    return server, f"http://localhost:{int(server.server_address[1])}"


def _stay_alive(server) -> None:
    print(_paint("[oryn-ui] press Ctrl+C to stop the dashboard.", _Color.DIM))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(_paint("\n[oryn-ui] shutting down…", _Color.DIM))
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oryn-ui", description="Launch the Oryn dashboard.")
    parser.add_argument("--port", type=int, default=4242, help="first port to try (default: 4242)")
    parser.add_argument("--web", action="store_true", help="open in the default browser instead of an app window")
    args = parser.parse_args(argv)

    server, url = _serve_in_background(args.port)
    print(f"{_paint('[oryn-ui]', _Color.BLUE)} live at {url}")

    if args.web:
        webbrowser.open(url)
        _stay_alive(server)
        return 0

    if _launch_webview(url):
        server.shutdown()
        server.server_close()
        return 0

    if _launch_app_window(url):
        print(_paint("[oryn-ui] opened in an app window.", _Color.DIM))
        _stay_alive(server)
        return 0

    print(_paint("[oryn-ui] no app window available; opening your browser.", _Color.DIM))
    webbrowser.open(url)
    _stay_alive(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
