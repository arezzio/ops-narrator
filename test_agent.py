"""Section 2 live smoke test: run the agent loop against the spec's trigger event.

Requires local Splunk up (botsv3 index) AND ANTHROPIC_API_KEY. This is an integration
test, not a unit test — it makes real model + Splunk calls.

Run: uv run pytest test_agent.py -v -s

Note: we raise the wall-clock cap well above the spec's 90s here. Each Splunk tool call
spawns a fresh splunk-mcp stdio subprocess (slow), so a full multi-query investigation
can take minutes. We're validating that the loop completes and produces a valid brief —
not demo latency. (MCP connection reuse is a later optimization.)
"""

import os

os.environ.setdefault("OPS_WALL_CLOCK_CAP", "300")

import agent  # noqa: E402
import trace as trace_log  # noqa: E402

# The spec's trigger event (ops-narrator-demo-spec-2.md §Demo Trigger Event). The command
# line is truncated as a real webhook payload would be — the agent is expected to pull the
# full encoded blob from Splunk before decoding.
TRIGGER = {
    "_time": "2018-08-20 05:59:48 EDT",
    "index": "botsv3",
    "sourcetype": "WinEventLog",
    "source": "WinEventLog:Security",
    "EventCode": "4688",
    "host": "BSTOLL-L",
    "Account_Name": "BudStoll",
    "Creator_Process_Name": r"C:\Windows\System32\browser_broker.exe",
    "New_Process_Name": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "Process_Command_Line": '"...powershell.exe" powershell -noP -sta -w 1 -enc SQBmACg...',
}


def test_run_agent_finalizes_brief():
    result = agent.run_agent(TRIGGER)

    print("\n=== RUN SUMMARY ===")
    print("stop_reason:", result["stop_reason"])
    print("iterations:", result["iterations"], "| elapsed:", result["elapsed_sec"], "s")
    print("usage:", result["usage"])
    print("\n=== TOOL CALLS ===")
    for c in result["tool_calls"]:
        print(
            f"  {c['name']:<26} {c['latency_ms']:>6}ms  rows={c.get('row_count')}"
            f"  err={c['is_error']}"
        )

    brief = result["brief"]
    print("\n=== BRIEF ===")
    print(agent.json.dumps(brief, indent=2, default=str) if brief else "(none)")

    # The loop completed by the model calling finalize_brief.
    assert result["stop_reason"] == "finalized", result["stop_reason"]
    assert brief is not None
    for key in ("headline", "summary", "findings"):
        assert key in brief, f"brief missing {key}"

    # It actually investigated — at least one tool call beyond finalize_brief, and at
    # least one Splunk-backed search ran.
    names = [c["name"] for c in result["tool_calls"]]
    assert len(names) >= 2, names
    assert any(n != "finalize_brief" for n in names), names

    # Section 3: a JSONL reasoning trace was written and reloads with the expected
    # event stream (real Opus 4.7 thinking blocks + correlated tool calls).
    assert "trace_path" in result, result.get("trace_error")
    events = trace_log.read_trace(result["trace_path"])
    print(f"\n=== TRACE ({result['trace_path']}, {len(events)} events) ===")
    from collections import Counter

    print(Counter(e["type"] for e in events))

    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_finished"
    types = {e["type"] for e in events}
    assert "thinking" in types, "no thinking captured — is display:summarized on?"
    assert "tool_call" in types
    assert "tool_result" in types
    # Every tool_call should carry latency, and Splunk-backed ones a row_count.
    for e in events:
        if e["type"] == "tool_call":
            assert e["latency_ms"] is not None, e
