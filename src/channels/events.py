"""Channel-specific outbound event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OutboundMessage:
    """Message rendered by the runtime and ready to be sent by a channel."""

    channel: str
    chat_id: str
    text: str
    artifact_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
