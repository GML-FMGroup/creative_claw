"""User-home runtime config loading and initialization helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conf.schema import CreativeClawConfig

_CONFIG_HOME_ENV_VAR = "CREATIVE_CLAW_HOME"
_CONFIG_FILE_NAME = "conf.json"

_APP_CONFIG: CreativeClawConfig | None = None


def get_instance_root() -> Path:
    """Return the runtime instance directory for the current user."""
    configured_root = os.getenv(_CONFIG_HOME_ENV_VAR, "~/.creative-claw")
    return Path(configured_root).expanduser()


def get_config_path() -> Path:
    """Return the runtime config file path."""
    return get_instance_root() / _CONFIG_FILE_NAME


def get_logs_dir() -> Path:
    """Return the runtime log directory."""
    return get_instance_root() / "logs"


def build_default_config() -> CreativeClawConfig:
    """Return the default runtime config."""
    config = CreativeClawConfig(workspace=str(get_instance_root() / "workspace"))
    _apply_recommended_provider_defaults(config)
    return config


def load_app_config(*, reload: bool = False) -> CreativeClawConfig:
    """Load runtime config from the user-home config file or defaults."""
    global _APP_CONFIG

    if _APP_CONFIG is not None and not reload:
        return _APP_CONFIG

    config_path = get_config_path()
    config = build_default_config()
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        config = CreativeClawConfig.model_validate(data)

    apply_env_fallbacks(config)
    sync_env_from_config(config)
    _APP_CONFIG = config
    return config


def save_app_config(config: CreativeClawConfig) -> Path:
    """Persist one runtime config file to the user-home config path."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as file:
        json.dump(config.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
        file.write("\n")
    return config_path


def initialize_runtime_config(*, force: bool = False) -> tuple[Path, Path, bool]:
    """Create the instance directory, config file, and workspace if needed."""
    config = build_default_config()
    config_path = get_config_path()
    workspace_path = config.workspace_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)

    created = force or not config_path.exists()
    if created:
        save_app_config(config)
    else:
        load_app_config(reload=True)
    return config_path, workspace_path, created


def sync_env_from_config(config: CreativeClawConfig) -> None:
    """Expose config-backed credentials as process env vars for SDK compatibility."""
    mappings = {
        "OPENAI_API_KEY": config.providers.openai.api_key,
        "ANTHROPIC_API_KEY": config.providers.anthropic.api_key,
        "GOOGLE_API_KEY": config.providers.gemini.api_key,
        "GEMINI_API_KEY": config.providers.gemini.api_key,
        "GROQ_API_KEY": config.providers.groq.api_key,
        "DEEPSEEK_API_KEY": config.providers.deepseek.api_key,
        "DASHSCOPE_API_KEY": config.providers.dashscope.api_key,
        "ZAI_API_KEY": config.providers.zhipu.api_key,
        "MOONSHOT_API_KEY": config.providers.moonshot.api_key,
        "MINIMAX_API_KEY": config.providers.minimax.api_key,
        "MISTRAL_API_KEY": config.providers.mistral.api_key,
        "STEPFUN_API_KEY": config.providers.stepfun.api_key,
        "QIANFAN_API_KEY": config.providers.qianfan.api_key,
        "ARK_API_KEY": config.services.ark_api_key,
        "DDS_API_KEY": config.services.dds_api_key,
        "SERPER_API_KEY": config.services.serper_api_key,
        "BRAVE_API_KEY": config.services.brave_api_key,
        "VOLCENGINE_APPID": config.services.volcengine_app_id,
        "VOLCENGINE_ACCESS_TOKEN": config.services.volcengine_access_token,
        "TENCENTCLOUD_SECRET_ID": config.services.tencentcloud_secret_id,
        "TENCENTCLOUD_SECRET_KEY": config.services.tencentcloud_secret_key,
        "TENCENTCLOUD_SESSION_TOKEN": config.services.tencentcloud_session_token,
        "TENCENTCLOUD_REGION": config.services.tencentcloud_region,
        "TELEGRAM_BOT_TOKEN": config.channels.telegram.bot_token,
        "TELEGRAM_ALLOW_FROM": ",".join(config.channels.telegram.allow_from),
        "FEISHU_APP_ID": config.channels.feishu.app_id,
        "FEISHU_APP_SECRET": config.channels.feishu.app_secret,
        "FEISHU_ENCRYPT_KEY": config.channels.feishu.encrypt_key,
        "FEISHU_VERIFICATION_TOKEN": config.channels.feishu.verification_token,
        "FEISHU_ALLOW_FROM": ",".join(config.channels.feishu.allow_from),
    }

    for key, value in mappings.items():
        cleaned = str(value or "").strip()
        if cleaned:
            os.environ[key] = cleaned
        else:
            os.environ.pop(key, None)


def apply_env_fallbacks(config: CreativeClawConfig) -> None:
    """Fill empty secret fields from process environment variables."""
    provider_mappings = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
        "zhipu": "ZAI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "stepfun": "STEPFUN_API_KEY",
        "qianfan": "QIANFAN_API_KEY",
    }
    service_mappings = {
        "ark_api_key": "ARK_API_KEY",
        "dds_api_key": "DDS_API_KEY",
        "serper_api_key": "SERPER_API_KEY",
        "brave_api_key": "BRAVE_API_KEY",
        "volcengine_app_id": "VOLCENGINE_APPID",
        "volcengine_access_token": "VOLCENGINE_ACCESS_TOKEN",
        "tencentcloud_secret_id": "TENCENTCLOUD_SECRET_ID",
        "tencentcloud_secret_key": "TENCENTCLOUD_SECRET_KEY",
        "tencentcloud_session_token": "TENCENTCLOUD_SESSION_TOKEN",
        "tencentcloud_region": "TENCENTCLOUD_REGION",
    }

    for provider_name, env_var in provider_mappings.items():
        provider_config = getattr(config.providers, provider_name)
        if not str(provider_config.api_key or "").strip():
            env_value = os.getenv(env_var, "").strip()
            if env_value:
                provider_config.api_key = env_value

    if not str(config.providers.gemini.api_key or "").strip():
        gemini_env = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_env:
            config.providers.gemini.api_key = gemini_env

    for field_name, env_var in service_mappings.items():
        if not str(getattr(config.services, field_name) or "").strip():
            env_value = os.getenv(env_var, "").strip()
            if env_value:
                setattr(config.services, field_name, env_value)


def _apply_recommended_provider_defaults(config: CreativeClawConfig) -> None:
    """Populate practical provider defaults for the generated `conf.json` template."""
    config.providers.openrouter.api_base = "https://openrouter.ai/api/v1"
    config.providers.deepseek.api_base = "https://api.deepseek.com"
    config.providers.zhipu.api_base = "https://open.bigmodel.cn/api/paas/v4"
    config.providers.dashscope.api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    config.providers.ollama.api_base = "http://localhost:11434/v1"
    config.providers.moonshot.api_base = "https://api.moonshot.ai/v1"
    config.providers.minimax.api_base = "https://api.minimax.chat/v1"
    config.providers.stepfun.api_base = "https://api.stepfun.com/v1"
    config.providers.siliconflow.api_base = "https://api.siliconflow.cn/v1"
    config.providers.volcengine.api_base = "https://ark.cn-beijing.volces.com/api/v3"
    config.providers.byteplus.api_base = "https://ark.ap-southeast.bytepluses.com/api/v3"
    config.providers.qianfan.api_base = "https://qianfan.baidubce.com/v2"
    config.providers.azure_openai.api_base = "https://your-resource.openai.azure.com"
    config.providers.azure_openai.api_version = "2024-10-21"
    config.providers.custom.api_base = "https://your-openai-compatible-endpoint/v1"
