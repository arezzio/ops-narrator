# Ops Narrator

**An AI SOC analyst that investigates a Splunk alert end to end and writes the incident brief.**

Submitted to the **Security** track of the Splunk Agentic Ops Hackathon.

A Splunk saved search fires a webhook on a suspicious event (encoded PowerShell). Instead of
paging a human, the alert is handed to an autonomous agent (Anthropic Claude Opus 4.7) that
runs its own follow‑up Splunk searches through the **Splunk MCP server**, decodes the malicious
payload, reconstructs the kill chain across hosts, and delivers a SOC‑grade incident brief —
plus a step‑by‑step JSONL trace of *how* it reasoned, not just its conclusion.

---

## The problem

A SOC drowns in alerts. A single "encoded PowerShell ran" alert is hours of analyst work:
decode the payload, figure out what it does, check whether it spread, find privilege
escalation and lateral movement, scope the blast radius, write it up. Most alerts never get
that treatment — they're triaged in seconds and closed. Ops Narrator does the full
investigation automatically and shows its work, so an analyst gets a finished brief to verify
instead of a raw alert to chase.

## What it does / how AI is used

The core is a **tool‑using agent loop** (`agent.py`) built on the Anthropic SDK with
`claude-opus-4-7`, adaptive extended thinking, and a hard iteration + wall‑clock budget. The
model is given one triggering alert and a menu of investigative tools, and it decides — turn
by turn — what to look at next:

| Tool | What it does |
|---|---|
| `decode_payload` | Decode an encoded PowerShell command line (base64 → UTF‑16LE, nested layers) and extract indicators |
| `splunk_search` | Run ad‑hoc SPL over a time window |
| `find_process_ancestry` | Reconstruct the process‑creation chain on one host |
| `find_pattern_across_hosts` | Measure how widely a command pattern has spread |
| `check_unusual_parents` | Look for UAC‑bypass / privilege‑escalation parent processes |
| `find_lateral_execution` | Find processes spawned by remote‑execution services (e.g. WMI) |
| `trace_account_activity` | Trace where an account authenticated |
| `finalize_brief` | Submit the structured incident brief and end the run |

All Splunk‑backed tools reach Splunk through the **official Splunk MCP Server** (Splunkbase
app 7931) — installed into Splunk and called over streamable HTTP via the Model Context
Protocol — so the agent operates over real Splunk data, not hardcoded answers. (A community
`livehybrid/splunk-mcp` stdio backend is also supported as a fallback; see Configuration.)
Every run also produces a **reasoning trace** (`trace.py` → `traces/*.jsonl`): one event per
thinking step, tool call (with latency + row count), tool result, and detected hypothesis
revision.

> **Note on the tool design:** the model‑facing tool descriptions and system prompt are
> deliberately *generic* — they never name the dataset, threat family, hosts, or expected
> outcome. The agent has to discover the incident from the data on every run.

## Architecture

See [`architecture_diagram.md`](architecture_diagram.md) for the full diagram. In short:

```
Splunk saved search ──webhook──▶ FastAPI /alert ──▶ agent loop (Claude Opus 4.7)
                                                       │  tool calls
                                                       ▼
                                            Splunk MCP server (stdio) ──▶ Splunk REST API
                                                       │
                              brief (JSON) ◀───────────┴──────────▶ trace.jsonl ──▶ trace viewer
```

## Tech stack

- **Anthropic Claude** (`claude-opus-4-7`) via the `anthropic` Python SDK — the agent's reasoning
- **Official Splunk MCP Server** (Splunkbase app 7931, streamable HTTP) — the agent's bridge to
  Splunk over the Model Context Protocol
- **Splunk Enterprise** with the **BOTSv3** dataset — the log data under investigation
- **FastAPI + Uvicorn** — the webhook that receives the Splunk alert *(in progress, see Status)*
- **Python ≥ 3.10**, managed with [`uv`](https://docs.astral.sh/uv/)

## Prerequisites

1. **Splunk Enterprise** (10.2+) running locally with the **BOTSv3** dataset indexed, and the
   Windows TA so 4688 process‑creation fields extract. (Per the Hackathon rules, run it under a
   Splunk Developer License.)
2. The **official Splunk MCP Server** app installed into that Splunk instance
   ([Splunkbase app 7931](https://splunkbase.splunk.com/app/7931)):
   - Install the app and restart Splunk.
   - Grant the role you'll use the `mcp_tool_execute` (and, for full access, `mcp_tool_admin`)
     capability.
   - Create a Splunk **authentication token** for that user — this is the bearer token the agent
     sends. The endpoint is `https://<host>:8089/services/mcp`.
3. An **Anthropic API key** with access to `claude-opus-4-7`.

## Setup

```bash
# 1. clone and install deps
git clone <your-public-repo-url> ops-narrator && cd ops-narrator
uv sync

# 2. configure secrets
cp .env.example .env
#   then fill in (for the default 'official' backend):
#     ANTHROPIC_API_KEY=...
#     SPLUNK_HOST=localhost:8089        # host:port of the Splunk management API
#     SPLUNK_MCP_TOKEN=<bearer token>   # token created in Splunk (mcp_tool_execute)
```

`.env` is gitignored.

## Configuration — choosing the MCP backend

`OPS_MCP_BACKEND` selects how the agent reaches Splunk:

| Value | Server | Transport | Auth | Needs |
|---|---|---|---|---|
| `official` *(default)* | Official Splunk MCP Server (Splunkbase 7931) | streamable HTTP `…/services/mcp` | bearer token | `SPLUNK_MCP_TOKEN` + app installed in Splunk |
| `livehybrid` | community `livehybrid/splunk-mcp` | stdio subprocess | username/password | `SPLUNK_USERNAME`/`SPLUNK_PASSWORD` + `SPLUNK_MCP_DIR` |

TLS verification follows `VERIFY_SSL` (default `false` for a local self‑signed Splunk).

## Run

```bash
# Investigate the bundled demo alert and print the brief + trace path:
uv run python main.py

# Or investigate your own alert payload (JSON of SIEM fields):
uv run python main.py my_alert.json

# Tests
uv run pytest test_trace.py -v        # fast, offline (trace logger)
uv run pytest test_tools.py -v        # live: requires Splunk
uv run pytest test_agent.py -v -s     # live: requires Splunk + Anthropic key (full run)

# Reproduce the stability check (N live runs, scored clean/dirty)
uv run python validate_runs.py 5
```

Tunable via env vars: `OPS_MODEL`, `OPS_EFFORT` (low|medium|high|xhigh|max), `OPS_MAX_ITERS`,
`OPS_WALL_CLOCK_CAP`.

## Project layout

| Path | Purpose |
|---|---|
| `agent.py` | The agent loop: tool schemas, dispatch, model calls, iteration/time budgets |
| `tools.py` | The eight tool implementations (Splunk MCP client + payload decoder) |
| `trace.py` | Reconstructs a JSONL reasoning trace from a run |
| `main.py` | CLI entrypoint |
| `prompts/` | System prompt + user alert template |
| `validate_runs.py` | Multi‑run stability grader |
| `test_*.py` | Unit + live integration tests |
| `architecture_diagram.md` | Required architecture diagram |
| `SUBMISSION_CHECKLIST.md` | Hackathon submission/eligibility tracker |

## Status

Ops Narrator was **created during the Hackathon Submission Period** (first commit 2026‑05‑22).
Build progresses in numbered sessions tracked in `PROGRESS.md`:

- ✅ Tool wrappers over the Splunk MCP server (live‑tested)
- ✅ Agent loop (Claude Opus 4.7, adaptive thinking, budgeted)
- ✅ Reasoning‑trace logger (JSONL)
- ✅ Incident‑brief schema + system prompt
- ⬜ FastAPI webhook endpoint (`/alert`)
- ⬜ Splunk saved‑search alert action wiring
- ⬜ Single‑page trace viewer

The CLI (`main.py`) exercises the full investigate‑and‑brief path today; the webhook and viewer
package that path for the live demo.

## Attribution

- Investigation runs against the **BOTSv3** dataset (Splunk Boss of the SOC v3), used per its
  terms for research/education.
- Splunk access is via a **Splunk MCP server** (Model Context Protocol).
- Reasoning by **Anthropic Claude**.

This project builds on these tools and the Splunk platform; all original code here is the
authors' own work.

## License

[MIT](LICENSE).
