"""Notion authentication helper for Oryn.

Option 1 (and the default when run without arguments) is the 1-click flow: a
throwaway localhost server catches Notion's redirect, exchanges the code for an
access token, and writes it to ``.env``.

Usage:
    python auth_notion.py           # 1-click browser login
    python auth_notion.py menu      # full menu (credentials, test, status)
    python auth_notion.py test      # verify the current connection
"""

import os
import sys
from pathlib import Path

import httpx

ORYN_ROOT = Path(__file__).resolve().parent
if str(ORYN_ROOT) not in sys.path:
    sys.path.insert(0, str(ORYN_ROOT))

from dotenv import load_dotenv  # noqa: E402

ENV_FILE = ORYN_ROOT / ".env"
load_dotenv(ENV_FILE, override=True)

from orc import oauth  # noqa: E402
from orc.notion_auth import (  # noqa: E402
    NOTION_VERSION,
    NotionAuthError,
    is_configured,
    login,
    workspace_name,
)

API = "https://api.notion.com/v1"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def test_connection() -> bool:
    """Verify the saved token against Notion's users endpoint."""
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    if not token:
        print("\n[NOTICE] No NOTION_TOKEN found in .env.")
        print("Run the 1-click login first: python auth_notion.py login")
        return False

    print("\nChecking the Notion token...")
    try:
        response = httpx.get(f"{API}/users/me", headers=_headers(token), timeout=10)
    except Exception as exc:  # noqa: BLE001 - reported to the user
        print(f"[ERROR] Could not reach Notion: {exc}")
        return False

    if response.status_code == 200:
        bot = response.json()
        name = bot.get("name") or bot.get("bot", {}).get("owner", {}).get("workspace_name") or "bot"
        workspace = workspace_name()
        label = f" in {workspace}" if workspace else ""
        print(f"[SUCCESS] Connected to Notion as {name}{label}.")
        return True
    if response.status_code == 401:
        print("[FAILED] The Notion token is invalid or was revoked.")
        print("Run the 1-click login again: python auth_notion.py login")
        return False
    print(f"[FAILED] Notion returned HTTP {response.status_code}: {response.text[:300]}")
    return False


def show_status() -> None:
    print("\nNotion configuration:")
    print(f"  NOTION_CLIENT_ID      {'set' if os.environ.get('NOTION_CLIENT_ID') else 'missing'}")
    print(f"  NOTION_CLIENT_SECRET  {'set' if os.environ.get('NOTION_CLIENT_SECRET') else 'missing'}")
    print(f"  NOTION_TOKEN          {'set' if is_configured() else 'missing'}")
    print(f"  workspace             {workspace_name() or 'unknown'}")
    if not os.environ.get("NOTION_CLIENT_ID"):
        print("\n  Create a public integration at https://www.notion.so/my-integrations")
        print(f"  and add {oauth.redirect_uri()} as a redirect URI (option 2).")


def run_login() -> int:
    print("=" * 62)
    print("        ORYN NOTION - 1-CLICK BROWSER LOGIN")
    print("=" * 62)
    print("\nOryn will open Notion in your default browser.")
    print("Pick a workspace, click Allow, and the token is saved for good.\n")
    try:
        result = login(announce=print)
    except NotionAuthError as exc:
        print(f"\n[FAILED] {exc}")
        print("Nothing was changed.")
        return 1
    except KeyboardInterrupt:
        print("\n[CANCELLED] Login aborted. Nothing was changed.")
        return 130

    print("\n[SUCCESS] NOTION_TOKEN saved to .env.")
    if result.workspace_name:
        print(f"Workspace: {result.workspace_name}")
    print("\nRemember to share the pages you want Oryn to see with the integration.")
    print("Verifying the new connection...")
    test_connection()
    print()
    return 0


def enter_credentials() -> bool:
    print("\n--- Notion Public Integration ---")
    print("Create one at https://www.notion.so/my-integrations")
    print(f"Add this redirect URI: {oauth.redirect_uri()}\n")
    client_id = input("Enter NOTION_CLIENT_ID: ").strip()
    client_secret = input("Enter NOTION_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        print("[CANCELLED] Missing Client ID or Secret.")
        return False

    oauth.update_env_file(
        {"NOTION_CLIENT_ID": client_id, "NOTION_CLIENT_SECRET": client_secret},
        env_file=ENV_FILE,
    )
    os.environ["NOTION_CLIENT_ID"] = client_id
    os.environ["NOTION_CLIENT_SECRET"] = client_secret
    print("\n[SAVED] Client credentials written to .env.")
    return True


def menu() -> int:
    print("=" * 62)
    print("          ORYN NOTION AUTHENTICATION SETUP")
    print("=" * 62)
    print("\nChoose an authentication method:\n")
    print("  [1] 1-Click Login (opens your browser - recommended)")
    print("  [2] Enter Notion integration Client ID & Secret, then log in")
    print("  [3] Test current Notion connection")
    print("  [4] Show configuration status")
    print("  [5] Exit\n")

    choice = input("Select [1/2/3/4/5]: ").strip() or "1"

    if choice == "1":
        return run_login()
    if choice == "2":
        if not enter_credentials():
            return 1
        return run_login()
    if choice == "3":
        return 0 if test_connection() else 1
    if choice == "4":
        show_status()
        return 0
    print("\nExiting setup.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = [a.lower() for a in (sys.argv[1:] if argv is None else argv)]
    command = args[0] if args else "login"
    if command in {"login", "auth", "1"}:
        return run_login()
    if command in {"menu", "--menu", "m"}:
        return menu()
    if command in {"test", "status-check"}:
        return 0 if test_connection() else 1
    if command in {"status", "info"}:
        show_status()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
