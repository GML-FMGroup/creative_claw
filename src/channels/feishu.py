"""Feishu chat adapter for Creative Claw."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from src.logger import logger
from src.runtime import InboundMessage, MessageAttachment
from src.runtime.workspace import channel_inbox_dir

from .base import BaseChannel
from .events import OutboundMessage

try:
    import lark_oapi as lark
    import lark_oapi.ws.client as lark_ws_client_module
    from lark_oapi.api.im.v1 import (
        CreateFileRequest,
        CreateFileRequestBody,
        CreateImageRequest,
        CreateImageRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        GetFileRequest,
        GetMessageRequest,
        GetMessageResourceRequest,
        PatchMessageRequest,
        PatchMessageRequestBody,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
        UpdateMessageRequest,
        UpdateMessageRequestBody,
    )

    FEISHU_AVAILABLE = True
    FEISHU_REACTION_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    lark = None
    lark_ws_client_module = None
    CreateFileRequest = None
    CreateFileRequestBody = None
    CreateImageRequest = None
    CreateImageRequestBody = None
    CreateMessageRequest = None
    CreateMessageRequestBody = None
    CreateMessageReactionRequest = None
    CreateMessageReactionRequestBody = None
    Emoji = None
    GetFileRequest = None
    GetMessageRequest = None
    GetMessageResourceRequest = None
    PatchMessageRequest = None
    PatchMessageRequestBody = None
    P2ImMessageReceiveV1 = None
    ReplyMessageRequest = None
    ReplyMessageRequestBody = None
    UpdateMessageRequest = None
    UpdateMessageRequestBody = None
    FEISHU_AVAILABLE = False
    FEISHU_REACTION_AVAILABLE = False


_STAGE_TITLES = {
    "started": "Starting",
    "attachment_received": "Attachment Received",
    "in_progress": "In Progress",
    "completed": "Completed",
}

_MESSAGE_DEDUP_TTL_SECONDS = 600.0
_MESSAGE_DEDUP_MAX_ENTRIES = 4096
_FINAL_REPLY_TEXT_MAX_LEN = 700
_FINAL_REPLY_CARD_MIN_LEN = 180
_FEISHU_FILE_TYPE_BY_SUFFIX = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}


def _build_status_card(text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one lightweight Feishu card for progress or final text."""
    info = metadata or {}
    display_style = str(info.get("display_style", "")).strip().lower()
    stage = str(info.get("stage", "")).strip().lower()
    if display_style == "final":
        title = str(info.get("stage_title", "")).strip() or "Completed"
        template = "green"
    else:
        title = str(info.get("stage_title", "")).strip() or _STAGE_TITLES.get(stage, "Current Progress")
        template = {
            "started": "blue",
            "attachment_received": "wathet",
            "in_progress": "indigo",
            "completed": "green",
        }.get(stage, "blue")

    body = str(text or "").strip() or "No content available."
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": body,
            }
        ],
    }


def _should_use_interactive_card(text: str, metadata: dict[str, Any] | None = None) -> bool:
    """Return whether one outbound text should be rendered as a status card."""
    info = metadata or {}
    display_style = str(info.get("display_style", "")).strip().lower()
    if display_style == "progress":
        return True
    if display_style == "final":
        return False
    return len(str(text or "").strip()) > 180


def _build_final_reply_card(text: str) -> dict[str, Any]:
    """Build a Feishu card for rich final replies without status-style framing."""
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "elements": [
            {
                "tag": "markdown",
                "content": str(text or "").strip() or "[empty message]",
            }
        ],
    }


def _final_reply_needs_card(text: str) -> bool:
    """Return whether final reply text needs rich card rendering."""
    stripped = str(text or "").strip()
    if len(stripped) > _FINAL_REPLY_TEXT_MAX_LEN:
        return True
    return _has_complex_markdown(stripped) and len(stripped) > _FINAL_REPLY_CARD_MIN_LEN


def _has_complex_markdown(text: str) -> bool:
    """Return whether text contains markdown that Feishu plain text would expose awkwardly."""
    markers = ("```", "|", "# ", "## ", "- ", "* ", "1. ", "**", "__", "~~", "](")
    return any(marker in text for marker in markers)


def _progress_state_key(chat_id: str, metadata: dict[str, Any] | None = None) -> tuple[str, str, str] | None:
    """Build the Feishu progress-card state key for one session turn."""
    info = metadata or {}
    session_id = str(info.get("session_id", "") or "").strip()
    if not session_id:
        return None
    turn_index = str(info.get("turn_index", "") or "").strip()
    return (chat_id, session_id, turn_index)


def _iter_post_lang_payloads(content_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all language-specific payload blocks for one Feishu post message."""
    payloads: list[dict[str, Any]] = []
    if isinstance(content_json.get("content"), list):
        payloads.append(content_json)
    for lang_key in ("zh_cn", "en_us", "ja_jp"):
        lang = content_json.get(lang_key)
        if isinstance(lang, dict) and isinstance(lang.get("content"), list):
            payloads.append(lang)
    return payloads


def _extract_post_image_keys(content_json: dict[str, Any]) -> list[str]:
    """Extract all unique image keys embedded inside one Feishu post payload."""
    image_keys: list[str] = []
    for lang in _iter_post_lang_payloads(content_json):
        blocks = lang.get("content", [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, list):
                continue
            for element in block:
                if not isinstance(element, dict):
                    continue
                if element.get("tag") not in {"img", "image"}:
                    continue
                image_key = str(element.get("image_key", "")).strip()
                if image_key:
                    image_keys.append(image_key)
    return list(dict.fromkeys(image_keys))


class FeishuChannel(BaseChannel):
    """Minimal Feishu adapter using the official long connection SDK."""

    name = "feishu"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        inbound_handler: Callable[[InboundMessage], Awaitable[None]],
        allow_from: list[str] | None = None,
        encrypt_key: str = "",
        verification_token: str = "",
        group_policy: str = "mention",
        reply_to_message: bool = False,
    ) -> None:
        super().__init__()
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.encrypt_key = encrypt_key.strip()
        self.verification_token = verification_token.strip()
        self.group_policy = group_policy.strip().lower() if group_policy else "mention"
        if self.group_policy not in {"mention", "open"}:
            self.group_policy = "mention"
        self.reply_to_message = bool(reply_to_message)
        self.inbound_handler = inbound_handler
        self.allow_from = {
            str(item).strip()
            for item in (allow_from or [])
            if str(item).strip()
        }
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._seen_message_ids: OrderedDict[str, float] = OrderedDict()
        self._progress_cards: dict[tuple[str, str, str], str] = {}
        self._bot_open_id: str | None = None

    async def start(self) -> None:
        """Start Feishu long connection."""
        if not FEISHU_AVAILABLE:
            raise RuntimeError("Feishu channel requires `lark-oapi`.")
        if not self.app_id or not self.app_secret:
            raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")
        if self._ws_thread and self._ws_thread.is_alive():
            return

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._client = (
            lark.Client.builder()  # type: ignore[union-attr]
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.INFO)  # type: ignore[union-attr]
            .build()
        )
        dispatcher = (
            lark.EventDispatcherHandler.builder(  # type: ignore[union-attr]
                self.encrypt_key or "",
                self.verification_token or "",
            )
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )

        def _run_ws_forever() -> None:
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            self._ws_loop = thread_loop
            if lark_ws_client_module is not None:
                lark_ws_client_module.loop = thread_loop
            self._ws_client = lark.ws.Client(  # type: ignore[union-attr]
                self.app_id,
                self.app_secret,
                event_handler=dispatcher,
                log_level=lark.LogLevel.INFO,  # type: ignore[union-attr]
                auto_reconnect=False,
            )

            while self._running:
                try:
                    self._ws_client.start()
                except RuntimeError as exc:
                    stop_text = str(exc)
                    if not self._running and (
                        "event loop stopped before future completed" in stop_text.lower()
                        or "event loop is closed" in stop_text.lower()
                    ):
                        break
                    logger.exception("Feishu websocket loop failed; retrying")
                    if self._running:
                        time.sleep(3)
                except Exception:
                    logger.exception("Feishu websocket loop failed; retrying")
                    if self._running:
                        time.sleep(3)

            try:
                pending = asyncio.all_tasks(thread_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    thread_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                logger.exception("Feishu websocket thread cleanup failed")
            finally:
                self._ws_client = None
                self._ws_loop = None
                thread_loop.close()

        self._ws_thread = threading.Thread(target=_run_ws_forever, daemon=True)
        self._ws_thread.start()
        self._bot_open_id = await self._fetch_bot_open_id()

    async def stop(self) -> None:
        """Stop Feishu long connection."""
        self._running = False
        ws_client = self._ws_client
        ws_loop = self._ws_loop
        if ws_client and ws_loop and not ws_loop.is_closed():
            try:
                setattr(ws_client, "_auto_reconnect", False)
                disconnect_fn = getattr(ws_client, "_disconnect", None)
                if callable(disconnect_fn) and ws_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(disconnect_fn(), ws_loop)
                    future.result(timeout=5)
                if ws_loop.is_running():
                    ws_loop.call_soon_threadsafe(ws_loop.stop)
            except Exception:
                logger.exception("Failed stopping Feishu websocket client")
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        self._ws_thread = None

    async def _fetch_bot_open_id(self) -> str | None:
        """Fetch the bot open id for accurate group mention matching."""
        if not self._client:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_bot_open_id_sync)

    def _fetch_bot_open_id_sync(self) -> str | None:
        """Fetch the bot open id through the Feishu bot info API when available."""
        if not self._client or lark is None:
            return None
        base_request = getattr(lark, "BaseRequest", None)
        http_method = getattr(lark, "HttpMethod", None)
        access_token_type = getattr(lark, "AccessTokenType", None)
        if base_request is None or http_method is None or access_token_type is None:
            return None
        try:
            request = (
                base_request.builder()
                .http_method(http_method.GET)
                .uri("/open-apis/bot/v3/info")
                .token_types({access_token_type.APP})
                .build()
            )
            response = self._client.request(request)
            self._ensure_success(response, "bot info")
            raw_content = getattr(getattr(response, "raw", None), "content", b"")
            if isinstance(raw_content, bytes | bytearray):
                payload = json.loads(bytes(raw_content).decode("utf-8"))
            else:
                payload = json.loads(str(raw_content or "{}"))
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            bot = data.get("bot", data) if isinstance(data, dict) else {}
            open_id = str(bot.get("open_id", "")).strip() if isinstance(bot, dict) else ""
            return open_id or None
        except Exception as exc:
            logger.warning("Failed fetching Feishu bot open_id: {}", exc)
            return None

    async def send(self, message: OutboundMessage) -> None:
        """Send one outbound Feishu message and artifacts."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_sync, message)

    def _send_sync(self, message: OutboundMessage) -> None:
        """Blocking Feishu send path."""
        text = message.text.strip() if message.text else ""
        display_style = str((message.metadata or {}).get("display_style", "")).strip().lower()
        if display_style == "final":
            self._complete_progress_card_sync(message.chat_id, message.metadata)
        if text or not message.artifact_paths:
            rendered_text = text or "[empty message]"
            if display_style == "final":
                self._send_final_reply_sync(message.chat_id, rendered_text, message.metadata)
            elif _should_use_interactive_card(rendered_text, message.metadata):
                self._send_card_message_sync(message.chat_id, rendered_text, message.metadata)
            else:
                self._send_text_sync(message.chat_id, rendered_text)
        total_artifacts = len(message.artifact_paths)
        for index, artifact_path in enumerate(message.artifact_paths, start=1):
            cleaned_path = artifact_path.strip()
            if not cleaned_path:
                continue
            path_info = _describe_local_file(cleaned_path)
            if _is_image_file(cleaned_path):
                artifact_kind = "image"
            elif _is_video_file(cleaned_path):
                artifact_kind = "video"
            else:
                artifact_kind = "file"
            logger.debug(
                "Feishu outbound artifact send starting: index={} total={} kind={} path={} exists={} size_bytes={} mime_type={}",
                index,
                total_artifacts,
                artifact_kind,
                cleaned_path,
                path_info["exists"],
                path_info["size_bytes"],
                path_info["mime_type"],
            )
            try:
                if artifact_kind == "image":
                    self._send_image_sync(message.chat_id, cleaned_path)
                elif artifact_kind == "video":
                    self._send_video_sync(message.chat_id, cleaned_path)
                else:
                    self._send_file_sync(message.chat_id, cleaned_path)
            except Exception as exc:
                logger.opt(exception=exc).error(
                    "Feishu outbound artifact send failed: index={} total={} kind={} path={} exists={} size_bytes={} mime_type={}",
                    index,
                    total_artifacts,
                    artifact_kind,
                    cleaned_path,
                    path_info["exists"],
                    path_info["size_bytes"],
                    path_info["mime_type"],
                )
                raise
            logger.debug(
                "Feishu outbound artifact send finished: index={} total={} kind={} path={}",
                index,
                total_artifacts,
                artifact_kind,
                cleaned_path,
            )

    def _complete_progress_card_sync(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Best-effort update the active progress card to a short completed state."""
        state_key = _progress_state_key(chat_id, metadata)
        if state_key is None:
            return
        existing_message_id = self._progress_cards.pop(state_key, "")
        if not existing_message_id:
            return
        card = _build_status_card(
            "Done.",
            {"display_style": "progress", "stage": "completed", "stage_title": "Completed"},
        )
        try:
            self._patch_interactive_sync(existing_message_id, card)
        except Exception as exc:
            logger.warning("Failed completing Feishu progress card: {}", exc)

    def _send_final_reply_sync(
        self,
        chat_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send the final agent reply in a conversational style."""
        rendered_text = text.strip() or "[empty message]"
        if _final_reply_needs_card(rendered_text):
            return self._send_interactive_with_optional_reply_sync(
                chat_id,
                _build_final_reply_card(rendered_text),
                metadata,
            )
        return self._send_text_with_optional_reply_sync(chat_id, rendered_text, metadata)

    def _send_text_sync(self, chat_id: str, text: str) -> str:
        """Send one text message to Feishu."""
        if not self._client or CreateMessageRequest is None or CreateMessageRequestBody is None:
            raise RuntimeError("Feishu client is unavailable.")
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(self._resolve_receive_id_type(chat_id))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        return self._extract_message_id(response, "text")

    def _send_text_with_optional_reply_sync(
        self,
        chat_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send text, using Feishu reply API when configured and possible."""
        content = json.dumps({"text": text}, ensure_ascii=False)
        reply_message_id = self._resolve_reply_message_id(metadata)
        if reply_message_id:
            try:
                return self._reply_message_sync(reply_message_id, "text", content)
            except Exception as exc:
                logger.warning("Feishu reply text failed; falling back to create: {}", exc)
        return self._send_text_sync(chat_id, text)

    def _send_interactive_sync(self, chat_id: str, card: dict[str, Any]) -> str:
        """Send one interactive card message to Feishu."""
        if not self._client or CreateMessageRequest is None or CreateMessageRequestBody is None:
            raise RuntimeError("Feishu client is unavailable.")
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(self._resolve_receive_id_type(chat_id))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        return self._extract_message_id(response, "interactive")

    def _send_interactive_with_optional_reply_sync(
        self,
        chat_id: str,
        card: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send an interactive final reply card with optional Feishu reply context."""
        content = json.dumps(card, ensure_ascii=False)
        reply_message_id = self._resolve_reply_message_id(metadata)
        if reply_message_id:
            try:
                return self._reply_message_sync(reply_message_id, "interactive", content)
            except Exception as exc:
                logger.warning("Feishu reply card failed; falling back to create: {}", exc)
        return self._send_interactive_sync(chat_id, card)

    def _reply_message_sync(self, parent_message_id: str, msg_type: str, content: str) -> str:
        """Reply to one Feishu message and return the created message id."""
        if not self._client or ReplyMessageRequest is None or ReplyMessageRequestBody is None:
            raise RuntimeError("Feishu reply API is unavailable.")
        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.reply(request)
        return self._extract_message_id(response, f"{msg_type} reply")

    def _resolve_reply_message_id(self, metadata: dict[str, Any] | None = None) -> str:
        """Return the Feishu message id that should receive a contextual reply."""
        info = metadata or {}
        thread_id = str(info.get("thread_id", "") or "").strip()
        if thread_id:
            return (
                str(info.get("root_id", "") or "").strip()
                or str(info.get("message_id", "") or "").strip()
            )
        if not self.reply_to_message:
            return ""
        return str(info.get("message_id", "") or "").strip()

    def _patch_interactive_sync(self, message_id: str, card: dict[str, Any]) -> None:
        """Update one existing interactive message when the SDK supports it."""
        if not self._client:
            raise RuntimeError("Feishu client is unavailable.")
        content = json.dumps(card, ensure_ascii=False)
        if PatchMessageRequest is not None and PatchMessageRequestBody is not None:
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.patch(request)
        elif UpdateMessageRequest is not None and UpdateMessageRequestBody is not None:
            request = (
                UpdateMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    UpdateMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.update(request)
        else:
            raise RuntimeError("Feishu message patch API is unavailable.")
        self._ensure_success(response, "interactive patch")

    def _send_card_message_sync(self, chat_id: str, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Send or update one rendered card depending on display style and session scope."""
        info = metadata or {}
        card = _build_status_card(text, info)
        display_style = str(info.get("display_style", "")).strip().lower()
        state_key = _progress_state_key(chat_id, info) if display_style == "progress" else None

        if state_key is not None:
            existing_message_id = self._progress_cards.get(state_key, "")
            if existing_message_id:
                self._patch_interactive_sync(existing_message_id, card)
                return existing_message_id

        message_id = self._send_interactive_sync(chat_id, card)
        if state_key is not None and message_id:
            self._progress_cards[state_key] = message_id

        if display_style == "final":
            final_state_key = _progress_state_key(chat_id, info)
            if final_state_key is not None:
                self._progress_cards.pop(final_state_key, None)
        return message_id

    def _send_image_sync(self, chat_id: str, image_path: str) -> str:
        """Upload one image and send it to Feishu."""
        if not self._client or CreateImageRequest is None or CreateImageRequestBody is None:
            raise RuntimeError("Feishu image API is unavailable.")
        image_key = self._upload_image_sync(image_path)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(self._resolve_receive_id_type(chat_id))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        return self._extract_message_id(response, "image")

    def _send_file_sync(self, chat_id: str, file_path: str) -> str:
        """Upload one file and send it to Feishu."""
        if not self._client or CreateFileRequest is None or CreateFileRequestBody is None:
            raise RuntimeError("Feishu file API is unavailable.")
        file_key = self._upload_file_sync(file_path)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(self._resolve_receive_id_type(chat_id))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        return self._extract_message_id(response, "file")

    def _send_video_sync(self, chat_id: str, video_path: str) -> str:
        """Upload one video and send it to Feishu as an inline video message."""
        if not self._client or CreateFileRequest is None or CreateFileRequestBody is None:
            raise RuntimeError("Feishu video API is unavailable.")
        file_key = self._upload_file_sync(video_path)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(self._resolve_receive_id_type(chat_id))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("video")
                .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        return self._extract_message_id(response, "video")

    def _upload_image_sync(self, image_path: str) -> str:
        """Upload one image to Feishu and return image key."""
        if not self._client or CreateImageRequest is None or CreateImageRequestBody is None:
            raise RuntimeError("Feishu image upload API is unavailable.")
        target = Path(image_path).expanduser().resolve()
        with target.open("rb") as image_file:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(image_file)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.image.create(request)
        self._ensure_success(response, "image upload")
        image_key = getattr(getattr(response, "data", None), "image_key", "")
        if not image_key:
            raise RuntimeError("Feishu image upload returned empty image_key.")
        return str(image_key)

    def _upload_file_sync(self, file_path: str) -> str:
        """Upload one file to Feishu and return file key."""
        if not self._client or CreateFileRequest is None or CreateFileRequestBody is None:
            raise RuntimeError("Feishu file upload API is unavailable.")
        target = Path(file_path).expanduser().resolve()
        path_info = _describe_local_file(str(target))
        logger.debug(
            "Feishu file upload starting: path={} exists={} size_bytes={} mime_type={} file_name={}",
            str(target),
            path_info["exists"],
            path_info["size_bytes"],
            path_info["mime_type"],
            target.name,
        )
        with target.open("rb") as file_obj:
            file_type = _resolve_feishu_file_type(target)
            request = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(target.name)
                    .file(file_obj)
                    .build()
                )
                .build()
            )
            try:
                response = self._client.im.v1.file.create(request)
            except Exception as exc:
                raw_response = self._extract_raw_response_from_exception(exc)
                response_status = getattr(raw_response, "status_code", None)
                response_headers = getattr(raw_response, "headers", {}) or {}
                response_body = self._summarize_raw_payload(getattr(raw_response, "content", b""))
                logger.opt(exception=exc).error(
                    "Feishu file upload failed before SDK parse completed: path={} exists={} size_bytes={} mime_type={} file_name={} response_status={} response_content_type={} response_body={}",
                    str(target),
                    path_info["exists"],
                    path_info["size_bytes"],
                    path_info["mime_type"],
                    target.name,
                    response_status,
                    response_headers.get("Content-Type") or response_headers.get("content-type") or "",
                    response_body,
                )
                raise
        self._ensure_success(response, "file upload")
        file_key = getattr(getattr(response, "data", None), "file_key", "")
        if not file_key:
            raise RuntimeError("Feishu file upload returned empty file_key.")
        logger.debug(
            "Feishu file upload finished: path={} file_name={} size_bytes={} mime_type={} file_key={}",
            str(target),
            target.name,
            path_info["size_bytes"],
            path_info["mime_type"],
            file_key,
        )
        return str(file_key)

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """Best-effort reaction API call executed in a worker thread."""
        if (
            not self._client
            or not FEISHU_REACTION_AVAILABLE
            or CreateMessageReactionRequest is None
            or CreateMessageReactionRequestBody is None
            or Emoji is None
        ):
            return
        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            self._client.im.v1.message_reaction.create(request)
        except Exception:
            logger.exception("Failed adding Feishu reaction")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """Add one reaction to an inbound message without blocking message handling."""
        if not message_id:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """Bridge SDK callback thread into the main event loop."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: Any) -> None:
        """Normalize one Feishu inbound event and pass it to the runtime."""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            sender_type = getattr(sender, "sender_type", "")
            if sender_type == "bot":
                return

            sender_id = str(getattr(getattr(sender, "sender_id", None), "open_id", "") or "unknown")
            if not self._is_allowed(sender_id):
                return

            message_id = str(getattr(message, "message_id", "") or "")
            if message_id and self._mark_message_seen(message_id):
                logger.info("Feishu inbound duplicate ignored: message_id={}", message_id)
                return
            chat_id = str(getattr(message, "chat_id", "") or "")
            chat_type = str(getattr(message, "chat_type", "") or "")
            msg_type = str(getattr(message, "message_type", "") or "")
            raw_content = str(getattr(message, "content", "") or "")
            if chat_type == "group" and not self._is_group_message_for_bot(message):
                logger.debug("Feishu inbound group message ignored because bot was not mentioned: message_id={}", message_id)
                return
            if message_id:
                await self._add_reaction(message_id, "THUMBSUP")
            logger.debug(
                "Feishu inbound message received: message_id={} chat_id={} chat_type={} msg_type={} content={}",
                message_id,
                chat_id,
                chat_type,
                msg_type,
                raw_content,
            )
            text, attachments = await self._extract_inbound_content(
                msg_type=msg_type,
                raw_content=raw_content,
                message_id=message_id,
            )
            text = self._normalize_mention_text(text, getattr(message, "mentions", None))
            parent_id = str(getattr(message, "parent_id", "") or "")
            root_id = str(getattr(message, "root_id", "") or "")
            thread_id = str(getattr(message, "thread_id", "") or "")
            if parent_id:
                reply_context = await self._get_reply_context(parent_id)
                if reply_context:
                    text = f"{reply_context}\n{text}".strip()
            logger.debug(
                "Feishu inbound normalized: message_id={} msg_type={} text_len={} attachment_count={}",
                message_id,
                msg_type,
                len(text or ""),
                len(attachments),
            )
            if not text and not attachments:
                logger.debug(
                    "Feishu inbound ignored because no supported content was extracted: message_id={} msg_type={}",
                    message_id,
                    msg_type,
                )
                return

            target_chat_id = chat_id if chat_type == "group" else sender_id
            await self.inbound_handler(
                InboundMessage(
                    channel=self.name,
                    sender_id=sender_id,
                    chat_id=target_chat_id,
                    text=text or "Please analyze the attached file.",
                    attachments=attachments,
                    metadata={
                        "message_id": message_id,
                        "chat_type": chat_type,
                        "msg_type": msg_type,
                        "parent_id": parent_id,
                        "root_id": root_id,
                        "thread_id": thread_id,
                    },
                )
            )
        except Exception:
            logger.exception("Failed handling Feishu inbound message")

    def _is_group_message_for_bot(self, message: Any) -> bool:
        """Return whether a group message should trigger the bot."""
        if self.group_policy == "open":
            return True
        return self._is_bot_mentioned(message)

    def _is_bot_mentioned(self, message: Any) -> bool:
        """Return whether the current Feishu message mentions this bot."""
        raw_content = str(getattr(message, "content", "") or "")
        if "@_all" in raw_content:
            return True
        return any(self._is_bot_mention(mention) for mention in getattr(message, "mentions", None) or [])

    def _is_bot_mention(self, mention: Any) -> bool:
        """Return whether one Feishu mention object points at this bot."""
        mention_id = getattr(mention, "id", None)
        if mention_id is None:
            return False
        mention_open_id = str(getattr(mention_id, "open_id", "") or "").strip()
        if self._bot_open_id:
            return mention_open_id == self._bot_open_id
        mention_user_id = str(getattr(mention_id, "user_id", "") or "").strip()
        return bool(mention_open_id.startswith("ou_") and not mention_user_id)

    def _normalize_mention_text(self, text: str, mentions: list[Any] | None) -> str:
        """Replace Feishu mention placeholders and remove this bot's own mention."""
        normalized = str(text or "")
        if not normalized or not mentions:
            return normalized.strip()
        for mention in mentions:
            key = str(getattr(mention, "key", "") or "").strip()
            if not key:
                continue
            if self._is_bot_mention(mention):
                normalized = normalized.replace(key, "")
                continue
            display_name = str(getattr(mention, "name", "") or "").strip() or "user"
            normalized = normalized.replace(key, f"@{display_name}")
        return " ".join(normalized.split()).strip()

    async def _get_reply_context(self, parent_message_id: str) -> str:
        """Return a short textual description of the message being replied to."""
        if not parent_message_id or not self._client:
            return ""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._get_reply_context_sync, parent_message_id)
        return result or ""

    def _get_reply_context_sync(self, parent_message_id: str) -> str:
        """Fetch a short reply context string from one Feishu parent message."""
        if not self._client or GetMessageRequest is None:
            return ""
        try:
            request = GetMessageRequest.builder().message_id(parent_message_id).build()
            response = self._client.im.v1.message.get(request)
            self._ensure_success(response, "message get")
            items = getattr(getattr(response, "data", None), "items", None) or []
            if not items:
                return ""
            parent_message = items[0]
            msg_type = str(getattr(parent_message, "msg_type", "") or "")
            body = getattr(parent_message, "body", None)
            raw_content = str(getattr(body, "content", "") or "")
            if msg_type == "text":
                text = self._extract_text(raw_content)
            elif msg_type == "post":
                text = self._extract_post_text(raw_content)
            else:
                text = ""
            text = " ".join(str(text or "").split()).strip()
            if not text:
                return ""
            if len(text) > 200:
                text = f"{text[:197].rstrip()}..."
            return f"[Reply to: {text}]"
        except Exception as exc:
            logger.debug("Failed fetching Feishu reply context: parent_message_id={} error={}", parent_message_id, exc)
            return ""

    def _mark_message_seen(self, message_id: str) -> bool:
        """Remember one inbound Feishu message id and report whether it was already seen."""
        normalized = str(message_id or "").strip()
        if not normalized:
            return False

        now = time.monotonic()
        self._prune_seen_message_ids(now)
        if normalized in self._seen_message_ids:
            self._seen_message_ids.move_to_end(normalized)
            self._seen_message_ids[normalized] = now
            return True

        self._seen_message_ids[normalized] = now
        while len(self._seen_message_ids) > _MESSAGE_DEDUP_MAX_ENTRIES:
            self._seen_message_ids.popitem(last=False)
        return False

    def _prune_seen_message_ids(self, now: float | None = None) -> None:
        """Drop expired cached message ids to keep dedup state bounded."""
        cutoff = (time.monotonic() if now is None else now) - _MESSAGE_DEDUP_TTL_SECONDS
        while self._seen_message_ids:
            oldest_message_id = next(iter(self._seen_message_ids))
            if self._seen_message_ids[oldest_message_id] >= cutoff:
                break
            self._seen_message_ids.popitem(last=False)

    async def _extract_inbound_content(
        self,
        *,
        msg_type: str,
        raw_content: str,
        message_id: str,
    ) -> tuple[str, list[MessageAttachment]]:
        """Convert one Feishu payload into normalized text plus attachments."""
        if msg_type == "text":
            return self._extract_text(raw_content), []
        if msg_type == "post":
            payload = self._parse_json_dict(raw_content)
            text_content = self._extract_post_text(raw_content)
            image_keys = _extract_post_image_keys(payload) if payload else []
            attachments: list[MessageAttachment] = []
            image_errors: list[str] = []
            for image_key in image_keys:
                try:
                    local_path = await self._download_image(image_key, message_id)
                except Exception as exc:
                    logger.exception(
                        "Failed downloading Feishu post image: message_id={} image_key={}",
                        message_id,
                        image_key,
                    )
                    image_errors.append(f"{image_key}: {exc}")
                    continue
                attachments.append(
                    MessageAttachment(
                        path=str(local_path),
                        name=Path(local_path).name,
                        mime_type=_guess_mime_type(str(local_path)),
                        description="feishu post image attachment",
                    )
                )
            parts: list[str] = []
            if text_content:
                parts.append(text_content)
            if image_errors:
                parts.append("Failed downloading images:\n" + "\n".join(image_errors))
            return "\n\n".join(parts).strip(), attachments
        if msg_type == "image":
            payload = self._parse_json_dict(raw_content)
            image_key = str(payload.get("image_key", "")).strip()
            if not image_key:
                return "Received an image message without image_key.", []
            local_path = await self._download_image(image_key, message_id)
            return "Received image attachment.", [
                MessageAttachment(
                    path=str(local_path),
                    name=Path(local_path).name,
                    mime_type="image/png",
                    description="feishu image attachment",
                )
            ]
        if msg_type == "file":
            payload = self._parse_json_dict(raw_content)
            file_key = str(payload.get("file_key", "")).strip()
            file_name = str(payload.get("file_name", "")).strip()
            if not file_key:
                return "Received a file message without file_key.", []
            local_path = await self._download_file(file_key, file_name, message_id)
            return "Received file attachment.", [
                MessageAttachment(
                    path=str(local_path),
                    name=file_name or Path(local_path).name,
                    mime_type=_guess_mime_type(file_name or str(local_path)),
                    description="feishu file attachment",
                )
            ]
        if msg_type in {"media", "video"}:
            payload = self._parse_json_dict(raw_content)
            file_key = str(payload.get("file_key", "") or payload.get("video_key", "")).strip()
            file_name = str(payload.get("file_name", "") or payload.get("name", "")).strip()
            if not file_key:
                return "Received a video message without file_key.", []
            local_path = await self._download_file(file_key, file_name or f"{file_key}.mp4", message_id)
            return "Received video attachment.", [
                MessageAttachment(
                    path=str(local_path),
                    name=file_name or Path(local_path).name,
                    mime_type=_guess_mime_type(file_name or str(local_path)),
                    description="feishu video attachment",
                )
            ]
        if msg_type in {"audio", "voice"}:
            payload = self._parse_json_dict(raw_content)
            file_key = str(payload.get("file_key", "") or payload.get("audio_key", "")).strip()
            file_name = str(payload.get("file_name", "") or payload.get("name", "")).strip()
            if not file_key:
                return "Received an audio message without file_key.", []
            local_path = await self._download_file(file_key, file_name or f"{file_key}.opus", message_id)
            return "Received audio attachment.", [
                MessageAttachment(
                    path=str(local_path),
                    name=file_name or Path(local_path).name,
                    mime_type=_guess_mime_type(file_name or str(local_path)),
                    description="feishu audio attachment",
                )
            ]
        logger.debug("Feishu inbound message type is not yet supported: msg_type={} content={}", msg_type, raw_content)
        return "", []

    async def _download_image(self, image_key: str, message_id: str) -> Path:
        """Download one Feishu image resource in a worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._download_image_sync, image_key, message_id)

    async def _download_file(self, file_key: str, file_name: str, message_id: str) -> Path:
        """Download one Feishu file resource in a worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._download_file_sync, file_key, file_name, message_id)

    def _download_image_sync(self, image_key: str, message_id: str) -> Path:
        """Download one Feishu image resource."""
        return self._download_resource_sync(
            resource_key=image_key,
            message_id=message_id,
            resource_type="image",
            suggested_name=f"{image_key}.png",
            default_suffix=".png",
            allow_legacy_file_api=False,
        )

    def _download_file_sync(self, file_key: str, file_name: str, message_id: str) -> Path:
        """Download one Feishu file resource."""
        return self._download_resource_sync(
            resource_key=file_key,
            message_id=message_id,
            resource_type="file",
            suggested_name=file_name or f"{file_key}.bin",
            default_suffix=".bin",
            allow_legacy_file_api=True,
        )

    def _download_resource_sync(
        self,
        *,
        resource_key: str,
        message_id: str,
        resource_type: str,
        suggested_name: str,
        default_suffix: str,
        allow_legacy_file_api: bool,
    ) -> Path:
        """Download one inbound Feishu message resource into the channel inbox."""
        if not self._client:
            raise RuntimeError("Feishu client is unavailable.")

        response: Any
        message_resource_api = getattr(getattr(getattr(self._client, "im", None), "v1", None), "message_resource", None)
        if GetMessageResourceRequest is not None and message_resource_api is not None:
            request = (
                GetMessageResourceRequest.builder()
                .type(resource_type)
                .message_id(message_id)
                .file_key(resource_key)
                .build()
            )
            response = message_resource_api.get(request)
            self._ensure_success(response, f"{resource_type} download")
        elif allow_legacy_file_api and GetFileRequest is not None:
            request = (
                GetFileRequest.builder()
                .file_key(resource_key)
                .build()
            )
            response = self._client.im.v1.file.get(request)
            self._ensure_success(response, "file download")
        else:
            raise RuntimeError("Feishu resource download API is unavailable.")

        file_bytes = self._read_downloaded_bytes(response)
        target_name = Path(str(getattr(response, "file_name", "") or suggested_name)).name
        if not Path(target_name).suffix:
            target_name = f"{target_name}{default_suffix}"
        destination = channel_inbox_dir("feishu", message_id) / target_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(file_bytes)
        return destination

    @staticmethod
    def _read_downloaded_bytes(response: Any) -> bytes:
        """Read binary bytes from one Feishu download response."""
        file_obj = getattr(response, "file", None)
        if file_obj is not None:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            if hasattr(file_obj, "read"):
                data = file_obj.read()
            else:
                data = file_obj
        else:
            raw = getattr(response, "raw", b"")
            if hasattr(raw, "content"):
                data = raw.content
            else:
                data = raw

        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, str):
            return data.encode("utf-8")
        raise RuntimeError(f"Unexpected Feishu download payload type: {type(data)!r}")

    @staticmethod
    def _extract_text(raw_content: str) -> str:
        """Extract plain text from Feishu text message content."""
        try:
            parsed = json.loads(raw_content)
        except Exception:
            return raw_content
        if isinstance(parsed, dict):
            return str(parsed.get("text", "")).strip()
        return raw_content

    @staticmethod
    def _extract_post_text(raw_content: str) -> str:
        """Extract readable text from Feishu post message content."""
        try:
            parsed = json.loads(raw_content)
        except Exception:
            return raw_content
        if not isinstance(parsed, dict):
            return raw_content
        for payload in _iter_post_lang_payloads(parsed):
            parts: list[str] = []
            title = str(payload.get("title", "")).strip()
            if title:
                parts.append(title)
            blocks = payload.get("content", [])
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, list):
                    continue
                for item in block:
                    if isinstance(item, dict) and item.get("tag") in {"text", "a"}:
                        text = str(item.get("text", "")).strip()
                        if text:
                            parts.append(text)
            if parts:
                return " ".join(parts)
        return ""

    @staticmethod
    def _parse_json_dict(raw_content: str) -> dict[str, Any]:
        """Parse one JSON message body into dict."""
        try:
            parsed = json.loads(raw_content)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _resolve_receive_id_type(chat_id: str) -> str:
        """Choose Feishu receive id type from chat id format."""
        return "chat_id" if chat_id.startswith("oc_") else "open_id"

    @staticmethod
    def _extract_message_id(response: Any, action_name: str) -> str:
        """Extract one message id from Feishu SDK response."""
        FeishuChannel._ensure_success(response, action_name)
        message_id = getattr(getattr(response, "data", None), "message_id", "")
        if not message_id:
            raise RuntimeError(f"Feishu {action_name} returned empty message_id.")
        return str(message_id)

    @staticmethod
    def _ensure_success(response: Any, action_name: str) -> None:
        """Raise an error if Feishu SDK response is not successful."""
        success_fn = getattr(response, "success", None)
        if callable(success_fn) and not success_fn():
            code = getattr(response, "code", "")
            message = getattr(response, "msg", "")
            raise RuntimeError(f"Feishu {action_name} failed: code={code}, msg={message}")

    @staticmethod
    def _extract_raw_response_from_exception(exc: BaseException) -> Any | None:
        """Best-effort extract one SDK raw response object from an exception traceback."""
        tb = exc.__traceback__
        while tb is not None:
            candidate = tb.tb_frame.f_locals.get("resp")
            if candidate is not None and hasattr(candidate, "status_code") and hasattr(candidate, "content"):
                return candidate
            tb = tb.tb_next
        return None

    @staticmethod
    def _summarize_raw_payload(payload: Any, limit: int = 300) -> str:
        """Convert one raw response payload into a short, log-friendly string."""
        if payload is None:
            return ""
        if isinstance(payload, bytes | bytearray):
            text = bytes(payload).decode("utf-8", errors="replace")
        else:
            text = str(payload)
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[:limit]}..."

    def _is_allowed(self, sender_id: str) -> bool:
        """Return whether one sender passes the allow list."""
        if not self.allow_from:
            return True
        return sender_id in self.allow_from


def _is_image_file(file_path: str) -> bool:
    """Return whether a file path looks like an image."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return bool(mime_type and mime_type.startswith("image/"))


def _is_video_file(file_path: str) -> bool:
    """Return whether a file path looks like a video."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return bool(mime_type and mime_type.startswith("video/"))


def _guess_mime_type(file_name: str) -> str:
    """Guess mime type for one inbound file name."""
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"


def _resolve_feishu_file_type(path: Path) -> str:
    """Return Feishu upload file_type from one local path."""
    return _FEISHU_FILE_TYPE_BY_SUFFIX.get(path.suffix.lower(), "stream")


def _describe_local_file(file_path: str) -> dict[str, Any]:
    """Collect lightweight diagnostics for one local file path."""
    target = Path(file_path).expanduser()
    exists = target.exists()
    size_bytes = target.stat().st_size if exists and target.is_file() else None
    return {
        "exists": exists,
        "size_bytes": size_bytes,
        "mime_type": _guess_mime_type(target.name or file_path),
    }
