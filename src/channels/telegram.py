"""Telegram chat adapter for Creative Claw."""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import requests

from src.logger import logger
from src.runtime import InboundMessage, MessageAttachment
from src.runtime.workspace import channel_inbox_dir

from .base import BaseChannel
from .events import OutboundMessage


class TelegramChannel(BaseChannel):
    """Minimal Telegram adapter using long polling."""

    name = "telegram"

    def __init__(
        self,
        *,
        token: str,
        inbound_handler: Callable[[InboundMessage], Awaitable[None]],
        allow_from: list[str] | None = None,
        api_base: str = "https://api.telegram.org",
        poll_timeout_seconds: int = 20,
    ) -> None:
        super().__init__()
        self.token = token.strip()
        self.inbound_handler = inbound_handler
        self.api_base = api_base.rstrip("/")
        self.poll_timeout_seconds = max(int(poll_timeout_seconds), 1)
        self.allow_from = {
            str(item).strip()
            for item in (allow_from or [])
            if str(item).strip()
        }
        self._poll_task: asyncio.Task[None] | None = None
        self._offset: int = 0

    async def start(self) -> None:
        """Start Telegram long polling."""
        if not self.token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN for telegram channel.")
        if self._poll_task and not self._poll_task.done():
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="creative-claw-telegram-poll")

    async def stop(self) -> None:
        """Stop Telegram long polling."""
        self._running = False
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        self._poll_task = None

    async def send(self, message: OutboundMessage) -> None:
        """Send one outbound Telegram message and its artifacts."""
        text = message.text.strip() if message.text else "[empty message]"
        await self._api_call("sendMessage", {"chat_id": message.chat_id, "text": text})

        for artifact_path in message.artifact_paths:
            cleaned_path = artifact_path.strip()
            if not cleaned_path:
                continue
            if _is_image_file(cleaned_path):
                await self._send_file(
                    method="sendPhoto",
                    file_field="photo",
                    chat_id=message.chat_id,
                    file_path=cleaned_path,
                )
            else:
                await self._send_file(
                    method="sendDocument",
                    file_field="document",
                    chat_id=message.chat_id,
                    file_path=cleaned_path,
                )

    async def _poll_loop(self) -> None:
        """Run Telegram polling until the channel stops."""
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.opt(exception=exc).error(
                    "Telegram polling iteration failed: error_type={} error={!r}",
                    type(exc).__name__,
                    exc,
                )
                await asyncio.sleep(2)

    async def _poll_once(self) -> None:
        """Fetch one update batch from Telegram."""
        result = await self._api_call(
            "getUpdates",
            {
                "offset": self._offset,
                "timeout": self.poll_timeout_seconds,
                "allowed_updates": ["message"],
            },
        )
        updates = result if isinstance(result, list) else []
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset, update_id + 1)
            await self._process_update(update)

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Normalize one Telegram update and dispatch it into the runtime."""
        message = update.get("message")
        if not isinstance(message, dict):
            return

        sender = message.get("from")
        if not isinstance(sender, dict):
            return
        sender_id_raw = sender.get("id")
        if sender_id_raw is None:
            return
        sender_id = str(sender_id_raw)
        username = str(sender.get("username", "")).strip()
        sender_identity = f"{sender_id}|@{username}" if username else sender_id
        if not self._is_allowed(sender_id, sender_identity):
            logger.warning("Telegram sender {} is not in allow list.", sender_identity)
            return

        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        chat_id_raw = chat.get("id")
        if chat_id_raw is None:
            return
        chat_id = str(chat_id_raw)

        text = str(message.get("text", "")).strip()
        if not text:
            text = str(message.get("caption", "")).strip()
        attachments = self._extract_attachments(message)
        if not text and not attachments:
            return

        await self.inbound_handler(
            InboundMessage(
                channel=self.name,
                sender_id=sender_identity,
                chat_id=chat_id,
                text=text or "Please analyze the attached file.",
                attachments=attachments,
                metadata={
                    "message_id": str(message.get("message_id", "")),
                    "chat_type": str(chat.get("type", "")),
                    "update_id": str(update.get("update_id", "")),
                },
            )
        )

    async def _api_call(self, method: str, payload: dict[str, Any]) -> Any:
        """Run one Telegram JSON API call in a worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._api_call_sync, method, payload)

    def _api_call_sync(self, method: str, payload: dict[str, Any]) -> Any:
        """Perform one blocking Telegram JSON API call."""
        response = requests.post(
            self._endpoint(method),
            json=payload,
            timeout=self.poll_timeout_seconds + 10,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict) or not parsed.get("ok", False):
            description = parsed.get("description", "unknown error") if isinstance(parsed, dict) else "unknown error"
            raise RuntimeError(f"Telegram API failed ({method}): {description}")
        return parsed.get("result")

    async def _send_file(self, *, method: str, file_field: str, chat_id: str, file_path: str) -> None:
        """Upload one local file to Telegram."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._send_file_sync,
            method,
            file_field,
            chat_id,
            file_path,
        )

    def _send_file_sync(self, method: str, file_field: str, chat_id: str, file_path: str) -> None:
        """Perform one blocking Telegram multipart upload."""
        path = Path(file_path)
        with path.open("rb") as file_obj:
            response = requests.post(
                self._endpoint(method),
                data={"chat_id": chat_id},
                files={file_field: (path.name, file_obj)},
                timeout=self.poll_timeout_seconds + 20,
            )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict) or not parsed.get("ok", False):
            description = parsed.get("description", "unknown error") if isinstance(parsed, dict) else "unknown error"
            raise RuntimeError(f"Telegram API failed ({method}): {description}")

    def _extract_attachments(self, message: dict[str, Any]) -> list[MessageAttachment]:
        """Convert supported Telegram file payloads into local attachments."""
        attachments: list[MessageAttachment] = []
        photo_list = message.get("photo")
        if isinstance(photo_list, list) and photo_list:
            best_photo = photo_list[-1]
            if isinstance(best_photo, dict):
                file_id = str(best_photo.get("file_id", "")).strip()
                if file_id:
                    saved_path = self._download_telegram_file(file_id)
                    attachments.append(
                        MessageAttachment(
                            path=saved_path,
                            name=Path(saved_path).name,
                            mime_type="image/jpeg",
                            description="telegram photo attachment",
                        )
                    )
        document = message.get("document")
        if isinstance(document, dict):
            file_id = str(document.get("file_id", "")).strip()
            file_name = str(document.get("file_name", "")).strip()
            mime_type = str(document.get("mime_type", "")).strip()
            if file_id:
                saved_path = self._download_telegram_file(file_id, preferred_name=file_name)
                attachments.append(
                    MessageAttachment(
                        path=saved_path,
                        name=file_name or Path(saved_path).name,
                        mime_type=mime_type,
                        description="telegram document attachment",
                    )
                )
        return attachments

    def _download_telegram_file(self, file_id: str, preferred_name: str = "") -> str:
        """Download one Telegram file into the local uploads directory."""
        file_meta = self._api_call_sync("getFile", {"file_id": file_id})
        if not isinstance(file_meta, dict):
            raise RuntimeError("Telegram getFile returned an invalid payload.")
        file_path = str(file_meta.get("file_path", "")).strip()
        if not file_path:
            raise RuntimeError("Telegram getFile did not return file_path.")

        target_name = preferred_name.strip() or Path(file_path).name
        safe_name = Path(target_name).name
        destination = channel_inbox_dir("telegram", safe_name) / safe_name
        destination.parent.mkdir(parents=True, exist_ok=True)

        download_url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        response = requests.get(download_url, timeout=self.poll_timeout_seconds + 20)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return str(destination)

    def _endpoint(self, method: str) -> str:
        """Return one Telegram Bot API endpoint."""
        return f"{self.api_base}/bot{self.token}/{method}"

    def _is_allowed(self, sender_id: str, sender_identity: str) -> bool:
        """Return whether a sender passes the current allow list."""
        if not self.allow_from:
            return True
        return sender_id in self.allow_from or sender_identity in self.allow_from


def _is_image_file(file_path: str) -> bool:
    """Return whether a file should be sent as photo."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return bool(mime_type and mime_type.startswith("image/"))
