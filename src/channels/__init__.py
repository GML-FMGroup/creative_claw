"""Chat channel adapters for Creative Claw."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "FeishuChannel",
    "LocalChannel",
    "OutboundMessage",
    "TelegramChannel",
    "WebChannel",
]


def __getattr__(name: str):
    """Lazily resolve channel exports to avoid circular imports."""
    module_map = {
        "BaseChannel": ".base",
        "OutboundMessage": ".events",
        "LocalChannel": ".local",
        "ChannelManager": ".manager",
        "FeishuChannel": ".feishu",
        "TelegramChannel": ".telegram",
        "WebChannel": ".web",
    }
    module_name = module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
