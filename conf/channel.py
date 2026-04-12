"""Channel configuration loaded from `.env`."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


def _parse_allow_from(raw_value: str) -> list[str]:
    """Parse one comma-separated allow list from env."""
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _parse_bool(raw_value: str, *, default: bool) -> bool:
    """Parse one boolean value from env."""
    cleaned = str(raw_value or "").strip().lower()
    if not cleaned:
        return default
    return cleaned in {"1", "true", "yes", "on"}


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


class WebChannelConfig(BaseModel):
    """Web chat channel configuration."""

    host: str = "127.0.0.1"
    port: int = 18900
    open_browser: bool = False
    title: str = "CreativeClaw Web Chat"


class ChannelConfig(BaseModel):
    """All supported chat channel configuration."""

    telegram: TelegramChannelConfig
    feishu: FeishuChannelConfig
    web: WebChannelConfig


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
        web=WebChannelConfig(
            host=os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("WEB_PORT", "18900").strip() or "18900"),
            open_browser=_parse_bool(os.getenv("WEB_OPEN_BROWSER", ""), default=False),
            title=os.getenv("WEB_TITLE", "CreativeClaw Web Chat").strip() or "CreativeClaw Web Chat",
        ),
    )


CHANNEL_CONFIG = load_channel_config()
