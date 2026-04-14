"""Credential snapshot loaded from the user-home config."""

from __future__ import annotations

from pydantic import BaseModel

from conf.app_config import load_app_config


class APIConfig(BaseModel):
    """Centralized API-key snapshot for runtime code that prefers typed access."""

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    ZAI_API_KEY: str = ""
    MOONSHOT_API_KEY: str = ""
    MINIMAX_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    STEPFUN_API_KEY: str = ""
    QIANFAN_API_KEY: str = ""
    ARK_API_KEY: str = ""
    DDS_API_KEY: str = ""
    SERPER_API_KEY: str = ""
    BRAVE_API_KEY: str = ""
    VOLCENGINE_APPID: str = ""
    VOLCENGINE_ACCESS_TOKEN: str = ""
    TENCENTCLOUD_SECRET_ID: str = ""
    TENCENTCLOUD_SECRET_KEY: str = ""
    TENCENTCLOUD_SESSION_TOKEN: str = ""
    TENCENTCLOUD_REGION: str = ""


def load_api_config() -> APIConfig:
    """Load API keys from the runtime config without eager validation."""
    config = load_app_config()
    return APIConfig(
        OPENAI_API_KEY=config.providers.openai.api_key.strip(),
        ANTHROPIC_API_KEY=config.providers.anthropic.api_key.strip(),
        GOOGLE_API_KEY=config.providers.gemini.api_key.strip(),
        GEMINI_API_KEY=config.providers.gemini.api_key.strip(),
        GROQ_API_KEY=config.providers.groq.api_key.strip(),
        DEEPSEEK_API_KEY=config.providers.deepseek.api_key.strip(),
        DASHSCOPE_API_KEY=config.providers.dashscope.api_key.strip(),
        ZAI_API_KEY=config.providers.zhipu.api_key.strip(),
        MOONSHOT_API_KEY=config.providers.moonshot.api_key.strip(),
        MINIMAX_API_KEY=config.providers.minimax.api_key.strip(),
        MISTRAL_API_KEY=config.providers.mistral.api_key.strip(),
        STEPFUN_API_KEY=config.providers.stepfun.api_key.strip(),
        QIANFAN_API_KEY=config.providers.qianfan.api_key.strip(),
        ARK_API_KEY=config.services.ark_api_key.strip(),
        DDS_API_KEY=config.services.dds_api_key.strip(),
        SERPER_API_KEY=config.services.serper_api_key.strip(),
        BRAVE_API_KEY=config.services.brave_api_key.strip(),
        VOLCENGINE_APPID=config.services.volcengine_app_id.strip(),
        VOLCENGINE_ACCESS_TOKEN=config.services.volcengine_access_token.strip(),
        TENCENTCLOUD_SECRET_ID=config.services.tencentcloud_secret_id.strip(),
        TENCENTCLOUD_SECRET_KEY=config.services.tencentcloud_secret_key.strip(),
        TENCENTCLOUD_SESSION_TOKEN=config.services.tencentcloud_session_token.strip(),
        TENCENTCLOUD_REGION=config.services.tencentcloud_region.strip(),
    )


API_CONFIG = load_api_config()
