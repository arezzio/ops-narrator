# Splunk Agentic Ops Hackathon — Submission Checklist

Tracks this project against the Official Rules. Key dates: **Submission Period
May 18 – Jun 15, 2026 9:00 AM PDT**. Track: **Security**.

## What's left (as of 2026‑05‑24)

The full pipeline is **built and verified offline end to end**: Splunk saved search →
webhook → agent loop → incident brief + JSONL trace → trace viewer. The remaining work
falls into two buckets:

**A. Blocked only on Anthropic API credits** (code is done; just needs a funded account):
- [ ] **Session 4 validation** — `uv run python validate_runs.py 5` (5 consecutive clean runs:
      finalized + schema‑complete + hits ground truth). The schema + system prompt are committed
      but never run against a live model.
- [ ] **Session 5 — force the pivot** — tune tool outputs/descriptions so 8/10 runs show a clean
      hypothesis pivot (needs live runs to measure).
- [ ] **One true end‑to‑end fire** — arm the saved search (`disabled = 0`) → real Splunk alert →
      real agent run → brief + trace rendered in the viewer. (Plumbing already dry‑fired; only the
      agent run is gated.)

**B. Entrant‑only actions** (cannot be done from the repo — see sections below):
public GitHub push, demo video, Devpost submission, Splunk Developer License, eligibility
confirmation, plus polish (1‑page handout, positioning slide, final recording).

> The hard blocker is **Anthropic credits**. Everything that does not require them is finished.

## Done in this repo

- [x] **Open-source LICENSE** at repo root ([`LICENSE`](LICENSE), MIT) — edit the copyright
      line to your legal/team name before submitting.
- [x] **Clear README** with setup, run instructions, dependencies, and dataset/config notes
      ([`README.md`](README.md)).
- [x] **`architecture_diagram.md`** at repo root — shows Splunk interaction, AI/agent
      integration, and data flow (rule requires this exact filename, `.md|.pdf|.png`).
- [x] Runnable entrypoint (`main.py`) and reproducible tests / stability grader.
- [x] **FastAPI webhook** ([`webhook.py`](webhook.py)) — `POST /alert` maps the Splunk payload,
      returns 200 immediately, runs the agent in the background, and persists `briefs/` + `traces/`;
      also serves the viewer, a runs dashboard, `/runs`, and `/healthz`. **12/12 offline tests**
      (`test_webhook.py`); **dry‑fired end to end** against live Splunk (real POST → 200 ack).
- [x] **Splunk saved search + webhook alert action** — created and version‑controlled as
      [`splunk/savedsearches.conf`](splunk/savedsearches.conf); deterministically fires on the
      patient‑zero event and POSTs to the webhook. Dry‑fired end to end (shipped `disabled` until
      demo time).
- [x] **Trace viewer** ([`viewer.html`](viewer.html)) — single self‑contained, offline‑capable
      file; renders the alert, outcome, and a filterable reasoning timeline. Served by the webhook.
- [x] All materials in English.
- [x] Created **during** the Submission Period (first commit 2026‑05‑22).

## Action required by the entrant (cannot be done from the repo)

- [ ] **Push to a PUBLIC GitHub repo** and confirm the MIT license is auto‑detected and shown
      in the repo's **About** sidebar (the rules require the license be visible at the top of
      the repo page). The repo has **no remote yet** — create one and `git push`.
- [ ] **Demo video < 3 minutes**, public on YouTube/Vimeo/Youku. Must show the project
      functioning, how AI is used, the problem, and the value. Only demo features that
      actually work in the recording (rules: must function "as depicted in the video").
      No third‑party trademarks / copyrighted music without permission.
- [ ] **Register on Devpost** (splunk.devpost.com) and complete the **Enter a Submission**
      form before the deadline, selecting the **Security** track and pasting the public repo
      URL + video link.
- [ ] **Splunk setup per rules:** free Splunk account, Splunk Enterprise trial, and a
      **Developer License** applied to the instance (via dev.splunk.com developer program).
- [x] **Official Splunk MCP Server installed + wired up.** App 7931 installed; `admin` has
      `mcp_tool_execute`/`mcp_tool_admin`; token minted via `/services/mcp_token`
      (`mint_token.py`) into `.env`. **8/8 live tool tests pass** against it
      (`uv run pytest test_tools.py`). Remaining: one full agent run end to end
      (`uv run python main.py`) once Anthropic credits are restored.
- [ ] **Confirm eligibility:** age of majority; not in an excluded jurisdiction; not an
      employee/affiliate of Cisco/Splunk or Devpost, a government/state‑owned entity, or a
      Judge; team ≤ 2 with a designated Representative if applicable.
- [ ] (Optional, separate prize) **Most Valuable Feedback** — submit one actionable feedback
      form during the Feedback Period for a chance at a $200 bonus. Note: entrants who *only*
      submit feedback are not eligible for other prizes.

## ⚠️ Eligibility / strategy flags — please decide

1. **"Leverage Splunk's latest AI capabilities" (Stage One pass/fail).** Stage One screens
   whether a Project "reasonably fits the theme and reasonably applies the required APIs/SDKs."
   Ops Narrator uses **Splunk data via an MCP server** for the investigation but does the
   *reasoning* with **Anthropic Claude**, not a Splunk‑hosted model. This is a legitimate
   Security‑track agentic design, but confirm the judges accept a third‑party LLM as
   satisfying "Splunk's AI capabilities." If in doubt, the rules let you submit a written
   request for clarification before the deadline.

2. **"Best Use of Splunk MCP Server" bonus ($1,000).** ✅ *Done & validated.* The agent uses
   the **official Splunk MCP Server** (Splunkbase app 7931, `OPS_MCP_BACKEND=official`) over
   streamable HTTP at `…/services/mcp` with an encrypted bearer token; its `splunk_run_query`
   tool runs every investigative search, validated by 8/8 live tool tests. The community
   `livehybrid/splunk-mcp` stays as a selectable fallback. Only a full agent run remains
   (blocked on Anthropic credits, not on Splunk).

3. **"Best Use of Splunk Hosted Models" bonus ($1,000).** Not currently eligible — we use
   Claude, not Splunk‑hosted models (anomaly detection, forecasting, etc.). Pursuing this
   would require adding Splunk‑hosted‑model usage to the pipeline. *Likely out of scope; flag
   only.*

4. **"Best Use of Splunk Developer Tools" bonus ($1,000).** Partial — we use the Splunk SDK
   indirectly through the MCP server. Could be strengthened by packaging as a Splunk app and
   running App Inspect. *Optional.*

5. **Functionality matches description.** The FastAPI webhook, Splunk saved‑search trigger, and
   trace viewer are now **built and verified offline** (the webhook dry‑fired end to end against
   live Splunk). The **only** thing not yet shown working against a real model is the agent run
   itself (credit‑gated). Before final submission, record the demo only after one real
   end‑to‑end fire succeeds, so the video shows the project functioning "as depicted." (The
   README Status section has been updated to reflect the now‑built components.) A project can win
   only one Grand/Track prize + one bonus prize.
