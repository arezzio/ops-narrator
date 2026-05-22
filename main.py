"""Ops Narrator CLI — run the SOC-analyst agent on one alert and print the brief.

Usage:
    uv run python main.py                 # run on the bundled demo trigger event
    uv run python main.py path/to/alert.json   # run on an alert payload from a file

Reads an alert payload (the fields a SIEM webhook would deliver), runs the agent
loop end to end, prints the incident brief as JSON, and reports the JSONL trace
path. Requires ANTHROPIC_API_KEY and a reachable Splunk instance (see README).
"""

from __future__ import annotations

import json
import sys

import agent

# A representative encoded-PowerShell process-creation alert, as a SIEM saved
# search would deliver it. The command line is truncated like a real webhook
# payload — the agent pulls the full encoded blob from the logs itself.
DEMO_TRIGGER = {
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


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            alert = json.load(f)
    else:
        alert = DEMO_TRIGGER

    result = agent.run_agent(alert)

    print(json.dumps(result.get("brief"), indent=2, default=str))
    print(
        f"\nstop_reason={result['stop_reason']}  iterations={result['iterations']}  "
        f"elapsed={result['elapsed_sec']}s",
        file=sys.stderr,
    )
    if result.get("trace_path"):
        print(f"trace written to: {result['trace_path']}", file=sys.stderr)
    return 0 if result.get("brief") else 1


if __name__ == "__main__":
    raise SystemExit(main())
