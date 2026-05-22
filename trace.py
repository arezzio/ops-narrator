"""Ops Narrator — Section 3 trace logger.

Turns one `run_agent()` result into a JSONL reasoning trace: one event per line,
in the order the agent produced it. The trace is the raw material for the Section 8
viewer and for showing the judges *how* the agent reasoned, not just its conclusion.

Design notes:
- Standalone: this module does NOT import `agent`, so it can be run over a saved
  result or unit-tested with synthetic data. `agent` imports *it*.
- Reconstructs the timeline from `result["messages"]` (the full transcript, incl.
  summarized thinking blocks — `display:"summarized"` is on, see PROGRESS gotcha #9)
  plus `result["tool_calls"]` (latency_ms / row_count / is_error per call, in
  execution order).
- Blocks may be SDK objects (live runs) or plain dicts (tests/serialized runs);
  `_get` reads either.

Event types (each line is one of):
  run_started · thinking · assistant_text · tool_call · tool_result ·
  hypothesis_revision · run_finished
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Tool-result content can be large JSON; cap it so traces stay readable.
CONTENT_CAP = 6000

# Cue phrases that, appearing in a thinking block, suggest the agent changed its
# mind relative to the previous one. Curated to favour genuine pivots over noise
# (bare "but" is deliberately excluded). Lower-cased; matched as substrings.
PIVOT_CUES: tuple[str, ...] = (
    "actually",
    "wait,",
    "wait —",
    "wait-",
    "however",
    "instead",
    "rather than",
    "on second thought",
    "reconsider",
    "re-evaluat",
    "reevaluat",
    "revise",
    "revisit",
    "rule out",
    "ruled out",
    "rules out",
    "contradict",
    "turns out",
    "in fact",
    "correction",
    "i was wrong",
    "scratch that",
    "pivot",
    "doesn't fit",
    "does not fit",
    "not actually",
    "that changes",
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


# --------------------------------------------------------------------------- #
# Block accessors (work for both SDK objects and plain dicts)
# --------------------------------------------------------------------------- #
def _get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, cap: int = CONTENT_CAP) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= cap:
        return text, False
    return text[:cap], True


# --------------------------------------------------------------------------- #
# Hypothesis-revision heuristic
# --------------------------------------------------------------------------- #
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _sentence_with_cue(text: str, cue: str) -> str:
    low = text.lower()
    idx = low.find(cue)
    if idx == -1:
        return ""
    for sent in _sentences(text):
        if cue in sent.lower():
            return sent[:240]
    # Cue spanned a boundary; fall back to a window around it.
    start = max(0, idx - 80)
    return text[start : idx + 160].strip()[:240]


def _detect_revision(prev_text: str, cur_text: str) -> dict | None:
    """If `cur_text` reads like a pivot away from `prev_text`, describe it.

    Pure heuristic: trigger on a curated pivot cue appearing in the newer thinking
    block (and there being a prior block to pivot from). Returns the matched cue,
    the sentence it sits in, and the tail of the previous block for context.
    """
    if not prev_text or not cur_text or len(cur_text) < 40:
        return None
    low = cur_text.lower()
    for cue in PIVOT_CUES:
        if cue in low:
            prev_sents = _sentences(prev_text)
            return {
                "cue": cue.strip(),
                "excerpt": _sentence_with_cue(cur_text, cue),
                "prev_excerpt": (prev_sents[-1][:240] if prev_sents else ""),
            }
    return None


# --------------------------------------------------------------------------- #
# Event builder
# --------------------------------------------------------------------------- #
def _alert_summary(alert: dict | None) -> dict:
    if not alert:
        return {}
    cmd = alert.get("Process_Command_Line") or alert.get("command_line") or ""
    return {
        "time": alert.get("_time") or alert.get("time"),
        "host": alert.get("host"),
        "account": alert.get("Account_Name") or alert.get("account"),
        "event_code": alert.get("EventCode") or alert.get("event_code"),
        "command_line": cmd[:300],
    }


def build_events(
    result: dict,
    *,
    alert: dict | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Reconstruct the ordered list of trace events from a run result.

    Pure function (no I/O), so it's cheap to unit-test.
    """
    events: list[dict] = []
    seq = 0

    def emit(etype: str, **fields: Any) -> None:
        nonlocal seq
        events.append({"seq": seq, "type": etype, **fields})
        seq += 1

    generated_at = _now()
    emit(
        "run_started",
        generated_at=generated_at,
        config=config or {},
        alert=_alert_summary(alert),
    )

    messages = result.get("messages", [])
    tool_calls = result.get("tool_calls", [])
    tc_idx = 0  # tool_calls are in execution order, 1:1 with tool_use blocks
    id_to_meta: dict[str, dict] = {}
    prev_thinking: str | None = None
    iteration = 0

    for msg in messages:
        role = _get(msg, "role")
        content = _get(msg, "content")
        if not isinstance(content, list):
            continue

        if role == "assistant":
            iteration += 1
            for block in content:
                btype = _get(block, "type")
                if btype == "thinking":
                    text = _get(block, "thinking", "") or ""
                    emit("thinking", iteration=iteration, text=text)
                    rev = _detect_revision(prev_thinking or "", text)
                    if rev:
                        emit("hypothesis_revision", iteration=iteration, **rev)
                    prev_thinking = text
                elif btype == "redacted_thinking":
                    emit("thinking", iteration=iteration, redacted=True, text="")
                elif btype == "text":
                    emit("assistant_text", iteration=iteration, text=_get(block, "text", ""))
                elif btype == "tool_use":
                    meta = tool_calls[tc_idx] if tc_idx < len(tool_calls) else {}
                    tc_idx += 1
                    tid = _get(block, "id")
                    if tid:
                        id_to_meta[tid] = meta
                    emit(
                        "tool_call",
                        iteration=iteration,
                        tool_use_id=tid,
                        name=_get(block, "name"),
                        input=dict(_get(block, "input", {}) or {}),
                        latency_ms=meta.get("latency_ms"),
                        row_count=meta.get("row_count"),
                        is_error=meta.get("is_error"),
                        error=meta.get("error"),
                    )

        elif role == "user":
            # The loop's tool-result turn (skip the initial alert user turn).
            for block in content:
                if _get(block, "type") != "tool_result":
                    continue
                tid = _get(block, "tool_use_id")
                meta = id_to_meta.get(tid, {})
                raw = _get(block, "content", "")
                text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
                preview, truncated = _truncate(text)
                emit(
                    "tool_result",
                    tool_use_id=tid,
                    name=meta.get("name"),
                    is_error=bool(_get(block, "is_error", False)),
                    row_count=meta.get("row_count"),
                    content=preview,
                    truncated=truncated,
                )

    brief = result.get("brief")
    n_thinking = sum(1 for e in events if e["type"] == "thinking")
    n_revisions = sum(1 for e in events if e["type"] == "hypothesis_revision")
    emit(
        "run_finished",
        generated_at=_now(),
        stop_reason=result.get("stop_reason"),
        iterations=result.get("iterations"),
        elapsed_sec=result.get("elapsed_sec"),
        usage=result.get("usage"),
        brief_headline=(brief or {}).get("headline") if isinstance(brief, dict) else None,
        n_tool_calls=len(tool_calls),
        n_thinking=n_thinking,
        n_revisions=n_revisions,
    )
    return events


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #
def _default_path(alert: dict | None, traces_dir: str | Path) -> Path:
    host = (alert or {}).get("host") or (alert or {}).get("Account_Name") or "run"
    host = re.sub(r"[^A-Za-z0-9._-]", "_", str(host))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path(traces_dir) / f"trace-{stamp}-{host}.jsonl"


def write_trace(
    result: dict,
    *,
    alert: dict | None = None,
    config: dict | None = None,
    path: str | Path | None = None,
    traces_dir: str | Path = "traces",
) -> Path:
    """Write the trace as JSONL and return its path.

    `path` overrides the auto name `traces/trace-<utc>-<host>.jsonl`.
    """
    events = build_events(result, alert=alert, config=config)
    out = Path(path) if path else _default_path(alert, traces_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    return out


def read_trace(path: str | Path) -> list[dict]:
    """Load a trace file back into a list of events (convenience for the viewer/tests)."""
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
