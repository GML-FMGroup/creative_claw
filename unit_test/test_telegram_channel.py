import tempfile
import unittest
from pathlib import Path

from src.channels.events import OutboundMessage
from src.channels.telegram import TelegramChannel
from src.runtime import InboundMessage


class _TestTelegramChannel(TelegramChannel):
    def __init__(self, *, inbound_messages: list[InboundMessage]) -> None:
        async def _handler(message: InboundMessage) -> None:
            inbound_messages.append(message)

        super().__init__(token="token", inbound_handler=_handler, allow_from=["1001"])
        self.api_calls: list[tuple[str, dict]] = []
        self.uploads: list[tuple[str, str, str, str]] = []
        self.downloaded_files: dict[str, str] = {}

    async def _api_call(self, method: str, payload: dict):
        self.api_calls.append((method, payload))
        if method == "getUpdates":
            return []
        return {"ok": True}

    def _api_call_sync(self, method: str, payload: dict):
        if method == "getFile":
            file_id = payload["file_id"]
            return {"file_path": f"photos/{file_id}.jpg"}
        return {"ok": True}

    async def _send_file(self, *, method: str, file_field: str, chat_id: str, file_path: str) -> None:
        self.uploads.append((method, file_field, chat_id, file_path))

    def _download_telegram_file(self, file_id: str, preferred_name: str = "") -> str:
        return self.downloaded_files.get(file_id) or preferred_name or f"telegram_{file_id}.jpg"


class TelegramChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_text_update_dispatches_inbound_message(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestTelegramChannel(inbound_messages=inbound_messages)

        await channel._process_update(
            {
                "update_id": 10,
                "message": {
                    "message_id": 20,
                    "text": "hello",
                    "from": {"id": 1001, "username": "tester"},
                    "chat": {"id": 3001, "type": "private"},
                },
            }
        )

        self.assertEqual(len(inbound_messages), 1)
        message = inbound_messages[0]
        self.assertEqual(message.channel, "telegram")
        self.assertEqual(message.chat_id, "3001")
        self.assertEqual(message.text, "hello")
        self.assertEqual(message.sender_id, "1001|@tester")

    async def test_process_update_ignores_sender_outside_allow_list(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestTelegramChannel(inbound_messages=inbound_messages)

        await channel._process_update(
            {
                "update_id": 11,
                "message": {
                    "message_id": 21,
                    "text": "hello",
                    "from": {"id": 2002, "username": "blocked"},
                    "chat": {"id": 3001, "type": "private"},
                },
            }
        )

        self.assertEqual(inbound_messages, [])

    async def test_send_uploads_text_and_image_artifact(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestTelegramChannel(inbound_messages=inbound_messages)

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "image.png"
            image_path.write_bytes(b"fake-image")

            await channel.send(
                OutboundMessage(
                    channel="telegram",
                    chat_id="3001",
                    text="done",
                    artifact_paths=[str(image_path)],
                )
            )

            self.assertEqual(channel.api_calls[0][0], "sendMessage")
            self.assertEqual(channel.uploads, [("sendPhoto", "photo", "3001", str(image_path))])


if __name__ == "__main__":
    unittest.main()
