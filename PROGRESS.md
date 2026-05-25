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
  installed + working, HTTP at `https://localhost:8089/services/mcp`, bearer token
  `SPLUNK_MCP_TOKEN`. Re-mint the token with `uv run python mint_token.py`. See gotcha #11.
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
11. **Official Splunk MCP Server backend — VALIDATED (post-Session-4; for the "Best Use of Splunk MCP Server" bonus + Stage-One theme fit).** `tools.py` defaults to `OPS_MCP_BACKEND=official` — the official Splunk MCP Server (Splunkbase app **7931**, app dir `/Applications/Splunk/etc/apps/Splunk_MCP_Server`), over **streamable HTTP** at `https://<host>:8089/services/mcp` with a **bearer token** (`SPLUNK_MCP_TOKEN`). **8/8 `test_tools.py` pass live against it** (~5.5s total — far faster than livehybrid's per-call subprocess spawn, so gotcha #10's perf worry is largely moot on this backend). Hard-won setup facts:
    - **SPL tool is `splunk_run_query(query, earliest_time, latest_time, row_limit≤1000)`** — NOT the `run_splunk_query` the public docs implied, and NOT livehybrid's `search_splunk(search_query,…,max_results)`. Other tools: `splunk_get_info/_indexes/_index_info/_user_list/_user_info/_metadata/_kv_store_collections/_knowledge_objects/_run_saved_search`.
    - **Results are wrapped:** `{results:[...], total_rows, truncated}`. `_unwrap_official_rows()` unwraps so `row_count` is right (was reporting 1 instead of 3). SPL may start with `index=` (no leading `search`) — works.
    - **Token is the gotcha.** A normal Splunk token (Settings>Tokens or `/services/authorization/tokens`) is REJECTED → HTTP 403 `Invalid token audience`. Basic auth also 403. The app's `mcp.conf` sets `require_encrypted_token = true`; tokens must have **audience `mcp`** and be **RSA-encrypted with the server's public key**. Get one from the app's own endpoint **`GET /services/mcp_token?username=<u>&expires_on=+90d`** (authenticate with admin Basic; returns `{"token": <encrypted>}`). `mint_token.py` does this and writes `SPLUNK_MCP_TOKEN` into `.env`. Token auth must be enabled in Splunk (it was); `admin` already had `mcp_tool_admin`+`mcp_tool_execute`.
    - Connects via `mcp.client.streamable_http.streamablehttp_client` + custom `httpx_client_factory` honoring `VERIFY_SSL` (default off for local self-signed). (SDK warns to use `streamable_http_client` — cosmetic.)
    - `livehybrid` stdio backend retained as fallback (`OPS_MCP_BACKEND=livehybrid`), validated in Sessions 1–3.
    - **Still blocked:** full agent run (`main.py`/`validate_runs.py`) needs Anthropic credits (gotcha: out of credits). Tools/MCP path itself is proven.
12. **Real Splunk webhook payload shape — CAPTURED via a live dry-fire (Session 7, 2026-05-24).**
    Dispatched the saved search with `trigger_actions=1` against a running `webhook.py` and
    inspected exactly what Splunk POSTed. Findings:
    - **Shape is `{"result": {<first row fields>}, "search_name", "sid", "results_link", ...}`** —
      `build_alert` unwraps `result`. Confirmed correct.
    - **Multivalue fields arrive as JSON arrays with `"-"` as Splunk's null marker** —
      `Account_Name` came as `["BudStoll", "-"]`, not a scalar. `build_alert._collapse_mv` now
      drops null markers and collapses to a scalar (else the prompt rendered `['BudStoll','-']`).
      Also drops the parallel `__mv_<field>` twins. (Tested in `test_webhook.py`.)
    - **`_time` is delivered as an epoch string** (`"1534759188"` = 05:59:48 EDT). The agent
      computes its own windows so this is tolerated; not converted.
    - **BIG narrative finding: the real alert carries the FULL `-enc <base64>` blob**, not the
      spec's hand-truncated `SQBmACg...` stub (`main.py`'s `DEMO_TRIGGER` truncates it). So in the
      *live webhook path* the agent can decode the stager on turn 1 — the "agent pulls the full
      blob from the logs" step only happens with the truncated CLI demo trigger. Both paths are
      valid; **decide in Session 9 which to demo.** The kill-chain reconstruction (ancestry,
      cross-host spread, WMI lateral, C2) is unaffected — that always needs Splunk pivots.

## Plan / session checklist
- [x] **One-time setup** — uv project, deps, `.env` (all secrets), Splunk up, spec + tool-menu committed.
- [x] **Session 1 — Tool wrappers** — `tools.py` + `test_tools.py`, **8/8 passing** live. Confirmed: 3 hosts (BSTOLL-L/ABUNGST-L/FYODOR-L), 1 WMI lateral hit (FYODOR-L), UAC path empty (dead end).
- [x] **Session 2 — Agent loop** — `agent.py` (manual loop, opus-4-7, **adaptive thinking + effort** — not budget_tokens, see gotcha #9 — ≤12 model iters, 90s cap), `prompts/system.md` (anti-recall placeholder) + `prompts/user_template.md`, `run_agent()`, `test_agent.py`. Live test PASSES: loop pulled the full enc blob from Splunk, decoded the stager, mapped spread to 3 hosts, found the WMI lateral hit, and called `finalize_brief` (`stop_reason=finalized`). Agent investigated for real (found the SharePoint LNK lure + fodhelper UAC bypass on FYODOR-L, the path spec marked unvalidated).
- [x] **Session 3 — Trace logger** — `trace.py` (standalone; reconstructs JSONL from `run_agent` result) + `test_trace.py` (9/9 fast, offline). Wired into `run_agent` (writes `traces/trace-<utc>-<host>.jsonl` by default, guarded; `trace_path` in result). Live `test_agent.py` PASSES with trace assertions: real run produced **52 events** (8 thinking, 18 tool_call+18 tool_result, 4 assistant_text, **2 hypothesis_revision**). Event types: `run_started · thinking · assistant_text · tool_call · tool_result · hypothesis_revision · run_finished`.
- [~] **Session 4 — System prompt + brief schema** — **code done; single-run live verification PASSED 2026-05-24 (after key rotation restored credits); 5-consecutive-clean streak still pending.** A single `OPS_EFFORT=low` live run (trace `traces/trace-20260524-200934-BSTOLL-L.jsonl`) graded **CLEAN** under `validate_runs.evaluate` (finalized in 3 iters/242s, schema-complete P2 brief, all ground truth: 3 hosts, C2 45.77.53.176, WMI lateral, Empire stager + SharePoint .lnk lure; decode_payload + log searches called). **Cost ≈ \$1.2–1.4/run at low effort** (Opus 4.7 = **\$5/\$25 per Mtok in/out**, cache read 0.5 / write 6.25; an earlier note's "\$4.28" used wrong 15/75 rates). So a 5-run `validate_runs.py 5` ≈ **\$6–7**, not ~\$21. High effort (validate_runs default) costs somewhat more. NEXT: `uv run python validate_runs.py 5` for the 5-streak. Wrote the real anti-recall `prompts/system.md` (describes the brief shape + P1–P4 severity + evidence discipline; still no dataset/threat/host/IP/outcome names) and expanded `finalize_brief`'s schema to codify the Session-3 brief shape: `severity, headline, summary, findings[{title,evidence,mitre[],iocs[]}], timeline[], iocs{}, scope{}, gaps[], recommended_containment[]` (required: severity/headline/summary/findings/recommended_containment). Added `validate_runs.py` (grader scores each run clean = finalized + schema-complete + ground-truth substance: 3 hosts, C2 IP, WMI lateral, payload decoded — ground truth kept OUT of the prompt). Agent imports clean. **The 5-consecutive-clean-runs bar is NOT yet met:** the smoke run 400'd with "credit balance is too low" (the big Session-3 run drained the Anthropic account). Resume: top up API credits, then `uv run python validate_runs.py 5`.
- [ ] **Session 5 — Force the pivot** (tune tool *outputs/descriptions* — not the system prompt — so 8/10 runs show a clean hypothesis pivot).
- [x] **Session 6 — FastAPI webhook** — `webhook.py` + `test_webhook.py` (**10/10 offline, mocked
  `run_agent` — no credits/Splunk needed**). `POST /alert` maps the Splunk webhook `result` row →
  alert dict (`build_alert`, drops `__mv_*` twins, accepts bare dicts for curl), returns **200
  immediately** with `{status,run_id,brief_url}`, and runs the agent loop in a **background task**
  (FastAPI threadpool; `tools.py` is sync `asyncio.run` per call → safe). The compact run record
  (transcript + per-tool `input` stripped) is written to `briefs/<run_id>.json`; the JSONL trace to
  `traces/` by `run_agent`. A failed investigation still 200s and records `stop_reason:"error"`.
  **Also closes the Session-8 FastAPI-static-wiring gap:** serves `viewer.html` at `/viewer.html`,
  mounts `traces/` + `briefs/` static, and a `/` runs-dashboard (HTML, sev-colored, each row links
  `viewer.html?trace=traces/...`) + `/runs` JSON + `/healthz`. The viewer's existing relative
  `?trace=traces/<f>.jsonl` fetch resolves against these mounts **with zero viewer.html changes**.
  Live-booted the real app stack (mocked agent) — all routes verified. Run:
  `uv run uvicorn webhook:app --host 0.0.0.0 --port 8000`.
- [x] **Session 7 — Splunk saved search** — created via REST (`servicesNS/admin/search/saved/searches`):
  name **`Ops Narrator - Encoded PowerShell Beachhead`**, generic encoded-PS detection
  (`EventCode=4688 … "*powershell*" "*-enc*"`) + `| sort 0 _time | head 1 | table …` so it returns
  exactly one deterministic row = **patient zero BSTOLL-L at 03:59:48 CST (= 05:59:48 EDT, the spec's
  DEMO_TRIGGER)**. Alert when results>0, **webhook alert action → `http://localhost:8000/alert`**.
  Stored window is relative (`-24h`) so a stray background run matches nothing; **created
  `disabled=1`** to prevent any background firing (and accidental credit burn once topped up).
  **Dry-fire VERIFIED end-to-end (no credits spent):** enabled → dispatched with `trigger_actions=1`
  + explicit Aug-2018 window → Splunk POSTed to the live `webhook.py` → 200 ack, run_id
  `…-BSTOLL-L`, agent 400'd on credits (expected) and the error was recorded. Captured the real
  payload shape — see **gotcha #12** (multivalue `Account_Name`, epoch `_time`, full `-enc` blob).
  Re-disabled afterward. **To arm for the demo:** set `disabled=0`, then either let the cron fire or
  dispatch with `trigger_actions=1` + the Aug-2018 window. (Done over REST, not Splunk Web, but the
  saved search shows up in Web → Settings → Searches for the user to inspect/tune.)
- [~] **Session 8 — Trace UI** — `viewer.html` built (single self-contained file, **zero external/CDN
  assets** so it works offline at demo time). Renders an alert card + run config chips (from
  `run_started`), an outcome banner (severity/`stop_reason` badge, brief headline, stat grid:
  iterations/tool-calls/reasoning/pivots/elapsed/tokens, from `run_finished`), and an
  iteration-grouped, **filterable** timeline (Reasoning · Tool calls · Narration · Pivots toggles).
  Each `tool_call` is folded together with its matching `tool_result` (paired by `tool_use_id`) into
  one collapsible card with latency/row-count badges; `hypothesis_revision` renders as a highlighted
  pivot callout (cue + from/→to excerpts). Loads a trace 3 ways: file picker, drag-drop, and
  `?trace=<path>` fetch (the last is how FastAPI will serve it, and how it was verified). **VERIFIED
  offline (no Anthropic credits needed):** (a) data layer — Node assertions over `buildModel()` against
  both real traces (52- and 36-event), all event counts match and every tool call paired with its
  result; (b) visual layer — headless-Chrome screenshots of *both* trace shapes (8-pivot anthropic run
  and 0-pivot gemini run) render correctly. **Remaining:** wire it into FastAPI static serving — deferred
  to Session 6 (`webhook.py`), which doesn't exist yet. The pure transform fns (`parseTrace`,
  `buildModel`) are `module.exports`-guarded so they're Node-testable; DOM code is `typeof document`-guarded.
  **FastAPI-static wiring DONE in Session 6** (`webhook.py` serves `/viewer.html` + mounts `/traces`).
- [~] **Session 9 — Rehearsals / demo setup** (user; live-vs-prerecord decision). **Demo stack
  stood up + verified 2026-05-24 ($0, no model calls):** `uv run uvicorn webhook:app --host
  127.0.0.1 --port 8000`, then open **http://127.0.0.1:8000/** (runs dashboard). Synthesized
  `briefs/20260524T200934Z-BSTOLL-L.json` from the clean trace so the dashboard shows a P2 row →
  "open trace" → viewer renders the full reasoning timeline (alert card, outcome banner w/ correct
  C2 45.77.53.176, stat grid, foldable tool calls). Headless-Chrome screenshot confirms the visual.
  Direct viewer URL: `/viewer.html?trace=traces/trace-20260524-200934-BSTOLL-L.jsonl`. The replay
  path needs no credits/Splunk. **Open: live-fire (arm saved search disabled=0 + 1 high-effort run
  ~$1.50) vs pre-recorded replay of this clean trace.**
- [ ] **Session 10 — Polish + handoff** (README, 1-page handout, positioning slide, final recording).
  - *Compliance pass (done early):* `LICENSE` (MIT), `README.md`, `architecture_diagram.md`, and
    `SUBMISSION_CHECKLIST.md` added to satisfy the Hackathon's mandatory submission artifacts;
    `main.py` is now a real CLI entrypoint. Remaining for Session 10: 1-page handout, positioning
    slide, final recording, and the user-only items in `SUBMISSION_CHECKLIST.md` (public GitHub
    push, demo video, Devpost submit, Splunk Developer License).
    **DEMO_RUNBOOK.md added 2026-05-24** — full spoken script (timed, ~2 min), click-by-click
    actions, judge Q&A cheat sheet, backup plan, and a copy/paste 1-page handout. Built for the
    pre-recorded replay of `traces/trace-20260524-200934-BSTOLL-L.jsonl`. **POSITIONING_SLIDE.md +
    positioning_slide.html added 2026-05-24** — paste-ready slide text + an on-brand 16:9 HTML slide
    (dark/teal/amber, matches the viewer; open + screenshot at 2x for the deck). So the 1-page handout,
    demo script, AND positioning slide are now DRAFTED; still open: the final recording itself, and the
    user-only checklist items above. **Open strategic question flagged
    there:** whether to target the *official* Splunk MCP Server (vs the community one) for the
    "Best Use of Splunk MCP Server" bonus + Stage-One theme fit.

## Current position
**CREDITS RESTORED 2026-05-24 (user rotated the Anthropic key; 1-token probe returns OK).** The
first credited run is done: a single cheap probe (`OPS_EFFORT=low`) graded CLEAN — see Session 4.
Sessions 6, 7, 8 already complete/verified offline. **Remaining credit-gated work: the 5-clean
streak (`validate_runs.py 5`), Session 5 pivot tuning, the true end-to-end fire, and rehearsals.**
(History: at the prior session start a 1-token call returned `credit balance is too low`, so we
built out everything reachable offline:)
- **Session 6 — `webhook.py`** (FastAPI): `/alert` maps the Splunk payload → 200 ack → background
  `run_agent` → `briefs/<run_id>.json` + trace; serves `viewer.html` + `/traces` + `/briefs` + a `/`
  runs dashboard. `test_webhook.py` 12/12 (mocked agent); real app stack live-booted, all routes pass.
  Also closed Session 8's leftover FastAPI-static-wiring gap.
- **Session 7 — Splunk saved search** created via REST, **dry-fired end-to-end** (Splunk really
  POSTed to the live webhook; 200 ack; agent 400'd on credits as expected). Real payload shape
  captured → **gotcha #12** + a `build_alert` multivalue fix. Search left `disabled=1`.
- **Session 8 — `viewer.html`** (prior session) verified offline; now actually served by `webhook.py`.

**Credits are no longer the blocker — they're live.** This session (2026-05-24) we did TWO
live low-effort probe runs and added prompt caching. What's left:
- **Session 4 validation** — `uv run python validate_runs.py 5` (5 consecutive clean runs).
  **DECISION (2026-05-24, user): DON'T run the 5× high-effort streak** — it's reliability
  insurance the demo doesn't need, and it costs credits. Capability is already proven (Run 1
  clean, full ground truth). Treat Session 4 as functionally done for demo purposes; leave the
  checklist `[~]` (the formal 5-streak was never met, but we're deliberately skipping it).
  **What to spend on instead:** ONE **high-effort** run for the actual take the judges see
  (~\$1.50). Reason: at low effort, 1 of 2 runs truncated the C2 IP (`45.77.53.17`) — a bad
  look in a SOC brief; high effort de-risks the single demo run. Pre-record so you keep the
  best take. Build everything else NOW on the existing clean trace
  (`traces/trace-20260524-200934-BSTOLL-L.jsonl`) for \$0.
- **Session 5** — force the pivot (needs live runs to tune/measure).
- **The true end-to-end fire** — arm the saved search (`disabled=0`) → real Splunk alert → real
  agent run → brief + trace rendered in the viewer. (Plumbing is proven; only the agent run is gated.)
- **Sessions 9–10** — rehearsals, recording, handout (user-driven).

**What the two live runs this session established:**
- **Run 1 (low effort, baseline):** graded CLEAN under `validate_runs.evaluate` — finalized in 3
  iters, schema-complete P2 brief, all ground truth (3 hosts, C2 45.77.53.176, WMI, Empire stager,
  SharePoint .lnk). So the Session-4 schema + anti-recall system prompt are now LIVE-VERIFIED on a
  real run — the loop/MCP/schema path is sound.
- **Run 2 (low effort, after caching):** did MORE investigation (7 tool calls incl. find_lateral_execution),
  P1, all 3 hosts — but **truncated the C2 IP to `45.77.53.17`** (dropped the final `6`), so it graded
  DIRTY on the C2 check. This is **low-effort model variance, NOT a caching bug** (caching is billing-only;
  identical tokens reach the model). It's the concrete reason to run the 5-streak at **high effort**.
- So: the architecture is proven end-to-end live; the remaining Session-4 work is purely the
  5-consecutive-clean bar at demo (high) effort. (NB: `validate_runs.py` piped through `tee` masks the
  Python exit code — check the printed table / for a traceback, not just `$?`.)

**PUSHED TO PUBLIC GITHUB 2026-05-24: https://github.com/arezzio/ops-narrator** (`origin/master`).
Commit `f9ef92f` = prompt caching + demo kit; a follow-up commit bundles one verified-clean sample
(`traces/trace-20260524-200934-BSTOLL-L.jsonl` + `briefs/20260524T200934Z-BSTOLL-L.json`, force-added
past the gitignore) so a fresh clone is self-demoing (`uvicorn webhook:app` → dashboard → open trace).
`.env` verified absent from the remote and from history. Demo video reported made by user; remaining
submission items are user-only (Devpost submit, video upload, Splunk Developer License).

After Session 4 is verified, the plan continues at **Session 5 — Force the pivot** (tune tool
*outputs/descriptions* — not the system prompt — so 8/10 runs show a clean hypothesis pivot).
Note the Session-3 live run already produced a rich, well-structured brief and 2 genuine
hypothesis pivots, so the raw behavior is close; Sessions 4–5 are about *codifying the brief
shape* and *making the pivot reliable*.

Prompt caching (added 2026-05-24, after credits restored — out of the session sequence):
- `providers/anthropic_client.py` now sets `cache_control:ephemeral` rolling breakpoints
  on the last block of the most-recent 2 user turns (`_with_cache_breakpoints`,
  `_MSG_CACHE_BREAKPOINTS=2`), applied to a COPY (canonical loop transcript + trace stay
  clean; other providers never see cache_control). Combined with the existing system-block
  breakpoint (agent.py:410, caches tools+system) = 3 breakpoints, under the API max of 4.
  Anthropic-only by design (cache_control is an Anthropic feature).
- **LIVE-VERIFIED 2026-05-24:** a low-effort run went from `input=225254, cache_read=8544`
  (system-only caching) to `input=8, cache_read=222074, cache_write=156812` — the growing
  transcript (incl. a 500-row ancestry tool_result) is now read from cache, not re-sent.
  Apples-to-apples (same token stream, different billing) ≈ **42% prompt-token cost cut**
  on a 4-turn run; compounds on longer runs and across a within-5-min batch (the static
  tools+system prefix is reused run-to-run). `test_providers.py`/`test_trace.py`/
  `test_webhook.py` = 35 green after the change. NB: caching is billing-only — it does NOT
  change model output (verified: one run still truncated the C2 IP to `45.77.53.17` at low
  effort, a quality variance, not a cache artifact).

Multi-provider model support (added 2026-05-24, out of the session sequence):
- The model call is abstracted behind `providers/` (`base.py` + `anthropic_client.py`,
  `google_client.py`, `openai_compat.py`, factory in `__init__.py`). Select via
  `OPS_MODEL_PROVIDER` (default `anthropic`); the loop, tool dispatch, and trace format are
  unchanged. `agent.py` dispatches through `providers.get_client()` and records
  provider+model in the run-start trace config and the result; `main.py` prints a footer.
- Adapters: `anthropic` (unchanged behavior — adaptive thinking + `effort`, raw SDK blocks
  passed through to preserve thinking signatures), `google` (Gemini 2.5 Flash via the
  **new `google-genai` SDK**, not the deprecated `google-generativeai`; native thinking via
  `ThinkingConfig(include_thoughts=True)`), `groq` + `ollama` (both via the `openai` SDK's
  OpenAI-compatible endpoint; no native thinking → one info line logged at run start).
- Canonical in-loop transcript stays Anthropic-shaped; non-anthropic adapters re-translate
  the whole history each call and normalize responses back to Anthropic-shaped blocks.
- **Gemini schema gotcha:** Gemini's function-declaration schema is an OpenAPI subset —
  types must be UPPER-CASE and a property-less OBJECT is rejected, so `_clean_gemini_schema`
  uppercases types and **prunes the free-form `iocs` object** from `finalize_brief` (Gemini
  won't emit structured `iocs`; acceptable for a dev backend, `iocs` isn't required).
- **Verified:** offline `test_providers.py` (14 tests — schema translation + response
  normalization per provider, all mocked) and a fake-client end-to-end loop smoke. `uv run
  pytest test_trace.py test_providers.py` = 23 green.
- **LIVE-VERIFIED 2026-05-24** (groq + gemini keys added to `.env`). Both backends exercised
  against real Splunk MCP via `main.py` on the demo trigger:
  - **google / gemini-2.5-flash — plumbing WORKS end-to-end.** Loop ran 11 iters, dispatched
    all tools (splunk_search, find_process_ancestry, find_pattern_across_hosts, etc.),
    `stop_reason=finalized`, schema-complete brief, 91.8s. Schema cleaning + thinking config +
    Gemini→Anthropic block normalization all held up live.
    - **Fix applied this session:** free-tier gemini-2.5-flash caps at **5 requests/min**; the
      loop fires calls back-to-back and 429'd mid-run. `google_client.py` now retries 429s with
      backoff, honoring the server's `RetryInfo.retryDelay` (`_generate_with_retry` /
      `_retry_delay_seconds`, ≤6 retries). With rate-limit pacing a run needs a bigger
      `OPS_WALL_CLOCK_CAP` (used 480 here vs the 90 default).
    - **QUALITY IS INSUFFICIENT for the ground-truth bar — would fail `validate_runs.py`.** The
      run produced a *fabricated* incident: invented C2 `stollen.com` + `crypto.exe`, 1 host
      only, NO lateral movement, NO WMI, wrong C2 (truth is `45.77.53.176`). Root cause in the
      trace: Gemini never pulled the full encoded blob from the logs — it decoded the truncated
      `-enc SQBmACg...` stub from the alert (→ 2 chars, `"If�"`), then **fabricated a fake
      base64 blob and passed it to `decode_payload`**, which faithfully base64-decoded the
      garbage into mojibake (`If( [System.Net.DnsResolver] -methoddnew "stollen.com"...`). The
      tools are fine; the 2.5-flash model fabricates evidence. Confirms the architecture call:
      **anthropic = demo/quality backend; gemini = plumbing/dev iteration only.**
  - **groq / llama-3.3-70b-versatile — UNUSABLE on free tier.** Adapter formed a correct
    request, but the first call alone is ~19k tokens (system prompt + tool schemas) and the
    free `on_demand` tier caps at **12k TPM** → HTTP 413 `rate_limit_exceeded`. No single
    request can fit regardless of pacing; needs a paid tier or a much smaller prompt.
  - **Still NOT verified live:** the anthropic regression / Session-4 `validate_runs.py 5`
    (Anthropic account still out of credits — the whole reason gemini/groq were added).
    Neither free dev backend clears the 5-clean-runs bar, so real Session-4 validation still
    needs Anthropic credits (or possibly gemini-2.5-pro — tighter limits, unproven). Deps:
    `openai`, `google-genai`.

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
