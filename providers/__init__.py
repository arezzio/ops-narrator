"""Multi-provider model backends for the Ops Narrator agent loop.

`get_client()` reads OPS_MODEL_PROVIDER (default "anthropic") and returns the matching
adapter. Anthropic is the default — best-quality reasoning, native extended thinking,
and the most reliable structured brief — and the one used for the demo + submission.
The others exist so we can iterate during development without burning Anthropic credits.

Adapter imports are lazy so the default path never requires the optional SDKs
(google-genai / openai) to be importable, and so existing tests pass with the env var
unset.
"""

from __future__ import annotations

import os

from .base import BaseClient, ModelResponse, ToolCall, system_to_text

# Providers with no native server-side thinking we can surface. agent.py logs one info
# line per run for these (the agent relies on tool-call reasoning instead).
THINKING_NOTES = {
    "groq": "Provider groq has no native thinking; relying on tool-call reasoning",
    "ollama": "Provider ollama has no native thinking; relying on tool-call reasoning",
}

__all__ = [
    "BaseClient",
    "ModelResponse",
    "ToolCall",
    "THINKING_NOTES",
    "get_client",
    "system_to_text",
]


def get_client(provider: str | None = None) -> BaseClient:
    """Return the model adapter for `provider` (or OPS_MODEL_PROVIDER, default anthropic)."""
    provider = (provider or os.environ.get("OPS_MODEL_PROVIDER", "anthropic")).lower()

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient()

    if provider == "google":
        from .google_client import GoogleClient

        return GoogleClient()

    if provider == "groq":
        from .openai_compat import OpenAICompatClient

        return OpenAICompatClient(
            provider="groq",
            model=os.environ.get("OPS_GROQ_MODEL", "llama-3.3-70b-versatile"),
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY", ""),
        )

    if provider == "ollama":
        from .openai_compat import OpenAICompatClient

        host = os.environ.get("OPS_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        return OpenAICompatClient(
            provider="ollama",
            model=os.environ.get("OPS_OLLAMA_MODEL", "qwen2.5:14b"),
            base_url=f"{host}/v1",
            api_key="ollama",  # Ollama ignores the key; the OpenAI SDK requires a non-empty one.
        )

    raise ValueError(
        f"Unknown OPS_MODEL_PROVIDER: {provider!r} (expected anthropic|google|groq|ollama)"
    )
