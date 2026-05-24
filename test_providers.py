"""Offline unit tests for the provider adapters.

No live keys, no network: we set dummy API keys, construct each adapter (construction is
lazy in every SDK we use), then replace the adapter's inner SDK client with a fake that
returns a synthetic raw response shaped like the real provider's. We assert the adapter
(a) translates the Anthropic-style tool defs + history into the provider's shape, and
(b) normalizes the raw response back into the ModelResponse contract — tool calls above all.

Run: uv run pytest test_providers.py -v
"""

import json
import os
from types import SimpleNamespace

# Dummy keys so the SDK clients construct. None of these are used — we mock the calls.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic")
os.environ.setdefault("GOOGLE_API_KEY", "test-google")
os.environ.setdefault("GROQ_API_KEY", "test-groq")
os.environ.pop("OPS_MODEL_PROVIDER", None)  # default path = anthropic

import pytest  # noqa: E402

import providers  # noqa: E402
from providers import base  # noqa: E402

# The real tool defs (incl. the deep finalize_brief schema) drive the translation tests.
import agent  # noqa: E402

TOOL_DEFS = agent.TOOL_DEFS


# A two-turn Anthropic-shaped transcript (normalized-dict form) the non-anthropic
# adapters must translate: initial alert, an assistant tool_use, a tool_result.
def _history():
    return [
        {"role": "user", "content": [{"type": "text", "text": "alert: investigate"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me search."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "splunk_search",
                    "input": {"spl": "index=x", "earliest_time": "a", "latest_time": "b"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": '{"row_count": 2, "rows": []}',
                    "is_error": False,
                }
            ],
        },
    ]


SYSTEM = [{"type": "text", "text": "You are an analyst.", "cache_control": {"type": "ephemeral"}}]


# --------------------------------------------------------------------------- #
# Tool-schema translation
# --------------------------------------------------------------------------- #
def test_to_openai_tools_shape():
    oai = base.to_openai_tools(TOOL_DEFS)
    assert len(oai) == len(TOOL_DEFS)
    first = oai[0]
    assert first["type"] == "function"
    assert first["function"]["name"] == TOOL_DEFS[0]["name"]
    # input_schema is carried through verbatim as `parameters`.
    assert first["function"]["parameters"] == TOOL_DEFS[0]["input_schema"]


def test_to_gemini_tools_uppercases_types_and_prunes_propertyless_objects():
    gem = base.to_gemini_tools(TOOL_DEFS)
    decls = gem[0]["function_declarations"]
    assert len(decls) == len(TOOL_DEFS)

    # Types are upper-cased to Gemini's enum vocabulary.
    search = next(d for d in decls if d["name"] == "splunk_search")
    assert search["parameters"]["type"] == "OBJECT"
    assert search["parameters"]["properties"]["spl"]["type"] == "STRING"

    # The free-form `iocs` object (type:object, no properties) is pruned from the
    # finalize_brief schema, and unsupported keywords are gone.
    fb = next(d for d in decls if d["name"] == "finalize_brief")
    brief = fb["parameters"]["properties"]["brief"]
    assert "iocs" not in brief["properties"], "property-less object should be pruned for Gemini"
    blob = json.dumps(gem)
    assert "additionalProperties" not in blob
    assert '"object"' not in blob and '"string"' not in blob  # all types upper-cased


def test_clean_gemini_schema_drops_dangling_required():
    schema = {
        "type": "object",
        "properties": {"keep": {"type": "string"}, "bag": {"type": "object"}},
        "required": ["keep", "bag"],
    }
    cleaned = base._clean_gemini_schema(schema)
    assert "bag" not in cleaned["properties"]
    assert cleaned["required"] == ["keep"]


def test_system_to_text_flattens_block_list():
    assert base.system_to_text(SYSTEM) == "You are an analyst."
    assert base.system_to_text("plain") == "plain"


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def test_get_client_defaults_to_anthropic():
    client = providers.get_client()
    assert client.provider == "anthropic"
    assert client.model == "claude-opus-4-7"


def test_get_client_unknown_raises():
    with pytest.raises(ValueError):
        providers.get_client("does-not-exist")


def test_get_client_groq_and_ollama_models():
    groq = providers.get_client("groq")
    assert groq.provider == "groq"
    assert groq.model == "llama-3.3-70b-versatile"
    ollama = providers.get_client("ollama")
    assert ollama.provider == "ollama"
    assert ollama.model == "qwen2.5:14b"


def test_thinking_notes_only_for_groq_and_ollama():
    assert set(providers.THINKING_NOTES) == {"groq", "ollama"}
    assert "anthropic" not in providers.THINKING_NOTES
    assert "google" not in providers.THINKING_NOTES


# --------------------------------------------------------------------------- #
# Anthropic adapter — raw SDK blocks pass through; usage incl. cache fields
# --------------------------------------------------------------------------- #
def test_anthropic_normalizes_tool_calls_and_passes_content_through():
    from providers.anthropic_client import AnthropicClient

    client = AnthropicClient()

    raw_content = [
        SimpleNamespace(type="thinking", thinking="hmm"),
        SimpleNamespace(type="text", text="searching"),
        SimpleNamespace(
            type="tool_use", id="toolu_1", name="splunk_search", input={"spl": "index=x"}
        ),
    ]
    raw = SimpleNamespace(
        content=raw_content,
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=5,
        ),
    )
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return raw

    client._client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

    resp = client.call_model(
        messages=_history(), tools=TOOL_DEFS, system=SYSTEM, max_tokens=16000, effort="high"
    )

    # Tools pass through unchanged (already Anthropic shape); thinking config preserved.
    assert captured["tools"] is TOOL_DEFS
    assert captured["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert captured["output_config"] == {"effort": "high"}

    # Content is the raw SDK blocks, untouched (signatures survive for the next turn).
    assert resp.content is raw_content
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "toolu_1"
    assert resp.tool_calls[0].name == "splunk_search"
    assert resp.tool_calls[0].input == {"spl": "index=x"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage == {"input": 100, "output": 20, "cache_read": 10, "cache_write": 5}
    assert resp.text == "searching"


# --------------------------------------------------------------------------- #
# OpenAI-compatible adapter (groq / ollama)
# --------------------------------------------------------------------------- #
def _fake_openai_response(*, content, tool_calls, finish_reason):
    tcs = [
        SimpleNamespace(
            id=tc["id"],
            function=SimpleNamespace(name=tc["name"], arguments=json.dumps(tc["input"])),
        )
        for tc in tool_calls
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=tcs or None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=40),
    )


def _openai_client():
    from providers.openai_compat import OpenAICompatClient

    return OpenAICompatClient(
        provider="groq", model="llama-3.3-70b-versatile",
        base_url="https://example.test/v1", api_key="test",
    )


def test_openai_normalizes_tool_call_and_parses_arguments():
    client = _openai_client()
    raw = _fake_openai_response(
        content=None,
        tool_calls=[{"id": "call_abc", "name": "decode_payload", "input": {"command_line": "x"}}],
        finish_reason="tool_calls",
    )
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return raw

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    resp = client.call_model(
        messages=_history(), tools=TOOL_DEFS, system=SYSTEM, max_tokens=16000, effort="high"
    )

    # Tools were translated to OpenAI function shape.
    assert captured["tools"][0]["type"] == "function"
    # Arguments came back as a JSON string and were parsed to a dict.
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "call_abc"
    assert resp.tool_calls[0].name == "decode_payload"
    assert resp.tool_calls[0].input == {"command_line": "x"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage == {"input": 200, "output": 40, "cache_read": 0, "cache_write": 0}
    # A tool_use block is appended to content so the next call can replay it.
    assert any(b["type"] == "tool_use" for b in resp.content)


def test_openai_stop_reason_mapping_for_plain_text():
    client = _openai_client()
    raw = _fake_openai_response(content="final answer", tool_calls=[], finish_reason="stop")
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: raw))
    )
    resp = client.call_model(
        messages=_history(), tools=TOOL_DEFS, system=SYSTEM, max_tokens=16000, effort="high"
    )
    assert resp.stop_reason == "end_turn"
    assert resp.tool_calls == []
    assert resp.text == "final answer"


def test_openai_message_translation_round_trips_ids():
    client = _openai_client()
    oai = client._to_openai_messages(_history(), SYSTEM)
    roles = [m["role"] for m in oai]
    assert roles[0] == "system"
    # assistant turn carries tool_calls; tool turn carries the matching tool_call_id.
    assistant = next(m for m in oai if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"])["spl"] == "index=x"
    tool_msg = next(m for m in oai if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"


# --------------------------------------------------------------------------- #
# Google Gemini adapter
# --------------------------------------------------------------------------- #
def _fake_gemini_response():
    # A thought summary, an assistant text, and a function call (no id — Gemini).
    parts = [
        SimpleNamespace(thought=True, text="thinking about it", function_call=None),
        SimpleNamespace(thought=False, text="I will search", function_call=None),
        SimpleNamespace(
            thought=False,
            text=None,
            function_call=SimpleNamespace(name="splunk_search", args={"spl": "index=x"}),
        ),
    ]
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason="STOP")
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=300, candidates_token_count=60, cached_content_token_count=0
        ),
    )


def _google_client():
    from providers.google_client import GoogleClient

    return GoogleClient()


def test_google_normalizes_function_call_and_synthesizes_id():
    client = _google_client()
    raw = _fake_gemini_response()
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return raw

    client._client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate))

    resp = client.call_model(
        messages=_history(), tools=TOOL_DEFS, system=SYSTEM, max_tokens=16000, effort="high"
    )

    # One tool call, with a synthesized id (Gemini function calls have none).
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "splunk_search"
    assert resp.tool_calls[0].id == "splunk_search__0"
    assert resp.tool_calls[0].input == {"spl": "index=x"}
    assert resp.stop_reason == "tool_use"

    # Thought summary is kept as a thinking block (for the trace); text is captured.
    assert any(b["type"] == "thinking" for b in resp.content)
    assert resp.text == "I will search"
    assert resp.usage == {"input": 300, "output": 60, "cache_read": 0, "cache_write": 0}


def test_google_recovers_tool_name_for_function_response():
    client = _google_client()
    contents = client._to_gemini_contents(_history())
    # alert(user) + assistant(model) + tool_result(user) -> 3 Content objects.
    assert len(contents) == 3
    assert contents[1].role == "model"
    # The tool_result turn becomes a function_response whose name was recovered from
    # the preceding assistant tool_use (Gemini matches responses by name, not id).
    fr_part = contents[2].parts[0]
    assert fr_part.function_response.name == "splunk_search"
