# Splunk Agentic Ops Hackathon — Submission Checklist

Tracks this project against the Official Rules. Key dates: **Submission Period
May 18 – Jun 15, 2026 9:00 AM PDT**. Track: **Security**.

## Done in this repo

- [x] **Open-source LICENSE** at repo root ([`LICENSE`](LICENSE), MIT) — edit the copyright
      line to your legal/team name before submitting.
- [x] **Clear README** with setup, run instructions, dependencies, and dataset/config notes
      ([`README.md`](README.md)).
- [x] **`architecture_diagram.md`** at repo root — shows Splunk interaction, AI/agent
      integration, and data flow (rule requires this exact filename, `.md|.pdf|.png`).
- [x] Runnable entrypoint (`main.py`) and reproducible tests / stability grader.
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
- [ ] **Install the official Splunk MCP Server** ([Splunkbase app 7931](https://splunkbase.splunk.com/app/7931))
      into your Splunk instance, restart Splunk, grant your role the `mcp_tool_execute`
      capability, and create an auth token. Put the token in `.env` as `SPLUNK_MCP_TOKEN`.
      Then validate end to end with `OPS_MCP_BACKEND=official uv run python main.py` (needs
      Anthropic credits). Until then, `OPS_MCP_BACKEND=livehybrid` keeps the community server
      working.
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

2. **"Best Use of Splunk MCP Server" bonus ($1,000).** ✅ *Addressed.* The agent now defaults
   to the **official Splunk MCP Server** (Splunkbase app 7931, `OPS_MCP_BACKEND=official`),
   reached over streamable HTTP at `…/services/mcp` with a bearer token; its `run_splunk_query`
   tool runs every investigative search. The community `livehybrid/splunk-mcp` stays as a
   selectable fallback. **Still to do (you):** install the app + token in Splunk and run one
   end‑to‑end validation once Anthropic credits are restored (the HTTP backend code is written
   but unvalidated against a live instance).

3. **"Best Use of Splunk Hosted Models" bonus ($1,000).** Not currently eligible — we use
   Claude, not Splunk‑hosted models (anomaly detection, forecasting, etc.). Pursuing this
   would require adding Splunk‑hosted‑model usage to the pipeline. *Likely out of scope; flag
   only.*

4. **"Best Use of Splunk Developer Tools" bonus ($1,000).** Partial — we use the Splunk SDK
   indirectly through the MCP server. Could be strengthened by packaging as a Splunk app and
   running App Inspect. *Optional.*

5. **Functionality matches description.** The README marks the FastAPI webhook and trace
   viewer as in‑progress. Before final submission, either finish them or make sure the video
   and text describe only what runs. (A project can only win one Grand/Track prize + one
   bonus prize.)
