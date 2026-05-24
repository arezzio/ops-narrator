"""Provider abstraction — the model-call surface the agent loop talks to.

Why this exists: `agent.py`'s loop is built around Anthropic's message format. It
appends the assistant's raw content blocks (thinking blocks included — Opus 4.7
requires them back verbatim, see PROGRESS gotcha #9) into `messages`, iterates them
for tool calls, and `trace.py` reconstructs everything from that same transcript.

So the *canonical in-loop format stays Anthropic-shaped*, and each adapter is
bidirectional: it translates the Anthropic-shaped `messages`/`tools`/`system` into the
provider's native shape on the way in, and normalizes the provider's raw response back
into Anthropic-shaped blocks on the way out. The Anthropic adapter is a near
pass-through (preserving thinking signatures); the others re-translate every call (they
are stateless, which is fine — the loop re-sends the full history each turn).

`ModelResponse.content` is what the loop appends to history — raw SDK blocks for
Anthropic, normalized dicts for the others. Each adapter's *input* translation skips
the block types that provider can't (or shouldn't) re-consume — e.g. Gemini thought
summaries are kept in `content` so `trace.py` can log them, but are not echoed back to
Gemini as model turns.

We use direct provider SDKs (anthropic / google-genai / openai) rather than LiteLLM:
it keeps the dependency surface small and lets the Anthropic adapter preserve native
extended thinking cleanly, which a lowest-common-denominator wrapper would flatten away.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# The normalized response contract
# --------------------------------------------------------------------------- #
@dataclass
class ToolCall:
    """One tool invocation the model requested, normalized across providers.

    `id` is the provider's tool-call id where it has one (Anthropic, OpenAI). Gemini
    function calls carry no id, so the adapter synthesizes one for the loop's
    tool_use_id <-> tool_result bookkeeping.
    """

    id: str
    name: str
    input: dict


@dataclass
class ModelResponse:
    """What every adapter returns. Conceptually the (text, tool_calls, stop_reason,
    usage) contract from the spec, plus `content`: the assistant blocks the loop
    appends to `messages` (raw SDK blocks for Anthropic so thinking signatures survive;
    normalized dicts otherwise)."""

    content: Any  # list of blocks to append to messages
    tool_calls: list[ToolCall]
    stop_reason: str  # normalized to Anthropic vocab: tool_use | end_turn | max_tokens | refusal
    usage: dict  # {input, output, cache_read, cache_write}
    text: str = ""


class BaseClient(ABC):
    """A model backend. `provider` and `model` are read by run_agent for the run-start
    trace event and the brief footer."""

    provider: str = "base"
    model: str = ""

    @abstractmethod
    def call_model(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        system: Any,
        max_tokens: int,
        effort: str,
    ) -> ModelResponse:
        ...


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def system_to_text(system: Any) -> str:
    """Flatten the agent loop's system value (a list of {type:text,text,...} blocks,
    carrying Anthropic cache_control) to a plain string for providers that take a bare
    system string."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(getattr(block, "text", "") or "")
        return "\n\n".join(p for p in parts if p)
    return str(system or "")


# --------------------------------------------------------------------------- #
# Tool-schema translation (Anthropic TOOL_DEFS -> provider shapes)
# --------------------------------------------------------------------------- #
def to_openai_tools(tool_defs: list[dict]) -> list[dict]:
    """Anthropic `{name, description, input_schema}` -> OpenAI/Groq/Ollama
    `{type:function, function:{name, description, parameters}}`."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tool_defs
    ]


# JSON-Schema keywords Gemini's function-declaration schema (an OpenAPI 3.0 subset)
# does not accept; strip them on the way through.
_GEMINI_DROP_KEYS = (
    "additionalProperties",
    "$schema",
    "$id",
    "title",
    "default",
    "examples",
    "const",
)
_GEMINI_TYPE_MAP = {
    "object": "OBJECT",
    "string": "STRING",
    "array": "ARRAY",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "null": "NULL",
}


def _clean_gemini_schema(node: Any) -> Any | None:
    """Recursively coerce a JSON Schema into Gemini's accepted subset.

    Two real hazards in our `finalize_brief` schema (see agent.py): (1) Gemini's Schema
    enum wants UPPER-CASE type names, and (2) an OBJECT node with no `properties` (our
    free-form `iocs` bag) is rejected. We uppercase types, drop unsupported keywords,
    and *prune* any property-less object — Gemini just won't emit structured `iocs`,
    which is acceptable for a development backend (and `iocs` is not a required field).
    Returns None when a node should be pruned from its parent.
    """
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _GEMINI_DROP_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = _GEMINI_TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            cleaned_props = {}
            for prop_name, prop_schema in value.items():
                cleaned = _clean_gemini_schema(prop_schema)
                if cleaned is not None:
                    cleaned_props[prop_name] = cleaned
            out[key] = cleaned_props
        elif key == "items":
            cleaned = _clean_gemini_schema(value)
            out[key] = cleaned if cleaned is not None else {"type": "STRING"}
        else:
            out[key] = value

    # Prune a property-less OBJECT (e.g. the free-form `iocs` bag) — Gemini rejects it.
    if out.get("type") == "OBJECT" and not out.get("properties"):
        return None

    # If pruning emptied an object's properties, drop now-dangling `required` entries.
    if "properties" in out and "required" in out and isinstance(out["required"], list):
        out["required"] = [r for r in out["required"] if r in out["properties"]]
        if not out["required"]:
            out.pop("required")

    return out


def to_gemini_tools(tool_defs: list[dict]) -> list[dict]:
    """Anthropic tool defs -> Gemini `[{function_declarations:[{name, description,
    parameters}]}]` with the schema cleaned to Gemini's subset."""
    declarations = []
    for t in tool_defs:
        params = _clean_gemini_schema(copy.deepcopy(t["input_schema"]))
        declarations.append(
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": params if params is not None else {"type": "OBJECT"},
            }
        )
    return [{"function_declarations": declarations}]


# --------------------------------------------------------------------------- #
# Message-history walkers (Anthropic-shaped transcript -> provider messages)
# --------------------------------------------------------------------------- #
def _block_get(block: Any, key: str, default: Any = None) -> Any:
    """Read a content block whether it's a dict (our normalized shape) or an SDK
    object (Anthropic pass-through)."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)
