import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.channels.events import OutboundMessage
from src.channels.feishu import FeishuChannel, _build_status_card, _should_use_interactive_card
from src.runtime import InboundMessage


def _make_download_response(payload: bytes, *, file_name: str) -> SimpleNamespace:
    response = SimpleNamespace(
        code=0,
        msg="",
        file=io.BytesIO(payload),
        file_name=file_name,
        raw=SimpleNamespace(headers={}),
    )
    response.success = lambda: True
    return response


def _record_and_return(calls: list, request: object, response: object) -> object:
    calls.append(request)
    return response


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
        self.sent_cards: list[tuple[str, dict]] = []
        self.patched_cards: list[tuple[str, dict]] = []
        self.sent_images: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []

    def _send_text_sync(self, chat_id: str, text: str) -> str:
        self.sent_texts.append((chat_id, text))
        return "om_text_1"

    def _send_interactive_sync(self, chat_id: str, card: dict) -> str:
        self.sent_cards.append((chat_id, card))
        return "om_card_1"

    def _patch_interactive_sync(self, message_id: str, card: dict) -> None:
        self.patched_cards.append((message_id, card))

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
    def test_download_file_sync_prefers_message_resource(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)
        message_resource_api = SimpleNamespace(
            calls=[],
            get=lambda request: _record_and_return(
                message_resource_api.calls,
                request,
                _make_download_response(b"image-bytes", file_name="uploaded.jpg"),
            ),
        )
        legacy_file_api = SimpleNamespace(
            calls=[],
            get=lambda request: _record_and_return(
                legacy_file_api.calls,
                request,
                _make_download_response(b"legacy-bytes", file_name="legacy.bin"),
            ),
        )
        channel._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message_resource=message_resource_api,
                    file=legacy_file_api,
                )
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch(
            "src.channels.feishu.channel_inbox_dir",
            return_value=Path(tmp_dir),
        ):
            output_path = channel._download_file_sync(
                "file_v3_demo",
                "nmrl8j_demo.jpg",
                "om_message_1",
            )
            self.assertEqual(output_path.name, "uploaded.jpg")
            self.assertEqual(output_path.read_bytes(), b"image-bytes")

        self.assertEqual(len(message_resource_api.calls), 1)
        self.assertEqual(message_resource_api.calls[0].message_id, "om_message_1")
        self.assertEqual(message_resource_api.calls[0].file_key, "file_v3_demo")
        self.assertEqual(message_resource_api.calls[0].type, "file")
        self.assertEqual(legacy_file_api.calls, [])

    def test_download_file_sync_falls_back_to_legacy_file_api(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)
        legacy_file_api = SimpleNamespace(
            calls=[],
            get=lambda request: _record_and_return(
                legacy_file_api.calls,
                request,
                _make_download_response(b"legacy-bytes", file_name="fallback.bin"),
            ),
        )
        channel._client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    file=legacy_file_api,
                )
            )
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch(
            "src.channels.feishu.channel_inbox_dir",
            return_value=Path(tmp_dir),
        ), patch("src.channels.feishu.GetMessageResourceRequest", None):
            output_path = channel._download_file_sync(
                "file_v3_demo",
                "demo.bin",
                "om_message_2",
            )
            self.assertEqual(output_path.name, "fallback.bin")
            self.assertEqual(output_path.read_bytes(), b"legacy-bytes")

        self.assertEqual(len(legacy_file_api.calls), 1)
        self.assertEqual(legacy_file_api.calls[0].file_key, "file_v3_demo")

    def test_build_status_card_uses_final_header(self) -> None:
        card = _build_status_card("图片已经生成好了。", {"display_style": "final"})

        self.assertEqual(card["header"]["title"]["content"], "处理结果")
        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("图片已经生成好了。", card["elements"][0]["content"])

    def test_should_use_interactive_card_for_progress_messages(self) -> None:
        self.assertTrue(_should_use_interactive_card("我先处理一下你的请求。", {"display_style": "progress"}))
        self.assertTrue(_should_use_interactive_card("处理完成。", {"display_style": "final"}))
        self.assertFalse(_should_use_interactive_card("done", {}))

    def test_build_status_card_uses_stage_title_when_provided(self) -> None:
        card = _build_status_card(
            "正在继续处理，请稍等。",
            {"display_style": "progress", "stage": "in_progress", "stage_title": "正在生成图片"},
        )

        self.assertEqual(card["header"]["title"]["content"], "正在生成图片")

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

    async def test_send_uses_interactive_card_for_progress_message(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="我先处理一下你的请求。",
                metadata={"display_style": "progress", "stage": "started"},
            )
        )

        self.assertEqual(channel.sent_texts, [])
        self.assertEqual(len(channel.sent_cards), 1)
        self.assertEqual(channel.sent_cards[0][0], "oc_group_1")
        self.assertEqual(channel.sent_cards[0][1]["header"]["title"]["content"], "开始处理")

    async def test_send_updates_existing_progress_card_for_same_session(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        first = OutboundMessage(
            channel="feishu",
            chat_id="oc_group_1",
            text="我先处理一下你的请求。",
            metadata={"display_style": "progress", "stage": "started", "session_id": "s1"},
        )
        second = OutboundMessage(
            channel="feishu",
            chat_id="oc_group_1",
            text="当前进展：正在生成图片。",
            metadata={"display_style": "progress", "stage": "in_progress", "session_id": "s1"},
        )

        await channel.send(first)
        await channel.send(second)

        self.assertEqual(len(channel.sent_cards), 1)
        self.assertEqual(len(channel.patched_cards), 1)
        self.assertEqual(channel.patched_cards[0][0], "om_card_1")
        self.assertEqual(channel.patched_cards[0][1]["header"]["title"]["content"], "处理中")

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

    async def test_on_post_message_downloads_embedded_image_attachment(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_4",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="post",
                    content=(
                        '{"zh_cn":{"content":[['
                        '{"tag":"text","text":"请描述这张图"},'
                        '{"tag":"img","image_key":"img_post_1"}'
                        ']]}}'
                    ),
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(inbound_messages[0].text, "请描述这张图")
        self.assertEqual(len(inbound_messages[0].attachments), 1)
        self.assertEqual(inbound_messages[0].attachments[0].path, "/tmp/om_4_img_post_1.png")


if __name__ == "__main__":
    unittest.main()
