"""Channel configuration loaded from `.env`."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


def _parse_allow_from(raw_value: str) -> list[str]:
    """Parse one comma-separated allow list from env."""
    return [item.strip() for item in raw_value.split(",") if item.strip()]


class TelegramChannelConfig(BaseModel):
    """Telegram channel configuration."""

    bot_token: str = ""
    allow_from: list[str] = []


class FeishuChannelConfig(BaseModel):
    """Feishu channel configuration."""

    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = []


class ChannelConfig(BaseModel):
    """All supported chat channel configuration."""

    telegram: TelegramChannelConfig
    feishu: FeishuChannelConfig


def load_channel_config() -> ChannelConfig:
    """Load channel configuration from environment variables."""
    return ChannelConfig(
        telegram=TelegramChannelConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            allow_from=_parse_allow_from(os.getenv("TELEGRAM_ALLOW_FROM", "")),
        ),
        feishu=FeishuChannelConfig(
            app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", "").strip(),
            verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
            allow_from=_parse_allow_from(os.getenv("FEISHU_ALLOW_FROM", "")),
        ),
    )


CHANNEL_CONFIG = load_channel_config()
