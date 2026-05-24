"""Offline tests for webhook.py — no Anthropic credits, no live Splunk.

`agent.run_agent` is monkeypatched with a fake, so the whole HTTP + persistence path
(POST /alert → background run → briefs/ → /runs → static trace serving) is exercised
deterministically. Run: ``uv run pytest test_webhook.py``
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import agent
import webhook

SPLUNK_PAYLOAD = {
    "search_name": "Encoded PowerShell process creation",
    "sid": "scheduler__admin__search__RMD5x_at_1600000000_1",
    "results_link": "http://localhost:8000/app/search/...",
    "result": {
        "_time": "2018-08-20 05:59:48 EDT",
        "host": "BSTOLL-L",
        "Account_Name": "BudStoll",
        "Creator_Process_Name": r"C:\Windows\System32\browser_broker.exe",
        "New_Process_Name": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "Process_Command_Line": '"...powershell.exe" powershell -noP -w 1 -enc SQBmACg...',
        "__mv_host": "$BSTOLL-L$",  # Splunk multivalue twin — must be dropped
    },
}


def _fake_result() -> dict:
    return {
        "brief": {
            "severity": "P1",
            "headline": "Encoded PowerShell beachhead with lateral movement",
            "summary": "...",
            "findings": [{"title": "x", "evidence": "y", "mitre": [], "iocs": []}],
            "recommended_containment": ["isolate host"],
        },
        "stop_reason": "finalized",
        "iterations": 7,
        "elapsed_sec": 12.3,
        "tool_calls": [
            {"name": "splunk_search", "input": {"spl": "index=... huge query"},
             "latency_ms": 51, "row_count": 3, "is_error": False},
        ],
        "usage": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
        "messages": [{"role": "assistant", "content": "a very large transcript " * 50}],
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "trace_path": "traces/trace-20180820-BSTOLL-L.jsonl",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect persistence to a temp dir so tests never touch the repo's briefs/.
    monkeypatch.setattr(webhook, "BRIEFS_DIR", tmp_path / "briefs")
    monkeypatch.setattr(agent, "run_agent", lambda alert, **kw: _fake_result())
    return TestClient(webhook.app)


# ---- build_alert (pure mapping) -------------------------------------------------

def test_build_alert_unwraps_splunk_result_and_drops_mv():
    alert = webhook.build_alert(SPLUNK_PAYLOAD)
    assert alert["host"] == "BSTOLL-L"
    assert alert["Account_Name"] == "BudStoll"
    assert "__mv_host" not in alert  # multivalue twins stripped


def test_build_alert_accepts_bare_dict():
    bare = {"host": "FYODOR-L", "EventCode": "4688"}
    assert webhook.build_alert(bare) == bare


def test_build_alert_handles_garbage():
    assert webhook.build_alert({"result": "not-a-dict"}) == {}


# ---- POST /alert + background persistence ---------------------------------------

def test_alert_acks_200_and_persists_compact_brief(client, tmp_path):
    r = client.post("/alert", json=SPLUNK_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    run_id = body["run_id"]
    assert "BSTOLL-L" in run_id
    assert body["brief_url"] == f"/briefs/{run_id}.json"

    # TestClient runs the background task before returning, so the record exists now.
    saved = json.loads((tmp_path / "briefs" / f"{run_id}.json").read_text())
    assert saved["brief"]["severity"] == "P1"
    assert saved["search_name"] == "Encoded PowerShell process creation"
    assert saved["alert"]["host"] == "BSTOLL-L"
    # Heavy fields stripped for a compact record:
    assert "messages" not in saved
    assert "input" not in saved["tool_calls"][0]
    assert saved["tool_calls"][0]["row_count"] == 3


def test_runs_endpoint_lists_the_run(client):
    client.post("/alert", json=SPLUNK_PAYLOAD)
    runs = client.get("/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["severity"] == "P1"
    assert runs[0]["host"] == "BSTOLL-L"
    assert runs[0]["trace_path"] == "traces/trace-20180820-BSTOLL-L.jsonl"


def test_index_html_shows_run(client):
    client.post("/alert", json=SPLUNK_PAYLOAD)
    html = client.get("/").text
    assert "Ops Narrator" in html
    assert "BSTOLL-L" in html
    assert "viewer.html?trace=traces/" in html  # link wires into the viewer


def test_failed_investigation_still_records(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook, "BRIEFS_DIR", tmp_path / "briefs")

    def boom(alert, **kw):
        raise RuntimeError("MCP unreachable")

    monkeypatch.setattr(agent, "run_agent", boom)
    client = TestClient(webhook.app)

    r = client.post("/alert", json=SPLUNK_PAYLOAD)
    assert r.status_code == 200  # webhook never fails the caller
    run_id = r.json()["run_id"]
    saved = json.loads((tmp_path / "briefs" / f"{run_id}.json").read_text())
    assert saved["stop_reason"] == "error"
    assert "MCP unreachable" in saved["error"]


# ---- static serving (the viewer's ?trace= path) ---------------------------------

def test_viewer_served_at_route(client):
    html = client.get("/viewer.html").text
    assert "buildModel" in html  # the real single-file viewer


def test_traces_served_statically(client):
    # Drop a trace into the real mounted dir (the mount binds at import time).
    webhook.TRACES_DIR.mkdir(parents=True, exist_ok=True)
    f = webhook.TRACES_DIR / "trace-unittest-tmp.jsonl"
    f.write_text('{"seq":0,"type":"run_started"}\n', encoding="utf-8")
    try:
        r = client.get(f"/traces/{f.name}")
        assert r.status_code == 200
        assert '"type":"run_started"' in r.text
    finally:
        f.unlink()


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"
