# Ops Narrator — Architecture

Ops Narrator turns a single Splunk alert into a finished incident investigation. A Splunk
saved search fires a webhook; an Anthropic Claude agent loop investigates by running its own
Splunk queries through the **Splunk MCP server**; it emits an incident brief and a JSONL
reasoning trace.

## System diagram

```mermaid
flowchart TD
    subgraph SPL["Splunk Enterprise"]
        IDX[("BOTSv3 index<br/>(Windows 4688, stream:http, …)")]
        SS["Saved search / alert<br/>(encoded PowerShell)"]
        REST["Splunk REST API<br/>:8089 (search)"]
        IDX --> SS
        IDX --- REST
    end

    subgraph APP["Ops Narrator (this repo)"]
        WH["FastAPI webhook<br/>POST /alert  (planned)"]
        CLI["main.py CLI<br/>(works today)"]
        LOOP["Agent loop — agent.py<br/>Claude Opus 4.7 + tools<br/>iteration & wall-clock budget"]
        TOOLS["Tool layer — tools.py<br/>8 tools incl. payload decoder"]
        MCPC["MCP client<br/>(HTTP, official backend)"]
        TRACE["Trace logger — trace.py"]
        BRIEF[["Incident brief (JSON)"]]
        TJ[["traces/*.jsonl"]]
        VIEW["Trace viewer (planned)"]
    end

    subgraph AI["Anthropic API"]
        CLAUDE["claude-opus-4-7<br/>adaptive thinking + tool use"]
    end

    MCP["Official Splunk MCP Server<br/>(Splunkbase app 7931, in-Splunk)<br/>HTTP :8089/services/mcp"]

    SS -- webhook --> WH
    WH --> LOOP
    CLI --> LOOP
    LOOP <-- "messages + tool_use / tool_result" --> CLAUDE
    LOOP --> TOOLS
    TOOLS --> MCPC
    MCPC -- "splunk_run_query (SPL, time window)<br/>bearer token over HTTP" --> MCP
    MCP -- "runs SPL internally" --> REST
    REST -- "rows (JSON)" --> MCP --> MCPC --> TOOLS --> LOOP
    LOOP --> BRIEF
    LOOP --> TRACE --> TJ --> VIEW
```

## Investigation sequence (one run)

```mermaid
sequenceDiagram
    participant Splunk
    participant App as Agent loop (agent.py)
    participant Claude as Claude Opus 4.7
    participant MCP as Official Splunk MCP Server (in-Splunk)

    Splunk->>App: alert (4688 encoded PowerShell)
    loop until finalize_brief / budget hit
        App->>Claude: messages + tool schemas
        Claude-->>App: thinking + tool_use (e.g. splunk_search)
        App->>MCP: splunk_run_query(SPL, earliest, latest) over HTTP + bearer
        MCP->>Splunk: runs the SPL internally
        Splunk-->>MCP: rows
        MCP-->>App: rows (JSON)
        App->>Claude: tool_result (+ latency, row_count captured)
    end
    Claude-->>App: finalize_brief(brief)
    App-->>Splunk: (planned) brief delivered / stored
    Note over App: trace.py writes traces/<run>.jsonl
```

## How the three required pieces connect

**1. How the application interacts with Splunk.**
Splunk is both the *trigger* and the *evidence store*. A saved search detects the suspicious
event and POSTs it to the webhook (`/alert`; the `main.py` CLI provides the same entrypoint
today). During the investigation, every Splunk‑backed tool in `tools.py` calls the **official
Splunk MCP Server** (Splunkbase app 7931) — which runs *inside* Splunk and is reached over
streamable HTTP at `https://<host>:8089/services/mcp` with a bearer token. Its `splunk_run_query`
tool executes the SPL against live Splunk data and returns rows; the app never embeds answers.
(A community `livehybrid/splunk-mcp` stdio backend is selectable via `OPS_MCP_BACKEND` as a
fallback.)

**2. How AI models / agents are integrated.**
The agent is a manual tool‑use loop (`agent.py`) over `claude-opus-4-7` with adaptive extended
thinking. Claude is given the alert plus eight tool schemas and chooses each next action; the
loop executes the tool, feeds the result back, and repeats under a hard iteration and
wall‑clock budget until Claude calls `finalize_brief`. The model‑facing prompt and tool
descriptions are intentionally generic (no dataset/threat/host/outcome names) so the agent
reasons from evidence rather than recall.

**3. Data flow between services, APIs, and components.**
`Splunk saved search → webhook/CLI → agent loop ⇄ Anthropic API` for reasoning, and
`agent loop → tools → MCP stdio client → Splunk MCP server → Splunk REST → rows back up the
same path`. Two artifacts fall out of every run: the **incident brief** (structured JSON from
`finalize_brief`) and a **JSONL reasoning trace** written by `trace.py` (one event per thinking
step, tool call with latency + row count, tool result, and detected hypothesis revision),
which the planned single‑page viewer renders.
