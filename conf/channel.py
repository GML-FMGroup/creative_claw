"""Channel configuration loaded from the user-home config."""

from __future__ import annotations

from conf.app_config import load_app_config
from conf.schema import (
    ChannelConfig,
    FeishuChannelConfig,
    TelegramChannelConfig,
    WebChannelConfig,
)


def load_channel_config() -> ChannelConfig:
    """Load channel configuration from the runtime config."""
    return load_app_config().channels.model_copy(deep=True)


CHANNEL_CONFIG = load_channel_config()
