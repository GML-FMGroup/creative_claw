"""Shared runtime configuration schema for Creative Claw."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Connection settings for one LLM provider."""

    api_key: str = ""
    api_base: str | None = None
    api_version: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ProvidersConfig(BaseModel):
    """All first-round non-OAuth LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    stepfun: ProviderConfig = Field(default_factory=ProviderConfig)
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)
    qianfan: ProviderConfig = Field(default_factory=ProviderConfig)


class ServiceConfig(BaseModel):
    """Non-LLM service credentials used by experts and tools."""

    ark_api_key: str = ""
    kling_access_key: str = ""
    kling_secret_key: str = ""
    kling_api_base: str = ""
    dds_api_key: str = ""
    serper_api_key: str = ""
    brave_api_key: str = ""
    volcengine_app_id: str = ""
    volcengine_access_token: str = ""
    tencentcloud_secret_id: str = ""
    tencentcloud_secret_key: str = ""
    tencentcloud_session_token: str = ""
    tencentcloud_region: str = ""


class TelegramChannelConfig(BaseModel):
    """Telegram channel configuration."""

    bot_token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class FeishuChannelConfig(BaseModel):
    """Feishu channel configuration."""

    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["mention", "open"] = "mention"
    reply_to_message: bool = False


class WebChannelConfig(BaseModel):
    """Web chat channel configuration."""

    host: str = "127.0.0.1"
    port: int = 18900
    open_browser: bool = False
    title: str = "CreativeClaw Web Chat"


class ChannelConfig(BaseModel):
    """All supported channel settings."""

    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    feishu: FeishuChannelConfig = Field(default_factory=FeishuChannelConfig)
    web: WebChannelConfig = Field(default_factory=WebChannelConfig)


class LlmConfig(BaseModel):
    """Default orchestrator and text-agent model selection."""

    provider: str = "openai"
    model: str = "gpt-5.4"
    temperature: float = 0.1
    max_tokens: int = 8192


class SystemSettings(BaseModel):
    """App-level runtime defaults."""

    app_name: str = "CreativeClaw"
    user_id_default: str = "art_user_001"
    session_id_default_prefix: str = "art_session_"
    max_iterations_orchestrator: int = 10
    log_level: str = "DEBUG"
    log_file: str = "creative_claw_{time}.log"
    retention: str = "7 days"
    rotation: str = "10 MB"


class CreativeClawConfig(BaseModel):
    """Root runtime config stored under the user home directory."""

    workspace: str = "~/.creative-claw/workspace"
    llm: LlmConfig = Field(default_factory=LlmConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    system: SystemSettings = Field(default_factory=SystemSettings)

    @property
    def workspace_path(self) -> Path:
        """Return the expanded workspace path."""
        return Path(self.workspace).expanduser()
