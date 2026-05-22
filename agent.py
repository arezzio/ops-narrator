"""Ops Narrator — Section 2 agent loop.

A manual Anthropic agentic loop (claude-opus-4-7) that investigates a single triggering
alert by calling the eight tools in tools.py, then ends by calling `finalize_brief`.

Why a *manual* loop rather than the SDK tool runner: we need fine-grained control the
runner doesn't give us — a hard tool-iteration cap, a wall-clock cap, and per-call
latency/row_count capture that Session 3's trace logger will hang off of.

Model config notes (see PROGRESS.md gotcha #9):
- Opus 4.7 removed `thinking={"type":"enabled","budget_tokens":N}` — it 400s. The spec's
  "thinking budget 8000" maps to adaptive thinking + the `effort` parameter instead.
- `display:"summarized"` so thinking text is populated (Opus 4.7 omits it by default),
  which Session 3 will log.
- Sampling params (temperature/top_p/top_k) are also removed on 4.7 — we set none.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

import tools

load_dotenv()

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
import os

MODEL = os.environ.get("OPS_MODEL", "claude-opus-4-7")
EFFORT = os.environ.get("OPS_EFFORT", "high")  # low | medium | high | xhigh | max
MAX_TOKENS = int(os.environ.get("OPS_MAX_TOKENS", "16000"))
MAX_ITERS = int(os.environ.get("OPS_MAX_ITERS", "12"))  # model invocations
WALL_CLOCK_CAP = float(os.environ.get("OPS_WALL_CLOCK_CAP", "90"))  # seconds

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (_PROMPTS / "system.md").read_text()
USER_TEMPLATE = (_PROMPTS / "user_template.md").read_text()

_client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env


# --------------------------------------------------------------------------- #
# Tool registry: model-facing schemas + Python dispatch
# --------------------------------------------------------------------------- #
# Descriptions are anti-recall (no dataset/threat/host/outcome names) — see tool-menu.md.
_TIME_PROPS = {
    "earliest_time": {
        "type": "string",
        "description": "Window start, ISO-8601 (e.g. 2018-08-20T05:55:00).",
    },
    "latest_time": {
        "type": "string",
        "description": "Window end, ISO-8601 (e.g. 2018-08-20T06:30:00).",
    },
}

TOOL_DEFS: list[dict] = [
    {
        "name": "decode_payload",
        "description": (
            "Decode an encoded PowerShell command line. Strips launcher flags, "
            "base64-decodes the -enc blob, converts UTF-16LE to UTF-8, and decodes one "
            "level of nested base64 if present. Returns the cleartext script plus "
            "extracted indicators (URIs, launcher flags, cookies, notable strings)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command_line": {
                    "type": "string",
                    "description": "The full process command line containing the -enc blob.",
                }
            },
            "required": ["command_line"],
        },
    },
    {
        "name": "splunk_search",
        "description": (
            "Run an arbitrary SPL search over a time window. Use when no specialized tool "
            "fits — e.g. inspecting network/DNS/HTTP telemetry or following an ad-hoc lead. "
            "Do not put time bounds inside the SPL; pass them as the time parameters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spl": {"type": "string", "description": "The SPL search string."},
                **_TIME_PROPS,
            },
            "required": ["spl", "earliest_time", "latest_time"],
        },
    },
    {
        "name": "find_process_ancestry",
        "description": (
            "Reconstruct the process-creation sequence on a single host in time order: "
            "which parent processes spawned which children. Use to establish how a "
            "suspicious process was launched."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname to inspect."},
                **_TIME_PROPS,
            },
            "required": ["host", "earliest_time", "latest_time"],
        },
    },
    {
        "name": "find_pattern_across_hosts",
        "description": (
            "Find every host where a process command line matches a pattern within the "
            "window, with first-seen time, distinct accounts, and execution count per host. "
            "Use to gauge whether activity is isolated or spreading."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command_pattern": {
                    "type": "string",
                    "description": 'Wildcard command-line pattern. Defaults to "*-enc*".',
                },
                **_TIME_PROPS,
            },
            "required": ["earliest_time", "latest_time"],
        },
    },
    {
        "name": "check_unusual_parents",
        "description": (
            "Check whether processes on a host were spawned by parents commonly abused for "
            "privilege escalation / UAC bypass (e.g. fodhelper, eventvwr, computerdefaults, "
            "sdclt). A strong early lead when escalation is suspected. Zero rows is a real "
            "result, not an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname to inspect."},
                **_TIME_PROPS,
            },
            "required": ["host", "earliest_time", "latest_time"],
        },
    },
    {
        "name": "find_lateral_execution",
        "description": (
            "Find process creations spawned by remote-execution service hosts (e.g. "
            "WmiPrvSE.exe) across all hosts in the window — a signature of lateral movement "
            "or remote tasking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_TIME_PROPS},
            "required": ["earliest_time", "latest_time"],
        },
    },
    {
        "name": "trace_account_activity",
        "description": (
            "Trace where an account authenticated (successes and failures) across hosts in "
            "the window. Use to scope which systems a compromised identity touched."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_name": {
                    "type": "string",
                    "description": "Account/username to trace.",
                },
                **_TIME_PROPS,
            },
            "required": ["account_name", "earliest_time", "latest_time"],
        },
    },
    {
        "name": "finalize_brief",
        "description": (
            "Submit the finished incident brief. Call this exactly once, when the "
            "investigation is complete, to end the run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "brief": {
                    "type": "object",
                    "description": (
                        "The structured incident brief. Include at minimum a headline, a "
                        "narrative summary, and a list of findings; add timeline, IOCs, "
                        "MITRE techniques, gaps, and recommended containment as warranted."
                    ),
                    "properties": {
                        "headline": {"type": "string"},
                        "summary": {"type": "string"},
                        "findings": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["headline", "summary", "findings"],
                }
            },
            "required": ["brief"],
        },
    },
]

# name -> callable(tool_input) -> result dict
_DISPATCH: dict[str, Any] = {
    "decode_payload": lambda i: tools.decode_payload(i["command_line"]),
    "splunk_search": lambda i: tools.splunk_search(
        i["spl"], i["earliest_time"], i["latest_time"]
    ),
    "find_process_ancestry": lambda i: tools.find_process_ancestry(
        i["host"], i["earliest_time"], i["latest_time"]
    ),
    "find_pattern_across_hosts": lambda i: tools.find_pattern_across_hosts(
        i.get("command_pattern", "*-enc*"), i["earliest_time"], i["latest_time"]
    ),
    "check_unusual_parents": lambda i: tools.check_unusual_parents(
        i["host"], i["earliest_time"], i["latest_time"]
    ),
    "find_lateral_execution": lambda i: tools.find_lateral_execution(
        i["earliest_time"], i["latest_time"]
    ),
    "trace_account_activity": lambda i: tools.trace_account_activity(
        i["account_name"], i["earliest_time"], i["latest_time"]
    ),
    "finalize_brief": lambda i: tools.finalize_brief(i["brief"]),
}


def _row_count(result: Any) -> int | None:
    if isinstance(result, dict) and "row_count" in result:
        return result["row_count"]
    return None


def _execute_tool(name: str, tool_input: dict) -> tuple[str, dict]:
    """Run one tool. Returns (content_text, call_metadata). Never raises."""
    t0 = time.monotonic()
    meta: dict[str, Any] = {"name": name, "input": tool_input}
    try:
        result = _DISPATCH[name](tool_input)
        meta["latency_ms"] = round((time.monotonic() - t0) * 1000)
        meta["row_count"] = _row_count(result)
        meta["is_error"] = False
        return json.dumps(result, default=str), meta
    except Exception as e:  # surface to the model so it can recover
        meta["latency_ms"] = round((time.monotonic() - t0) * 1000)
        meta["is_error"] = True
        meta["error"] = f"{type(e).__name__}: {e}"
        return f"ERROR: {type(e).__name__}: {e}", meta


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #
def _build_user_text(alert: dict) -> str:
    return USER_TEMPLATE.format(
        time=alert.get("_time") or alert.get("time", ""),
        index=alert.get("index", ""),
        sourcetype=alert.get("sourcetype", ""),
        source=alert.get("source", ""),
        event_code=alert.get("EventCode") or alert.get("event_code", ""),
        host=alert.get("host", ""),
        account=alert.get("Account_Name") or alert.get("account", ""),
        parent_process=alert.get("Creator_Process_Name") or alert.get("parent_process", ""),
        new_process=alert.get("New_Process_Name") or alert.get("new_process", ""),
        command_line=alert.get("Process_Command_Line") or alert.get("command_line", ""),
    )


def run_agent(alert_payload: dict) -> dict:
    """Investigate one triggering alert. Returns the brief + run metadata.

    The loop ends when the model calls finalize_brief, or when it hits the iteration
    cap, the wall-clock cap, or stops on its own (end_turn).
    """
    system = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    messages: list[dict] = [
        {"role": "user", "content": [{"type": "text", "text": _build_user_text(alert_payload)}]}
    ]

    start = time.monotonic()
    iters = 0
    tool_calls: list[dict] = []
    brief: dict | None = None
    stop = "unknown"
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    while True:
        if iters >= MAX_ITERS:
            stop = "max_iters"
            break
        if time.monotonic() - start > WALL_CLOCK_CAP:
            stop = "timeout"
            break
        iters += 1

        resp = _client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": EFFORT},
            tools=TOOL_DEFS,
            messages=messages,
        )

        u = resp.usage
        usage["input"] += u.input_tokens
        usage["output"] += u.output_tokens
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        # Preserve the full assistant turn (thinking blocks included — required when
        # thinking is on and tool use follows).
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            stop = resp.stop_reason  # end_turn, max_tokens, refusal, ...
            break

        tool_results = []
        finalized = False
        for block in resp.content:
            if block.type != "tool_use":
                continue
            content_text, meta = _execute_tool(block.name, dict(block.input))
            tool_calls.append(meta)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content_text,
                    "is_error": meta["is_error"],
                }
            )
            if block.name == "finalize_brief" and not meta["is_error"]:
                brief = dict(block.input)["brief"]
                finalized = True

        messages.append({"role": "user", "content": tool_results})

        if finalized:
            stop = "finalized"
            break

    return {
        "brief": brief,
        "stop_reason": stop,
        "iterations": iters,
        "elapsed_sec": round(time.monotonic() - start, 1),
        "tool_calls": tool_calls,
        "usage": usage,
        "messages": messages,
    }
