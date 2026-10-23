"""Google Drive authentication helper for Oryn.

Option 1 (and the default when run without arguments) is the 1-click flow: a
throwaway localhost server catches Google's redirect, exchanges the code for a
refresh token, and writes it to ``.env``. No OAuth Playground, no copy-pasting.

Usage:
    python auth_drive.py            # 1-click browser login
    python auth_drive.py menu       # full menu (manual credentials, test, exit)
    python auth_drive.py test       # verify the current connection
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

from orc.drive_auth import (  # noqa: E402
    ENV_FILE as AUTH_ENV_FILE,
    DriveAuthError,
    has_refresh_token,
    is_configured,
    login,
    update_env_file,
)


def update_env(key: str, value: str) -> None:
    from orc.oauth import persist_env
    persist_env({key: value})


def drive_test_token(token: str) -> bool:
    """Test if a Google Drive bearer token is valid."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/files?pageSize=1",
            headers=headers,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the user
        print(f"[ERROR] Failed to connect to Google Drive: {exc}")
        return False

    if resp.status_code == 200:
        return True
    print(f"[ERROR] Google Drive API returned HTTP {resp.status_code}: {resp.text[:300]}")
    return False


def _current_access_token() -> tuple[str | None, str | None]:
    """Return ``(token, error)`` using Oryn's refresh-aware token resolver."""
    try:
        from agent.config import NotConfigured, Settings
        from agent.tools.drive import access_token_for

        return access_token_for(Settings.load(ORYN_ROOT)), None
    except NotConfigured as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - reported to the user
        return None, f"{type(exc).__name__}: {exc}"


def test_connection() -> bool:
    """Verify the saved credentials against the Drive API."""
    if not is_configured():
        print("\n[NOTICE] No Google Drive credentials found in .env.")
        print("Run the 1-click login (python auth_drive.py login) first.")
        return False

    print("\nUsing stored credentials...")
    token, error = _current_access_token()
    if not token:
        print(f"[FAILED] {error}")
        return False
    if drive_test_token(token):
        mode = "refresh token (permanent)" if has_refresh_token() else "access token (expires hourly)"
        print(f"[SUCCESS] Google Drive connection is active via {mode}.")
        return True
    print("[FAILED] Existing credentials are invalid or expired.")
    print("Run the 1-click login to replace them: python auth_drive.py login")
    return False


def show_status() -> None:
    client_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    print("\nGoogle Drive configuration:")
    print(f"  GOOGLE_CLIENT_ID      {'set' if client_id else 'missing'}")
    print(f"  GOOGLE_CLIENT_SECRET  {'set' if os.environ.get('GOOGLE_CLIENT_SECRET') else 'missing'}")
    print(f"  GOOGLE_REFRESH_TOKEN  {'set (permanent)' if has_refresh_token() else 'missing'}")
    print(f"  GOOGLE_ACCESS_TOKEN   {'set' if os.environ.get('GOOGLE_ACCESS_TOKEN') else 'missing'}")
    if not client_id:
        print("\n  Add a Desktop OAuth client from")
        print("  https://console.cloud.google.com/apis/credentials using option 2.")


def run_login() -> int:
    print("=" * 62)
    print("      ORYN GOOGLE DRIVE - 1-CLICK BROWSER LOGIN")
    print("=" * 62)
    print("\nOryn will open Google in your default browser.")
    print("Sign in, click Allow, and Drive is connected permanently.\n")
    try:
        result = login(announce=print)
    except DriveAuthError as exc:
        print(f"\n[FAILED] {exc}")
        print("Nothing was changed.")
        return 1
    except KeyboardInterrupt:
        print("\n[CANCELLED] Login aborted. Nothing was changed.")
        return 130

    print("\n[SUCCESS] Credentials saved to .env.")
    if result.has_refresh_token:
        print("Oryn now has permanent Google Drive access - no more token updates.")
    else:
        print("Google did not return a refresh token, so access expires within an hour.")
    print("\nVerifying the new connection...")
    test_connection()
    print()
    return 0


def enter_credentials() -> tuple[str, str] | None:
    print("\n--- Google Cloud Console OAuth Client ---")
    print("Create a Desktop app client at:")
    print("  https://console.cloud.google.com/apis/credentials\n")
    print("Enable the Google Drive API first:")
    print("  https://console.cloud.google.com/apis/library/drive.googleapis.com\n")
    client_id = input("Enter GOOGLE_CLIENT_ID: ").strip()
    client_secret = input("Enter GOOGLE_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        print("[CANCELLED] Missing Client ID or Secret.")
        return None

    update_env_file(
        {"GOOGLE_CLIENT_ID": client_id, "GOOGLE_CLIENT_SECRET": client_secret},
        env_file=AUTH_ENV_FILE,
    )
    os.environ["GOOGLE_CLIENT_ID"] = client_id
    os.environ["GOOGLE_CLIENT_SECRET"] = client_secret
    print("\n[SAVED] Client credentials written to .env.")
    return client_id, client_secret


def menu() -> int:
    print("=" * 62)
    print("       ORYN GOOGLE DRIVE AUTHENTICATION SETUP")
    print("=" * 62)
    print("\nChoose an authentication method:\n")
    print("  [1] 1-Click Login (opens your browser - recommended)")
    print("  [2] Enter Google Cloud Client ID & Secret, then log in")
    print("  [3] Test current Google Drive connection")
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
