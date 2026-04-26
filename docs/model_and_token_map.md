# Model And Token Map

This document lists the concrete model names currently used in the codebase, the expert that uses them, and the credential or API-key application link.

| Model Name | Expert | Required Key / Token | Application Link |
| --- | --- | --- | --- |
| `gpt-5.4` | `OrchestratorAgent`, `KnowledgeAgent`, and the default text-LLM path | `OPENAI_API_KEY` | [OpenAI API Key](https://platform.openai.com/docs/quickstart/step-2-set-up-your-api-key%23.class) |
| `gpt-image-2` | `ImageGenerationAgent` (`gpt_image`) | `OPENAI_API_KEY` | [OpenAI API Key](https://platform.openai.com/docs/quickstart/step-2-set-up-your-api-key%23.class) |
| `gemini-3.1-flash-image-preview` | `ImageGenerationAgent` (`nano_banana`), `ImageEditingAgent` (`nano_banana`) | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `veo-3.1-generate-preview` | `VideoGenerationAgent` (`veo`) | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `doubao-seedream-5-0-260128` | `ImageGenerationAgent` (`seedream`), `ImageEditingAgent` (`seedream`) | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `doubao-seedance-2-0-260128` | `VideoGenerationAgent` (`seedance`, default), `ShortVideoProductionManager` default runtime | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `doubao-seedance-2-0-fast-260128` | `VideoGenerationAgent` (`seedance` fast), short-video fast path | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `doubao-seedance-1-0-pro-250528` | `VideoGenerationAgent` (`seedance`, legacy-compatible) | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `kling-v3` | `VideoGenerationAgent` (`kling`, default basic-route model) | `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | [Kling AI API Access](https://app.klingai.com/global/dev/document-api) |
| `kling-v1-6` | `VideoGenerationAgent` (`kling`, `multi_reference`) | `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | [Kling AI API Access](https://app.klingai.com/global/dev/document-api) |
| `DINO-XSeek-1.0` | `ImageGroundingAgent` | `DDS_API_KEY` | [DeepDataSpace DINO-X Platform](https://cloud.deepdataspace.com/zh/dashboard/token-key) |
| `DINO-X-1.0` | `ImageSegmentationAgent` | `DDS_API_KEY` | [DeepDataSpace DINO-X Platform](https://cloud.deepdataspace.com/zh/dashboard/token-key) |

## Text LLM Provider Credentials

The text-LLM layer supports more providers than the single default example `gpt-5.4`. These providers are configured under `providers.<name>.api_key` in `~/.creative-claw/conf.json`. If a config field is empty, runtime falls back to the matching environment variable.

| Provider | Typical Model Examples | Config Field | Environment Variable | Application Link |
| --- | --- | --- | --- | --- |
| `openai` | `gpt-5.4`, `gpt-4.1` | `providers.openai.api_key` | `OPENAI_API_KEY` | [OpenAI API Key](https://platform.openai.com/docs/quickstart/step-2-set-up-your-api-key%23.class) |
| `anthropic` | `claude-sonnet-4-5` | `providers.anthropic.api_key` | `ANTHROPIC_API_KEY` | [Anthropic API Keys](https://docs.anthropic.com/en/api/getting-started) |
| `gemini` | `gemini-2.5-flash`, `gemini-2.5-pro` | `providers.gemini.api_key` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `openrouter` | `openai/gpt-5`, `anthropic/claude-sonnet-4` | `providers.openrouter.api_key` | no dedicated fallback env var | [OpenRouter Keys](https://openrouter.ai/settings/keys) |
| `deepseek` | `deepseek-chat` | `providers.deepseek.api_key` | `DEEPSEEK_API_KEY` | [DeepSeek API Platform](https://platform.deepseek.com/api_keys) |
| `groq` | `llama-3.3-70b-versatile` | `providers.groq.api_key` | `GROQ_API_KEY` | [Groq API Keys](https://console.groq.com/keys) |
| `zhipu` | `glm-4.5` | `providers.zhipu.api_key` | `ZAI_API_KEY` | [Zhipu BigModel Keys](https://open.bigmodel.cn/usercenter/apikeys) |
| `dashscope` | `qwen-plus` | `providers.dashscope.api_key` | `DASHSCOPE_API_KEY` | [DashScope API Key](https://bailian.console.aliyun.com/?tab=model#/api-key) |
| `vllm` | self-hosted OpenAI-compatible models | `providers.vllm.api_key` | no dedicated fallback env var | vendor-specific or self-hosted |
| `ollama` | `qwen3`, `llama3.1` | `providers.ollama.api_key` | no dedicated fallback env var | local deployment; usually no key |
| `moonshot` | `moonshot-v1-8k` | `providers.moonshot.api_key` | `MOONSHOT_API_KEY` | [Moonshot API Keys](https://platform.moonshot.ai/console/api-keys) |
| `minimax` | `MiniMax-M1`, `abab7-chat-preview` | `providers.minimax.api_key` | `MINIMAX_API_KEY` | [MiniMax API Key](https://platform.minimaxi.com/user-center/basic-information/interface-key) |
| `mistral` | `mistral-large-latest` | `providers.mistral.api_key` | `MISTRAL_API_KEY` | [Mistral API Keys](https://console.mistral.ai/api-keys/) |
| `stepfun` | `step-2-16k` | `providers.stepfun.api_key` | `STEPFUN_API_KEY` | [StepFun Platform](https://platform.stepfun.com/) |
| `siliconflow` | `deepseek-ai/DeepSeek-V3` | `providers.siliconflow.api_key` | no dedicated fallback env var | [SiliconFlow API Keys](https://cloud.siliconflow.cn/account/ak) |
| `volcengine` | Ark OpenAI-compatible chat models | `providers.volcengine.api_key` | no dedicated fallback env var | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `byteplus` | BytePlus Ark OpenAI-compatible chat models | `providers.byteplus.api_key` | no dedicated fallback env var | [BytePlus ModelArk API Key](https://console.byteplus.com/ark) |
| `qianfan` | `ernie-4.5-8k` | `providers.qianfan.api_key` | `QIANFAN_API_KEY` | [Baidu Qianfan API Key](https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application) |
| `azure_openai` | Azure-hosted OpenAI deployment names | `providers.azure_openai.api_key` | no dedicated fallback env var | [Azure OpenAI Keys](https://learn.microsoft.com/azure/ai-services/openai/reference#authentication) |
| `custom` | OpenAI-compatible custom endpoints | `providers.custom.api_key` | no dedicated fallback env var | vendor-specific |

## Expert / Service Credentials

Some capabilities use service credentials rather than the general text-LLM provider registry.

| Capability | Expert / Tool | Required Credential | Config Field | Environment Variable | Application Link |
| --- | --- | --- | --- | --- | --- |
| Image generation (`seedream`) | `ImageGenerationAgent` | Volcengine Ark key | `services.ark_api_key` | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| Image editing (`seedream`) | `ImageEditingAgent` | Volcengine Ark key | `services.ark_api_key` | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| Video generation (`seedance`) | `VideoGenerationAgent` | Volcengine Ark key | `services.ark_api_key` | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| Video generation (`kling`) | `VideoGenerationAgent` | Kling API access key and secret key | `services.kling_access_key`, `services.kling_secret_key`, optional `services.kling_api_base` | `KLING_ACCESS_KEY`, `KLING_SECRET_KEY`, optional `KLING_API_BASE` | [Kling AI API Access](https://app.klingai.com/global/dev/document-api) |
| Search image mode | `SearchAgent` | Serper key | `services.serper_api_key` | `SERPER_API_KEY` | [Serper API Key](https://serper.dev/api-key) |
| Built-in web search | `web_search` tool | Brave Search key | `services.brave_api_key` | `BRAVE_API_KEY` | [Brave Search API](https://brave.com/search/api/) |
| 3D generation (`hy3d`) | `ThreeDGenerationAgent` | Tencent Cloud credentials | `services.tencentcloud_secret_id`, `services.tencentcloud_secret_key`, optional `services.tencentcloud_session_token`, optional `services.tencentcloud_region` | `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY`, optional `TENCENTCLOUD_SESSION_TOKEN`, optional `TENCENTCLOUD_REGION` | [Tencent Cloud API Key](https://console.cloud.tencent.com/cam/capi) |

## Notes

- The table lists concrete model names that are explicitly used by the current code.
- The text-LLM layer supports more providers than the single default example `gpt-5.4`, so this document also includes provider-level credential mapping for runtime configuration.
- Some experts select providers dynamically. In those cases, the table records the model names that are actually invoked by the provider-specific code paths.
- Kling now defaults to `kling-v3` for prompt and image-guided routes in the built-in provider path, while `multi_reference` follows the official `kling-v1-6` schema. When `KLING_API_BASE` is not configured explicitly, the provider probes the official Beijing and Singapore gateways and caches the first working base. Kling input images are validated against the documented file constraints, but the expert does not auto-resize or auto-crop them.
- Gemini runtime accepts `GOOGLE_API_KEY` as the primary fallback environment variable and also accepts `GEMINI_API_KEY` as a compatibility alias.
- DeepDataSpace runtime primarily uses `DDS_API_KEY`, and some code paths also accept compatibility aliases such as `DDS_TOKEN` and `DINO_XSEEK_TOKEN`.
- For providers marked as "no dedicated fallback env var", the runtime config field still works, but `apply_env_fallbacks()` does not currently auto-import that provider from a provider-specific environment variable.
