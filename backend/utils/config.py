"""Centralized provider configuration for LLM models.

Supports switching between OpenAI and NVIDIA Nemotron models at runtime
via a provider dropdown in the frontend.
"""

import contextvars
import os

from openai import AsyncOpenAI

# Context variable holding the current provider for the active request.
# Set by the agent at the start of each request so tools can read it.
current_provider: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_provider", default="openai"
)

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "primary_model": "gpt-5",
        "secondary_model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "model_provider": "openai",  # for LangChain init_chat_model
    },
    "nvidia": {
        "primary_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
        "secondary_model": "nvidia/llama-3.1-nemotron-nano-8b-v1",
        "embedding_model": "nvidia/nv-embedqa-e5-v5",
        "model_provider": "nvidia",
    },
}


def get_secondary_client(provider: str | None = None) -> AsyncOpenAI:
    """Return an OpenAI-compatible async client for the given provider.

    If *provider* is None, reads from the current_provider context variable.
    """
    if provider is None:
        provider = current_provider.get("openai")
    if provider == "nvidia":
        return AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
        )
    return AsyncOpenAI()  # uses OPENAI_API_KEY from env


def get_secondary_model(provider: str | None = None) -> str:
    """Return the secondary model name for the given provider."""
    if provider is None:
        provider = current_provider.get("openai")
    return PROVIDERS.get(provider, PROVIDERS["openai"])["secondary_model"]


def get_embedding_model(provider: str | None = None) -> str:
    """Return the embedding model name for the given provider."""
    if provider is None:
        provider = current_provider.get("openai")
    return PROVIDERS.get(provider, PROVIDERS["openai"])["embedding_model"]
