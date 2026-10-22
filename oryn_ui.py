"""
Oryn UI Launcher.
Starts the lightweight Oryn Desktop & Web Dashboard on http://localhost:4242.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orc.ui_server import start_ui_server

if __name__ == "__main__":
    start_ui_server()
