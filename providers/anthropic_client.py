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
            messages=messages,
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
