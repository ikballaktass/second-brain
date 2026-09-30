"""One-time Google Calendar authorization (read-only).

Run on a machine with a browser:

    PYTHONPATH=src python scripts/google_auth.py

It reads the OAuth client file at GOOGLE_CREDENTIALS_PATH, opens the Google
consent screen, and writes the token to GOOGLE_TOKEN_PATH (mode 0600). Copy
that token file to the server; it must never go into the vault or into git.

On a headless server use --no-browser and forward the printed port over SSH.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from second_brain.config import Config
from second_brain.tools.calendar import DEFAULT_TOKEN_PATH, SCOPES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-browser", action="store_true", help="print the URL instead")
    parser.add_argument("--port", type=int, default=0, help="local callback port (0 = any)")
    args = parser.parse_args()

    client_path = Path(Config.get("GOOGLE_CREDENTIALS_PATH", "secrets/google_credentials.json"))
    token_path = Path(Config.get("GOOGLE_TOKEN_PATH", DEFAULT_TOKEN_PATH)).expanduser()
    if not client_path.is_file():
        print(f"OAuth client file not found: {client_path}", file=sys.stderr)
        print("Download it from Google Cloud Console (Desktop app client).", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    creds = flow.run_local_server(port=args.port, open_browser=not args.no_browser)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    os.chmod(token_path, 0o600)  # also tighten a pre-existing file
    print(f"Token saved to {token_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
