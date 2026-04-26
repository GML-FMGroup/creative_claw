<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw: your personal creative assistant</h1>
  <h3>One conversation. Endless creativity.</h3>
  <p><a href="README_zh.md">中文</a> · <strong>English</strong></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw is a creative workflow system powered by multiple autonomous agents, turning the creative process from tool switching into continuous conversation.

It comes with multiple built-in agents that provide reliable creative capabilities and intelligently invoke different tools through the Skill mechanism. With conversation alone, you can complete an end-to-end creative workflow covering image and video generation, image understanding, content refinement, and information search.

No more jumping back and forth between different tools.
With CreativeClaw, you can keep iterating around a single idea and move from inspiration to final output in one flow.

## 📰 News
 - 2026-04-26: Documented the current LLM/orchestrator tool surface, short-video production tool, and Seedance 2.0 / 2.0 fast video generation support.
 - 2026-04-21: Added Kling video provider integration, including text-to-video, image-to-video, multi-reference video generation, gateway probing, and updated docs.
 - 2026-04-20: Expanded Veo video generation support.
 - 2026-04-14: Added HY 3D support, merged image-to-prompt into image understanding, and introduced 5 new experts across text, video, speech, and music.
 - 2026-04-13: Expanded support to 20 LLM providers and added image segmentation.
 - 2026-04-12: Released v0.1.1, introducing basic image and video operations across Web, CLI, and Feishu chat.


## ✨ Key Features of CreativeClaw

- **Built for creative workflows**: image generation, image editing, image understanding, prompt extraction, grounding, search, and video generation are first-class capabilities.
- **Supports multiple models and providers**: image and video flows can use different providers so you can balance quality, speed, and cost.
- **Iterative through conversation**: send a reference image for analysis, then keep asking follow-up questions, editing, and refining prompts.
- **Extensible by design**: skills let you add specialized workflows such as MiniMax CLI.
- **Coding-based asset processing**: besides generating content directly, it can also help process assets in batches through OpenCV / Python scripts.
- **Deterministic media operations**: supports local image, video, and audio inspection and transformation through `ImageBasicOperations`, `VideoBasicOperations`, and `AudioBasicOperations`.
  See [docs/media_basic_operations.md](docs/media_basic_operations.md) for a quick reference.

## 🏗️ Architecture

The following diagram shows the high-level architecture of CreativeClaw, including the orchestrator, expert agents, skills, and channel integrations.

![CreativeClaw architecture](asset/framework.png)

## 🤖 Supported Models

### 🧠 LLM

-  `openai`, `anthropic`, `gemini`, `openrouter`, `deepseek`, `groq`, `zhipu`, `dashscope`, `vllm`, `ollama`, `moonshot`, `minimax`, `mistral`, `stepfun`, `siliconflow`, `volcengine`, `byteplus`, `qianfan`, `azure_openai`, `custom`

### 🖼️ Image Generation

- Nano Banana Pro (`gemini-3.1-flash-image-preview`)
- Seedream 5.0 (`doubao-seedream-5-0-260128`)
- GPT Image 2 (`gpt-image-2`)

### 🎬 Video Generation

- Seedance 2.0 (`doubao-seedance-2-0-260128`, default) and Seedance 2.0 fast (`doubao-seedance-2-0-fast-260128`)
- Seedance 1.0 Pro (`doubao-seedance-1-0-pro-250528`, legacy-compatible)
- Veo 3.1 (`veo-3.1-generate-preview`)
- Kling 3 (`kling-v3`; `multi_reference` currently uses `kling-v1-6`)

### 📦 3D Generation
 - HY 3D (`3.0`, `3.1`)

### 🔊 Speech Synthesis

- ByteDance / Volcengine streaming TTS (`seed-tts-1.0`)

### 🎵 Music Generation

- MiniMax Music Generation API (`music-2.5`)

### 🎤 Speech Recognition
 - Volcengine BigASR Flash (`volc.bigasr.auc_turbo`)
 - Volcengine subtitle generation and alignment (`vc.async.default`, `volc.ata.default`)


## 🚀 Quick Start

### 1. Set up the environment

```bash
git clone https://github.com/GML-FMGroup/creative_claw.git
cd creative_claw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

If you want deterministic local video or audio operations, also make sure `ffmpeg` and `ffprobe` are installed and available on `PATH`.
For operation parameters and example payloads, see [docs/media_basic_operations.md](docs/media_basic_operations.md).

### 2. Initialize the runtime directory

```bash
creative-claw init
```

This creates:

- `~/.creative-claw/conf.json`
- `~/.creative-claw/workspace/`

### 3. Add the minimum required API key

The minimum working config looks like this:

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

Notes:

- This is enough to try the default CLI chat flow.
- Image, video, search, and some provider-specific capabilities only need extra credentials when you actually use them.
- `VideoGenerationAgent` provider `seedance` now defaults to `doubao-seedance-2-0-260128`. For faster generation use `model_name="doubao-seedance-2-0-fast-260128"` and keep `resolution` at `720p`; legacy `model_name="doubao-seedance-1-0-pro-250528"` remains accepted.
- For exact dialogue or native generated audio with Seedance 2.0, use `provider="seedance"`, `generate_audio=true`, and `prompt_rewrite="off"` so quoted dialogue is preserved.
- For `VideoGenerationAgent` with `provider="kling"`, prompt and image-guided routes now default to `kling-v3`, while `mode="multi_reference"` follows the official `kling-v1-6` schema.
- If `services.kling_api_base` or `KLING_API_BASE` is not set explicitly, the built-in Kling provider probes the official Beijing and Singapore gateways and caches the first working base.
- Kling image-guided routes validate the documented input constraints but do not auto-resize or auto-crop input images. If preprocessing is needed, do it first with local image tools before calling `VideoGenerationAgent`.
- `SpeechRecognitionExpert` uses Volcengine speech services. Besides `VOLCENGINE_APPID` and `VOLCENGINE_ACCESS_TOKEN`, the current backend also needs these resource grants: `volc.bigasr.auc_turbo` for `task=asr`, `vc.async.default` for subtitle generation, and `volc.ata.default` for subtitle timing when `subtitle_text` / `audio_text` is provided. The activation entry is the [Volcengine speech console](https://console.volcengine.com/speech/app). Missing grants usually surface as `requested resource not granted` or `requested grant not found`.
- Resolution order is: `conf.json` first; if an API key is empty in `conf.json`, runtime falls back to the matching environment variable.
- The first-round text LLM providers include `openai`, `anthropic`, `gemini`, `openrouter`, `deepseek`, `groq`, `zhipu`, `dashscope`, `vllm`, `ollama`, `moonshot`, `minimax`, `mistral`, `stepfun`, `siliconflow`, `volcengine`, `byteplus`, `qianfan`, `azure_openai`, and `custom`.
- For the full environment and credential matrix, the reference full template, and common field descriptions, see [docs/development.md](docs/development.md).

### 3. Start chatting

If you already ran `pip install -e .`, you can use the command directly:

```bash
creative-claw chat cli
```

If you have not installed the console script yet, use the module entrypoint:

```bash
python -m src.creative_claw_cli chat cli
```

You can also send a single request directly:

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

Ask with an image attachment:

```bash
creative-claw chat cli \
  --message "Describe this image and write a better prompt for recreating it" \
  --attachment ./example.png
```

## 💡 Common Usage

### Generate an image

```bash
creative-claw chat cli --message "Create a cinematic travel poster for Hangzhou in spring"
```

### Improve a prompt from a reference image

```bash
creative-claw chat cli \
  --message "Look at this reference image and write a cleaner generation prompt" \
  --attachment ./reference.png
```

### Understand an image before deciding how to edit it

```bash
creative-claw chat cli \
  --message "Describe this image, identify the subject, and suggest three editing directions" \
  --attachment ./input.png
```

### Start a new session

Inside the chat, use:

- `/help`
- `/new`

## 🧰 Built-in Tools and Expert Tools

The main LLM orchestrator can call these tool groups directly:

- **Workspace file tools**: `list_dir`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`.
- **Deterministic media tools**: `image_crop`, `image_rotate`, `image_flip`, `image_info`, `image_resize`, `image_convert`, `video_info`, `video_extract_frame`, `video_trim`, `video_concat`, `video_convert`, `audio_info`, `audio_trim`, `audio_concat`, `audio_convert`.
- **Runtime and web tools**: `exec_command`, `process_session`, `web_search`, `web_fetch`, `list_session_files`.
- **Production tool**: `run_short_video_production` for durable short-video P0 flows with plan review, revision, reference asset updates, and final artifact tracking.
- **Expert dispatch**: `invoke_agent` routes structured requests to expert agents such as `ImageGenerationAgent`, `ImageEditingAgent`, `ImageUnderstandingAgent`, `VideoGenerationAgent`, `SpeechRecognitionExpert`, `SpeechSynthesisExpert`, `MusicGenerationExpert`, and `3DGeneration`.

`VideoGenerationAgent` currently exposes these provider-aware tool parameters:

- Common controls: `provider`, `mode`, `prompt_rewrite`, `aspect_ratio`, `resolution`, `duration_seconds`, `negative_prompt`, `seed`, and optional `input_path` / `input_paths`.
- `seedance`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `reference_asset`, `reference_style`; model ids `doubao-seedance-2-0-260128`, `doubao-seedance-2-0-fast-260128`, `doubao-seedance-1-0-pro-250528`; extra controls `generate_audio` and `watermark`.
- `veo`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `reference_asset`, `reference_style`, `video_extension`; model id `veo-3.1-generate-preview`; extra control `person_generation`.
- `kling`: modes `prompt`, `first_frame`, `first_frame_and_last_frame`, `multi_reference`; model ids `kling-v3` and `kling-v1-6` for `multi_reference`; extra control `kling_mode` (`std` or `pro`).

## 🌐 Supported Channels

CreativeClaw currently supports:

- **CLI Chat**: the easiest way to get started
- **Local Web Chat**: browser-based chat with realtime progress and artifact previews
- **Telegram**: chat in Telegram
- **Feishu**: chat in Feishu

### Local Web Chat

```bash
creative-claw chat web
```

The default address is `http://127.0.0.1:18900`.

You can also set it explicitly:

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

### Telegram

After filling the Telegram fields in `~/.creative-claw/conf.json`:

```bash
creative-claw chat telegram
```

### Feishu

After filling the Feishu fields in `~/.creative-claw/conf.json`:

```bash
creative-claw chat feishu
```

Additional notes:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are the main required values for Feishu.
- `FEISHU_ENCRYPT_KEY` and `FEISHU_VERIFICATION_TOKEN` are only needed when the matching security settings are enabled in the Feishu platform.
- Web chat defaults also live in `~/.creative-claw/conf.json`, and CLI flags can still override them for one run.

## 🧰 Built-in Skill

### 🎵 MiniMax CLI Skill

CreativeClaw includes a project-level MiniMax skill at `skills/minimax-cli-skill/SKILL.md`.

Use it when:

- you explicitly want MiniMax or `mmx`
- you want MiniMax music generation
- you want MiniMax speech synthesis
- you need MiniMax file upload or `file_id`-based follow-up workflows

For agent-style usage, API key login is the recommended setup:

```bash
# install CLI globally
npm install -g mmx-cli
# Authenticate
mmx auth login --api-key sk-xxxxx
mmx auth status
```

> Requires [Node.js](https://nodejs.org) 18+

> **Requires a MiniMax Token Plan** — [Global](https://platform.minimax.io/subscribe/token-plan) · [CN](https://platform.minimaxi.com/subscribe/token-plan)

In practice, you only need this skill when you explicitly want MiniMax-specific capabilities.

## 📚 More Docs

- [docs/development.md](docs/development.md): architecture, environment, credentials, tests, and development notes
- [docs/short_video_production_zh.md](docs/short_video_production_zh.md): short-video P0 usage, Seedance 2.0/fast routing, and Feishu acceptance checklist
- [docs/model_and_token_map.md](docs/model_and_token_map.md): model names, mapped experts, and token application links
- [docs/expert_model_capability_map_zh.md](docs/expert_model_capability_map_zh.md): current expert capability boundaries, including Kling route coverage and constraints

## 🛠️ TODO

- [ ] Support more image-generation and video-generation models
- [ ] Add more creativity-related skills
- [x] Support more LLM providers
- [ ] Support more channels
