# Ops Narrator — Demo Runbook

Everything to **say** and **do** for the judges' demo. Format: **pre-recorded replay** of a
verified-clean investigation (zero live-failure risk, $0). Target length: **~2 minutes** of
narration over the screen recording.

---

## 0. Setup before you record (5 min, do once)

1. **Start the demo server** (from the repo root):
   ```
   uv run uvicorn webhook:app --host 127.0.0.1 --port 8000
   ```
2. **Open two browser tabs:**
   - Tab A — dashboard: `http://127.0.0.1:8000/`
   - Tab B — viewer: `http://127.0.0.1:8000/viewer.html?trace=traces/trace-20260524-200934-BSTOLL-L.jsonl`
3. In Tab B, click **"Expand all"** once so the timeline is open, then scroll back to the top.
4. Do one silent dry-run of the scroll path so the recording is smooth.
5. Start screen recording on **Tab A** (the dashboard).

> The trace you're replaying is the **verified-clean run**: every fact in it is correct
> (C2 `45.77.53.176`, all 3 hosts, the Empire stager, the SharePoint lure). Replaying it
> carries no accuracy risk. Don't regenerate it live.

---

## 1. The hook — 20 seconds (say over the dashboard, Tab A)

> "A security operations center drowns in alerts. A single *'encoded PowerShell ran'* alert
> is hours of analyst work — decode the payload, figure out what it does, check if it spread,
> scope the blast radius, write it up. Most alerts never get that treatment; they're closed in
> seconds.
>
> **Ops Narrator is an AI SOC analyst.** A Splunk saved search fires, and instead of paging a
> human, it hands the alert to an autonomous agent that investigates end to end and writes the
> incident brief. This is its dashboard of finished investigations — here's one it ran."

**[DO: point at the P2 row, then click "open trace".]**

---

## 2. The walkthrough — ~80 seconds (Tab B, top to bottom)

### a) The alert (top card) — 10s
> "This is all it started with: a single Windows process-creation event on host BSTOLL-L —
> PowerShell launched with a Base64-encoded command line. That's the only input."

**[DO: gesture at the ALERT card — `-enc` blob, host, account BudStoll.]**

### b) The outcome banner + stats — 15s
> "Working entirely on its own, the agent reached a P2 verdict and a one-line headline a SOC
> lead can read in five seconds. It got there in **3 reasoning iterations and 5 tool calls** —
> about four minutes — calling real Splunk searches through the **Splunk MCP server** the whole
> time. It is not replaying a script; it operates over live Splunk data."

**[DO: point at the FINALIZED badge, the headline, then the stat grid.]**

### c) The reasoning timeline — 40s (the core of the demo)
> "And here's the part that matters — its actual reasoning, step by step.
>
> **First**, it decodes the payload itself — Base64 to a PowerShell one-liner — and recognizes a
> textbook **Empire stager**: it disables AMSI and PowerShell ScriptBlock logging to blind the
> defender, then RC4-decrypts a second-stage payload from a command-and-control server at
> **45.77.53.176**.
>
> **Then** it pivots: it reconstructs the process ancestry on the host and traces the lure back to
> a malicious shortcut — *'BRUCE BIRTHDAY HAPPY HOUR PICS'* — downloaded from the company's own
> SharePoint.
>
> **Then** it asks the obvious follow-up a human would: *did this spread?* It searches the same
> pattern fleet-wide and finds the **same attack hit three users** — on BSTOLL-L, FYODOR-L, and
> ABUNGST-L — within half an hour."

**[DO: slowly scroll the timeline — decode_payload → find_process_ancestry → find_pattern_across_hosts. Expand one tool call to show the real Splunk result rows.]**

### d) The brief + honesty — 15s
> "It writes a full brief — MITRE ATT&CK mappings, a cross-host timeline, indicators of
> compromise, and containment steps. And notice what it *didn't* do: it didn't invent lateral
> movement it couldn't prove. It explicitly characterizes this as **parallel user compromises,
> not host-to-host movement**, and lists what it couldn't confirm. A trustworthy analyst reports
> its gaps."

**[DO: scroll to the brief's findings / timeline / containment, then the gaps section.]**

---

## 3. The close — 20 seconds

> "So: one alert in, a verified incident brief out — plus a complete, auditable record of *how*
> it reasoned, not just its conclusion. Two design choices make it real:
>
> One — every investigative tool reaches Splunk through the **official Splunk MCP Server**, so the
> agent works over real data.
>
> Two — and this is the integrity check — the tools and the prompt are **deliberately generic**.
> They never name the dataset, the threat, the hosts, or the answer. The agent rediscovers the
> entire incident from the data on every run. Nothing is hardcoded.
>
> That's Ops Narrator — turning a raw alert into a SOC-grade investigation, automatically."

---

## 4. Judge Q&A — cheat sheet

- **"Is the answer hardcoded / does it know the dataset?"**
  No. The system prompt and every tool description are scrubbed of dataset, threat-family, host,
  IP, and outcome names (anti-recall). The agent gets one alert + a generic tool menu and must
  discover the incident from Splunk each run. Grading ground truth lives only in the offline
  validator, never in the model's context.

- **"How does it talk to Splunk?"**
  Through the **official Splunk MCP Server** (Splunkbase app 7931), installed into Splunk and
  called over streamable HTTP via the Model Context Protocol. A community stdio MCP server is a
  supported fallback. The agent's Splunk tools are thin wrappers over MCP `splunk_run_query`.

- **"What model, and how is it controlled?"**
  Anthropic **Claude Opus 4.7** with adaptive extended thinking, a hard iteration cap (12) and a
  wall-clock budget, so a run can't run away. Prompt caching keeps token cost down.

- **"What's the trace / why JSONL?"**
  Every run emits one event per thinking step, tool call (with latency + row count), tool result,
  and detected hypothesis revision. That's the explainability layer — an analyst verifies the
  agent's work instead of trusting a black box. The viewer renders it.

- **"Is this live or recorded? Can it run live?"**
  Recorded for reliability, but the full live path works: a Splunk saved search → webhook →
  `POST /alert` → background agent run → brief + trace served in this same viewer. We can fire it
  live on request.

- **"What did it cost / how fast?"**
  This run: ~4 minutes, ~235K tokens (heavily cached), a few dollars at most. The point is it
  replaces hours of tier-1 analyst time per alert.

- **"What if the agent is wrong?"**
  It surfaces a `gaps` section and doesn't overclaim (e.g. it declined to assert WMI lateral
  movement here). The brief is meant to be analyst-verified, and the trace shows exactly which
  evidence supports each finding.

---

## 5. If something breaks (backup plan)

- **Viewer won't load the trace** → it also loads via drag-and-drop and a file picker; drag
  `traces/trace-20260524-200934-BSTOLL-L.jsonl` onto the page.
- **Server won't start / port busy** → `lsof -ti:8000 | xargs kill`, then relaunch; or use a
  different `--port`.
- **Total fallback** → the recording itself. Have the screen recording on disk and ready to play.
- **Dashboard empty** → re-run the brief-record synthesis (see PROGRESS.md → Session 9) or just
  open the direct viewer URL in Tab B.

---

## 6. One-page handout (copy/paste)

**Ops Narrator — an AI SOC analyst** · Splunk Agentic Ops Hackathon, Security track

**Problem.** SOCs triage more alerts than humans can investigate. A single "encoded PowerShell"
alert is hours of work and usually gets closed in seconds.

**What it does.** A Splunk saved search fires a webhook; an autonomous agent (Claude Opus 4.7)
investigates end to end — decodes the payload, reconstructs the kill chain across hosts, scopes
the blast radius — and writes a SOC-grade incident brief plus an auditable reasoning trace.

**How.** A tool-using agent loop calls Splunk through the **official Splunk MCP Server**
(streamable HTTP). Tools: `decode_payload`, `splunk_search`, `find_process_ancestry`,
`find_pattern_across_hosts`, `check_unusual_parents`, `find_lateral_execution`,
`trace_account_activity`, `finalize_brief`. Adaptive thinking + hard iteration/time budgets.

**Integrity.** Tool descriptions and the system prompt are deliberately generic — no dataset,
threat, host, or answer is named. The agent rediscovers the incident from data every run.

**Sample result (this demo).** From one 4688 event on BSTOLL-L, the agent found an Empire
PowerShell stager delivered by a malicious SharePoint `.lnk` lure, AMSI + ScriptBlock-logging
defense evasion, HTTPS C2 to `45.77.53.176`, and the same campaign hitting **3 users** in
parallel — with MITRE ATT&CK mappings, a cross-host timeline, IOCs, and containment steps. P2.

**Stack.** Anthropic Claude Opus 4.7 · official Splunk MCP Server (app 7931) · FastAPI webhook ·
single-file offline trace viewer · Splunk `botsv3`.

---

## Quick reference — what to have open

| Tab | URL | Used in |
|---|---|---|
| A — dashboard | `http://127.0.0.1:8000/` | Hook (§1), close |
| B — viewer | `http://127.0.0.1:8000/viewer.html?trace=traces/trace-20260524-200934-BSTOLL-L.jsonl` | Walkthrough (§2) |
