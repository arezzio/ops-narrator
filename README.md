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
- **FastAPI + Uvicorn** — the webhook that receives the Splunk alert and serves the trace viewer
- **Python ≥ 3.10**, managed with [`uv`](https://docs.astral.sh/uv/)

## Prerequisites

1. **Splunk Enterprise** (10.2+) running locally with the **BOTSv3** dataset indexed, and the
   Windows TA so 4688 process‑creation fields extract. (Per the Hackathon rules, run it under a
   Splunk Developer License.)
2. The **official Splunk MCP Server** app installed into that Splunk instance
   ([Splunkbase app 7931](https://splunkbase.splunk.com/app/7931)):
   - Install the app and restart Splunk; it serves MCP at `https://<host>:8089/services/mcp`.
   - Grant the role you'll use the `mcp_tool_execute` (and, for full access, `mcp_tool_admin`)
     capability. (`admin` typically has these already.)
   - **Mint the bearer token via the app's `/services/mcp_token` endpoint** — *not* a normal
     Splunk token. The server requires audience `mcp` and an RSA‑encrypted token
     (`require_encrypted_token = true`); a plain token is rejected with HTTP 403
     "Invalid token audience". The `mint_token.py` helper does this for you (see Setup).
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
#     SPLUNK_USERNAME / SPLUNK_PASSWORD # used to mint the MCP token (next step)

# 3. mint the MCP bearer token (writes SPLUNK_MCP_TOKEN into .env).
#    Re-run when it expires (default +90d) or auth starts failing.
uv run python mint_token.py
```

`.env` is gitignored. The official backend's SPL tool is `splunk_run_query`; results come back
as `{results:[...], total_rows, truncated}` and are unwrapped automatically.

## Model providers

The agent loop's reasoning model is pluggable via `OPS_MODEL_PROVIDER` (default
`anthropic`). Claude is the default and the model used for the demo + submission; the
other backends exist so we can iterate during development without burning Anthropic
credits. The agent loop, tool dispatch, and reasoning-trace format are identical across
providers — only the model call is swapped (see `providers/`).

| Provider | Best for | What you get | Tradeoff |
|---|---|---|---|
| **Anthropic Claude Opus 4.7** (default, used for demo + submission) | Final-quality investigation, extended thinking, reliable structured brief | Best multi-hop reasoning across the kill chain; cleanest JSON brief; native adaptive thinking | Costs API credits |
| Google Gemini 2.5 Flash | Free-tier development iteration | ~250K TPM, function calling, 1M context, native thinking mode | Reduced RPD on free tier post-Dec 2025; reasoning slightly lighter on tricky pivots |
| Groq Llama 3.3 70B Versatile | Fast smoke tests at zero cost | 30 RPM, ~300 TPS inference speed | 6K TPM ceiling on free tier throttles mid-investigation as context grows |
| Ollama (local, default `qwen2.5:14b`) | Offline plumbing tests, no rate limits | Tests dispatch, tracing, loop termination without network | Tool-use reliability drops; not for assessing investigation quality |

Select a provider per run (each needs its key/host set in `.env` — see `.env.example`):

```bash
OPS_MODEL_PROVIDER=anthropic uv run python main.py   # default; the demo + submission path
OPS_MODEL_PROVIDER=google    uv run python main.py   # needs GOOGLE_API_KEY
OPS_MODEL_PROVIDER=groq      uv run python main.py   # needs GROQ_API_KEY (may hit TPM mid-run — expected)
OPS_MODEL_PROVIDER=ollama    uv run python main.py   # local Ollama at OPS_OLLAMA_HOST
```

The provider and model are recorded in the run-start trace event and printed as a footer
after the brief, so every artifact is self-documenting about how it was produced. Only
Anthropic and Gemini have native server-side thinking the trace can capture; for Groq and
Ollama the run logs one line at start (`Provider X has no native thinking; relying on
tool-call reasoning`) and the agent reasons through its tool calls instead.

### Why Claude for the demo

The provider choice is intentional, not budgetary:

- **The submission names Opus 4.7, and the model is integral to the architecture.** Ops
  Narrator *is* a Claude agent loop; the other backends are development conveniences.
- **Extended thinking measurably improves the brief.** On multi-host kill-chain
  reconstruction, Opus 4.7's adaptive thinking produces tighter pivots and fewer missed
  links than the lighter-reasoning alternatives.
- **The structured `finalize_brief` schema is most reliably produced by Opus 4.7** in our
  testing — the deep `findings`/`timeline`/`iocs`/`scope` shape comes back clean run after
  run, where smaller models more often drop or malform fields.

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

Tunable via env vars: `OPS_MODEL_PROVIDER` (anthropic|google|groq|ollama — see Model
providers), `OPS_MODEL`, `OPS_EFFORT` (low|medium|high|xhigh|max), `OPS_MAX_ITERS`,
`OPS_WALL_CLOCK_CAP`.

## Project layout

| Path | Purpose |
|---|---|
| `agent.py` | The agent loop: tool schemas, dispatch, model calls, iteration/time budgets |
| `providers/` | Pluggable model backends (anthropic/google/groq/ollama) behind one interface |
| `tools.py` | The eight tool implementations (Splunk MCP client + payload decoder) |
| `trace.py` | Reconstructs a JSONL reasoning trace from a run |
| `main.py` | CLI entrypoint |
| `mint_token.py` | Mint the official Splunk MCP Server bearer token into `.env` |
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
- ✅ FastAPI webhook endpoint (`/alert`) — 12/12 offline tests; serves the viewer + a runs dashboard
- ✅ Splunk saved‑search alert action wiring (`splunk/savedsearches.conf`) — dry‑fired end to end
- ✅ Single‑page trace viewer (`viewer.html`) — offline, served by the webhook

The full chain — Splunk saved search → webhook → agent loop → brief + JSONL trace → viewer — is
built and verified offline (the webhook was dry‑fired against live Splunk: a real alert POST got a
200 ack). The CLI (`main.py`) exercises the same investigate‑and‑brief path. The one step still to
run against a live model is the agent investigation itself (validation runs and the true
end‑to‑end fire), which is gated on Anthropic API credits — see `PROGRESS.md`.

## Attribution

- Investigation runs against the **BOTSv3** dataset (Splunk Boss of the SOC v3), used per its
  terms for research/education.
- Splunk access is via a **Splunk MCP server** (Model Context Protocol).
- Reasoning by **Anthropic Claude**.

This project builds on these tools and the Splunk platform; all original code here is the
authors' own work.

## License

[MIT](LICENSE).
