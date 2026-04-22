"""Minimal channel manager for runtime dispatch."""

from __future__ import annotations

from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.step_events import configure_step_event_publisher
from src.runtime.workflow_service import CreativeClawRuntime
from src.runtime.tool_context import route_context

from .base import BaseChannel
from .events import OutboundMessage


class ChannelManager:
    """Coordinate channel lifecycle and route messages into the runtime."""

    def __init__(self, runtime: CreativeClawRuntime) -> None:
        self.runtime = runtime
        self.channels: dict[str, BaseChannel] = {}
        configure_step_event_publisher(self._publish_realtime_outbound)

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

        with route_context(message.channel, message.chat_id):
            async for event in self.runtime.run_message(message):
                outbound = _render_outbound(message.channel, message.chat_id, event)
                if outbound is None:
                    continue
                await channel.send(outbound)

    async def _publish_realtime_outbound(self, message: OutboundMessage) -> None:
        """Send one realtime outbound message emitted directly from tool callbacks."""
        channel = self.channels.get(message.channel)
        if channel is None:
            return
        await channel.send(message)


def _render_outbound(channel: str, chat_id: str, event: WorkflowEvent) -> OutboundMessage | None:
    """Convert one workflow event into a user-facing outbound payload."""
    metadata = dict(event.metadata)
    if metadata.get("visible") is False:
        return None

    text = str(event.text or "").strip()
    if event.event_type == "error" and text and not text.startswith("Error: "):
        text = f"Error: {text}"

    return OutboundMessage(
        channel=channel,
        chat_id=chat_id,
        text=text,
        artifact_paths=list(event.artifact_paths),
        metadata=metadata,
    )
