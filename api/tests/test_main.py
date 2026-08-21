"""Checks conversation-title generation behavior."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from starlette.websockets import WebSocketState


class _Completions:
    def __init__(self, content: str):
        self.content = content
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class ConversationTitleTests(unittest.TestCase):
    def test_title_and_empty_response_fallback(self) -> None:
        completions = _Completions("Useful title")
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with (
            patch.object(main, "get_secondary_client", return_value=client),
            patch.object(main, "get_secondary_model", return_value="test-model"),
        ):
            title = asyncio.run(main.generate_conversation_title("Build a timer", "Done"))

        self.assertEqual(title, "Useful title")
        self.assertEqual(completions.kwargs["max_completion_tokens"], 100)

        completions.content = ""
        with (
            patch.object(main, "get_secondary_client", return_value=client),
            patch.object(main, "get_secondary_model", return_value="test-model"),
        ):
            fallback = asyncio.run(
                main.generate_conversation_title("Build a focused reading timer now", "Done")
            )

        self.assertEqual(fallback, "Build a focused reading timer now")

    def test_background_update_skips_closed_socket(self) -> None:
        websocket = SimpleNamespace(
            client_state=WebSocketState.DISCONNECTED,
            application_state=WebSocketState.DISCONNECTED,
            send_json=AsyncMock(),
        )

        asyncio.run(main.send_if_connected(websocket, {"type": "update"}))

        websocket.send_json.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
