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

2. **"Best Use of Splunk MCP Server" bonus ($1,000).** This is our strongest bonus fit — the
   whole agent is MCP‑driven. **Confirm whether the judges expect the *official* Splunk MCP
   Server** versus the community MCP server this project currently shells out to. If an
   official Splunk MCP Server exists, switching to it (or supporting both) materially
   strengthens this bonus claim. *Decision needed.*

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
