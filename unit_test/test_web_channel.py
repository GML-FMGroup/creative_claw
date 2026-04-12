import asyncio
import contextlib
import json
import unittest
import uuid
from pathlib import Path
from urllib.request import urlopen

import websockets

from conf.channel import WebChannelConfig
from src.channels.events import OutboundMessage
from src.channels.web import WebChannel
from src.runtime import InboundMessage
from src.runtime.workspace import generated_root


class WebChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.inbound_messages: list[InboundMessage] = []

        async def _handler(message: InboundMessage) -> None:
            self.inbound_messages.append(message)

        self.channel = WebChannel(
            config=WebChannelConfig(
                host="127.0.0.1",
                port=0,
                open_browser=False,
                title="CreativeClaw Web Chat",
            ),
            inbound_handler=_handler,
        )
        await self.channel.start()

    async def asyncTearDown(self) -> None:
        await self.channel.stop()

    async def test_web_channel_serves_index_page(self) -> None:
        def fetch_index():
            with urlopen(self.channel.url) as response:  # noqa: S310 - local test server
                return response.status, response.read().decode("utf-8")

        status, body = await asyncio.to_thread(fetch_index)
        self.assertEqual(status, 200)
        self.assertIn("CreativeClaw Web Chat", body)
        self.assertIn("/app.js", body)

    async def test_web_channel_bridges_websocket_messages_and_artifacts(self) -> None:
        generated_file = generated_root() / f"web_channel_{uuid.uuid4().hex[:8]}.png"
        generated_file.write_bytes(b"fake-image")

        try:
            async with websockets.connect(f"ws://127.0.0.1:{self.channel._port}/ws?session_id=test-session") as websocket:
                ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["sessionId"], "test-session")

                await websocket.send(json.dumps({"type": "chat", "content": "hello web"}))
                inbound = await asyncio.wait_for(self._consume_inbound(), timeout=2)
                self.assertEqual(inbound.channel, "web")
                self.assertEqual(inbound.chat_id, "test-session")
                self.assertEqual(inbound.text, "hello web")

                await self.channel.send(
                    OutboundMessage(
                        channel="web",
                        chat_id="test-session",
                        text="working on it",
                        metadata={"display_style": "progress", "stage_title": "Planning"},
                    )
                )
                progress = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
                self.assertEqual(progress["type"], "progress")
                self.assertEqual(progress["metadata"]["stage_title"], "Planning")

                await self.channel.send(
                    OutboundMessage(
                        channel="web",
                        chat_id="test-session",
                        text="final answer",
                        artifact_paths=[str(generated_file)],
                        metadata={"display_style": "final"},
                    )
                )
                final_message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
                self.assertEqual(final_message["type"], "assistant_message")
                self.assertEqual(final_message["content"], "final answer")
                self.assertEqual(len(final_message["artifacts"]), 1)
                artifact = final_message["artifacts"][0]
                self.assertTrue(artifact["isImage"])

                def fetch_artifact():
                    with urlopen(f"{self.channel.url}{artifact['url']}") as response:  # noqa: S310 - local test server
                        return response.status, response.read()

                status, body = await asyncio.to_thread(fetch_artifact)
                self.assertEqual(status, 200)
                self.assertEqual(body, b"fake-image")
        finally:
            with contextlib.suppress(FileNotFoundError):
                generated_file.unlink()

    async def _consume_inbound(self) -> InboundMessage:
        for _ in range(20):
            if self.inbound_messages:
                return self.inbound_messages.pop(0)
            await asyncio.sleep(0.01)
        raise AssertionError("Inbound message was not received.")


if __name__ == "__main__":
    unittest.main()
