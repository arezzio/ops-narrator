"""Section 4 stability harness: run the agent N times against the trigger event and
score each run as "clean".

A run is clean when the agent (a) finalized on its own, (b) returned a schema-complete
brief, and (c) actually surfaced the ground-truth substance of the incident. The
ground-truth checks live HERE, in the grader — never in the model-facing prompt/tools
(anti-recall, PROGRESS gotcha #4). The agent must rediscover these facts every run.

Usage:
    uv run python validate_runs.py [N]        # default N=5
    OPS_VALIDATE_RUNS=3 uv run python validate_runs.py

Exit code 0 iff all N runs are clean (i.e. an N-run clean streak).
"""

from __future__ import annotations

import json
import os
import sys

# Sets OPS_WALL_CLOCK_CAP=300 and imports agent; reuse its trigger event.
from test_agent import TRIGGER  # noqa: E402
import agent  # noqa: E402

# Ground truth for the BOTSv3 Empire scenario (grader-only; see module docstring).
EXPECTED_HOSTS = ("bstoll-l", "abungst-l", "fyodor-l")
EXPECTED_C2 = "45.77.53.176"
WMI_MARKERS = ("wmiprvse", "wmi")  # lateral-movement signature
REQUIRED_BRIEF_KEYS = ("severity", "headline", "summary", "findings", "recommended_containment")


def evaluate(result: dict) -> tuple[bool, list[str]]:
    """Return (clean, reasons-it-was-not-clean)."""
    fails: list[str] = []

    if result.get("stop_reason") != "finalized":
        fails.append(f"stop_reason={result.get('stop_reason')} (not finalized)")

    brief = result.get("brief")
    if not isinstance(brief, dict):
        fails.append("no brief")
        return False, fails

    for k in REQUIRED_BRIEF_KEYS:
        if not brief.get(k):
            fails.append(f"brief missing/empty: {k}")
    if isinstance(brief.get("findings"), list) and len(brief["findings"]) < 2:
        fails.append(f"only {len(brief['findings'])} finding(s)")

    blob = json.dumps(brief, default=str).lower()

    missing_hosts = [h for h in EXPECTED_HOSTS if h not in blob]
    if missing_hosts:
        fails.append(f"hosts not in brief: {missing_hosts}")
    if EXPECTED_C2 not in blob:
        fails.append(f"C2 IP {EXPECTED_C2} not in brief")
    if not any(m in blob for m in WMI_MARKERS):
        fails.append("no WMI lateral-movement reference")

    names = [c.get("name") for c in result.get("tool_calls", [])]
    if "decode_payload" not in names:
        fails.append("payload never decoded")
    if not any(n not in ("decode_payload", "finalize_brief") for n in names):
        fails.append("no log search ran")

    return (len(fails) == 0), fails


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("OPS_VALIDATE_RUNS", "5"))
    print(f"Running {n} validation run(s). Each makes real model + Splunk calls (~5-6 min).\n")

    results = []
    streak = 0
    best_streak = 0
    for i in range(1, n + 1):
        result = agent.run_agent(TRIGGER)
        clean, fails = evaluate(result)
        streak = streak + 1 if clean else 0
        best_streak = max(best_streak, streak)
        results.append((i, clean, fails, result))

        sev = (result.get("brief") or {}).get("severity", "?")
        ncalls = len(result.get("tool_calls", []))
        nfind = len((result.get("brief") or {}).get("findings", []) or [])
        status = "CLEAN" if clean else "DIRTY"
        print(
            f"[run {i}/{n}] {status}  sev={sev}  iters={result.get('iterations')}  "
            f"calls={ncalls}  findings={nfind}  {result.get('elapsed_sec')}s  "
            f"trace={result.get('trace_path')}"
        )
        if not clean:
            for f in fails:
                print(f"           - {f}")

    n_clean = sum(1 for _, c, _, _ in results if c)
    print(f"\n=== {n_clean}/{n} clean · longest clean streak: {best_streak} ===")
    return 0 if n_clean == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
