"""Ops Narrator webhook — the FastAPI endpoint a Splunk saved-search alert action POSTs to.

A Splunk "webhook" alert action fires an HTTP POST when the encoded-PowerShell saved
search matches. This service accepts that POST, maps the triggering result row to the
alert dict the agent expects, returns **200 immediately**, and runs the investigation in
the background (the agent loop is minutes long; Splunk's webhook action times out fast).
The finished incident brief is written to ``briefs/<run_id>.json`` and the JSONL reasoning
trace to ``traces/`` (by ``run_agent`` itself).

Routes
    POST /alert        Splunk webhook target. 202-style ack with run_id (returns 200).
    GET  /             Recent-runs dashboard (HTML), links each run into the viewer.
    GET  /runs         JSON list of runs (for polling / programmatic use).
    GET  /viewer.html  The single-file trace UI (static).
    GET  /traces/...   JSONL traces (static; the viewer fetches ``?trace=traces/...``).
    GET  /briefs/...   Finished briefs (static JSON).
    GET  /healthz      Liveness probe.

Run it:  ``uv run uvicorn webhook:app --host 0.0.0.0 --port 8000``
Then point a Splunk webhook alert action at ``http://localhost:8000/alert`` (Session 7).

The agent call is reached through the module-level ``agent`` import, so tests can
monkeypatch ``agent.run_agent`` and exercise the whole HTTP + persistence path offline,
with no Anthropic credits or live Splunk.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import agent

log = logging.getLogger("ops_narrator")

ROOT = Path(__file__).resolve().parent
TRACES_DIR = ROOT / "traces"
BRIEFS_DIR = ROOT / "briefs"
VIEWER = ROOT / "viewer.html"

# The fields the agent's user-template reads (see agent._build_user_text). Splunk's
# webhook `result` row delivers them under these exact keys via the saved search's
# `| table` clause; anything missing degrades gracefully to "".
ALERT_FIELDS = (
    "_time", "time", "index", "sourcetype", "source",
    "EventCode", "event_code", "host",
    "Account_Name", "account",
    "Creator_Process_Name", "parent_process",
    "New_Process_Name", "new_process",
    "Process_Command_Line", "command_line",
)

# Keep these out of the persisted run record: `messages` is huge (full transcript +
# thinking), and per-tool `input` can carry large blobs (encoded payloads, the brief).
_HEAVY_KEYS = ("messages",)


def build_alert(payload: dict) -> dict:
    """Map a raw webhook POST body to the alert dict ``run_agent`` expects.

    Splunk's webhook action wraps the triggering row under ``result``; a bare alert
    dict (curl tests, other SIEMs) is accepted as-is. Splunk also emits parallel
    ``__mv_<field>`` multivalue keys — we ignore those and keep the plain values.
    """
    row = payload.get("result", payload)
    if not isinstance(row, dict):
        return {}
    return {k: v for k, v in row.items() if not k.startswith("__mv_")}


def _slug(value: str, *, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-")
    return cleaned or fallback


def _make_run_id(alert: dict) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(alert.get('host'), fallback='host')}"


def _compact_record(result: dict) -> dict:
    """Strip the heavy transcript and per-call inputs from a run result for persistence."""
    record = {k: v for k, v in result.items() if k not in _HEAVY_KEYS}
    calls = record.get("tool_calls")
    if isinstance(calls, list):
        record["tool_calls"] = [
            {k: c.get(k) for k in ("name", "latency_ms", "row_count", "is_error")}
            for c in calls if isinstance(c, dict)
        ]
    return record


def _investigate(alert: dict, run_id: str, meta: dict) -> None:
    """Background worker: run the agent loop and persist a compact run record.

    Guarded end to end — a webhook handler must never crash the server, and a failed
    investigation should still leave a readable record explaining why.
    """
    record: dict[str, Any] = {"run_id": run_id, **meta, "alert": alert}
    try:
        result = agent.run_agent(alert)
        record.update(_compact_record(result))
        log.info(
            "run %s finished: stop=%s iters=%s elapsed=%ss",
            run_id, result.get("stop_reason"), result.get("iterations"),
            result.get("elapsed_sec"),
        )
    except Exception as e:  # pragma: no cover - defensive; surfaced into the record
        record["stop_reason"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"
        log.exception("run %s crashed", run_id)
    try:
        BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
        (BRIEFS_DIR / f"{run_id}.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
    except Exception:  # pragma: no cover - last-resort guard
        log.exception("failed to persist brief for run %s", run_id)


def _load_runs() -> list[dict]:
    """Read persisted run records newest-first (cheap dir scan; survives restarts)."""
    if not BRIEFS_DIR.exists():
        return []
    runs = []
    for f in sorted(BRIEFS_DIR.glob("*.json"), reverse=True):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return runs


def _run_summary(rec: dict) -> dict:
    brief = rec.get("brief") or {}
    return {
        "run_id": rec.get("run_id"),
        "received_at": rec.get("received_at"),
        "search_name": rec.get("search_name"),
        "host": (rec.get("alert") or {}).get("host"),
        "stop_reason": rec.get("stop_reason"),
        "severity": brief.get("severity"),
        "headline": brief.get("headline"),
        "iterations": rec.get("iterations"),
        "elapsed_sec": rec.get("elapsed_sec"),
        "provider": rec.get("provider"),
        "model": rec.get("model"),
        "trace_path": rec.get("trace_path"),
        "error": rec.get("error"),
    }


app = FastAPI(title="Ops Narrator", description="AI SOC analyst webhook + trace viewer")


def _ensure_dirs() -> None:
    # StaticFiles refuses to mount a missing directory; create run-artifact dirs first.
    # Called at import (below) before the mounts — no startup hook needed.
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/alert")
async def alert(request: Request, background: BackgroundTasks) -> JSONResponse:
    """Splunk webhook target. Acks fast; investigates in the background."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    alert_dict = build_alert(payload)
    run_id = _make_run_id(alert_dict)
    meta = {
        "received_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "search_name": payload.get("search_name"),
        "sid": payload.get("sid"),
        "results_link": payload.get("results_link"),
    }
    background.add_task(_investigate, alert_dict, run_id, meta)
    log.info("accepted alert run %s (host=%s)", run_id, alert_dict.get("host"))
    return JSONResponse(
        status_code=200,
        content={
            "status": "accepted",
            "run_id": run_id,
            "brief_url": f"/briefs/{run_id}.json",
        },
    )


@app.get("/runs")
def runs() -> JSONResponse:
    return JSONResponse(content={"runs": [_run_summary(r) for r in _load_runs()]})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


_SEV_COLOR = {"P1": "#c0392b", "P2": "#e67e22", "P3": "#f1c40f", "P4": "#3498db"}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    rows = []
    for r in (_run_summary(x) for x in _load_runs()):
        sev = r.get("severity") or "—"
        color = _SEV_COLOR.get(str(sev).upper(), "#7f8c8d")
        headline = r.get("headline") or r.get("error") or "(in progress / no brief)"
        trace = r.get("trace_path")
        link = (
            f'<a href="/viewer.html?trace={trace}">open trace</a>'
            if trace else "<span style='color:#999'>no trace</span>"
        )
        rows.append(
            "<tr>"
            f"<td><code>{r.get('run_id','')}</code></td>"
            f"<td><span class='sev' style='background:{color}'>{sev}</span></td>"
            f"<td>{r.get('host') or ''}</td>"
            f"<td>{headline}</td>"
            f"<td>{r.get('stop_reason') or ''}</td>"
            f"<td>{r.get('elapsed_sec') or ''}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    body = "".join(rows) or (
        "<tr><td colspan='7' style='color:#999;padding:24px'>"
        "No runs yet. POST an alert to <code>/alert</code>.</td></tr>"
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Ops Narrator — runs</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:32px;color:#222;background:#fafafa}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;background:#fff}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eee}}
 th{{font-size:12px;text-transform:uppercase;color:#888;letter-spacing:.04em}}
 code{{font-size:12px;color:#555}}
 .sev{{color:#fff;padding:2px 8px;border-radius:10px;font-weight:600;font-size:12px}}
 a{{color:#2563eb;text-decoration:none}} a:hover{{text-decoration:underline}}
</style></head><body>
<h1>Ops Narrator — recent investigations</h1>
<table><thead><tr>
<th>Run</th><th>Sev</th><th>Host</th><th>Headline</th><th>Outcome</th><th>Elapsed</th><th></th>
</tr></thead><tbody>{body}</tbody></table>
</body></html>"""
    return HTMLResponse(content=html)


# Static mounts come last so they don't shadow the routes above. The viewer fetches
# `?trace=traces/<file>.jsonl` as a path relative to the page, which resolves to
# `/traces/<file>.jsonl` here — no change to viewer.html needed.
@app.get("/viewer.html", response_class=HTMLResponse)
def viewer() -> HTMLResponse:
    return HTMLResponse(content=VIEWER.read_text(encoding="utf-8"))


_ensure_dirs()
app.mount("/traces", StaticFiles(directory=str(TRACES_DIR)), name="traces")
app.mount("/briefs", StaticFiles(directory=str(BRIEFS_DIR)), name="briefs")
