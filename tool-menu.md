# Section 1 — Tool Menu (8 functions)

Reconstructed from `ops-narrator-demo-spec-2.md` (the six-query investigation chain + decode + finalize).
This is the spec for `tools.py` in Session 1. Edit freely before we build.

## Conventions
- All Splunk-backed tools take **`earliest_time` and `latest_time` as separate ISO-8601 strings** (e.g. `"2018-08-20T05:59:00"`), never inline in SPL. The MCP/splunk-sdk converts them; the SPL parser would reject inline ISO. (Spec §"Time format note".)
- Every Splunk-backed tool returns a dict: `{ "rows": [ {...}, ... ], "row_count": int, "spl": "<final SPL>", "earliest_time": ..., "latest_time": ... }`. Returning the final SPL + count is what the trace logger (Session 3) records as `result_row_count`.
- Auth is **username/password** (the `SPLUNK_TOKEN` bearer path is broken — spec note). The stdio subprocess env needs `SPLUNK_HOST` (host only), `SPLUNK_PORT`, `SPLUNK_SCHEME=https`, `VERIFY_SSL=false`.
- Tool *descriptions shown to the model* must not name the threat, the dataset, specific hosts, or the expected outcome (anti-recall — Session 4). The descriptions below are written that way already.

## Summary

| # | Function | Backed by | Maps to spec query |
|---|----------|-----------|--------------------|
| 1 | `decode_payload` | pure Python | Query 1 (decode) |
| 2 | `splunk_search` | splunk-mcp `search` | generic escape hatch |
| 3 | `find_process_ancestry` | splunk-mcp `search` | Query 2 (parent/host context) |
| 4 | `find_pattern_across_hosts` | splunk-mcp `search` | Query 4 (spread) |
| 5 | `check_unusual_parents` | splunk-mcp `search` | Query 3 (same-host escalation) |
| 6 | `find_lateral_execution` | splunk-mcp `search` | Query 5 (WMI lateral) |
| 7 | `trace_account_activity` | splunk-mcp `search` | Query 6 (account scope) |
| 8 | `finalize_brief` | pure Python (validate) | Target Output |

---

## 1. `decode_payload`
**Type:** pure Python (no Splunk).
**Signature:** `decode_payload(command_line: str) -> dict`
**Model-facing description:** "Decode an encoded PowerShell command line. Strips launcher flags, base64-decodes the `-enc` blob, converts UTF-16LE → UTF-8, and recursively decodes one level of nested base64 if present. Returns the cleartext script."
**Logic:** extract the base64 blob after `-enc`/`-encodedcommand`; base64-decode; decode UTF-16LE → UTF-8; scan result for a nested base64 literal and decode one more level if found.
**Returns:** `{ "plaintext": str, "layers": int, "nested_base64_found": bool, "indicators": { "uris": [...], "launcher_flags": [...], "cookies": [...], "notable_strings": [...] } }`
> `indicators` extraction is where Session 5 tuning lives — surfacing *all* URIs/flags so the model can notice infrastructure differences on its own. Keep the raw `plaintext` complete regardless.

## 2. `splunk_search`
**Type:** splunk-mcp `search` wrapper (generic).
**Signature:** `splunk_search(spl: str, earliest_time: str, latest_time: str) -> dict`
**Model-facing description:** "Run an arbitrary Splunk SPL search over a time window. Use when no specialized tool fits — e.g. inspecting network/DNS/HTTP telemetry or following an ad-hoc lead."
**Logic:** pass `spl`, `earliest_time`, `latest_time` straight to the MCP search tool. Standard return shape.

## 3. `find_process_ancestry`
**Type:** splunk-mcp `search` wrapper.
**Signature:** `find_process_ancestry(host: str, earliest_time: str, latest_time: str) -> dict`
**Model-facing description:** "Reconstruct the process-creation sequence on a single host: which parent processes spawned which children, in time order. Use to establish how a suspicious process was launched."
**SPL:**
```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security" EventCode=4688 host=<host>
| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line
| sort _time
```
> Spec Query 2 establishes initial-access context via DNS/HTTP stream; that is network telemetry, so route it through `splunk_search`. This tool covers the process-tree half of "what was the parent doing."

## 4. `find_pattern_across_hosts`
**Type:** splunk-mcp `search` wrapper.
**Signature:** `find_pattern_across_hosts(command_pattern: str, earliest_time: str, latest_time: str) -> dict` (default `command_pattern="*-enc*"`)
**Model-facing description:** "Find every host where a process command line matches a pattern within the window, with first-seen time, distinct accounts, and execution count per host. Use to gauge whether activity is isolated or spreading."
**SPL:**
```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security" EventCode=4688 Process_Command_Line=<command_pattern>
| stats min(_time) as first_seen values(Account_Name) as users count by host
| sort first_seen
```

## 5. `check_unusual_parents`
**Type:** splunk-mcp `search` wrapper.
**Signature:** `check_unusual_parents(host: str, earliest_time: str, latest_time: str) -> dict`
**Model-facing description:** "Check whether processes were spawned by parents commonly abused for privilege escalation / UAC bypass (e.g. fodhelper, eventvwr, computerdefaults, sdclt) on a host. A strong early lead when escalation is suspected."
**SPL:**
```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security" EventCode=4688 host=<host>
  Creator_Process_Name IN ("*fodhelper.exe","*eventvwr.exe","*computerdefaults.exe","*sdclt.exe")
| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line
```
> Per Session 5, this is the tool that should read as attractive early — it sets up the UAC dead-end pivot. Returning **zero rows is a real result**, not an error.

## 6. `find_lateral_execution`
**Type:** splunk-mcp `search` wrapper.
**Signature:** `find_lateral_execution(earliest_time: str, latest_time: str) -> dict`
**Model-facing description:** "Find process creations spawned by remote-execution service hosts (e.g. WmiPrvSE.exe) across all hosts in the window — a signature of lateral movement / remote tasking."
**SPL:**
```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security" EventCode=4688 Creator_Process_Name="*WmiPrvSE.exe"
| table _time, host, Account_Name, New_Process_Name, Process_Command_Line
| sort _time
```

## 7. `trace_account_activity`
**Type:** splunk-mcp `search` wrapper.
**Signature:** `trace_account_activity(account_name: str, earliest_time: str, latest_time: str) -> dict`
**Model-facing description:** "Trace where an account authenticated (successes and failures) across hosts in the window. Use to scope which systems a compromised identity touched."
**SPL:**
```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security" (EventCode=4624 OR EventCode=4625) Account_Name=<account_name>
| stats min(_time) as first values(ComputerName) as hosts count by EventCode
```

## 8. `finalize_brief`
**Type:** pure Python (validate + return). No Splunk.
**Signature:** `finalize_brief(brief: dict) -> dict`
**Model-facing description:** "Submit the finished incident brief. Call this exactly once, when the investigation is complete, to end the run."
**Session 1 behavior:** validate that required top-level keys exist and return the structured dict (a stub). **Session 4** expands the schema to: `findings[]` (each with `confidence` HIGH/MED/LOW + `evidence` + `source_tool_call_ids`), `timeline[]`, `iocs[]`, `mitre[]` (with confidence tags), `gaps[]`, `candidate_spl_rule`, `containment[]`.
