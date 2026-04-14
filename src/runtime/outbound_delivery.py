"""Explicit outbound delivery helpers for model-invoked send actions."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from src.channels.events import OutboundMessage
from src.runtime.tool_context import get_route

_OUTBOUND_MESSAGE_PUBLISHER: Callable[[OutboundMessage], Awaitable[None] | None] | None = None


def configure_outbound_message_publisher(
    publisher: Callable[[OutboundMessage], Awaitable[None] | None] | None,
) -> None:
    """Configure the async publisher used by explicit outbound send tools."""
    global _OUTBOUND_MESSAGE_PUBLISHER
    _OUTBOUND_MESSAGE_PUBLISHER = publisher


async def _dispatch_outbound_message(message: OutboundMessage) -> None:
    """Send one outbound message through the configured publisher."""
    publisher = _OUTBOUND_MESSAGE_PUBLISHER
    if publisher is None:
        return
    maybe_awaitable = publisher(message)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def publish_outbound_message(
    *,
    text: str = "",
    artifact_paths: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Schedule one explicit outbound message for the active route."""
    publisher = _OUTBOUND_MESSAGE_PUBLISHER
    resolved_channel = str(channel or "").strip()
    resolved_chat_id = str(chat_id or "").strip()
    if not resolved_channel or not resolved_chat_id:
        route_channel, route_chat_id = get_route()
        resolved_channel = resolved_channel or str(route_channel or "").strip()
        resolved_chat_id = resolved_chat_id or str(route_chat_id or "").strip()
    if publisher is None or not resolved_channel or not resolved_chat_id:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    loop.create_task(
        _dispatch_outbound_message(
            OutboundMessage(
                channel=resolved_channel,
                chat_id=resolved_chat_id,
                text=str(text or ""),
                artifact_paths=list(artifact_paths or []),
                metadata=dict(metadata or {}),
            )
        )
    )
    return True
