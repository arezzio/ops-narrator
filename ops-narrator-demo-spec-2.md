# Ops Narrator — Demo Spec

**Project:** Ops Narrator (Splunk Agentic Ops Hackathon, due 2026-06-15)
**Dataset:** BOTSv3 (Frothly Brewing Co. scenario, attack date 2018-08-20)
**Stack:** Splunk Enterprise (local) + livehybrid/splunk-mcp (stdio) + Anthropic SDK agent loop + FastAPI webhook
**Last updated:** 2026-05-18 (post-MCP validation)

---

## Demo One-Liner

> Splunk alert fires on encoded PowerShell. An AI agent runs six follow-up SPL queries, decodes the C2 stager, reconstructs the full kill chain across three hosts, identifies a second rotated C2 listener, and delivers a SOC-grade incident brief in under 30 seconds.

---

## Demo Trigger Event

The agent is invoked off this single 4688 event. Every downstream query parameterizes off these values.

| Field | Value |
|---|---|
| `_time` | `2018-08-20 05:59:48 EDT` (verify TZ in Splunk Web before demo) |
| `index` | `botsv3` |
| `sourcetype` | `WinEventLog` |
| `source` | `WinEventLog:Security` |
| `EventCode` | `4688` |
| `host` | `BSTOLL-L` |
| `Account_Name` | `BudStoll` |
| `Creator_Process_Name` | `C:\Windows\System32\browser_broker.exe` |
| `New_Process_Name` | `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` |
| `Process_Command_Line` | `"...powershell.exe" powershell -noP -sta -w 1 -enc SQBmACg...` |

**Saved-search trigger SPL** (the alert that fires the webhook):

```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security"
  EventCode=4688
  Process_Command_Line="*powershell*"
  Process_Command_Line="*-enc*"
| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line
```

---

## Identified Threat

**PowerShell Empire 2.x stager** — with operator rotation between two HTTP listeners. Confirmed via decoded payload signature:
- `/admin/get.php` and `/login/process.php` URI patterns (Empire 2.x default profile)
- RC4 staging routine with hardcoded keys
- Empire-format session cookies baked into each agent

### C2 Infrastructure (IOCs)

Operator runs **two distinct Empire HTTP listeners** on the same C2 IP — first one drops the initial stagers via browser exploit, second one tasks the SYSTEM-context beachhead post-privesc.

| Item | Listener A (initial stagers) | Listener B (post-privesc SYSTEM agent) |
|---|---|---|
| C2 endpoint | `https://45.77.53.176:443` | `https://45.77.53.176:443` |
| Beacon path | `/admin/get.php` | `/login/process.php` |
| Session cookie | `PthAVgs=bKQxpuOd5LPCjyfRC1BxPqQ8FWI=` | `PthAVgs=G3dqnhr9M/vsRZosKQQH1HpDzGg=` |
| Launcher flags | `-noP -sta -w 1 -enc` | `-NonI -W hidden -enc` |
| Spawn vector | `browser_broker.exe` (user context) | `WmiPrvSE.exe` (SYSTEM context) |
| Empire module | initial HTTP launcher | `invoke_wmi` |
| RC4 key | `` 1AB<Yk6Z4#+vVu%o5}8&M-9UL~l|>0gP `` | (different key, second decode required) |
| Transport | RC4-encrypted payload over HTTPS | RC4-encrypted payload over HTTPS |
| User-Agent spoof | `Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko` (IE11/Win7) | Same |

### What the stager does (in execution order)

1. **AMSI bypass** — sets `amsiInitFailed = true` so PowerShell payloads aren't inspected (T1562.001)
2. **Disables ScriptBlock logging** — blinds the very logging that detected it (T1562.002)
3. **Spoofs User-Agent** as IE11/Win7 to blend with legacy traffic
4. **Bypasses SSL validation** — accepts attacker's self-signed cert via `ServerCertificateValidationCallback = $true`
5. **Inherits proxy credentials** from the user's session (works through corporate proxies)
6. **RC4-decrypts** C2 responses with the hardcoded key
7. **Beacons** to `https://45.77.53.176:443/admin/get.php` with the session cookie
8. **Pipes decrypted result to `IEX`** — in-memory execution, no disk artifact

---

## Kill Chain (compressed timeline)

Validated via MCP queries 4 and 5 on 2026-05-18.

| Time (EDT) | Host | Account | Parent Process | Encoded PS count | Stage |
|---|---|---|---|---|---|
| 05:59:48 | BSTOLL-L | BudStoll | `browser_broker.exe` | 1 | Initial Access — patient zero |
| 06:00:54 (+66s) | ABUNGST-L | AlBungstein | `browser_broker.exe` | 2 | Lateral spread #1 (user context) |
| 06:01:41 (+47s) | FYODOR-L | FyodorMalteskesko + `FYODOR-L$` | `browser_broker.exe` | 5 | Lateral spread #2 + **privilege escalation** (SYSTEM context observed) |
| 06:15:27 | FYODOR-L | `FYODOR-L$` | `WmiPrvSE.exe` | — | **WMI lateral execution** (T1047) — operator tasks new SYSTEM-context agent on beachhead via Listener B |

**Affected hosts:** BSTOLL-L, ABUNGST-L, FYODOR-L
**Attack window:** ~16 minutes
**Compromised users:** BudStoll, AlBungstein, FyodorMalteskesko (+ `FYODOR-L$` machine account)
**Beachhead:** FYODOR-L — 5 encoded-PS executions including SYSTEM-context, then re-tasked via WMI from a second listener 14 minutes after initial compromise. The 14-minute gap is consistent with operator dwell time (interactive triage of new agent → push SYSTEM stager via WMI).

> **Cadence note:** sub-minute hops between hosts (66s, 47s) are machine-speed — consistent with Empire's `invoke_psexec` / `invoke_wmi` modules firing across a target list, not a human typing.

---

## MITRE ATT&CK Mapping

| Technique | ID | Evidence |
|---|---|---|
| Command and Scripting Interpreter: PowerShell | T1059.001 | `powershell.exe -enc` across all victim hosts |
| Impair Defenses: Disable or Modify Tools (AMSI) | T1562.001 | Decoded stager sets `amsiInitFailed = true` |
| Impair Defenses: Disable Windows Event Logging | T1562.002 | Decoded stager nukes ScriptBlock logging |
| Windows Management Instrumentation | T1047 | `WmiPrvSE.exe` spawning `powershell.exe -NonI -W hidden -enc` on FYODOR-L as `FYODOR-L$` |
| Application Layer Protocol: Web Protocols | T1071.001 | Empire HTTPS beacons to 45.77.53.176:443 (two listeners) |
| Valid Accounts | T1078 | Machine account `FYODOR-L$` running encoded PS = SYSTEM-context execution |
| Abuse Elevation Control Mechanism: Bypass UAC | T1548.002 | *Pending validation* — Query 3 (same-host fodhelper/eventvwr) not yet run |

---

## Agent Investigation Chain (six queries)

Each query is parameterized off the trigger event. The agent runs them sequentially via MCP tool calls, then synthesizes findings into the brief.

**Time format note:** When calling `search_splunk` via MCP, pass `earliest_time` and `latest_time` as **separate ISO parameters** (`2018-08-20T05:59:00`), NOT inline in the SPL string. Splunk's SPL parser rejects inline ISO timestamps — it wants `MM/DD/YYYY:HH:MM:SS`. Going through the MCP tool API sidesteps the parser entirely; the splunk-sdk handles the conversion. The SPL blocks below show inline timestamps for human readability only — the agent strips them and passes them as tool params.

### Query 1 — Decode the payload
Not a Splunk query. Agent tool: `decode_powershell_enc(command_line: str)`. Strips `-enc`, base64-decodes, UTF-16LE → UTF-8. Returns plaintext stager. Re-decodes any nested base64.

### Query 2 — Parent context
What was browser_broker.exe doing? Establishes the initial access vector.

```spl
index=botsv3 host=BSTOLL-L
  (sourcetype="stream:dns" OR sourcetype="stream:http")
| table _time, sourcetype, src, dest, url, query
| sort _time
| head 50
```
Tool params: `earliest_time=2018-08-20T05:55:00`, `latest_time=2018-08-20T06:00:00`

### Query 3 — Same-host escalation
Did this host see UAC bypass attempts after the initial PS?

```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security"
  EventCode=4688 host=BSTOLL-L
  Creator_Process_Name IN ("*fodhelper.exe", "*eventvwr.exe", "*computerdefaults.exe", "*sdclt.exe")
| table _time, host, Account_Name, Creator_Process_Name, New_Process_Name, Process_Command_Line
```
Tool params: `earliest_time=2018-08-20T05:59:00`, `latest_time=2018-08-20T06:30:00`

### Query 4 — Spread check ✅ validated
Same encoded pattern on other hosts in the ±1hr window?

```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security"
  EventCode=4688 Process_Command_Line="*-enc*"
| stats min(_time) as first_seen values(Account_Name) as users count by host
| sort first_seen
```
Tool params: `earliest_time=2018-08-20T05:00:00`, `latest_time=2018-08-20T07:00:00`
**Result:** 3 hosts (BSTOLL-L, ABUNGST-L, FYODOR-L) with counts 1, 2, 5.

### Query 5 — WMI lateral movement ✅ validated
Any 4688 with `WmiPrvSE.exe` as Creator in the same window?

```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security"
  EventCode=4688 Creator_Process_Name="*WmiPrvSE.exe"
| table _time, host, Account_Name, New_Process_Name, Process_Command_Line
| sort _time
```
Tool params: `earliest_time=2018-08-20T05:00:00`, `latest_time=2018-08-20T07:00:00`
**Result:** 1 hit — FYODOR-L at 06:15:27, `FYODOR-L$` (SYSTEM), `powershell.exe -NonI -W hidden -enc <base64>` decoding to Listener B (`/login/process.php`).

### Query 6 — Account scope
What other hosts has the compromised user touched?

```spl
index=botsv3 sourcetype=WinEventLog source="WinEventLog:Security"
  (EventCode=4624 OR EventCode=4625) Account_Name=BudStoll
| stats min(_time) as first values(ComputerName) as hosts count by EventCode
```
Tool params: `earliest_time=2018-08-20T00:00:00`, `latest_time=2018-08-20T23:59:59`

---

## Target Output (incident brief)

The agent's final synthesized output should look like this:

> **🚨 P1 — PowerShell Empire C2 active, 3 hosts compromised, SYSTEM-level beachhead established**
>
> At 2018-08-20 05:59:48, host **BSTOLL-L** (user: BudStoll) executed an encoded PowerShell command spawned from `browser_broker.exe`, indicating browser-based initial access. The decoded payload is a **PowerShell Empire 2.x stager** beaconing to **`https://45.77.53.176:443/admin/get.php`** with RC4-encrypted HTTPS. The stager disables AMSI (T1562.001) and ScriptBlock logging (T1562.002) before establishing C2.
>
> Within 2 minutes, identical stagers executed on **ABUNGST-L** (AlBungstein, 2 events) and **FYODOR-L** (FyodorMalteskesko, 5 events) via the same `browser_broker.exe` vector — sub-minute cadence (66s, 47s) is machine-speed, consistent with Empire lateral-movement modules.
>
> **FYODOR-L is the beachhead.** SYSTEM-context execution (`FYODOR-L$` machine account in 4688) confirms privilege escalation. 14 minutes after initial compromise (06:15:27), the operator returned via **WMI lateral execution** (T1047) and tasked a fresh SYSTEM-context agent — this time beaconing to a **second rotated listener** at `/login/process.php` with a different session cookie, indicating active operator infrastructure rotation.
>
> **Affected hosts:** BSTOLL-L, ABUNGST-L, FYODOR-L
> **C2 IOC:** `45.77.53.176:443` — block at perimeter immediately.
> **Listener A URI:** `/admin/get.php` — cookie `PthAVgs=bKQxpuOd5LPCjyfRC1BxPqQ8FWI=`
> **Listener B URI:** `/login/process.php` — cookie `PthAVgs=G3dqnhr9M/vsRZosKQQH1HpDzGg=`
> **Recommended containment:** isolate the three hosts (priority: FYODOR-L), reset the three user passwords + FYODOR-L machine account, hunt for `45.77.53.176` across all proxy/firewall logs, search for either Empire session cookie pattern in HTTP logs.

---

## Build Status

- [x] Splunk Enterprise running locally (v10.2.3)
- [x] BOTSv3 indexed (2.08M events, 102 sourcetypes)
- [x] Required TAs installed and verified (4688 fields extract cleanly)
- [x] Demo storyline locked (Empire C2 kill chain + dual-listener twist)
- [x] Trigger event identified
- [x] C2 IOCs and TTPs decoded (both listeners)
- [x] Six-query investigation chain drafted
- [x] Install `livehybrid/splunk-mcp` via `uv sync`
- [x] Wire MCP to Splunk (`VERIFY_SSL=false`), validate via Claude Desktop
- [x] Queries 4 and 5 validated end-to-end through MCP
- [ ] Validate Query 3 (UAC bypass / fodhelper on BSTOLL-L or FYODOR-L) — currently unverified, may need to drop T1548.002 from MITRE map if data isn't there
- [ ] Build agent skeleton (FastAPI webhook → MCP client → Anthropic loop → brief)
- [ ] Configure Splunk saved search with webhook alert action
- [ ] End-to-end dry run
- [ ] Demo recording + pitch deck (partner)

---

## Notes

- BOTSv3 has **no Sysmon data** — all Windows telemetry is under `sourcetype=WinEventLog` with `source` distinguishing the channel (`WinEventLog:Security`, `WinEventLog:System`, etc.). 4688 process creation is used instead of Sysmon EventCode=1. This is actually a stronger enterprise pitch — most real SOCs use native auditing.
- BOTSv3 timestamps preserved — search with `earliest=...` or use "All time" picker. Events are timestamped Aug 2018; the "Last 24 hours" default picker hides everything.
- Sourcetype/source is case-sensitive when quoted in SPL. Use `source="WinEventLog:Security"` exactly.
- `Process_Command_Line` is fully extracted by Splunk_TA_windows — confirmed.
- `osquery:results` (219K events) is a backup process-telemetry source if the 4688 angle needs reinforcement during the demo.
- **SPL time format gotcha:** Splunk Web UI's SPL parser rejects inline ISO timestamps like `earliest="2018-08-20T05:59:00"`. Two workarounds: (1) use Splunk format `earliest="08/20/2018:05:59:00"` when typing into Web UI, or (2) pass ISO timestamps as separate `earliest_time` / `latest_time` parameters when calling via MCP — the splunk-sdk handles them. The agent uses option 2.
- **MCP token auth bug:** `SPLUNK_TOKEN` via bearer header doesn't work with this version of splunk-sdk through livehybrid/splunk-mcp — sessions fail with "not logged in". Fallback to `SPLUNK_USERNAME` + `SPLUNK_PASSWORD` in the Claude Desktop env config works. Same fallback will apply when the FastAPI agent spawns the stdio subprocess.
- **Timezone:** Splunk Web rendered the validation results in EDT. Spec previously said UTC. Confirm before demo with `... | eval epoch=strftime(_time, "%Y-%m-%dT%H:%M:%S %Z")` so the brief renders the right zone.
- **Listener rotation finding** (not in original spec) is the strongest demo moment — agent didn't just find one C2, it found a rotated listener and connected both to the same operator via shared IP and cookie format. Lead with this in the pitch.
