"""Feishu chat adapter for Creative Claw."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
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
        GetMessageResourceRequest,
        PatchMessageRequest,
        PatchMessageRequestBody,
        P2ImMessageReceiveV1,
        UpdateMessageRequest,
        UpdateMessageRequestBody,
    )

    FEISHU_AVAILABLE = True
    FEISHU_REACTION_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    lark = None
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
    GetMessageResourceRequest = None
    PatchMessageRequest = None
    PatchMessageRequestBody = None
    P2ImMessageReceiveV1 = None
    UpdateMessageRequest = None
    UpdateMessageRequestBody = None
    FEISHU_AVAILABLE = False
    FEISHU_REACTION_AVAILABLE = False


_STAGE_TITLES = {
    "started": "Starting",
    "attachment_received": "Attachment Received",
    "in_progress": "In Progress",
}


def _build_status_card(text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one lightweight Feishu card for progress or final text."""
    info = metadata or {}
    display_style = str(info.get("display_style", "")).strip().lower()
    stage = str(info.get("stage", "")).strip().lower()
    if display_style == "final":
        title = "Result"
        template = "green"
    else:
        title = str(info.get("stage_title", "")).strip() or _STAGE_TITLES.get(stage, "Current Progress")
        template = {
            "started": "blue",
            "attachment_received": "wathet",
            "in_progress": "indigo",
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
    """Return whether one outbound text should be rendered as a Feishu card."""
    info = metadata or {}
    display_style = str(info.get("display_style", "")).strip().lower()
    if display_style in {"progress", "final"}:
        return True
    return len(str(text or "").strip()) > 180


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
    ) -> None:
        super().__init__()
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.encrypt_key = encrypt_key.strip()
        self.verification_token = verification_token.strip()
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
        self._progress_cards: dict[tuple[str, str], str] = {}

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
        self._ws_client = lark.ws.Client(  # type: ignore[union-attr]
            self.app_id,
            self.app_secret,
            event_handler=dispatcher,
            log_level=lark.LogLevel.INFO,  # type: ignore[union-attr]
        )

        def _run_ws_forever() -> None:
            while self._running:
                try:
                    self._ws_client.start()
                except Exception:
                    logger.exception("Feishu websocket loop failed; retrying")
                    if self._running:
                        import time

                        time.sleep(3)

        self._ws_thread = threading.Thread(target=_run_ws_forever, daemon=True)
        self._ws_thread.start()

    async def stop(self) -> None:
        """Stop Feishu long connection."""
        self._running = False
        if self._ws_client:
            stop_fn = getattr(self._ws_client, "stop", None)
            close_fn = getattr(self._ws_client, "close", None)
            try:
                if callable(stop_fn):
                    stop_fn()
                elif callable(close_fn):
                    close_fn()
            except Exception:
                logger.exception("Failed stopping Feishu websocket client")

    async def send(self, message: OutboundMessage) -> None:
        """Send one outbound Feishu message and artifacts."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_sync, message)

    def _send_sync(self, message: OutboundMessage) -> None:
        """Blocking Feishu send path."""
        text = message.text.strip() if message.text else "[empty message]"
        if _should_use_interactive_card(text, message.metadata):
            self._send_card_message_sync(message.chat_id, text, message.metadata)
        else:
            self._send_text_sync(message.chat_id, text)
        for artifact_path in message.artifact_paths:
            cleaned_path = artifact_path.strip()
            if not cleaned_path:
                continue
            if _is_image_file(cleaned_path):
                self._send_image_sync(message.chat_id, cleaned_path)
            else:
                self._send_file_sync(message.chat_id, cleaned_path)

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
        session_id = str(info.get("session_id", "")).strip()
        state_key = (chat_id, session_id) if display_style == "progress" and session_id else None

        if state_key is not None:
            existing_message_id = self._progress_cards.get(state_key, "")
            if existing_message_id:
                self._patch_interactive_sync(existing_message_id, card)
                return existing_message_id

        message_id = self._send_interactive_sync(chat_id, card)
        if state_key is not None and message_id:
            self._progress_cards[state_key] = message_id

        if display_style == "final" and session_id:
            self._progress_cards.pop((chat_id, session_id), None)
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
        with target.open("rb") as file_obj:
            request = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(target.name)
                    .file(file_obj)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.file.create(request)
        self._ensure_success(response, "file upload")
        file_key = getattr(getattr(response, "data", None), "file_key", "")
        if not file_key:
            raise RuntimeError("Feishu file upload returned empty file_key.")
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
            if message_id:
                await self._add_reaction(message_id, "THUMBSUP")
            chat_id = str(getattr(message, "chat_id", "") or "")
            chat_type = str(getattr(message, "chat_type", "") or "")
            msg_type = str(getattr(message, "message_type", "") or "")
            raw_content = str(getattr(message, "content", "") or "")
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
                    },
                )
            )
        except Exception:
            logger.exception("Failed handling Feishu inbound message")

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

    def _is_allowed(self, sender_id: str) -> bool:
        """Return whether one sender passes the allow list."""
        if not self.allow_from:
            return True
        return sender_id in self.allow_from


def _is_image_file(file_path: str) -> bool:
    """Return whether a file path looks like an image."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return bool(mime_type and mime_type.startswith("image/"))


def _guess_mime_type(file_name: str) -> str:
    """Guess mime type for one inbound file name."""
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"
