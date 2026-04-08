"""Runtime data models shared by workflow and channel layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MessageAttachment:
    """Represents one inbound local file prepared for the workflow."""

    path: str
    name: str
    mime_type: str = ""
    description: str = ""


@dataclass(slots=True)
class InboundMessage:
    """Normalized message received from a chat channel."""

    channel: str
    sender_id: str
    chat_id: str
    text: str
    attachments: list[MessageAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        """Return the logical conversation key for channel-based sessions."""
        return f"{self.channel}:{self.chat_id}"


@dataclass(slots=True)
class WorkflowEvent:
    """Structured workflow event emitted by the Creative Claw runtime."""

    event_type: str
    text: str
    artifact_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
