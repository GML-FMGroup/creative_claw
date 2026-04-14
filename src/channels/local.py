"""CLI terminal channel implementation."""

from __future__ import annotations

from typing import Callable

from .base import BaseChannel
from .events import OutboundMessage


class LocalChannel(BaseChannel):
    """Simple stdout-backed channel for CLI development and testing."""

    name = "cli"

    def __init__(self, writer: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._writer = writer or print

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, message: OutboundMessage) -> None:
        payload = message.text.strip() if message.text else ""
        if payload or not message.artifact_paths:
            self._writer(payload or "[empty message]")
        for artifact_path in message.artifact_paths:
            self._writer(f"[artifact] {artifact_path}")
