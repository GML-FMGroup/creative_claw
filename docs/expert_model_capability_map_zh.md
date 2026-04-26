# CreativeClaw Expert 模型实例对照表

这份表以当前代码实现为准。

整理原则：

- 一行只对应“一个 expert 的一个具体模型实现”。
- 优先记录代码里真实调用的模型 ID。
- `支持的输入`、`支持的输出`、`Key 获取链接` 先留成便于后续编辑的占位列。
- 不依赖远程模型的 expert 也单独列出，方便后续统一整理。

## 1. 具体模型实例总表

| Expert | 代码中的模型 ID | 模型名称 / 对外叫法 | 支持的输入 | 支持的输出 | Key / Token | Key 获取链接 |
| --- | --- | --- | --- | --- | --- | --- |
| `KnowledgeAgent` | 运行时 `llm.model` | 通用 LLM | 待补 | 待补 | 取决于 provider | 待补 |
| `TextTransformExpert` | 运行时 `llm.model` | 通用 LLM | 待补 | 待补 | 取决于 provider | 待补 |
| `ImageUnderstandingAgent` | 运行时 `llm.model` | 通用多模态 LLM | 待补 | 待补 | 取决于 provider | 待补 |
| `VideoUnderstandingExpert` | 运行时 `llm.model` | 通用多模态 LLM | 待补 | 待补 | 取决于 provider | 待补 |
| `ImageGenerationAgent` | `gemini-3.1-flash-image-preview` | Gemini 3.1 Flash Image Preview | 待补 | 待补 | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | 待补 |
| `ImageGenerationAgent` | `doubao-seedream-5-0-260128` | Seedream 5.0 | 待补 | 待补 | `ARK_API_KEY` | 待补 |
| `ImageGenerationAgent` | `gpt-image-2` | GPT Image 2 | 待补 | 待补 | `OPENAI_API_KEY` | 待补 |
| `ImageEditingAgent` | `gemini-3.1-flash-image-preview` | Gemini 3.1 Flash Image Preview | 待补 | 待补 | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | 待补 |
| `ImageEditingAgent` | `doubao-seedream-5-0-260128` | Seedream 5.0 | 待补 | 待补 | `ARK_API_KEY` | 待补 |
| `ImageGroundingAgent` | `DINO-XSeek-1.0` | DINO-XSeek 1.0 | 待补 | 待补 | `DDS_API_KEY` | 待补 |
| `ImageSegmentationAgent` | `DINO-X-1.0` | DINO-X 1.0 | 待补 | 待补 | `DDS_API_KEY` | 待补 |
| `SearchAgent` | Serper 图片搜索接口 | Serper Image Search | 待补 | 待补 | `SERPER_API_KEY` | 待补 |
| `SearchAgent` | DuckDuckGo 文本搜索接口 | DuckDuckGo Search | 待补 | 待补 | 无或待补 | 待补 |
| `VideoGenerationAgent` | `doubao-seedance-2-0-260128` | Seedance 2.0（默认，有声视频） | 待补 | 待补 | `ARK_API_KEY` | 待补 |
| `VideoGenerationAgent` | `doubao-seedance-2-0-fast-260128` | Seedance 2.0 fast（快速路径，720p） | 待补 | 待补 | `ARK_API_KEY` | 待补 |
| `VideoGenerationAgent` | `doubao-seedance-1-0-pro-250528` | Seedance 1.0 Pro（兼容旧路径） | 待补 | 待补 | `ARK_API_KEY` | 待补 |
| `VideoGenerationAgent` | `veo-3.1-generate-preview` | Veo 3.1 Generate Preview | 待补 | 待补 | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | 待补 |
| `VideoGenerationAgent` | `kling-v3` | Kling 3（文生/图生默认模型；`multi_reference` 当前走 `kling-v1-6`） | 待补 | 待补 | `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | 待补 |
| `SpeechRecognitionExpert` | `volc.bigasr.auc_turbo` | Volcengine BigASR Flash | 待补 | 待补 | `VOLCENGINE_APPID` + `VOLCENGINE_ACCESS_TOKEN` | 待补 |
| `SpeechRecognitionExpert` | `vc.async.default` | Volcengine Subtitle Generation | 待补 | 待补 | `VOLCENGINE_APPID` + `VOLCENGINE_ACCESS_TOKEN` | 待补 |
| `SpeechRecognitionExpert` | `volc.ata.default` | Volcengine Subtitle Alignment | 待补 | 待补 | `VOLCENGINE_APPID` + `VOLCENGINE_ACCESS_TOKEN` | 待补 |
| `SpeechSynthesisExpert` | `seed-tts-1.0` | ByteDance / Volcengine TTS | 待补 | 待补 | `VOLCENGINE_APPID` + `VOLCENGINE_ACCESS_TOKEN` | 待补 |
| `MusicGenerationExpert` | `music-2.5` | MiniMax Music 2.5 | 待补 | 待补 | `MINIMAX_API_KEY` | 待补 |
| `3DGeneration` | `3.0` | Tencent Hunyuan 3D Pro 3.0 | 待补 | 待补 | 腾讯云密钥 | 待补 |
| `3DGeneration` | `3.1` | Tencent Hunyuan 3D Pro 3.1 | 待补 | 待补 | 腾讯云密钥 | 待补 |
| `3DGeneration` | `doubao-seed3d-2-0-260328` | Doubao Seed3D 2.0（图生 3D） | 待补 | 待补 | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `3DGeneration` | `hyper3d-gen2-260112` | Hyper3D Gen2（文生 / 图生 3D） | 待补 | 待补 | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `3DGeneration` | `hitem3d-2-0-251223` | Hitem3D 2.0（图生 3D） | 待补 | 待补 | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `ImageBasicOperations` | 无 | Pillow / 本地图片处理 | 待补 | 待补 | 无 | 无需 |
| `VideoBasicOperations` | 无 | ffmpeg / 本地视频处理 | 待补 | 待补 | 无 | 无需 |
| `AudioBasicOperations` | 无 | ffmpeg / 本地音频处理 | 待补 | 待补 | 无 | 无需 |

## 2. 通用 LLM Provider 占位表

下面这些 provider 主要服务于“不是固定模型 ID，而是走运行时配置”的 expert：

- `KnowledgeAgent`
- `TextTransformExpert`
- `ImageUnderstandingAgent`
- `VideoUnderstandingExpert`

| Provider | 运行时模型 ID | 典型示例 | Key / Token | Key 获取链接 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `openai` | 运行时配置 | `gpt-5.4` | `OPENAI_API_KEY` | 待补 |  |
| `anthropic` | 运行时配置 | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` | 待补 |  |
| `gemini` | 运行时配置 | `gemini-2.5-flash` | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | 待补 |  |
| `openrouter` | 运行时配置 | `openai/gpt-5` | `providers.openrouter.api_key` | 待补 |  |
| `deepseek` | 运行时配置 | `deepseek-chat` | `DEEPSEEK_API_KEY` | 待补 |  |
| `groq` | 运行时配置 | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | 待补 |  |
| `zhipu` | 运行时配置 | `glm-4.5` | `ZAI_API_KEY` | 待补 |  |
| `dashscope` | 运行时配置 | `qwen-plus` | `DASHSCOPE_API_KEY` | 待补 |  |
| `vllm` | 运行时配置 | 自部署模型 | `providers.vllm.api_key` | 待补 |  |
| `ollama` | 运行时配置 | `qwen3` | 通常无需 key | 待补 |  |
| `moonshot` | 运行时配置 | `moonshot-v1-8k` | `MOONSHOT_API_KEY` | 待补 |  |
| `minimax` | 运行时配置 | `MiniMax-M1` | `MINIMAX_API_KEY` | 待补 |  |
| `mistral` | 运行时配置 | `mistral-large-latest` | `MISTRAL_API_KEY` | 待补 |  |
| `stepfun` | 运行时配置 | `step-2-16k` | `STEPFUN_API_KEY` | 待补 |  |
| `siliconflow` | 运行时配置 | `deepseek-ai/DeepSeek-V3` | `providers.siliconflow.api_key` | 待补 |  |
| `volcengine` | 运行时配置 | Ark OpenAI-compatible chat models | `providers.volcengine.api_key` | 待补 |  |
| `byteplus` | 运行时配置 | BytePlus Ark OpenAI-compatible chat models | `providers.byteplus.api_key` | 待补 |  |
| `qianfan` | 运行时配置 | `ernie-4.5-8k` | `QIANFAN_API_KEY` | 待补 |  |
| `azure_openai` | 运行时配置 | Azure deployment name | `providers.azure_openai.api_key` | 待补 |  |
| `custom` | 运行时配置 | 自定义 OpenAI-compatible 模型 | `providers.custom.api_key` | 待补 |  |

## 3. 当前最需要注意的几个点

- `ImageGenerationAgent`、`ImageEditingAgent`、`VideoGenerationAgent` 现在都应该按“一个 provider 一行”来看，不适合再合并成一个笼统描述。
- `VideoGenerationAgent` 新增了 `kling` 候选 provider，但当前只开放这四种 mode：`prompt`、`first_frame`、`first_frame_and_last_frame`、`multi_reference`。
- `kling` 的基础文生/图生默认模型已切到 `kling-v3`；`multi_reference` 仍按官方独立接口走 `kling-v1-6`。
- `kling` 当前不支持 `reference_asset`、`reference_style`、`video_extension` 这三个集成层 mode，主 agent 路由时要避开。
- Kling 输入图像如果不满足官方限制，当前 expert 只会报错，不会自动 resize 或裁剪；需要时应先让主 agent 调 `image_info` / `image_resize` 等本地工具。
- `SpeechRecognitionExpert` 当前不是通用 LLM，而是三条火山语音资源路径：`volc.bigasr.auc_turbo`、`vc.async.default`、`volc.ata.default`。
- 图片反推 prompt 已统一归入 `ImageUnderstandingAgent` 的 `prompt` 模式，不再单独作为一个 expert 维护。
- `3DGeneration` 已接入三款火山 Ark 3D 模型：`doubao-seed3d-2-0-260328`、`hyper3d-gen2-260112`、`hitem3d-2-0-251223`。三者都走 Ark 异步任务接口，成功后下载 `content.file_url` 返回的 3D 产物。
