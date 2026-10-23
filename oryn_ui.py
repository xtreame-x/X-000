"""
Oryn UI Launcher.

Starts the Oryn dashboard on http://localhost:4242 and opens it as its own
desktop window. Falls back to an Edge/Chrome app window, then to your browser.

    python oryn_ui.py            native window
    python oryn_ui.py --web      default browser
    python oryn_ui.py --port 5050
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orc.desktop_app import main

if __name__ == "__main__":
    raise SystemExit(main())
