"""Ops Narrator — Section 1 tool wrappers.

Eight functions the agent can call. Six reach Splunk through an MCP server; two are pure
Python (`decode_payload`, `finalize_brief`).

Two selectable Splunk MCP backends (env `OPS_MCP_BACKEND`):

- ``official`` (default) — the **official Splunk MCP Server** (Splunkbase app 7931),
  installed *into* Splunk and exposed over streamable HTTP at
  ``https://<host>:8089/services/mcp`` with Bearer-token auth. SPL tool: ``run_splunk_query``.
  Requires the app installed in Splunk + a token in ``SPLUNK_MCP_TOKEN`` (see README).
- ``livehybrid`` — the community ``livehybrid/splunk-mcp`` stdio server, spawned as a
  subprocess. SPL tool: ``search_splunk``. Kept as a fallback for when the official app
  isn't yet installed. (It wants SPLUNK_HOST/SPLUNK_PORT split; `_splunk_env()` does that.)

See tool-menu.md for signatures/contracts and PROGRESS.md for gotchas.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()  # our .env: ANTHROPIC_API_KEY + SPLUNK_* creds

INDEX = os.environ.get("OPS_INDEX", "botsv3")

# official (HTTP) | livehybrid (stdio subprocess)
MCP_BACKEND = os.environ.get("OPS_MCP_BACKEND", "official").lower()


# --------------------------------------------------------------------------- #
# Backend: official Splunk MCP Server (streamable HTTP)
# --------------------------------------------------------------------------- #
def _official_url() -> str:
    """Endpoint of the in-Splunk MCP app: https://<host>:8089/services/mcp."""
    url = os.environ.get("SPLUNK_MCP_URL")
    if url:
        return url
    raw_host = os.environ.get("SPLUNK_HOST", "localhost:8089")
    if ":" not in raw_host:
        raw_host = f"{raw_host}:{os.environ.get('SPLUNK_PORT', '8089')}"
    scheme = os.environ.get("SPLUNK_SCHEME", "https")
    return f"{scheme}://{raw_host}/services/mcp"


def _official_token() -> str:
    tok = os.environ.get("SPLUNK_MCP_TOKEN") or os.environ.get("SPLUNK_TOKEN")
    if not tok:
        raise RuntimeError(
            "OPS_MCP_BACKEND=official needs a Splunk bearer token in SPLUNK_MCP_TOKEN "
            "(for https://<host>:8089/services/mcp). Install the Splunk MCP Server app, "
            "grant the mcp_tool_execute capability, and create a token — see README."
        )
    return tok


def _insecure_httpx_factory(headers=None, timeout=None, auth=None):
    """httpx client factory for streamablehttp_client; honors VERIFY_SSL (default off)
    so we can reach a local self-signed Splunk over TLS."""
    import httpx

    verify = os.environ.get("VERIFY_SSL", "false").lower() in ("1", "true", "yes")
    kwargs: dict[str, Any] = {"headers": headers, "auth": auth,
                              "follow_redirects": True, "verify": verify}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx.AsyncClient(**kwargs)


async def _acall_http(tool_name: str, arguments: dict) -> Any:
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {_official_token()}"}
    async with streamablehttp_client(
        _official_url(), headers=headers, httpx_client_factory=_insecure_httpx_factory
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


# --------------------------------------------------------------------------- #
# Backend: community livehybrid/splunk-mcp (stdio subprocess) — fallback
# --------------------------------------------------------------------------- #
SPLUNK_MCP_DIR = os.environ.get("SPLUNK_MCP_DIR", "/Users/arezziorietti/splunk-mcp")


def _splunk_env() -> dict[str, str]:
    """Build the subprocess env in the shape splunk-mcp expects (host/port split)."""
    raw_host = os.environ.get("SPLUNK_HOST", "localhost:8089")
    if ":" in raw_host:
        host, port = raw_host.split(":", 1)
    else:
        host, port = raw_host, os.environ.get("SPLUNK_PORT", "8089")

    env = dict(os.environ)  # inherit PATH etc. so `uv`/python resolve
    env.update(
        {
            "SPLUNK_HOST": host,
            "SPLUNK_PORT": port,
            "SPLUNK_SCHEME": os.environ.get("SPLUNK_SCHEME", "https"),
            "SPLUNK_USERNAME": os.environ["SPLUNK_USERNAME"],
            "SPLUNK_PASSWORD": os.environ["SPLUNK_PASSWORD"],
            "VERIFY_SSL": os.environ.get("VERIFY_SSL", "false"),
        }
    )
    env.pop("SPLUNK_TOKEN", None)  # bearer auth is broken with this SDK version
    return env


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=os.environ.get("UV_BIN", "uv"),
        args=["--directory", SPLUNK_MCP_DIR, "run", "python", "splunk_mcp.py", "stdio"],
        env=_splunk_env(),
    )


async def _acall_stdio(tool_name: str, arguments: dict) -> Any:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


# --------------------------------------------------------------------------- #
# Dispatch + result normalization (shared by both backends)
# --------------------------------------------------------------------------- #
async def _acall(tool_name: str, arguments: dict) -> Any:
    if MCP_BACKEND == "official":
        return await _acall_http(tool_name, arguments)
    return await _acall_stdio(tool_name, arguments)


def _texts(result: Any) -> list[str]:
    out = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            out.append(text)
    return out


def _rows_from_result(result: Any) -> Any:
    """Normalize a FastMCP CallToolResult into plain Python.

    Handles both structured output (`structuredContent`) and the older behavior where a
    List[Dict] return becomes one JSON TextContent block per row.
    """
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool error: {' '.join(_texts(result)) or 'unknown'}")

    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        return sc["result"] if set(sc.keys()) == {"result"} else sc

    parsed: list[Any] = []
    for text in _texts(result):
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            parsed.append(text)
    if len(parsed) == 1 and isinstance(parsed[0], list):
        return parsed[0]
    return parsed


def run_search(spl: str, earliest_time: str, latest_time: str, max_results: int = 500) -> dict:
    """Execute SPL via the active MCP backend and return the standard tool shape.

    Picks the right tool name + argument names for the backend: ``run_splunk_query``
    (query/earliest_time/latest_time) for the official server, ``search_splunk``
    (search_query/.../max_results) for livehybrid.
    """
    if MCP_BACKEND == "official":
        tool_name = "run_splunk_query"
        arguments = {
            "query": spl,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
        }
    else:
        tool_name = "search_splunk"
        arguments = {
            "search_query": spl,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "max_results": max_results,
        }
    result = asyncio.run(_acall(tool_name, arguments))
    rows = _rows_from_result(result)
    if rows is None:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]
    return {
        "rows": rows,
        "row_count": len(rows),
        "spl": spl,
        "earliest_time": earliest_time,
        "latest_time": latest_time,
    }


# --------------------------------------------------------------------------- #
# 1. decode_payload  (pure Python)
# --------------------------------------------------------------------------- #
_ENC_RE = re.compile(r"-(?:encodedcommand|enc|ec|e)\s+([A-Za-z0-9+/=]+)", re.IGNORECASE)
_FLAG_RE = re.compile(r"(?<!\S)(-[A-Za-z]+)")
_URI_RE = re.compile(r"https?://[^\s\"'<>)]+|/[A-Za-z0-9_./-]+\.(?:php|aspx?|jsp|html?)")
_COOKIE_RE = re.compile(r"[A-Za-z0-9_]{4,12}=[A-Za-z0-9+/]{16,}={0,2}")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_NOTABLE = [
    "amsiInitFailed", "ServerCertificateValidationCallback", "FromBase64String",
    "IEX", "Invoke-Expression", "ScriptBlock", "System.Net.WebClient",
    "DownloadString", "ServicePointManager", "RC4", "GetField", "Bypass",
]


def _b64decode(blob: str) -> bytes:
    blob = blob.strip()
    return base64.b64decode(blob + "=" * ((-len(blob)) % 4))


def _b64_to_text(blob: str) -> str:
    raw = _b64decode(blob)
    # PowerShell -enc payloads are UTF-16LE; fall back to UTF-8 for nested blobs.
    if b"\x00" in raw[:64]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def decode_payload(command_line: str) -> dict:
    """Decode an encoded PowerShell command line.

    Strips launcher flags, base64-decodes the -enc blob, converts UTF-16LE -> UTF-8,
    and decodes one level of nested base64 if present. Returns cleartext + extracted
    indicators (URIs, launcher flags, cookies, notable strings).
    """
    indicators = {
        "uris": [],
        "launcher_flags": _FLAG_RE.findall(command_line),
        "cookies": [],
        "notable_strings": [],
    }

    m = _ENC_RE.search(command_line)
    if not m:
        return {
            "plaintext": "",
            "layers": 0,
            "nested_base64_found": False,
            "indicators": indicators,
            "error": "no -enc base64 blob found in command line",
        }

    blob = m.group(1)
    layer1 = _b64_to_text(blob)
    layers = [layer1]
    nested_found = False

    for cand in _B64_RE.findall(layer1):
        if cand == blob:
            continue
        try:
            decoded = _b64_to_text(cand)
        except Exception:
            continue
        # Accept only if it looks like text (mostly printable)
        printable = sum(c.isprintable() or c in "\r\n\t" for c in decoded)
        if decoded and printable / len(decoded) > 0.8:
            layers.append(decoded)
            nested_found = True
            break

    full = "\n\n--- nested layer ---\n\n".join(layers)
    indicators["uris"] = sorted(set(_URI_RE.findall(full)))
    indicators["cookies"] = sorted(set(_COOKIE_RE.findall(full)))
    indicators["notable_strings"] = [s for s in _NOTABLE if s.lower() in full.lower()]

    return {
        "plaintext": full,
        "layers": len(layers),
        "nested_base64_found": nested_found,
        "indicators": indicators,
    }


# --------------------------------------------------------------------------- #
# 2. splunk_search  (generic escape hatch)
# --------------------------------------------------------------------------- #
def splunk_search(spl: str, earliest_time: str, latest_time: str) -> dict:
    """Run an arbitrary SPL search over a time window."""
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 3. find_process_ancestry  (4688 process tree on one host)
# --------------------------------------------------------------------------- #
def find_process_ancestry(host: str, earliest_time: str, latest_time: str) -> dict:
    spl = (
        f'index={INDEX} sourcetype=WinEventLog source="WinEventLog:Security" '
        f'EventCode=4688 host={host} '
        "| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line "
        "| sort _time"
    )
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 4. find_pattern_across_hosts  (spread)
# --------------------------------------------------------------------------- #
def find_pattern_across_hosts(
    command_pattern: str = "*-enc*", earliest_time: str = "", latest_time: str = ""
) -> dict:
    spl = (
        f'index={INDEX} sourcetype=WinEventLog source="WinEventLog:Security" '
        f'EventCode=4688 Process_Command_Line="{command_pattern}" '
        "| stats min(_time) as first_seen values(Account_Name) as users count by host "
        "| sort first_seen"
    )
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 5. check_unusual_parents  (UAC-bypass / escalation parents on one host)
# --------------------------------------------------------------------------- #
def check_unusual_parents(host: str, earliest_time: str, latest_time: str) -> dict:
    spl = (
        f'index={INDEX} sourcetype=WinEventLog source="WinEventLog:Security" '
        f'EventCode=4688 host={host} '
        'Creator_Process_Name IN ("*fodhelper.exe","*eventvwr.exe","*computerdefaults.exe","*sdclt.exe") '
        "| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line"
    )
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 6. find_lateral_execution  (remote-exec service hosts spawning processes)
# --------------------------------------------------------------------------- #
def find_lateral_execution(earliest_time: str, latest_time: str) -> dict:
    spl = (
        f'index={INDEX} sourcetype=WinEventLog source="WinEventLog:Security" '
        'EventCode=4688 Creator_Process_Name="*WmiPrvSE.exe" '
        "| table _time, host, Account_Name, New_Process_Name, Process_Command_Line "
        "| sort _time"
    )
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 7. trace_account_activity  (account logon scope)
# --------------------------------------------------------------------------- #
def trace_account_activity(account_name: str, earliest_time: str, latest_time: str) -> dict:
    # Auth events store the account as email/machine form (bstoll@froth.ly, HOST$), so a
    # field match on Account_Name misses; the bare token still matches the raw event text.
    spl = (
        f'index={INDEX} sourcetype=WinEventLog source="WinEventLog:Security" '
        f'(EventCode=4624 OR EventCode=4625) "{account_name}" '
        "| stats min(_time) as first values(ComputerName) as hosts count by EventCode"
    )
    return run_search(spl, earliest_time, latest_time)


# --------------------------------------------------------------------------- #
# 8. finalize_brief  (validate + return; full schema lands in Session 4)
# --------------------------------------------------------------------------- #
_REQUIRED_BRIEF_KEYS = ["headline", "summary", "findings"]


def finalize_brief(brief: dict) -> dict:
    """Submit the finished incident brief. Validates required keys and returns it."""
    if not isinstance(brief, dict):
        raise ValueError("brief must be a dict")
    missing = [k for k in _REQUIRED_BRIEF_KEYS if k not in brief]
    if missing:
        raise ValueError(f"brief missing required keys: {missing}")
    return {"status": "finalized", "brief": brief}
