"""Chat channel adapters for Creative Claw."""

from .base import BaseChannel
from .events import OutboundMessage
from .feishu import FeishuChannel
from .local import LocalChannel
from .manager import ChannelManager
from .telegram import TelegramChannel

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "FeishuChannel",
    "LocalChannel",
    "OutboundMessage",
    "TelegramChannel",
]
