import os
from collections.abc import Generator

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = "You are a helpful assistant."


def _build_messages(history: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
    ]


def get_chat_response(history: list[dict]) -> str:
    """Send conversation history to OpenAI and return the assistant's reply."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=_build_messages(history),
    )
    return response.choices[0].message.content


def stream_chat_response(history: list[dict]) -> Generator[str, None, None]:
    """Stream conversation history to OpenAI and yield text chunks."""
    stream = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=_build_messages(history),
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
