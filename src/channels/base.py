"""Base chat channel abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .events import OutboundMessage


class BaseChannel(ABC):
    """Base interface that every Creative Claw chat adapter must implement."""

    name: str = "base"

    def __init__(self) -> None:
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Start channel resources."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop channel resources."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Send one normalized outbound message through the channel."""

    @property
    def is_running(self) -> bool:
        """Return whether the channel has been started."""
        return self._running
