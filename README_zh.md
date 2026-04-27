<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw：你的个人创意助理</h1>
  <h3>One conversation. Endless creativity.</h3>
  <p><strong>简体中文</strong> · <a href="README.md">English</a></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw 是一个由多个自主智能体协同驱动的创意工作流系统，让你的创作方式从“频繁切换工具”升级为“持续自然对话”。

系统内置图像、视频、文本、音频等多种智能体，提供创意生产所需的稳定基础能力；同时通过 Skill 机制，为不同任务智能编排策略并调用各类工具。
你只需要通过对话，就能完成从图像与视频生成、图像理解，到内容优化、信息搜索等一整套创作流程。

不必再在不同工具之间反复跳转。
在 CreativeClaw 中，你可以围绕同一个创意持续迭代，从灵感萌发到作品成型，高效推进，一气呵成。


## 📰 News
 - 2026-04-26：完成短视频 P0 收口文档，明确 Seedance 2.0 为默认原生有声视频路径，Veo+TTS 为已实现的兼容 runtime。
 - 2026-04-26：补充当前 LLM/orchestrator 工具面、短视频生产工具，以及 Seedance 2.0 / 2.0 fast 视频生成支持说明。
 - 2026-04-21：接入 Kling 视频生成，支持文生视频、图生视频、多图参考生视频，并补充区域网关探测和相关文档。
 - 2026-04-20：增强 Veo 视频生成能力。
 - 2026-04-14：支持混元 3D；将图片反推提示词并入图像理解；新增 5 个文本/视频/语音/音乐 expert。
 - 2026-04-13: 增加支持的 LLM provider数量到20个；支持图像分割。
 - 2026-04-12: v0.1.1，支持基本的图像、视频操作，支持 web、cli、飞书以对话形式使用。


## ✨ CreativeClaw 的特性

- **面向创意工作流**：图像生成、图像编辑、图像理解、提示词提取、目标定位、搜索、视频生成都是一等能力。
- **支持多种模型与提供商**：图像和视频相关能力可以接不同 provider，方便按质量、速度和成本选择。
- **基于对话的反复迭代**：可以先发参考图让它分析，再继续追问、改图、补提示词。
- **可继续扩展**：通过 skills 可以把更多专用流程接进来，比如 MiniMax CLI。
- **基于coding的素材处理**：除了直接生成内容，也可以让它帮你用 OpenCV / Python 脚本 来批量处理素材。
- **确定性的媒体基础操作**：支持通过 `ImageBasicOperations`、`VideoBasicOperations`、`AudioBasicOperations` 对本地图像、视频、音频做检查和转换。
  详细参数和示例见 [docs/media_basic_operations.md](docs/media_basic_operations.md)。

## 🏗️ Architecture

下图展示了 CreativeClaw 的高层架构，包括 orchestrator、各类 expert agent、skills，以及不同渠道接入方式。

![CreativeClaw architecture](asset/framework.png)

## 🤖 支持模型

### 🧠 LLM
 - `openai`、`anthropic`、`gemini`、`openrouter`、`deepseek`、`groq`、`zhipu`、`dashscope`、`vllm`、`ollama`、`moonshot`、`minimax`、`mistral`、`stepfun`、`siliconflow`、`volcengine`、`byteplus`、`qianfan`、`azure_openai`、`custom`

### 🖼️ 图像生成
 - Nano Banana Pro（`gemini-3.1-flash-image-preview`）
 - Seedream 5.0（`doubao-seedream-5-0-260128`）
 - GPT Image 2（`gpt-image-2`）
### 🎬 视频生成
 - Seedance 2.0（`doubao-seedance-2-0-260128`，默认）和 Seedance 2.0 fast（`doubao-seedance-2-0-fast-260128`）
 - Seedance 1.0 Pro（`doubao-seedance-1-0-pro-250528`，兼容旧路径）
 - Veo 3.1（`veo-3.1-generate-preview`）
 - Kling 3（`kling-v3`；`multi_reference` 当前使用 `kling-v1-6`）

### 📦 3D Generation
 - 混元 3D（`3.0`、`3.1`）
 - 豆包 Seed3D 2.0（`doubao-seed3d-2-0-260328`）
 - Hyper3D Gen2（`hyper3d-gen2-260112`）
 - Hitem3D 2.0（`hitem3d-2-0-251223`）

### 🔊 语音合成
 - ByteDance / Volcengine 流式 TTS（默认 `seed-tts-2.0`，支持校验后的 Seed TTS 2.0 音色选择）

### 🎵 音乐生成
 - MiniMax Music Generation Model（`music-2.5`）

### 🎤 语音识别
 - Volcengine BigASR Flash（`volc.bigasr.auc_turbo`）
 - Volcengine 字幕生成与打轴（`vc.async.default`、`volc.ata.default`）


## 🚀 快速开始

### 1. 初始化环境

```bash
git clone https://github.com/GML-FMGroup/creative_claw.git
cd creative_claw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

如果你要使用确定性的本地视频或音频处理能力，还需要确保系统里已经安装 `ffmpeg` 和 `ffprobe`，并且它们在 `PATH` 里可用。
操作参数和示例 payload 可参考 [docs/media_basic_operations.md](docs/media_basic_operations.md)。

### 2. 初始化运行目录

```bash
creative-claw init
```

这会创建：

- `~/.creative-claw/conf.json`
- `~/.creative-claw/workspace/`

### 3. 填写最少必需的 API Key

最小可用配置如下：

```json
{
  "workspace": "~/.creative-claw/workspace",
  "llm": {
    "provider": "openai",
    "model": "gpt-5.4"
  },
  "providers": {
    "openai": {
      "api_key": "your_api_key_here"
    }
  }
}
```

说明：

- 这已经足够体验默认的 CLI 聊天流程。
- 图片、视频、搜索和某些特定 provider 只在用到时才需要额外凭证。
- `VideoGenerationAgent` 的 `provider="seedance"` 现在默认使用 `doubao-seedance-2-0-260128`。需要更快生成时使用 `model_name="doubao-seedance-2-0-fast-260128"`，并保持 `resolution` 为 `720p`；旧版 `model_name="doubao-seedance-1-0-pro-250528"` 仍可用于兼容路径。
- 如果要保留精确对白或使用 Seedance 2.0 原生音频，建议传 `provider="seedance"`、`generate_audio=true`、`prompt_rewrite="off"`，避免对白被本地提示词改写破坏。
- 对 `VideoGenerationAgent` 的 `provider="kling"` 来说，文生和图生默认模型已经切到 `kling-v3`，而 `mode="multi_reference"` 仍按官方独立接口走 `kling-v1-6`。
- 如果没有显式配置 `services.kling_api_base` 或 `KLING_API_BASE`，内置 Kling provider 会自动探测北京和新加坡官方网关，并缓存首个可用结果。
- Kling 的图像输入路径只做官方文档约束校验，不会自动 resize，也不会自动裁剪；如果图片不符合要求，应先用本地图像工具预处理，再调用 `VideoGenerationAgent`。
- `SpeechSynthesisExpert` 默认使用 Volcengine 流式 TTS 的 `seed-tts-2.0`。用户或主 agent 可以通过 `speaker`、`voice_type`、`voice_name` 选择 Seed TTS 2.0 音色；默认音色是 Vivi 2.0（`zh_female_vv_uranus_bigtts`）。
- `SpeechRecognitionExpert` 依赖 Volcengine 语音服务。除了 `VOLCENGINE_APPID` 和 `VOLCENGINE_ACCESS_TOKEN` 之外，当前后端还需要开通这些资源权限：`volc.bigasr.auc_turbo` 用于 `task=asr`，`vc.async.default` 用于直接生成字幕，`volc.ata.default` 用于在传入 `subtitle_text` / `audio_text` 时做自动字幕打轴。开通入口是 [Volcengine 语音控制台](https://console.volcengine.com/speech/app)。未开通时，接口通常会返回 `requested resource not granted` 或 `requested grant not found`。
- 读取顺序是：`conf.json` 优先；如果某个 API key 在 `conf.json` 里是空字符串，运行时会回退到同名环境变量。
- 第一轮文本 LLM provider 已支持：`openai`、`anthropic`、`gemini`、`openrouter`、`deepseek`、`groq`、`zhipu`、`dashscope`、`vllm`、`ollama`、`moonshot`、`minimax`、`mistral`、`stepfun`、`siliconflow`、`volcengine`、`byteplus`、`qianfan`、`azure_openai`、`custom`。
- 更完整的环境与凭证说明、完整模板参考、以及常用字段解释，统一见 [docs/development.md](docs/development.md)。

### 3. 开始聊天

如果你已经执行过 `pip install -e .`，可以直接使用命令：

```bash
creative-claw chat cli
```

如果你还没安装 console script，就用模块入口：

```bash
python -m src.creative_claw_cli chat cli
```

也可以直接发送单次请求：

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

带图提问：

```bash
creative-claw chat cli \
  --message "Describe this image and write a better prompt for recreating it" \
  --attachment ./example.png
```

## 💡 常见用法

### 生成一张图片

```bash
creative-claw chat cli --message "Create a cinematic travel poster for Hangzhou in spring"
```

### 根据参考图优化提示词

```bash
creative-claw chat cli \
  --message "Look at this reference image and write a cleaner generation prompt" \
  --attachment ./reference.png
```

### 先理解图片，再决定怎么改

```bash
creative-claw chat cli \
  --message "Describe this image, identify the subject, and suggest three editing directions" \
  --attachment ./input.png
```

### 开启一个新会话

在对话里可以使用：

- `/help`
- `/new`

## 🧰 内置工具与 Expert 工具

主 LLM orchestrator 可以直接调用这些工具组：

- **工作区文件工具**：`list_dir`、`glob`、`grep`、`read_file`、`write_file`、`edit_file`。
- **确定性媒体工具**：`image_crop`、`image_rotate`、`image_flip`、`image_info`、`image_resize`、`image_convert`、`video_info`、`video_extract_frame`、`video_trim`、`video_concat`、`video_convert`、`audio_info`、`audio_trim`、`audio_concat`、`audio_convert`。
- **运行时与网页工具**：`exec_command`、`process_session`、`web_search`、`web_fetch`、`list_session_files`。
- **PPT 生产工具**：`run_ppt_production`，用于 PPT P0/P1 的持久化生产流程，支持可选 brief 审阅、outline 审阅、页面 preview 审阅、原生可编辑 `.pptx` 生成、页面 preview、质量报告读取、输入记录、修改影响分析、目标级 outline/deck slide 修订、页面级 preview stale 状态、单页 PPTX segment、stale 页面 preview/segment 重生成和最终产物追踪。
- **生产工具**：`run_short_video_production`，用于短视频 P1e 的持久化生产流程，支持 storyboard 审阅、资产计划审阅、分段成片确认、定向分段修改、局部重生成、显式 provider/runtime 选择、质量报告读取、参考素材更新和最终产物追踪。
- **Expert 调度工具**：`invoke_agent`，把结构化请求路由到 `ImageGenerationAgent`、`ImageEditingAgent`、`ImageUnderstandingAgent`、`VideoGenerationAgent`、`SpeechRecognitionExpert`、`SpeechSynthesisExpert`、`MusicGenerationExpert`、`3DGeneration` 等 expert。

`VideoGenerationAgent` 当前暴露这些按 provider 区分的工具参数：

- 通用参数：`provider`、`mode`、`prompt_rewrite`、`aspect_ratio`、`resolution`、`duration_seconds`、`negative_prompt`、`seed`，以及可选的 `input_path` / `input_paths`。
- `seedance`：支持 `prompt`、`first_frame`、`first_frame_and_last_frame`、`reference_asset`、`reference_style`；模型 ID 为 `doubao-seedance-2-0-260128`、`doubao-seedance-2-0-fast-260128`、`doubao-seedance-1-0-pro-250528`；额外参数为 `generate_audio` 和 `watermark`。
- `veo`：支持 `prompt`、`first_frame`、`first_frame_and_last_frame`、`reference_asset`、`reference_style`、`video_extension`；模型 ID 为 `veo-3.1-generate-preview`；额外参数为 `person_generation`。
- `kling`：支持 `prompt`、`first_frame`、`first_frame_and_last_frame`、`multi_reference`；模型 ID 为 `kling-v3`，`multi_reference` 使用 `kling-v1-6`；额外参数为 `kling_mode`（`std` 或 `pro`）。

`3DGeneration` 当前暴露这些按 provider 区分的工具参数：

- `hy3d`：默认 provider；支持纯文本、纯图片，以及 `generate_type=sketch` 的文本加图片输入。
- `seed3d`：火山 Ark 图生 3D provider；需要一个 `input_path` / `input_paths` 或 `image_url`；可选参数包括 `file_format`（`glb|obj|usd|usdz`）和 `subdivision_level`（`low|medium|high`）。
- `hyper3d`：火山 Ark 文生 / 图生 3D provider；支持英文纯 prompt 或 1-5 张参考图；可选参数包括 `file_format`、`mesh_mode`、`material`、`quality_override`、`hd_texture`。
- `hitem3d`：火山 Ark 图生 3D provider；需要 1-4 个可公网访问的 `image_url` / `image_urls`；可选参数包括 `file_format`、`resolution`、`face_count`、`request_type`、`multi_images_bit`。

## 🌐 支持的接入渠道

CreativeClaw 当前支持：

- **CLI Chat**：最适合第一次上手
- **本地 Web Chat**：浏览器里聊天，能看到实时进度和产物预览
- **Telegram**：在 Telegram 中对话
- **飞书**：在飞书中对话

### 本地 Web Chat

```bash
creative-claw chat web
```

默认监听地址是 `http://127.0.0.1:18900`。

也可以显式指定：

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

### Telegram

在 `~/.creative-claw/conf.json` 里填好 Telegram 配置后：

```bash
creative-claw chat telegram
```

### 飞书

在 `~/.creative-claw/conf.json` 里填好飞书配置后：

```bash
creative-claw chat feishu
```

补充说明：

- `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是飞书接入的主要必填项。
- `FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN` 只有在飞书平台里开启对应安全选项时才需要。
- Web Chat 默认配置也在 `~/.creative-claw/conf.json` 里，单次启动仍然可以用 CLI 参数覆盖。

## 🧰 内置 skill
### 🎵 MiniMax CLI Skill

CreativeClaw 内置了一个基于 minimax-cli 的 skill：`skills/minimax-cli-skill/SKILL.md`，支持使用 MiniMax 模型进行图像、音乐、语音、视频方面的创作。

为了在 CreativeClaw 中正常使用MiniMax 模型，推荐直接用 API Key 登录：

```bash
# install CLI globally
npm install -g mmx-cli
# Authenticate
mmx auth login --api-key sk-xxxxx
mmx auth status 
```
> Requires [Node.js](https://nodejs.org) 18+

> **Requires a MiniMax Token Plan** — [Global](https://platform.minimax.io/subscribe/token-plan) · [CN](https://platform.minimaxi.com/subscribe/token-plan)




## 📚 更多文档

- [docs/development.md](docs/development.md)：架构、环境、凭证、测试和开发说明
- [docs/ppt_production_zh.md](docs/ppt_production_zh.md)：PPT 的可选 brief 审阅、outline/deck spec 审阅、原生可编辑 PPTX 生成、preview、质量报告、目标级修订、降级策略和 P1 边界
- [docs/short_video_production_zh.md](docs/short_video_production_zh.md)：短视频生产 P1e 的 storyboard-first、asset-plan、shot-review 和定向分段重生成运行方式，Seedance 2.0/fast、Veo+TTS 使用方式、基础质量报告和飞书验收检查项
- [docs/short_video_p0_completion_zh.md](docs/short_video_p0_completion_zh.md)：短视频 P0 完成状态、验收记录、已知限制和 P1 边界
- [docs/model_and_token_map.md](docs/model_and_token_map.md)：模型名、对应 expert 和 token 申请链接
- [docs/expert_model_capability_map_zh.md](docs/expert_model_capability_map_zh.md)：当前 expert 能力边界，包括 Kling 的路由覆盖和限制

## 🛠️ TODO
- [ ] 支持更多图像生成、视频生成模型
- [ ] 增加更多创意相关 skill
- [x] 支持更多LLM provider
- [ ] 支持更多 channel
