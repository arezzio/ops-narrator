"""Anthropic adapter — the default backend and the one used for the demo + submission.

This is the original `agent.py` model call, lifted behind the adapter interface with
its behavior intact: adaptive extended thinking + the OPS_EFFORT mapping, and the raw
SDK content blocks (thinking blocks with their signatures) passed straight back into
history. `tools` arrive already in Anthropic shape, so no translation is needed.
"""

from __future__ import annotations

import os

import anthropic

from .base import BaseClient, ModelResponse, ToolCall

# How many of the most-recent user turns to anchor with a cache breakpoint. The
# growing transcript is re-sent every iteration; without these the big tool results
# (e.g. a 500-row ancestry pull) pay full input price on every turn. With them, each
# call reads the prior conversation prefix instead. The `system` list carries its own
# breakpoint (set in agent.py, caches tools+system together), so total breakpoints =
# 1 (system) + this — kept <= 4, the API max. Two recent anchors also keep reads robust
# against the 20-block lookback window in tool-heavy turns.
_MSG_CACHE_BREAKPOINTS = 2


def _with_cache_breakpoints(messages: list[dict]) -> list[dict]:
    """Return a copy of `messages` with `cache_control: ephemeral` on the last content
    block of the most-recent `_MSG_CACHE_BREAKPOINTS` user turns.

    Only `user` turns are marked — their content is dict blocks we construct (the alert
    text and tool_result lists), so they're safe to annotate. Assistant turns hold raw
    Anthropic SDK blocks (thinking signatures intact) and are passed through untouched.
    Originals are never mutated: only the marked user messages are shallow-copied, so the
    canonical loop transcript (and the trace) stays clean and breakpoints never accumulate.
    """
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    mark = set(user_idxs[-_MSG_CACHE_BREAKPOINTS:])
    out: list[dict] = []
    for i, m in enumerate(messages):
        content = m.get("content")
        if i in mark and isinstance(content, list) and content and isinstance(content[-1], dict):
            new_content = list(content)
            new_content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
            out.append({**m, "content": new_content})
        else:
            out.append(m)
    return out


class AnthropicClient(BaseClient):
    provider = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        # Same default + env override as the original agent.py.
        self.model = model or os.environ.get("OPS_MODEL", "claude-opus-4-7")
        self._client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env

    def call_model(self, *, messages, tools, system, max_tokens, effort) -> ModelResponse:
        # Opus 4.7: adaptive thinking + `effort` (not budget_tokens, which 400s); see
        # PROGRESS gotcha #9. display:"summarized" so thinking text is populated for the trace.
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort},
            tools=tools,
            messages=_with_cache_breakpoints(messages),
        )

        u = resp.usage
        usage = {
            "input": u.input_tokens,
            "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }

        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in resp.content
            if b.type == "tool_use"
        ]
        text = "".join(b.text for b in resp.content if b.type == "text")

        # Pass the SDK content objects through untouched: the loop appends them to
        # history and the next call replays thinking blocks verbatim (signatures intact).
        return ModelResponse(
            content=resp.content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            usage=usage,
            text=text,
        )
