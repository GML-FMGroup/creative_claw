"""Minimal channel manager for runtime dispatch."""

from __future__ import annotations

from src.runtime import CreativeClawRuntime, InboundMessage, WorkflowEvent

from .base import BaseChannel
from .events import OutboundMessage


class ChannelManager:
    """Coordinate channel lifecycle and route messages into the runtime."""

    def __init__(self, runtime: CreativeClawRuntime) -> None:
        self.runtime = runtime
        self.channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        """Register one channel implementation by name."""
        self.channels[channel.name] = channel

    async def start_all(self) -> None:
        """Start all registered channels."""
        for channel in self.channels.values():
            await channel.start()

    async def stop_all(self) -> None:
        """Stop all registered channels."""
        for channel in self.channels.values():
            await channel.stop()

    async def handle_inbound(self, message: InboundMessage) -> None:
        """Run one inbound message and dispatch each emitted workflow event."""
        channel = self.channels.get(message.channel)
        if channel is None:
            raise ValueError(f"Channel '{message.channel}' is not registered.")

        async for event in self.runtime.run_message(message):
            await channel.send(_render_outbound(message.channel, message.chat_id, event))


def _render_outbound(channel: str, chat_id: str, event: WorkflowEvent) -> OutboundMessage:
    """Convert a workflow event into a channel-sendable payload."""
    prefixes = {
        "status": "[status]",
        "error": "[error]",
        "final": "[final]",
    }
    prefix = prefixes.get(event.event_type, "[event]")
    text = f"{prefix} {event.text}".strip()
    return OutboundMessage(
        channel=channel,
        chat_id=chat_id,
        text=text,
        artifact_paths=list(event.artifact_paths),
        metadata=dict(event.metadata),
    )
