"""Google Gemini adapter — Gemini 2.5 Flash via the google-genai SDK.

Function calling enabled, native thinking on (2.5 Flash thinks by default; we ask for
thought summaries so the trace can log them), and the 1M-token context the model
provides. We disable the SDK's automatic function calling so we get raw `function_call`
parts back and keep the agent loop in control of dispatch.

Translation notes:
- Gemini function calls have no id, so we synthesize `"<name>__<n>"` for the loop's
  tool_use_id bookkeeping, and on the way back in we recover the tool name from the
  preceding assistant turn (function_response is matched by name in Gemini).
- Thought-summary parts are kept in `content` (so trace.py logs them) but are NOT
  echoed back to Gemini as model turns — Gemini manages its own thinking.
- The tool schema is cleaned to Gemini's OpenAPI subset in base.to_gemini_tools (the
  free-form `iocs` object is pruned there; see _clean_gemini_schema).
"""

from __future__ import annotations

import logging
import os
import re
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger("ops_narrator")

# Free-tier Gemini caps generate_content at a few requests/minute (5 RPM for
# gemini-2.5-flash). The agent loop fires calls back-to-back, so we honor the
# server's RetryInfo and retry rather than crash the run. Bounded so a real
# outage still fails fast.
_MAX_429_RETRIES = 6
_DEFAULT_429_BACKOFF = 20.0  # seconds, if the server gives no retryDelay

from .base import (
    BaseClient,
    ModelResponse,
    ToolCall,
    _block_get,
    system_to_text,
    to_gemini_tools,
)


def _retry_delay_seconds(err: "genai_errors.ClientError") -> float:
    """Pull the server-suggested retry delay out of a 429, with a small safety margin.

    Gemini returns a RetryInfo detail (retryDelay: '34s') and also embeds
    'Please retry in 34.6s.' in the message; we read whichever we can find and
    fall back to a fixed backoff. +1s margin avoids retrying a hair too early.
    """
    delay = None
    details = getattr(err, "details", None)
    if isinstance(details, dict):
        for d in details.get("error", {}).get("details", []) or []:
            if str(d.get("@type", "")).endswith("RetryInfo"):
                m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
                if m:
                    delay = float(m.group(1))
    if delay is None:
        m = re.search(r"retry in ([\d.]+)s", str(getattr(err, "message", "") or err))
        if m:
            delay = float(m.group(1))
    return (delay if delay is not None else _DEFAULT_429_BACKOFF) + 1.0


class GoogleClient(BaseClient):
    provider = "google"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPS_GEMINI_MODEL", "gemini-2.5-flash")
        self._client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))

    # -- input translation -------------------------------------------------- #
    def _to_gemini_contents(self, messages: list[dict]) -> list:
        contents = []
        id_to_name: dict[str, str] = {}  # tool_use_id -> tool name, for function_response

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            if role == "assistant":
                parts = []
                for block in content:
                    btype = _block_get(block, "type")
                    if btype == "text":
                        txt = _block_get(block, "text", "") or ""
                        if txt:
                            parts.append(types.Part(text=txt))
                    elif btype == "tool_use":
                        name = _block_get(block, "name")
                        tid = _block_get(block, "id")
                        if tid:
                            id_to_name[tid] = name
                        parts.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    name=name, args=_block_get(block, "input", {}) or {}
                                )
                            )
                        )
                    # thinking blocks are not echoed back to Gemini.
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "user":
                parts = []
                for block in content:
                    btype = _block_get(block, "type")
                    if btype == "text":
                        txt = _block_get(block, "text", "") or ""
                        if txt:
                            parts.append(types.Part(text=txt))
                    elif btype == "tool_result":
                        tid = _block_get(block, "tool_use_id")
                        name = id_to_name.get(tid, tid or "tool")
                        raw = _block_get(block, "content", "")
                        parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=name,
                                    response={"result": raw},
                                )
                            )
                        )
                if parts:
                    contents.append(types.Content(role="user", parts=parts))

        return contents

    # -- the call ----------------------------------------------------------- #
    def call_model(self, *, messages, tools, system, max_tokens, effort) -> ModelResponse:
        decls = to_gemini_tools(tools)[0]["function_declarations"]
        config = types.GenerateContentConfig(
            system_instruction=system_to_text(system),
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=decls)],
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self._to_gemini_contents(messages)
        resp = self._generate_with_retry(contents, config)
        return self._normalize(resp)

    def _generate_with_retry(self, contents, config):
        """Call generate_content, retrying on free-tier 429s with the server's backoff."""
        for attempt in range(_MAX_429_RETRIES + 1):
            try:
                return self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except genai_errors.ClientError as e:
                if getattr(e, "code", None) != 429 or attempt == _MAX_429_RETRIES:
                    raise
                delay = _retry_delay_seconds(e)
                log.info(
                    "Gemini 429 (rate limit); sleeping %.1fs then retrying (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    _MAX_429_RETRIES,
                )
                time.sleep(delay)

    # -- output normalization ---------------------------------------------- #
    def _normalize(self, resp) -> ModelResponse:
        candidate = resp.candidates[0]
        parts = getattr(candidate.content, "parts", None) or []

        content: list[dict] = []
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        fc_n = 0

        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                name = fc.name
                tid = f"{name}__{fc_n}"
                fc_n += 1
                args = dict(fc.args) if fc.args else {}
                tool_calls.append(ToolCall(id=tid, name=name, input=args))
                content.append({"type": "tool_use", "id": tid, "name": name, "input": args})
                continue
            txt = getattr(part, "text", None)
            if not txt:
                continue
            if getattr(part, "thought", False):
                content.append({"type": "thinking", "thinking": txt})
            else:
                content.append({"type": "text", "text": txt})
                text_parts.append(txt)

        finish = str(getattr(candidate, "finish_reason", "") or "")
        if tool_calls:
            stop_reason = "tool_use"
        elif finish.endswith("MAX_TOKENS"):
            stop_reason = "max_tokens"
        elif finish.endswith("SAFETY") or finish.endswith("RECITATION"):
            stop_reason = "refusal"
        else:
            stop_reason = "end_turn"

        um = getattr(resp, "usage_metadata", None)
        usage = {
            "input": getattr(um, "prompt_token_count", 0) or 0,
            "output": getattr(um, "candidates_token_count", 0) or 0,
            "cache_read": getattr(um, "cached_content_token_count", 0) or 0,
            "cache_write": 0,
        }

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            text="".join(text_parts),
        )
