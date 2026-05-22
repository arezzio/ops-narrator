# Ops Narrator — Build Progress / Resume Doc

**Read this first when resuming.** This is the single source of truth for where the build is.
Update the "Current position" section and the session checklist whenever state changes.

---

## What we're building
An AI SOC analyst. A Splunk saved search fires a webhook on encoded PowerShell (4688);
a FastAPI endpoint hands the alert to an Anthropic agent loop (`claude-opus-4-7`, extended
thinking) that runs follow-up Splunk queries via the `splunk-mcp` stdio client, decodes the
C2 stager, reconstructs the kill chain across hosts, and writes a SOC-grade incident brief +
a JSONL trace of its reasoning. Demo for Splunk Agentic Ops Hackathon, **due 2026-06-15**.

Full detail: `ops-narrator-demo-spec-2.md` (in repo root). Tool definitions: `tool-menu.md`.

## Key locations
- **This repo:** `/Users/arezziorietti/ops-narrator` (git, branch `master`)
- **Spec:** `ops-narrator-demo-spec-2.md` · **Tool menu:** `tool-menu.md`
- **MCP backend (default `official`):** the official Splunk MCP Server (Splunkbase app **7931**),
  in-Splunk, HTTP at `https://localhost:8089/services/mcp`, bearer token `SPLUNK_MCP_TOKEN`.
  **Not yet installed in Splunk** — see gotcha #11.
- **MCP backend (fallback `livehybrid`):** `/Users/arezziorietti/splunk-mcp` community stdio server
  (run: `uv --directory /Users/arezziorietti/splunk-mcp run python splunk_mcp.py stdio`).
- **Secrets:** `.env` (gitignored) — `ANTHROPIC_API_KEY`, `SPLUNK_MCP_TOKEN` (official),
  `SPLUNK_USERNAME=admin`/`SPLUNK_PASSWORD` (livehybrid), `SPLUNK_HOST=localhost:8089`
- **Splunk app:** `/Applications/Splunk` (start: `/Applications/Splunk/bin/splunk start`)

## Verified environment facts (as of 2026-05-22)
- Anthropic key authenticates; `claude-opus-4-7` available on the account.
- Splunk **10.2.3** running; `botsv3` index loaded; admin auth verified over REST `:8089`.
- `search_splunk` MCP tool signature: `search_splunk(search_query, earliest_time="-24h", latest_time="now", max_results=100) -> List[Dict]`. It **auto-prepends `search `** if the query doesn't start with `|`/`search`, and accepts **ISO times** as kwargs.

## Critical gotchas (don't relearn these the hard way)
1. **Env-var shape:** splunk-mcp wants `SPLUNK_HOST` (host only) + `SPLUNK_PORT` + `SPLUNK_SCHEME=https` + `VERIFY_SSL=false`, but our `.env` stores `SPLUNK_HOST=localhost:8089`. `tools.py` splits it before spawning the subprocess.
2. **Auth = username/password.** `SPLUNK_TOKEN` bearer auth is broken with this SDK version. Do not set `SPLUNK_TOKEN` in the subprocess env.
3. **Time params:** pass `earliest_time`/`latest_time` as **separate ISO strings** (`2018-08-20T05:59:00`), never inline in SPL.
4. **Anti-recall:** model-facing tool descriptions + system prompt must NOT name BOTSv3, Frothly, Empire, the specific hosts, or the expected outcome. (Sessions 4–5.)
5. **Data quirks:** no Sysmon — use `EventCode=4688` with `source="WinEventLog:Security"` (case-sensitive when quoted). Timestamps are Aug 2018 (use explicit time windows).
6. **Pre-existing warning:** `botsv3_data_set` app `props.conf:102` has a bad `EXTRACT-src` regex (parse error at startup). Not ours; may affect `src_ip` extraction.
7. **TIMEZONE (confirmed Session 1):** this Splunk server renders in **CST (UTC-6)**, but the spec quotes **EDT (UTC-4)** wall-clock — a **2-hour** difference. Bare ISO `earliest_time`/`latest_time` are interpreted in CST. So the spec's trigger "05:59:48 EDT" is **03:59:48 CST**; the WMI lateral hit "06:15:27 EDT" is **04:15:27 CST**. When the agent builds windows in Session 2, do the EDT→CST shift (or pass offset-aware times) and keep windows wide. Tests use a `2018-08-20T03:00:00`–`08:00:00` morning window.
8. **Account-name quirk (confirmed Session 1):** 4688 process events use short names (`BudStoll`); 4624/4625 auth events store email/machine forms (`bstoll@froth.ly`, `BSTOLL-L$`). A `Account_Name=BudStoll` filter misses auth events — `trace_account_activity` matches the **bare keyword** instead, which hits the raw event text. (This is why spec Query 6 was never validated.)
9. **Opus 4.7 thinking API (confirmed Session 2):** the spec's "thinking budget 8000" does **not** translate to `thinking={"type":"enabled","budget_tokens":N}` — that **400s on `claude-opus-4-7`** (budget_tokens, temperature, top_p, top_k all removed). `agent.py` uses `thinking={"type":"adaptive","display":"summarized"}` + `output_config={"effort": EFFORT}` (env `OPS_EFFORT`, default `high`). `display:"summarized"` matters: 4.7 omits thinking text by default, so Session 3's trace logger needs it on to capture reasoning. SDK `anthropic 0.104.1` supports both.
10. **MCP subprocess perf (observed Session 2):** every Splunk-backed tool call spawns a fresh `uv --directory … run python splunk_mcp.py stdio` subprocess (~0.7–1.1s each) plus opus thinking time. A full investigation ran **~5 min** — far over the 90s wall-clock cap and the demo's <30s target. The cap is honored (loop exits), but for the live demo we'll need MCP connection reuse / a persistent session. Not blocking the loop build; flagged for a perf pass before rehearsals. NB: this was measured on the `livehybrid` stdio backend; the `official` HTTP backend (gotcha #11) avoids the per-call subprocess spawn and should be faster — re-measure once it's live.
11. **Official Splunk MCP Server backend (added post-Session-4, for the "Best Use of Splunk MCP Server" bonus + Stage-One theme fit):** `tools.py` now defaults to `OPS_MCP_BACKEND=official` — the official Splunk MCP Server (Splunkbase app **7931**, supports Splunk Enterprise 10.2+), reached over **streamable HTTP** at `https://<host>:8089/services/mcp` with a **bearer token** (`SPLUNK_MCP_TOKEN`; role needs `mcp_tool_execute`). SPL tool is **`run_splunk_query(query, earliest_time, latest_time)`** (NOT livehybrid's `search_splunk(search_query,…,max_results)`); `run_search()` branches on backend. Connects via `mcp.client.streamable_http.streamablehttp_client` with a custom `httpx_client_factory` that honors `VERIFY_SSL` (default off, for local self-signed TLS). **UNVALIDATED against a live instance:** the app is **not yet installed in Splunk** and we're out of Anthropic credits, so the HTTP path, the result-shape normalization (`_rows_from_result` may need tweaks for the official server's output), and token auth are all unproven. The `livehybrid` stdio backend is fully retained as a fallback (`OPS_MCP_BACKEND=livehybrid`) and is the one validated in Sessions 1–3. **To validate:** install app 7931, grant `mcp_tool_execute`, create token → `SPLUNK_MCP_TOKEN`, restore credits, then `OPS_MCP_BACKEND=official uv run python main.py`.

## Plan / session checklist
- [x] **One-time setup** — uv project, deps, `.env` (all secrets), Splunk up, spec + tool-menu committed.
- [x] **Session 1 — Tool wrappers** — `tools.py` + `test_tools.py`, **8/8 passing** live. Confirmed: 3 hosts (BSTOLL-L/ABUNGST-L/FYODOR-L), 1 WMI lateral hit (FYODOR-L), UAC path empty (dead end).
- [x] **Session 2 — Agent loop** — `agent.py` (manual loop, opus-4-7, **adaptive thinking + effort** — not budget_tokens, see gotcha #9 — ≤12 model iters, 90s cap), `prompts/system.md` (anti-recall placeholder) + `prompts/user_template.md`, `run_agent()`, `test_agent.py`. Live test PASSES: loop pulled the full enc blob from Splunk, decoded the stager, mapped spread to 3 hosts, found the WMI lateral hit, and called `finalize_brief` (`stop_reason=finalized`). Agent investigated for real (found the SharePoint LNK lure + fodhelper UAC bypass on FYODOR-L, the path spec marked unvalidated).
- [x] **Session 3 — Trace logger** — `trace.py` (standalone; reconstructs JSONL from `run_agent` result) + `test_trace.py` (9/9 fast, offline). Wired into `run_agent` (writes `traces/trace-<utc>-<host>.jsonl` by default, guarded; `trace_path` in result). Live `test_agent.py` PASSES with trace assertions: real run produced **52 events** (8 thinking, 18 tool_call+18 tool_result, 4 assistant_text, **2 hypothesis_revision**). Event types: `run_started · thinking · assistant_text · tool_call · tool_result · hypothesis_revision · run_finished`.
- [~] **Session 4 — System prompt + brief schema** — **code done, live validation BLOCKED on API credits.** Wrote the real anti-recall `prompts/system.md` (describes the brief shape + P1–P4 severity + evidence discipline; still no dataset/threat/host/IP/outcome names) and expanded `finalize_brief`'s schema to codify the Session-3 brief shape: `severity, headline, summary, findings[{title,evidence,mitre[],iocs[]}], timeline[], iocs{}, scope{}, gaps[], recommended_containment[]` (required: severity/headline/summary/findings/recommended_containment). Added `validate_runs.py` (grader scores each run clean = finalized + schema-complete + ground-truth substance: 3 hosts, C2 IP, WMI lateral, payload decoded — ground truth kept OUT of the prompt). Agent imports clean. **The 5-consecutive-clean-runs bar is NOT yet met:** the smoke run 400'd with "credit balance is too low" (the big Session-3 run drained the Anthropic account). Resume: top up API credits, then `uv run python validate_runs.py 5`.
- [ ] **Session 5 — Force the pivot** (tune tool *outputs/descriptions* — not the system prompt — so 8/10 runs show a clean hypothesis pivot).
- [ ] **Session 6 — FastAPI webhook** (`webhook.py` POST `/alert`, 200 + background task, writes `briefs/` + `traces/`).
- [ ] **Session 7 — Splunk saved search** (in Splunk Web; webhook alert action → `http://localhost:8000/alert`).
- [ ] **Session 8 — Trace UI** (`viewer.html`, single-file, served via FastAPI static).
- [ ] **Session 9 — Rehearsals** (user; 3 consecutive clean runs; live-vs-prerecord decision).
- [ ] **Session 10 — Polish + handoff** (README, 1-page handout, positioning slide, final recording).
  - *Compliance pass (done early):* `LICENSE` (MIT), `README.md`, `architecture_diagram.md`, and
    `SUBMISSION_CHECKLIST.md` added to satisfy the Hackathon's mandatory submission artifacts;
    `main.py` is now a real CLI entrypoint. Remaining for Session 10: 1-page handout, positioning
    slide, final recording, and the user-only items in `SUBMISSION_CHECKLIST.md` (public GitHub
    push, demo video, Devpost submit, Splunk Developer License). **Open strategic question flagged
    there:** whether to target the *official* Splunk MCP Server (vs the community one) for the
    "Best Use of Splunk MCP Server" bonus + Stage-One theme fit.

## Current position
**Session 4 code is committed but UNVERIFIED — live validation is blocked on Anthropic API
credits** (smoke run returned HTTP 400 "credit balance is too low"). The earlier Session-3
live run this session passed cleanly, so the loop/MCP path is sound; only the new schema +
system prompt are unverified against a real run. **To finish Session 4:** add API credits,
run `uv run python validate_runs.py 5`, fix anything that isn't clean, then flip the checklist
item to `[x]`. (NB: `validate_runs.py` piped through `tee` masks the Python exit code — check
the printed table / for a traceback, not just `$?`.)

After Session 4 is verified, the plan continues at **Session 5 — Force the pivot** (tune tool
*outputs/descriptions* — not the system prompt — so 8/10 runs show a clean hypothesis pivot).
Note the Session-3 live run already produced a rich, well-structured brief and 2 genuine
hypothesis pivots, so the raw behavior is close; Sessions 4–5 are about *codifying the brief
shape* and *making the pivot reliable*.

Trace-logger notes for whoever builds the Session 8 viewer:
- `trace.py` is standalone (no `agent` import); `agent` imports it. `run_agent(alert, trace=True)`
  writes `traces/trace-<utc>-<host>.jsonl` and sets `result["trace_path"]` (failure is caught →
  `result["trace_error"]`, never breaks the run). `traces/` + `briefs/` are gitignored.
- Each line is one event with `seq` (dense 0..n) + `type`. `build_events(result, alert=, config=)`
  is the pure reconstruction fn; `read_trace(path)` loads it back.
- `hypothesis_revision` is a heuristic: a curated pivot-cue word (`PIVOT_CUES`) appearing in a
  thinking block that has a prior block. On the live run it fired 2× and both were genuine
  pivots (search-strategy backtrack; .lnk/SharePoint reinterpretation) — tune `PIVOT_CUES` in
  Session 5 if recall is off. Tool-result content is truncated at `CONTENT_CAP` (6000 chars).
- We do NOT capture per-event wall-clock timestamps (only tool `latency_ms`); ordering is `seq`.
  If the viewer wants a real timeline, add `ts` capture in the loop (additive) later.

## How to resume
1. `cd ~/ops-narrator` and read this file + `tool-menu.md`.
2. Ensure Splunk is up: `/Applications/Splunk/bin/splunk status` (start if needed).
3. Check `git log --oneline` for the last committed step.
4. Continue from "Current position".
