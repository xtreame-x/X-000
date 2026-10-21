"""
Google Drive Authentication Helper for Oryn.
Guides the user through authenticating their Google Drive and saves credentials to .env.
"""

import os
import sys
import json
import webbrowser
import httpx
from pathlib import Path
from dotenv import load_dotenv

ORYN_ROOT = Path(__file__).resolve().parent
ENV_FILE = ORYN_ROOT / ".env"

load_dotenv(ENV_FILE)

def update_env(key: str, value: str):
    """Safely append or update a key in .env."""
    lines = []
    found = False
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    os.environ[key] = value


def test_token(token: str) -> bool:
    """Test if a Google Drive access token is valid."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get("https://www.googleapis.com/drive/v3/files?pageSize=1", headers=headers, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"[ERROR] Google Drive API returned HTTP {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Failed to connect to Google Drive: {e}")
        return False


def main():
    print("=" * 60)
    print("       ORYN GOOGLE DRIVE AUTHENTICATION SETUP")
    print("=" * 60)
    print("\nGoogle Drive requires OAuth permission to create and manage files.\n")
    print("Choose an authentication method:")
    print("  [1] Quick Token (Via Google OAuth Playground - 30 seconds)")
    print("  [2] Permanent Setup (Using Google Cloud Client ID & Secret)")
    print("  [3] Test current Google Drive connection")
    print("  [4] Exit\n")

    choice = input("Select [1/2/3/4]: ").strip()

    if choice == "1":
        print("\n--- Method 1: Google OAuth Playground ---")
        print("1. Opening Google OAuth 2.0 Playground in your browser...")
        playground_url = (
            "https://developers.google.com/oauthplayground/#step1"
            "&scopes=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file"
        )
        webbrowser.open(playground_url)
        print("2. On the webpage:")
        print("   a. Under 'Select & authorize APIs', find 'Drive API v3' and select:")
        print("      https://www.googleapis.com/auth/drive.file")
        print("   b. Click the blue 'Authorize APIs' button.")
        print("   c. Sign in with your Google account and click Continue/Allow.")
        print("   d. Under Step 2, click 'Exchange authorization code for tokens'.")
        print("   e. Copy the generated 'Access token' (starts with ya29...).\n")

        token = input("Paste your Access Token here: ").strip()
        if not token:
            print("[CANCELLED] No token entered.")
            return

        print("\nTesting token with Google Drive API...")
        if test_token(token):
            update_env("GOOGLE_ACCESS_TOKEN", token)
            print("\n[SUCCESS] Token verified and saved to .env!")
            print("Oryn can now create, search, and manage files on your Google Drive.\n")
        else:
            print("\n[FAILED] Token could not be verified. Please check the token and try again.")

    elif choice == "2":
        print("\n--- Method 2: Google Cloud Console OAuth ---")
        print("You will need a Client ID and Client Secret from https://console.cloud.google.com/apis/credentials")
        client_id = input("Enter GOOGLE_CLIENT_ID: ").strip()
        client_secret = input("Enter GOOGLE_CLIENT_SECRET: ").strip()
        if not client_id or not client_secret:
            print("[CANCELLED] Missing Client ID or Secret.")
            return

        update_env("GOOGLE_CLIENT_ID", client_id)
        update_env("GOOGLE_CLIENT_SECRET", client_secret)

        print("\nNow generate an authorization code or refresh token.")
        print("Visit Google OAuth Playground, click the gear icon (top right), check")
        print("'Use your own OAuth credentials', enter your Client ID & Secret, and authorize.")
        refresh_token = input("Enter GOOGLE_REFRESH_TOKEN: ").strip()
        if refresh_token:
            update_env("GOOGLE_REFRESH_TOKEN", refresh_token)
            print("\n[SUCCESS] Client credentials and Refresh Token saved to .env!")
        else:
            print("[SAVED] Saved Client ID and Secret. You can add GOOGLE_REFRESH_TOKEN later.")

    elif choice == "3":
        current_token = os.environ.get("GOOGLE_ACCESS_TOKEN")
        if not current_token:
            print("\n[NOTICE] No GOOGLE_ACCESS_TOKEN currently set in .env.")
        else:
            print("\nTesting existing GOOGLE_ACCESS_TOKEN...")
            if test_token(current_token):
                print("[SUCCESS] Your Google Drive connection is active and working!")
            else:
                print("[FAILED] Existing token is invalid or expired.")

    else:
        print("\nExiting setup.")

if __name__ == "__main__":
    main()
