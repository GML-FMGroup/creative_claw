import io
import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.channels.feishu as feishu_module
from src.channels.events import OutboundMessage
from src.channels.feishu import (
    FeishuChannel,
    _build_final_reply_card,
    _build_status_card,
    _should_use_interactive_card,
)
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
    def __init__(
        self,
        *,
        inbound_messages: list[InboundMessage],
        group_policy: str = "open",
        reply_to_message: bool = False,
    ) -> None:
        async def _handler(message: InboundMessage) -> None:
            inbound_messages.append(message)

        super().__init__(
            app_id="app-id",
            app_secret="app-secret",
            allow_from=["ou_allowed"],
            group_policy=group_policy,
            reply_to_message=reply_to_message,
            inbound_handler=_handler,
        )
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_cards: list[tuple[str, dict]] = []
        self.patched_cards: list[tuple[str, dict]] = []
        self.sent_images: list[tuple[str, str]] = []
        self.sent_videos: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str, str]] = []

    def _send_text_sync(self, chat_id: str, text: str) -> str:
        self.sent_texts.append((chat_id, text))
        return "om_text_1"

    def _send_interactive_sync(self, chat_id: str, card: dict) -> str:
        self.sent_cards.append((chat_id, card))
        return f"om_card_{len(self.sent_cards)}"

    def _patch_interactive_sync(self, message_id: str, card: dict) -> None:
        self.patched_cards.append((message_id, card))

    def _send_image_sync(self, chat_id: str, image_path: str) -> str:
        self.sent_images.append((chat_id, image_path))
        return "om_image_1"

    def _send_video_sync(self, chat_id: str, video_path: str) -> str:
        self.sent_videos.append((chat_id, video_path))
        return "om_video_1"

    def _send_file_sync(self, chat_id: str, file_path: str) -> str:
        self.sent_files.append((chat_id, file_path))
        return "om_file_1"

    def _reply_message_sync(self, parent_message_id: str, msg_type: str, content: str) -> str:
        self.replies.append((parent_message_id, msg_type, content))
        return "om_reply_1"

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        self.reactions.append((message_id, emoji_type))

    async def _download_image(self, image_key: str, message_id: str) -> Path:
        return Path(f"/tmp/{message_id}_{image_key}.png")

    async def _download_file(self, file_key: str, file_name: str, message_id: str) -> Path:
        return Path(f"/tmp/{message_id}_{file_name or file_key}.bin")


class FeishuChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_feishu_ws_client_inside_dedicated_thread_loop(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)
        main_loop = asyncio.get_running_loop()
        started = threading.Event()
        state: dict[str, object] = {}

        class _Builder:
            def app_id(self, _value: str) -> "_Builder":
                return self

            def app_secret(self, _value: str) -> "_Builder":
                return self

            def log_level(self, _value: object) -> "_Builder":
                return self

            def build(self) -> SimpleNamespace:
                return SimpleNamespace()

        class _DispatcherBuilder:
            def register_p2_im_message_receive_v1(self, _handler) -> "_DispatcherBuilder":
                return self

            def build(self) -> str:
                return "dispatcher"

        class _FakeWsClient:
            def __init__(
                self,
                _app_id: str,
                _app_secret: str,
                *,
                event_handler: object,
                log_level: object,
                auto_reconnect: bool,
            ) -> None:
                state["constructor_thread"] = threading.current_thread().name
                state["event_handler"] = event_handler
                state["log_level"] = log_level
                state["auto_reconnect"] = auto_reconnect
                self._conn = None

            def start(self) -> None:
                state["start_thread"] = threading.current_thread().name
                state["loop"] = feishu_module.lark_ws_client_module.loop
                started.set()
                while channel._running:
                    time.sleep(0.01)

            async def _disconnect(self) -> None:
                state["disconnect_called"] = True

        fake_lark = SimpleNamespace(
            Client=SimpleNamespace(builder=lambda: _Builder()),
            EventDispatcherHandler=SimpleNamespace(
                builder=lambda *_args: _DispatcherBuilder()
            ),
            LogLevel=SimpleNamespace(INFO="info"),
            ws=SimpleNamespace(Client=_FakeWsClient),
        )
        fake_ws_module = SimpleNamespace(loop=main_loop)

        with (
            patch("src.channels.feishu.FEISHU_AVAILABLE", True),
            patch("src.channels.feishu.lark", fake_lark),
            patch("src.channels.feishu.lark_ws_client_module", fake_ws_module),
        ):
            await channel.start()
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            await channel.stop()

        self.assertTrue(started.is_set())
        self.assertIsNot(state["loop"], main_loop)
        self.assertEqual(state["event_handler"], "dispatcher")
        self.assertEqual(state["log_level"], "info")
        self.assertFalse(state["auto_reconnect"])
        self.assertNotEqual(state["constructor_thread"], threading.current_thread().name)
        self.assertEqual(state["constructor_thread"], state["start_thread"])

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

    def test_build_status_card_does_not_use_result_header_for_final_style(self) -> None:
        card = _build_status_card("The image is ready.", {"display_style": "final"})

        self.assertEqual(card["header"]["title"]["content"], "Completed")
        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("The image is ready.", card["elements"][0]["content"])

    def test_build_final_reply_card_has_no_status_header(self) -> None:
        card = _build_final_reply_card("The image is ready.")

        self.assertNotIn("header", card)
        self.assertIn("The image is ready.", card["elements"][0]["content"])

    def test_should_use_interactive_card_for_progress_messages(self) -> None:
        self.assertTrue(_should_use_interactive_card("I'll start processing your request.", {"display_style": "progress"}))
        self.assertFalse(_should_use_interactive_card("Done.", {"display_style": "final"}))
        self.assertFalse(_should_use_interactive_card("done", {}))

    def test_build_status_card_uses_stage_title_when_provided(self) -> None:
        card = _build_status_card(
            "Still working, please wait.",
            {"display_style": "progress", "stage": "in_progress", "stage_title": "Generating Image"},
        )

        self.assertEqual(card["header"]["title"]["content"], "Generating Image")

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

    async def test_on_message_ignores_duplicate_message_id(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_dup_1",
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
        await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(channel.reactions, [("om_dup_1", "THUMBSUP")])

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

    async def test_on_group_message_requires_bot_mention_by_default_policy(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages, group_policy="mention")
        channel._bot_open_id = "ou_bot"

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_group_plain",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="text",
                    content='{"text":"hello group"}',
                    mentions=[],
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        await channel._on_message(data)

        self.assertEqual(inbound_messages, [])
        self.assertEqual(channel.reactions, [])

    async def test_on_group_message_accepts_bot_mention_and_strips_placeholder(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages, group_policy="mention")
        channel._bot_open_id = "ou_bot"

        bot_mention = SimpleNamespace(
            key="@_user_1",
            name="CreativeClaw",
            id=SimpleNamespace(open_id="ou_bot", user_id=""),
        )
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_group_mention",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="text",
                    content='{"text":"@_user_1 please create a poster"}',
                    mentions=[bot_mention],
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(inbound_messages[0].text, "please create a poster")
        self.assertEqual(channel.reactions, [("om_group_mention", "THUMBSUP")])

    async def test_on_message_includes_reply_context_and_thread_metadata(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_reply_1",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="text",
                    content='{"text":"make it brighter"}',
                    parent_id="om_parent",
                    root_id="om_root",
                    thread_id="omt_thread",
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_allowed"),
                ),
            )
        )

        with patch.object(channel, "_get_reply_context", return_value="[Reply to: original image]"):
            await channel._on_message(data)

        self.assertEqual(len(inbound_messages), 1)
        self.assertEqual(inbound_messages[0].text, "[Reply to: original image]\nmake it brighter")
        self.assertEqual(inbound_messages[0].metadata["parent_id"], "om_parent")
        self.assertEqual(inbound_messages[0].metadata["root_id"], "om_root")
        self.assertEqual(inbound_messages[0].metadata["thread_id"], "omt_thread")

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

    async def test_send_logs_artifact_details_when_file_send_fails(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "model.glb"
            file_path.write_bytes(b"glb-data")

            def _raise_send_failure(chat_id: str, path: str) -> str:
                raise RuntimeError(f"boom: {chat_id} {path}")

            channel._send_file_sync = _raise_send_failure  # type: ignore[method-assign]
            mock_logger = MagicMock()
            mock_logger.opt.return_value = mock_logger

            with patch("src.channels.feishu.logger", mock_logger):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await channel.send(
                        OutboundMessage(
                            channel="feishu",
                            chat_id="oc_group_1",
                            text="done",
                            artifact_paths=[str(file_path)],
                        )
                    )

            self.assertEqual(mock_logger.debug.call_count, 1)
            self.assertEqual(mock_logger.opt.call_count, 1)
            error_args = mock_logger.error.call_args.args
            self.assertEqual(
                error_args[0],
                "Feishu outbound artifact send failed: index={} total={} kind={} path={} exists={} size_bytes={} mime_type={}",
            )
            self.assertEqual(error_args[1:4], (1, 1, "file"))
            self.assertEqual(error_args[4], str(file_path))
            self.assertTrue(error_args[5])
            self.assertEqual(error_args[6], len(b"glb-data"))
            self.assertEqual(error_args[7], "model/gltf-binary")

    def test_upload_file_sync_logs_response_summary_when_sdk_parse_fails(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        def _raise_parse_failure(_request: object) -> object:
            resp = SimpleNamespace(
                status_code=400,
                headers={"Content-Type": "text/plain"},
                content=b"Error when parsing request",
            )
            raise json.JSONDecodeError("Expecting value", "Error when parsing request", 0)

        channel._client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(file=SimpleNamespace(create=_raise_parse_failure)))
        )
        mock_logger = MagicMock()
        mock_logger.opt.return_value = mock_logger

        with tempfile.TemporaryDirectory() as tmp_dir, patch("src.channels.feishu.logger", mock_logger):
            file_path = Path(tmp_dir) / "model.glb"
            file_path.write_bytes(b"glb-data")

            with self.assertRaises(json.JSONDecodeError):
                channel._upload_file_sync(str(file_path))

        self.assertEqual(mock_logger.debug.call_count, 1)
        self.assertEqual(mock_logger.opt.call_count, 1)
        error_args = mock_logger.error.call_args.args
        self.assertEqual(
            error_args[0],
            "Feishu file upload failed before SDK parse completed: path={} exists={} size_bytes={} mime_type={} file_name={} response_status={} response_content_type={} response_body={}",
        )
        self.assertEqual(error_args[1], str(file_path.resolve()))
        self.assertTrue(error_args[2])
        self.assertEqual(error_args[3], len(b"glb-data"))
        self.assertEqual(error_args[4], "model/gltf-binary")
        self.assertEqual(error_args[5], "model.glb")
        self.assertEqual(error_args[6], 400)
        self.assertEqual(error_args[7], "text/plain")
        self.assertEqual(error_args[8], "Error when parsing request")

    async def test_send_uses_interactive_card_for_progress_message(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="I'll start processing your request.",
                metadata={"display_style": "progress", "stage": "started"},
            )
        )

        self.assertEqual(channel.sent_texts, [])
        self.assertEqual(len(channel.sent_cards), 1)
        self.assertEqual(channel.sent_cards[0][0], "oc_group_1")
        self.assertEqual(channel.sent_cards[0][1]["header"]["title"]["content"], "Starting")

    async def test_send_updates_existing_progress_card_for_same_session(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        first = OutboundMessage(
            channel="feishu",
            chat_id="oc_group_1",
            text="I'll start processing your request.",
            metadata={"display_style": "progress", "stage": "started", "session_id": "s1"},
        )
        second = OutboundMessage(
            channel="feishu",
            chat_id="oc_group_1",
            text="Current progress: Generating image.",
            metadata={"display_style": "progress", "stage": "in_progress", "session_id": "s1"},
        )

        await channel.send(first)
        await channel.send(second)

        self.assertEqual(len(channel.sent_cards), 1)
        self.assertEqual(len(channel.patched_cards), 1)
        self.assertEqual(channel.patched_cards[0][0], "om_card_1")
        self.assertEqual(channel.patched_cards[0][1]["header"]["title"]["content"], "In Progress")

    async def test_send_keeps_progress_cards_separate_across_turns(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Working on the first request.",
                metadata={
                    "display_style": "progress",
                    "stage": "started",
                    "session_id": "s1",
                    "turn_index": 1,
                },
            )
        )
        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Working on the second request.",
                metadata={
                    "display_style": "progress",
                    "stage": "started",
                    "session_id": "s1",
                    "turn_index": 2,
                },
            )
        )
        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Second request is generating an image.",
                metadata={
                    "display_style": "progress",
                    "stage": "in_progress",
                    "session_id": "s1",
                    "turn_index": 2,
                },
            )
        )

        self.assertEqual(len(channel.sent_cards), 2)
        self.assertEqual(len(channel.patched_cards), 1)
        self.assertEqual(channel.patched_cards[0][0], "om_card_2")
        self.assertEqual(channel.patched_cards[0][1]["header"]["title"]["content"], "In Progress")

    async def test_send_final_reply_uses_chat_text_not_result_card(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Here is the final answer.",
                metadata={"display_style": "final", "session_id": "s1"},
            )
        )

        self.assertEqual(channel.sent_texts, [("oc_group_1", "Here is the final answer.")])
        self.assertEqual(channel.sent_cards, [])

    async def test_send_final_reply_patches_progress_card_to_completed(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Working.",
                metadata={"display_style": "progress", "stage": "started", "session_id": "s1"},
            )
        )
        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Here is the final answer.",
                metadata={"display_style": "final", "session_id": "s1"},
            )
        )

        self.assertEqual(len(channel.sent_cards), 1)
        self.assertEqual(len(channel.patched_cards), 1)
        self.assertEqual(channel.patched_cards[0][0], "om_card_1")
        self.assertEqual(channel.patched_cards[0][1]["header"]["title"]["content"], "Completed")
        self.assertEqual(channel.sent_texts, [("oc_group_1", "Here is the final answer.")])

    async def test_send_final_reply_completes_only_current_turn_progress_card(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="First request is working.",
                metadata={
                    "display_style": "progress",
                    "stage": "started",
                    "session_id": "s1",
                    "turn_index": 1,
                },
            )
        )
        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Second request is working.",
                metadata={
                    "display_style": "progress",
                    "stage": "started",
                    "session_id": "s1",
                    "turn_index": 2,
                },
            )
        )
        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Here is the second answer.",
                metadata={"display_style": "final", "session_id": "s1", "turn_index": 2},
            )
        )

        self.assertEqual(len(channel.sent_cards), 2)
        self.assertEqual(len(channel.patched_cards), 1)
        self.assertEqual(channel.patched_cards[0][0], "om_card_2")
        self.assertIn(("oc_group_1", "s1", "1"), channel._progress_cards)
        self.assertNotIn(("oc_group_1", "s1", "2"), channel._progress_cards)
        self.assertEqual(channel.sent_texts, [("oc_group_1", "Here is the second answer.")])

    async def test_send_final_reply_uses_reply_api_when_configured(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages, reply_to_message=True)

        await channel.send(
            OutboundMessage(
                channel="feishu",
                chat_id="oc_group_1",
                text="Here is the final answer.",
                metadata={"display_style": "final", "message_id": "om_user_1"},
            )
        )

        self.assertEqual(len(channel.replies), 1)
        self.assertEqual(channel.replies[0][0], "om_user_1")
        self.assertEqual(channel.replies[0][1], "text")
        self.assertEqual(json.loads(channel.replies[0][2]), {"text": "Here is the final answer."})
        self.assertEqual(channel.sent_texts, [])

    async def test_send_routes_video_artifact_as_video_message(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "clip.mp4"
            video_path.write_bytes(b"fake-video")

            await channel.send(
                OutboundMessage(
                    channel="feishu",
                    chat_id="oc_group_1",
                    text="done",
                    artifact_paths=[str(video_path)],
                )
            )

            self.assertEqual(channel.sent_texts, [("oc_group_1", "done")])
            self.assertEqual(channel.sent_videos, [("oc_group_1", str(video_path))])

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
                        '{"en_us":{"content":[['
                        '{"tag":"text","text":"Please describe this image"},'
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
        self.assertEqual(inbound_messages[0].text, "Please describe this image")
        self.assertEqual(len(inbound_messages[0].attachments), 1)
        self.assertEqual(inbound_messages[0].attachments[0].path, "/tmp/om_4_img_post_1.png")

    async def test_on_post_message_extracts_text_from_top_level_content(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_5",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="post",
                    content=(
                        '{"title":"Image grounding task","content":[['
                        '{"tag":"text","text":"Please return the person bbox coordinates from this image by using ImageGroundingAgent."},'
                        '{"tag":"img","image_key":"img_post_2"}'
                        ']]}'
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
        self.assertEqual(
            inbound_messages[0].text,
            "Image grounding task Please return the person bbox coordinates from this image by using ImageGroundingAgent.",
        )
        self.assertEqual(len(inbound_messages[0].attachments), 1)
        self.assertEqual(inbound_messages[0].attachments[0].path, "/tmp/om_5_img_post_2.png")

    async def test_on_post_message_falls_back_to_title_when_body_has_only_image(self) -> None:
        inbound_messages: list[InboundMessage] = []
        channel = _TestFeishuChannel(inbound_messages=inbound_messages)

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_6",
                    chat_id="oc_group_1",
                    chat_type="group",
                    message_type="post",
                    content=(
                        '{"zh_cn":{"title":"Please output the person bbox","content":[['
                        '{"tag":"img","image_key":"img_post_3"}'
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
        self.assertEqual(inbound_messages[0].text, "Please output the person bbox")
        self.assertEqual(len(inbound_messages[0].attachments), 1)
        self.assertEqual(inbound_messages[0].attachments[0].path, "/tmp/om_6_img_post_3.png")


if __name__ == "__main__":
    unittest.main()
