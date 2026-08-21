"""OpenAI model configuration shared by the agent and its tools."""

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

PRIMARY_MODEL = "gpt-5.2"
SECONDARY_MODEL = "gpt-5-nano-2025-08-07"
EMBEDDING_MODEL = "text-embedding-3-small"


def get_secondary_client() -> AsyncOpenAI:
    """Return the OpenAI client used by secondary model tasks."""
    return AsyncOpenAI()


def get_secondary_model() -> str:
    """Return the secondary model name."""
    return SECONDARY_MODEL


def get_embedding_model() -> str:
    """Return the embedding model name."""
    return EMBEDDING_MODEL
