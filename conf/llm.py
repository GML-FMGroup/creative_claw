"""Provider registry and model factory helpers for Creative Claw."""

from __future__ import annotations

from dataclasses import dataclass

from google.adk.models import BaseLlm, Gemini, LiteLlm

from conf.app_config import load_app_config


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Metadata needed to construct one ADK model backend."""

    name: str
    kind: str
    default_api_base: str = ""
    model_prefix: str = ""
    default_api_version: str = ""


PROVIDERS: dict[str, ProviderSpec] = {
    "custom": ProviderSpec(name="custom", kind="openai_compatible"),
    "azure_openai": ProviderSpec(
        name="azure_openai",
        kind="azure",
        model_prefix="azure",
        default_api_version="2024-10-21",
    ),
    "anthropic": ProviderSpec(name="anthropic", kind="litellm_prefix", model_prefix="anthropic"),
    "openai": ProviderSpec(name="openai", kind="litellm_prefix", model_prefix="openai"),
    "openrouter": ProviderSpec(
        name="openrouter",
        kind="litellm_prefix",
        model_prefix="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        kind="litellm_prefix",
        model_prefix="deepseek",
        default_api_base="https://api.deepseek.com",
    ),
    "groq": ProviderSpec(name="groq", kind="litellm_prefix", model_prefix="groq"),
    "zhipu": ProviderSpec(
        name="zhipu",
        kind="openai_compatible",
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        kind="openai_compatible",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "vllm": ProviderSpec(name="vllm", kind="openai_compatible"),
    "ollama": ProviderSpec(name="ollama", kind="litellm_prefix", model_prefix="ollama"),
    "gemini": ProviderSpec(name="gemini", kind="gemini"),
    "moonshot": ProviderSpec(
        name="moonshot",
        kind="openai_compatible",
        default_api_base="https://api.moonshot.ai/v1",
    ),
    "minimax": ProviderSpec(
        name="minimax",
        kind="openai_compatible",
        default_api_base="https://api.minimax.chat/v1",
    ),
    "mistral": ProviderSpec(name="mistral", kind="litellm_prefix", model_prefix="mistral"),
    "stepfun": ProviderSpec(
        name="stepfun",
        kind="openai_compatible",
        default_api_base="https://api.stepfun.com/v1",
    ),
    "siliconflow": ProviderSpec(
        name="siliconflow",
        kind="openai_compatible",
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    "volcengine": ProviderSpec(
        name="volcengine",
        kind="openai_compatible",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),
    "byteplus": ProviderSpec(
        name="byteplus",
        kind="openai_compatible",
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/v3",
    ),
    "qianfan": ProviderSpec(
        name="qianfan",
        kind="openai_compatible",
        default_api_base="https://qianfan.baidubce.com/v2",
    ),
}


def build_llm(
    model_reference: str | None = None,
    *,
    provider_override: str | None = None,
) -> BaseLlm:
    """Construct one ADK model backend from the current runtime config."""
    app_config = load_app_config()
    provider_name, model_name = resolve_provider_and_model(
        model_reference,
        provider_override=provider_override,
    )
    provider_config = getattr(app_config.providers, provider_name)
    spec = get_provider_spec(provider_name)

    if spec.kind == "gemini":
        return Gemini(model=model_name)

    kwargs: dict[str, object] = {}
    if provider_config.api_key:
        kwargs["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        kwargs["extra_headers"] = provider_config.extra_headers

    api_base = provider_config.api_base or spec.default_api_base
    if api_base:
        kwargs["api_base"] = api_base

    if spec.kind == "litellm_prefix":
        return LiteLlm(model=f"{spec.model_prefix}/{model_name}", **kwargs)

    if spec.kind == "azure":
        api_version = provider_config.api_version or spec.default_api_version
        if api_version:
            kwargs["api_version"] = api_version
        return LiteLlm(model=f"{spec.model_prefix}/{model_name}", **kwargs)

    kwargs["custom_llm_provider"] = "openai"
    return LiteLlm(model=model_name, **kwargs)


def resolve_llm_model_name(model_reference: str | None = None) -> str:
    """Return the fully-qualified provider/model name used for logs and results."""
    provider_name, model_name = resolve_provider_and_model(model_reference)
    return f"{provider_name}/{model_name}"


def resolve_provider_and_model(
    model_reference: str | None = None,
    *,
    provider_override: str | None = None,
) -> tuple[str, str]:
    """Resolve one provider name and model name from config plus optional overrides."""
    app_config = load_app_config()
    provider_name = (provider_override or app_config.llm.provider).strip()
    model_name = (model_reference or app_config.llm.model).strip()

    if model_reference and "/" in model_reference:
        prefix, bare_model = model_reference.split("/", 1)
        if prefix in PROVIDERS:
            provider_name = prefix
            model_name = bare_model.strip()

    if provider_name not in PROVIDERS:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported LLM provider '{provider_name}'. Supported providers: {supported}.")
    if not model_name:
        raise ValueError("LLM model name cannot be empty.")
    return provider_name, model_name


def get_provider_spec(name: str) -> ProviderSpec:
    """Return one provider spec or raise a clear error."""
    try:
        return PROVIDERS[name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported LLM provider '{name}'. Supported providers: {supported}.") from exc
