"""Local terminal channel implementation."""

from __future__ import annotations

from typing import Callable

from .base import BaseChannel
from .events import OutboundMessage


class LocalChannel(BaseChannel):
    """Simple stdout-backed channel for local development and testing."""

    name = "local"

    def __init__(self, writer: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._writer = writer or print

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, message: OutboundMessage) -> None:
        payload = message.text.strip() if message.text else "[empty message]"
        self._writer(payload)
        for artifact_path in message.artifact_paths:
            self._writer(f"[artifact] {artifact_path}")
