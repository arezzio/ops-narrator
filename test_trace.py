"""Section 3 unit tests for the trace logger.

Fast and offline: builds a synthetic run-result that mimics run_agent's output
(including SDK-style block *objects*, not just dicts, since live runs return those)
and checks that build_events/write_trace produce the expected event stream.

Run: uv run pytest test_trace.py -v
"""

from types import SimpleNamespace

import trace


def _block(**kw):
    """A stand-in for an SDK content block (attribute access, like the real thing)."""
    return SimpleNamespace(**kw)


def _synthetic_result():
    """A two-iteration run: think → search → think (a pivot) → finalize."""
    alert_msg = {"role": "user", "content": [{"type": "text", "text": "alert..."}]}

    assistant_1 = {
        "role": "assistant",
        "content": [
            _block(type="thinking", thinking="Initial read: this looks like a routine admin script."),
            _block(type="text", text="Let me pull the full command line."),
            _block(type="tool_use", id="t1", name="splunk_search",
                   input={"spl": "search ...", "earliest_time": "a", "latest_time": "b"}),
        ],
    }
    tool_results_1 = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": '{"row_count": 3, "rows": []}', "is_error": False},
        ],
    }
    assistant_2 = {
        "role": "assistant",
        "content": [
            _block(type="thinking",
                   thinking="Actually, that decoded blob is an Empire stager. This is not "
                            "routine; I need to rule out lateral movement."),
            _block(type="tool_use", id="t2", name="finalize_brief",
                   input={"brief": {"headline": "C2 compromise", "summary": "...", "findings": []}}),
        ],
    }
    tool_results_2 = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t2",
             "content": '{"status": "finalized"}', "is_error": False},
        ],
    }

    return {
        "brief": {"headline": "C2 compromise", "summary": "...", "findings": []},
        "stop_reason": "finalized",
        "iterations": 2,
        "elapsed_sec": 12.3,
        "tool_calls": [
            {"name": "splunk_search", "input": {}, "latency_ms": 812, "row_count": 3, "is_error": False},
            {"name": "finalize_brief", "input": {}, "latency_ms": 4, "row_count": None, "is_error": False},
        ],
        "usage": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
        "messages": [alert_msg, assistant_1, tool_results_1, assistant_2, tool_results_2],
    }


def test_event_stream_has_all_types():
    events = trace.build_events(
        _synthetic_result(),
        alert={"host": "BSTOLL-L", "Process_Command_Line": "powershell -enc ABC"},
        config={"model": "claude-opus-4-7", "effort": "high"},
    )
    types = [e["type"] for e in events]

    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    for expected in ("thinking", "assistant_text", "tool_call", "tool_result", "hypothesis_revision"):
        assert expected in types, f"missing {expected} in {types}"

    # seq is a dense 0..n-1 ordering.
    assert [e["seq"] for e in events] == list(range(len(events)))


def test_run_started_carries_config_and_alert():
    events = trace.build_events(
        _synthetic_result(),
        alert={"host": "BSTOLL-L", "Process_Command_Line": "powershell -enc ABC"},
        config={"model": "claude-opus-4-7", "effort": "high"},
    )
    started = events[0]
    assert started["config"]["model"] == "claude-opus-4-7"
    assert started["alert"]["host"] == "BSTOLL-L"
    assert started["alert"]["command_line"].startswith("powershell")


def test_tool_call_carries_latency_and_rowcount():
    events = trace.build_events(_synthetic_result())
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls[0]["name"] == "splunk_search"
    assert calls[0]["latency_ms"] == 812
    assert calls[0]["row_count"] == 3
    assert calls[0]["is_error"] is False
    # tool_result is correlated back to the call's row_count via tool_use_id.
    res = [e for e in events if e["type"] == "tool_result"][0]
    assert res["name"] == "splunk_search"
    assert res["row_count"] == 3


def test_hypothesis_revision_detected():
    events = trace.build_events(_synthetic_result())
    revs = [e for e in events if e["type"] == "hypothesis_revision"]
    assert len(revs) == 1
    assert revs[0]["cue"] == "actually"
    assert "Empire stager" in revs[0]["excerpt"]
    assert "routine admin script" in revs[0]["prev_excerpt"]


def test_no_revision_without_pivot_cue():
    assert trace._detect_revision("It is a beacon.", "It is still a beacon, confirmed.") is None
    assert trace._detect_revision("", "Actually a pivot.") is None  # no prior block


def test_redacted_thinking_becomes_thinking_event():
    result = _synthetic_result()
    result["messages"][1]["content"][0] = _block(type="redacted_thinking", data="xxx")
    events = trace.build_events(result)
    redacted = [e for e in events if e["type"] == "thinking" and e.get("redacted")]
    assert len(redacted) == 1
    assert redacted[0]["text"] == ""


def test_write_and_read_roundtrip(tmp_path):
    path = tmp_path / "trace.jsonl"
    out = trace.write_trace(_synthetic_result(), alert={"host": "BSTOLL-L"}, path=path)
    assert out == path
    loaded = trace.read_trace(path)
    assert loaded[0]["type"] == "run_started"
    assert loaded[-1]["type"] == "run_finished"
    assert loaded[-1]["n_revisions"] == 1
    assert loaded[-1]["brief_headline"] == "C2 compromise"


def test_default_path_uses_host_and_lives_in_traces_dir(tmp_path):
    out = trace.write_trace(_synthetic_result(), alert={"host": "BSTOLL-L"}, traces_dir=tmp_path)
    assert out.parent == tmp_path
    assert "BSTOLL-L" in out.name
    assert out.name.endswith(".jsonl")


def test_large_tool_result_is_truncated():
    result = _synthetic_result()
    big = '{"rows": "' + "x" * (trace.CONTENT_CAP + 500) + '"}'
    result["messages"][2]["content"][0]["content"] = big
    events = trace.build_events(result)
    res = [e for e in events if e["type"] == "tool_result"][0]
    assert res["truncated"] is True
    assert len(res["content"]) == trace.CONTENT_CAP
