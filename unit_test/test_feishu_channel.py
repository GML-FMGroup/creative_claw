import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.channels.events import OutboundMessage
from src.channels.feishu import FeishuChannel
from src.runtime import InboundMessage


class _TestFeishuChannel(FeishuChannel):
    def __init__(self, *, inbound_messages: list[InboundMessage]) -> None:
        async def _handler(message: InboundMessage) -> None:
            inbound_messages.append(message)

        super().__init__(
            app_id="app-id",
            app_secret="app-secret",
            allow_from=["ou_allowed"],
            inbound_handler=_handler,
        )
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_images: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []

    def _send_text_sync(self, chat_id: str, text: str) -> str:
        self.sent_texts.append((chat_id, text))
        return "om_text_1"

    def _send_image_sync(self, chat_id: str, image_path: str) -> str:
        self.sent_images.append((chat_id, image_path))
        return "om_image_1"

    def _send_file_sync(self, chat_id: str, file_path: str) -> str:
        self.sent_files.append((chat_id, file_path))
        return "om_file_1"

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        self.reactions.append((message_id, emoji_type))

    async def _download_image(self, image_key: str, message_id: str) -> Path:
        return Path(f"/tmp/{message_id}_{image_key}.png")

    async def _download_file(self, file_key: str, file_name: str, message_id: str) -> Path:
        return Path(f"/tmp/{message_id}_{file_name or file_key}.bin")


class FeishuChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_message_dispatches_text_message(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_1",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="text",
                    content='{"text":"hello from feishu"}',
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(inbound_messages[0].chat_id, "oc_group_1")
        self.assertEqual(inbound_messages[0].text, "hello from feishu")
        self.assertEqual(channel.reactions, [("om_1", "THUMBSUP")])

    async def test_on_message_respects_allow_list(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_2",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="text",
                    content='{"text":"hello"}',
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_blocked"),
                ),
            )
        )

        await channel._on_message(data)
        self.assertEqual(inbound_messages, [])
        self.assertEqual(channel.reactions, [])

    async def test_send_routes_text_and_image_artifact(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "image.png"
            image_path.write_bytes(b"fake-image")

            await channel.send(
                OutboundMessage(
                    channel="feishu",
                    chat_id="oc_group_1",
                    text="done",
                    artifact_paths=[str(image_path)],
                )
            )

            self.assertEqual(channel.sent_texts, [("oc_group_1", "done")])
            self.assertEqual(channel.sent_images, [("oc_group_1", str(image_path))])

    async def test_on_message_downloads_image_attachment(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_3",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="image",
                    content='{"image_key":"img_v2_1"}',
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(inbound_messages[0].attachments[0].path, "/tmp/om_3_img_v2_1.png")


if __name__ == "__main__":
    unittest.main()
