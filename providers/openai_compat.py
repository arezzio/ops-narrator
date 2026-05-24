"""OpenAI-compatible adapter — serves both Groq and Ollama.

Groq (Llama 3.3 70B Versatile) and Ollama (local, default qwen2.5:14b) both expose the
OpenAI chat-completions API, so one client class with a different `base_url`/`model`/key
covers both. Neither has native server-side reasoning we can surface, so there is no
thinking handling here — the run-start info line in agent.py notes that, and the agent
relies on tool-call reasoning instead.

Translation in both directions:
- Anthropic-shaped history -> OpenAI messages: assistant tool_use blocks become
  `tool_calls`; our tool_result turns become `role:"tool"` messages keyed by
  `tool_call_id`. IDs round-trip (the loop already wires ToolCall.id -> tool_use_id).
- OpenAI response -> normalized blocks: assistant text + one tool_use block per
  `tool_calls` entry (arguments JSON-parsed); finish_reason mapped to Anthropic vocab.
"""

from __future__ import annotations

import json

from openai import OpenAI

from .base import (
    BaseClient,
    ModelResponse,
    ToolCall,
    _block_get,
    system_to_text,
    to_openai_tools,
)

# OpenAI finish_reason -> Anthropic stop_reason vocabulary the loop expects.
_STOP_MAP = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
    "function_call": "tool_use",
}


class OpenAICompatClient(BaseClient):
    def __init__(self, *, provider: str, model: str, base_url: str, api_key: str) -> None:
        self.provider = provider
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    # -- input translation -------------------------------------------------- #
    def _to_openai_messages(self, messages: list[dict], system) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system_to_text(system)}]

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if not isinstance(content, list):
                out.append({"role": role, "content": content or ""})
                continue

            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    btype = _block_get(block, "type")
                    if btype == "text":
                        text_parts.append(_block_get(block, "text", "") or "")
                    elif btype == "tool_use":
                        tool_calls.append(
                            {
                                "id": _block_get(block, "id"),
                                "type": "function",
                                "function": {
                                    "name": _block_get(block, "name"),
                                    "arguments": json.dumps(
                                        _block_get(block, "input", {}) or {}
                                    ),
                                },
                            }
                        )
                    # thinking blocks (if any) are intentionally not echoed back.
                assistant_msg: dict = {"role": "assistant"}
                assistant_msg["content"] = "\n".join(text_parts) or None
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)

            elif role == "user":
                text_parts = []
                for block in content:
                    btype = _block_get(block, "type")
                    if btype == "tool_result":
                        raw = _block_get(block, "content", "")
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": _block_get(block, "tool_use_id"),
                                "content": raw if isinstance(raw, str) else json.dumps(raw),
                            }
                        )
                    elif btype == "text":
                        text_parts.append(_block_get(block, "text", "") or "")
                if text_parts:
                    out.append({"role": "user", "content": "\n".join(text_parts)})

        return out

    # -- the call ----------------------------------------------------------- #
    def call_model(self, *, messages, tools, system, max_tokens, effort) -> ModelResponse:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=self._to_openai_messages(messages, system),
            tools=to_openai_tools(tools),
            tool_choice="auto",
        )
        return self._normalize(resp)

    # -- output normalization ---------------------------------------------- #
    def _normalize(self, resp) -> ModelResponse:
        choice = resp.choices[0]
        msg = choice.message

        content: list[dict] = []
        text = msg.content or ""
        if text:
            content.append({"type": "text", "text": text})

        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args}
            )

        stop_reason = _STOP_MAP.get(choice.finish_reason, choice.finish_reason or "end_turn")
        # A model can emit tool calls while reporting finish_reason="stop"; trust the calls.
        if tool_calls and stop_reason != "tool_use":
            stop_reason = "tool_use"

        u = getattr(resp, "usage", None)
        usage = {
            "input": getattr(u, "prompt_tokens", 0) or 0,
            "output": getattr(u, "completion_tokens", 0) or 0,
            "cache_read": 0,
            "cache_write": 0,
        }

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            text=text,
        )
