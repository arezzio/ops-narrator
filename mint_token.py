"""Mint an MCP bearer token for the official Splunk MCP Server and store it in .env.

The official server (Splunkbase app 7931) only accepts tokens with audience ``mcp`` that are
RSA-encrypted with the server's public key (`require_encrypted_token = true`). A *normal*
Splunk token (Settings > Tokens, or POST /services/authorization/tokens) has the wrong
audience and is rejected with HTTP 403 "Invalid token audience". The app exposes
``/services/mcp_token`` which mints + encrypts the correct token; this script calls it (using
SPLUNK_USERNAME/SPLUNK_PASSWORD) and writes the result as SPLUNK_MCP_TOKEN in .env.

Tokens expire — re-run this when the official backend starts returning 401/403.

Usage:
    uv run python mint_token.py                 # default +90d expiry
    uv run python mint_token.py --expires +30d
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import httpx
from dotenv import load_dotenv

ENV = pathlib.Path(__file__).parent / ".env"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expires", default="+90d", help="token lifetime, e.g. +90d, +1h")
    args = ap.parse_args()

    load_dotenv(ENV)
    host = os.environ.get("SPLUNK_HOST", "localhost:8089")
    user = os.environ.get("SPLUNK_USERNAME")
    pw = os.environ.get("SPLUNK_PASSWORD")
    if not (user and pw):
        sys.exit("Need SPLUNK_USERNAME and SPLUNK_PASSWORD in .env to mint a token.")

    verify = os.environ.get("VERIFY_SSL", "false").lower() in ("1", "true", "yes")
    r = httpx.Client(verify=verify, timeout=20).get(
        f"https://{host}/services/mcp_token",
        auth=(user, pw),
        params={"username": user, "expires_on": args.expires, "output_mode": "json"},
    )
    if r.status_code != 200:
        sys.exit(f"/services/mcp_token failed: {r.status_code} {r.text[:200]}")
    token = r.json().get("token")
    if not token:
        sys.exit(f"no token in response: {r.text[:200]}")

    txt = ENV.read_text() if ENV.exists() else ""
    if re.search(r"(?m)^SPLUNK_MCP_TOKEN=.*$", txt):
        txt = re.sub(r"(?m)^SPLUNK_MCP_TOKEN=.*$", "SPLUNK_MCP_TOKEN=" + token, txt)
    else:
        sep = "" if (not txt or txt.endswith("\n")) else "\n"
        txt += f"{sep}SPLUNK_MCP_TOKEN={token}\n"
    ENV.write_text(txt)
    print(f"Wrote SPLUNK_MCP_TOKEN to {ENV} (len {len(token)}, expires {args.expires}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
