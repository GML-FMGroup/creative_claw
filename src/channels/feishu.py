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
        GetFileRequest,
        GetImageRequest,
        P2ImMessageReceiveV1,
    )

    FEISHU_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    lark = None
    CreateFileRequest = None
    CreateFileRequestBody = None
    CreateImageRequest = None
    CreateImageRequestBody = None
    CreateMessageRequest = None
    CreateMessageRequestBody = None
    GetFileRequest = None
    GetImageRequest = None
    P2ImMessageReceiveV1 = None
    FEISHU_AVAILABLE = False


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
        self._send_text_sync(message.chat_id, message.text.strip() if message.text else "[empty message]")
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
            chat_id = str(getattr(message, "chat_id", "") or "")
            chat_type = str(getattr(message, "chat_type", "") or "")
            msg_type = str(getattr(message, "message_type", "") or "")
            raw_content = str(getattr(message, "content", "") or "")
            text, attachments = await self._extract_inbound_content(
                msg_type=msg_type,
                raw_content=raw_content,
                message_id=message_id,
            )
            if not text and not attachments:
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
            return self._extract_post_text(raw_content), []
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
        if not self._client or GetImageRequest is None:
            raise RuntimeError("Feishu image download API is unavailable.")
        request = (
            GetImageRequest.builder()
            .image_key(image_key)
            .build()
        )
        response = self._client.im.v1.image.get(request)
        self._ensure_success(response, "image download")
        file_bytes = getattr(response, "file", None)
        if file_bytes is None:
            file_bytes = getattr(response, "raw", b"")
        destination = Path("outputs") / "uploads" / f"feishu_{message_id}_{image_key}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(file_bytes))
        return destination

    def _download_file_sync(self, file_key: str, file_name: str, message_id: str) -> Path:
        """Download one Feishu file resource."""
        if not self._client or GetFileRequest is None:
            raise RuntimeError("Feishu file download API is unavailable.")
        request = (
            GetFileRequest.builder()
            .file_key(file_key)
            .build()
        )
        response = self._client.im.v1.file.get(request)
        self._ensure_success(response, "file download")
        file_bytes = getattr(response, "file", None)
        if file_bytes is None:
            file_bytes = getattr(response, "raw", b"")
        target_name = Path(file_name).name if file_name else f"{file_key}.bin"
        destination = Path("outputs") / "uploads" / f"feishu_{message_id}_{target_name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(file_bytes))
        return destination

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
        for lang_key in ("zh_cn", "en_us", "ja_jp"):
            lang = parsed.get(lang_key)
            if not isinstance(lang, dict):
                continue
            blocks = lang.get("content", [])
            if not isinstance(blocks, list):
                continue
            parts: list[str] = []
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
