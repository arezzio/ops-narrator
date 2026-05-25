# Ops Narrator — Positioning Slide

The single "why this wins" slide for the Devpost submission / pitch deck. Paste-ready text
below; a matching on-brand visual is in `positioning_slide.html` (open it, screenshot the
16:9 frame, drop it in your deck).

---

## Slide content (paste into your deck)

**TITLE:** Ops Narrator — An AI SOC Analyst

**TAGLINE:** One alert in. A verified incident brief out. And it shows its work.

**PROBLEM (one line):**
A SOC gets more alerts than humans can investigate. A single "encoded PowerShell ran" alert is
hours of work — so most are triaged in seconds and closed.

**SOLUTION (one line):**
A Splunk saved search fires a webhook; an autonomous agent investigates the alert end to end and
writes the SOC-grade brief — decode, kill chain, blast radius, containment.

**WHY IT'S DIFFERENT (4 pillars):**

1. **Runs on real Splunk.** Every investigative step goes through the **official Splunk MCP
   Server** (Splunkbase app 7931) over streamable HTTP — not hardcoded answers. *(→ Best Use of
   Splunk MCP Server)*
2. **No hardcoding — provable integrity.** The tools and system prompt never name the dataset,
   threat, hosts, or expected answer. The agent **rediscovers the incident from data on every run.**
3. **Explainable, not a black box.** Every run emits a step-by-step reasoning trace — thinking,
   each tool call with latency + row counts, results, hypothesis pivots — rendered in a viewer an
   analyst can audit.
4. **Autonomous but bounded.** Claude Opus 4.7 with adaptive thinking and hard iteration +
   wall-clock budgets. It investigates like an analyst; it can't run away.

**PROOF (this demo — bottom strip):**
From **one** Windows 4688 event, the agent autonomously found: an **Empire PowerShell stager**
delivered by a malicious SharePoint `.lnk` lure → **AMSI + ScriptBlock-logging** defense evasion →
HTTPS **C2 to 45.77.53.176** → the same campaign hitting **3 users in parallel** — with MITRE
ATT&CK mappings, a cross-host timeline, IOCs, and containment steps. Verdict **P2**. ~4 minutes.

**FOOTER:** Splunk Agentic Ops Hackathon · Security track · Claude Opus 4.7 · Splunk MCP Server · FastAPI · `botsv3`

---

## The 10-second version (if you only get one sentence)

> "Ops Narrator turns a raw Splunk alert into a verified, SOC-grade incident brief automatically —
> running its investigation over real Splunk through the official MCP server, with nothing about
> the answer hardcoded, and a full trace of how it reasoned."

## Design notes
- Lead the eye with the **two strongest differentiators**: official Splunk MCP Server (the bonus
  category) and the anti-recall integrity. Those are what set it apart from a scripted demo.
- The proof strip is the credibility anchor — concrete findings from one event.
- Keep it to one slide. The reasoning-trace screenshot from the demo can be a second/backup slide.
