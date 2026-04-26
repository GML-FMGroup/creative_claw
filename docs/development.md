# CreativeClaw Development Guide

This document is for contributors, maintainers, and advanced users who want implementation details.

If you only want to try the product, start from [../README.md](../README.md).

For a compact user-facing list of concrete model names, mapped experts, and token application links, see [model_and_token_map.md](model_and_token_map.md).

## Architecture

CreativeClaw is a channel-oriented creative agent system built on Google's Agent Development Kit (ADK).

Core pieces:

- `Orchestrator`: the primary user-facing agent
- `invoke_agent(agent_name, prompt)`: the expert delegation entrypoint
- `runtime/expert_dispatcher.py`: normalizes expert parameters, creates child sessions, runs experts, and merges results back
- `~/.creative-claw/workspace/`: the filesystem source of truth for uploaded and generated files
- channel adapters: CLI chat, local Web chat, Telegram, and Feishu

Workspace behavior:

- uploaded files are staged into `workspace/inbox/...`
- generated outputs are written into `workspace/generated/...`

## Included Channels

- Unified CLI chat: `creative-claw chat cli`
- Unified local Web chat: `creative-claw chat web`
- Unified Telegram runner: `creative-claw chat telegram`
- Unified Feishu runner: `creative-claw chat feishu`

Module fallback before installing the console script:

- `python -m src.creative_claw_cli chat cli`
- `python -m src.creative_claw_cli chat web`
- `python -m src.creative_claw_cli chat telegram`
- `python -m src.creative_claw_cli chat feishu`

## Environment Setup

```bash
cd creative_claw
python3.12 -m venv .venv
source ./.venv/bin/activate
pip install -r requirements.txt
pip install -e .
creative-claw init
```

If you already have the repository-local virtual environment, reuse it instead of recreating it.

Important:

- runtime config now lives in `~/.creative-claw/conf.json`
- the default workspace is `~/.creative-claw/workspace`
- image, video, and channel credentials should be stored in `conf.json`, not in repository-local env files

## Runtime Config

The runtime config file is `~/.creative-claw/conf.json`.

The default text setup is:

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

For beginners, a practical `conf.json` usually looks like this:

```json
{
  "workspace": "~/.creative-claw/workspace",
  "llm": {
    "provider": "openai",
    "model": "gpt-5.4",
    "temperature": 0.1,
    "max_tokens": 8192
  },
  "providers": {
    "openai": {
      "api_key": "sk-your-openai-key",
      "api_base": null,
      "api_version": null,
      "extra_headers": {}
    },
    "gemini": {
      "api_key": "your-google-or-gemini-key",
      "api_base": null,
      "api_version": null,
      "extra_headers": {}
    },
    "anthropic": {
      "api_key": "your-anthropic-key",
      "api_base": null,
      "api_version": null,
      "extra_headers": {}
    }
  },
  "services": {
    "ark_api_key": "your-ark-key",
    "dds_api_key": "your-dds-key",
    "serper_api_key": "your-serper-key",
    "brave_api_key": "your-brave-key",
    "volcengine_app_id": "your-volcengine-app-id",
    "volcengine_access_token": "your-volcengine-access-token",
    "tencentcloud_secret_id": "your-tencentcloud-secret-id",
    "tencentcloud_secret_key": "your-tencentcloud-secret-key",
    "tencentcloud_session_token": "",
    "tencentcloud_region": "ap-guangzhou"
  },
  "channels": {
    "telegram": {
      "bot_token": "your-telegram-bot-token",
      "allow_from": ["123456789"]
    },
    "feishu": {
      "app_id": "your-feishu-app-id",
      "app_secret": "your-feishu-app-secret",
      "encrypt_key": "",
      "verification_token": "",
      "allow_from": ["ou_xxxxx"]
    },
    "web": {
      "host": "127.0.0.1",
      "port": 18900,
      "open_browser": false,
      "title": "CreativeClaw Web Chat"
    }
  }
}
```

How to fill it:

- If you mainly use OpenAI text generation, fill `providers.openai.api_key`.
- If you want Gemini image or VEO video support, fill `providers.gemini.api_key`.
- If you want Anthropic text models, fill `providers.anthropic.api_key`.
- If you want Seedream, Seedance, Seed3D, Hyper3D, or Hitem3D, fill `services.ark_api_key`.
- If you want image grounding or segmentation, fill `services.dds_api_key`.
- If you want SearchAgent image search, fill `services.serper_api_key`.
- If you want the built-in `web_search` tool, fill `services.brave_api_key`.
- If you want `SpeechSynthesisExpert` TTS, fill `services.volcengine_app_id` and `services.volcengine_access_token`. The default resource is `seed-tts-2.0`, and voices may be selected by `speaker`, `voice_type`, or `voice_name`.
- If you want `SpeechRecognitionExpert` ASR or subtitle capabilities, fill `services.volcengine_app_id` and `services.volcengine_access_token`. The service activation entry is the [Volcengine speech console](https://console.volcengine.com/speech/app).
- If you want Tencent Hunyuan 3D generation, fill `services.tencentcloud_secret_id` and `services.tencentcloud_secret_key`.
- `services.tencentcloud_session_token` is optional. Most personal API-key setups can leave it empty.
- `services.tencentcloud_region` is optional. If you are not sure, use `ap-guangzhou`.
- If you do not use Telegram or Feishu, you can leave those channel fields empty.
- `allow_from` means the user allow-list. Only listed user ids can access the bot.

Useful config sections:

- `workspace`: runtime file root
- `llm.provider` / `llm.model`: default text model selection
- `providers.*`: credentials and API base settings for text LLM providers
- `services.*`: extra keys for image/video/search integrations
- `channels.*`: Telegram, Feishu, and Web channel defaults

Speech synthesis, recognition, and subtitle service grants:

- `SpeechSynthesisExpert` routes text-to-speech through Volcengine streaming TTS. The default resource is `seed-tts-2.0`, default voice is Vivi 2.0 (`zh_female_vv_uranus_bigtts`), and user-provided Seed TTS 2.0 voices are validated against the official 2.0 voice list.
- `SpeechRecognitionExpert` routes `task=asr` and `task=subtitle` through Volcengine speech services.
- In addition to `services.volcengine_app_id` and `services.volcengine_access_token`, the current backend requires these Volcengine resource grants:
  - `seed-tts-2.0`: required for the default TTS path
  - `volc.bigasr.auc_turbo`: required for `task=asr`
  - `vc.async.default`: required for subtitle generation from audio or video
  - `volc.ata.default`: required for subtitle timing when `subtitle_text` or `audio_text` is provided
- Open or grant these resources from the [Volcengine speech console](https://console.volcengine.com/speech/app).
- If a grant is missing, the live API usually returns `requested resource not granted` or `requested grant not found`.
- The resource names above align with the current CreativeClaw backend routes and the official Volcengine speech product docs / live validation responses.

Credential resolution rule:

- `conf.json` is the primary source of truth
- if an API key field in `conf.json` is an empty string, runtime falls back to the matching environment variable
- this fallback applies to key-like secret fields, not to general settings such as `workspace` or `api_base`
- after config load, runtime also syncs configured secrets back into process environment variables for SDK compatibility

Current provider env-fallback coverage:

- auto-fallback is implemented for `openai`, `anthropic`, `gemini`, `groq`, `deepseek`, `dashscope`, `zhipu`, `moonshot`, `minimax`, `mistral`, `stepfun`, and `qianfan`
- `gemini` accepts `GOOGLE_API_KEY` as the primary env var and also accepts `GEMINI_API_KEY` as a compatibility alias
- providers such as `openrouter`, `vllm`, `ollama`, `siliconflow`, `volcengine`, `byteplus`, `azure_openai`, and `custom` can still use `providers.<name>.api_key` from `conf.json`, but `apply_env_fallbacks()` does not currently auto-import them from provider-specific environment variables

Common environment variables:

- text providers: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ZAI_API_KEY`, `MOONSHOT_API_KEY`, `MINIMAX_API_KEY`, `MISTRAL_API_KEY`, `STEPFUN_API_KEY`, `QIANFAN_API_KEY`
- service integrations: `ARK_API_KEY`, `DDS_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`, `VOLCENGINE_APPID`, `VOLCENGINE_ACCESS_TOKEN`
- Tencent Cloud 3D: `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY`, optional `TENCENTCLOUD_SESSION_TOKEN`, optional `TENCENTCLOUD_REGION`
- channel credentials: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOW_FROM`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_ENCRYPT_KEY`, `FEISHU_VERIFICATION_TOKEN`, `FEISHU_ALLOW_FROM`

Reference fuller template:

```json
{
  "workspace": "~/.creative-claw/workspace",
  "llm": {
    "provider": "openai",
    "model": "gpt-5.4",
    "temperature": 0.1,
    "max_tokens": 8192
  },
  "providers": {
    "openai": {
      "api_key": "",
      "api_base": null,
      "api_version": null,
      "extra_headers": {}
    },
    "openrouter": {
      "api_key": "",
      "api_base": "https://openrouter.ai/api/v1",
      "api_version": null,
      "extra_headers": {}
    },
    "gemini": {
      "api_key": "",
      "api_base": null,
      "api_version": null,
      "extra_headers": {}
    },
    "ollama": {
      "api_key": "",
      "api_base": "http://localhost:11434/v1",
      "api_version": null,
      "extra_headers": {}
    },
    "azure_openai": {
      "api_key": "",
      "api_base": "https://your-resource.openai.azure.com",
      "api_version": "2024-10-21",
      "extra_headers": {}
    },
    "custom": {
      "api_key": "",
      "api_base": "https://your-openai-compatible-endpoint/v1",
      "api_version": null,
      "extra_headers": {}
    }
  },
  "services": {
    "ark_api_key": "",
    "dds_api_key": "",
    "serper_api_key": "",
    "brave_api_key": "",
    "tencentcloud_secret_id": "",
    "tencentcloud_secret_key": "",
    "tencentcloud_session_token": "",
    "tencentcloud_region": ""
  },
  "channels": {
    "telegram": {
      "bot_token": "",
      "allow_from": []
    },
    "feishu": {
      "app_id": "",
      "app_secret": "",
      "encrypt_key": "",
      "verification_token": "",
      "allow_from": []
    },
    "web": {
      "host": "127.0.0.1",
      "port": 18900,
      "open_browser": false,
      "title": "CreativeClaw Web Chat"
    }
  },
  "system": {
    "app_name": "CreativeClaw",
    "user_id_default": "art_user_001",
    "session_id_default_prefix": "art_session_",
    "max_iterations_orchestrator": 10,
    "log_level": "DEBUG",
    "log_file": "creative_claw_{time}.log",
    "retention": "7 days",
    "rotation": "10 MB"
  }
}
```

Field notes:

| Field | Meaning | Typical usage |
| --- | --- | --- |
| `workspace` | Root directory for runtime files | Move generated content to another disk or shared mount |
| `llm.provider` | Default text-provider name | Switch the orchestrator from OpenAI to Gemini, Anthropic, or another provider |
| `llm.model` | Default model within the provider | Example: `gpt-5.4`, `gemini-2.5-flash`, `claude-sonnet-4-5` |
| `providers.<name>.api_key` | Provider credential | Required by most hosted providers |
| `providers.<name>.api_base` | Custom API endpoint | Needed for OpenAI-compatible gateways, self-hosted services, or Azure |
| `providers.<name>.api_version` | Provider-specific API version | Mainly Azure OpenAI |
| `providers.<name>.extra_headers` | Extra HTTP headers | Enterprise proxy or custom gateway integration |
| `providers.ollama.api_base` | Local Ollama endpoint | Prefilled as `http://localhost:11434/v1` by `creative-claw init` |
| `services.ark_api_key` | Volcengine Ark key | Seedream, Seedance, Seed3D, Hyper3D, and Hitem3D paths |
| `services.dds_api_key` | DeepDataSpace key | Image grounding and image segmentation |
| `services.serper_api_key` | Serper key | `SearchAgent` image mode |
| `services.brave_api_key` | Brave search key | Built-in web search tool |
| `services.tencentcloud_secret_id` / `services.tencentcloud_secret_key` | Tencent Cloud 3D credentials | `ThreeDGenerationAgent` (`hy3d`) |
| `services.tencentcloud_session_token` | Tencent Cloud temporary-session token | Optional STS token for `ThreeDGenerationAgent` |
| `services.tencentcloud_region` | Tencent Cloud region | Optional; defaults to `ap-guangzhou` when empty |
| `channels.telegram.*` | Telegram defaults | Bot token and allow-list |
| `channels.feishu.*` | Feishu defaults | App credentials and allow-list |
| `channels.web.*` | Local web-chat defaults | Host, port, title, and browser behavior |
| `system.*` | Internal runtime defaults | Logging and session defaults; usually left as-is |

First-round text LLM providers:

- `openai`
- `anthropic`
- `gemini`
- `openrouter`
- `deepseek`
- `groq`
- `zhipu`
- `dashscope`
- `vllm`
- `ollama`
- `moonshot`
- `minimax`
- `mistral`
- `stepfun`
- `siliconflow`
- `volcengine`
- `byteplus`
- `qianfan`
- `azure_openai`
- `custom`

Feature-specific extra service keys:

- `services.ark_api_key`: Seedream image generation, image editing, `VideoGenerationAgent` (`seedance`), and `ThreeDGenerationAgent` (`seed3d`, `hyper3d`, `hitem3d`)
- `services.kling_access_key` and `services.kling_secret_key`: `VideoGenerationAgent` (`kling`)
- `services.kling_api_base`: optional Kling API base override; when omitted, the provider probes the official Beijing and Singapore gateways and caches the first working base
- `services.dds_api_key`: `ImageGroundingAgent` and `ImageSegmentationAgent`
- `services.serper_api_key`: `SearchAgent` image mode
- `services.brave_api_key`: built-in `web_search` tool
- `services.tencentcloud_secret_id` and `services.tencentcloud_secret_key`: `ThreeDGenerationAgent` (`hy3d`)
- `services.tencentcloud_session_token`: optional STS token for `ThreeDGenerationAgent`
- `services.tencentcloud_region`: optional Tencent Cloud region for `ThreeDGenerationAgent`
- `providers.gemini.api_key`: Gemini-backed image and VEO paths

Additional compatibility aliases used by runtime code:

- `ImageGroundingAgent`: accepts `DDS_API_KEY`, `DDS_TOKEN`, and `DINO_XSEEK_TOKEN`
- `ImageSegmentationAgent`: accepts `DDS_API_KEY` and `DDS_TOKEN`

## Web Chat Notes

The local Web chat channel is configured through `channels.web` in `~/.creative-claw/conf.json`:

| Field | Default | Purpose |
| --- | --- | --- |
| `host` | `127.0.0.1` | Host interface for the local Web chat server |
| `port` | `18900` | Port for the local Web chat server |
| `title` | `CreativeClaw Web Chat` | Browser page title shown in the UI |
| `open_browser` | `false` | Whether to try opening the browser automatically on startup |

CLI flags can override these values for one run:

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

## Feishu Notes

For the current implementation:

- `channels.feishu.app_id` and `channels.feishu.app_secret` are the main required values
- `channels.feishu.encrypt_key` and `channels.feishu.verification_token` are not required for a basic test setup
- only set those two values if the matching security options are enabled in the Feishu platform configuration

## MiniMax CLI Skill

CreativeClaw now includes `skills/minimax-cli-skill/SKILL.md`.

Current behavior:

- skill discovery works automatically through the skill registry
- easier triggering depends on orchestrator prompt guidance because routing is currently model-driven

For non-interactive MiniMax usage, API key login is the recommended path:

```bash
mmx auth login --api-key sk-xxxxx
mmx auth status --output json --non-interactive
```

## Video Generation Expert

`VideoGenerationAgent` supports three providers:

- `seedance`: default provider, requires `services.ark_api_key`
- `veo`: Google VEO provider, requires `providers.gemini.api_key`
- `kling`: Kling official video API, requires `services.kling_access_key` and `services.kling_secret_key`

Supported modes:

- `prompt`
- `first_frame`
- `first_frame_and_last_frame`
- `multi_reference` (`kling` only)
- `reference_asset`
- `reference_style`
- `video_extension` (`veo` only)

Important `veo` notes:

- audio is generated natively from prompt cues such as dialogue, ambience, and sound effects
- do not pass a separate audio file to `VideoGenerationAgent`
- optional agent-side control `prompt_rewrite` accepts `auto` or `off` and controls local prompt rewriting before provider dispatch
- optional `veo` controls include `resolution`, `duration_seconds`, `negative_prompt`, `person_generation`, and `seed`
- `video_extension` accepts one workspace video path through `input_path` or `input_paths`

Important `kling` notes:

- current CreativeClaw integration supports only `prompt`, `first_frame`, `first_frame_and_last_frame`, and `multi_reference`
- prompt / image-guided Kling routes now default to `kling-v3`
- `multi_reference` requires 2-4 workspace images through `input_paths`
- `multi_reference` currently follows the official `create-multi-image-to-video` schema, which supports `model_name=kling-v1-6`
- official Kling multi-reference images must be `.jpg/.jpeg/.png`, each at most 10MB, at least 300px, and within aspect ratio `1:2.5 ~ 2.5:1`
- Kling image-guided paths validate the documented input constraints but do not auto-resize or auto-crop images; let the main agent decide whether to preprocess with local image tools
- Kling does not support `reference_asset`, `reference_style`, or `video_extension` in the current integration
- optional Kling controls include `model_name`, `kling_mode` (`std|pro`), `negative_prompt`, `duration_seconds`, and `aspect_ratio`
- Kling image-guided paths send workspace images as raw base64 strings to the official video API
- when `KLING_API_BASE` is not set explicitly, the provider probes the official Beijing and Singapore gateways and caches the first working base

Example `invoke_agent` payloads:

```json
{"prompt":"A cinematic orange cat surfing on neon waves at sunset","provider":"seedance","mode":"prompt","aspect_ratio":"16:9"}
```

```json
{"input_path":"inbox/cli/session_1/cat.png","prompt":"Animate this cat blinking and turning toward the camera","provider":"veo","mode":"first_frame","aspect_ratio":"9:16","resolution":"720p"}
```

```json
{"input_paths":["inbox/cli/session_1/look_a.png","inbox/cli/session_1/look_b.png"],"prompt":"Keep the same subject and motion language across both references","provider":"kling","mode":"multi_reference","duration_seconds":10,"kling_mode":"pro","model_name":"kling-v1-6"}
```

```json
{"input_path":"generated/session_1/clip.mp4","prompt":"Continue the motion naturally with wind and crowd ambience","provider":"veo","mode":"video_extension","resolution":"720p","duration_seconds":8,"negative_prompt":"glitches, abrupt cuts","seed":123}
```

## 3D Generation Expert

`3DGeneration` supports these provider paths:

- `hy3d`: default provider, Tencent Cloud Hunyuan 3D Pro, requires Tencent Cloud credentials.
- `seed3d`: Volcengine Ark `doubao-seed3d-2-0-260328`, requires `services.ark_api_key` or `ARK_API_KEY`.
- `hyper3d`: Volcengine Ark `hyper3d-gen2-260112`, requires `services.ark_api_key` or `ARK_API_KEY`.
- `hitem3d`: Volcengine Ark `hitem3d-2-0-251223`, requires `services.ark_api_key` or `ARK_API_KEY`.

Important `seed3d` notes:

- it is image-to-3D only
- pass exactly one workspace image through `input_path` / `input_paths`, or one remote `image_url`
- optional controls are `file_format` (`glb|obj|usd|usdz`), `subdivision_level` (`low|medium|high`), `timeout_seconds`, and `interval_seconds`
- returned 3D URLs are downloaded into the workspace because provider links expire
- `hyper3d` supports English prompt-only generation or 1-5 images with optional prompt/commands; key controls include `file_format`, `mesh_mode`, `material`, `quality_override`, `subdivision_level`, `hd_texture`, and `bbox_condition`
- `hitem3d` is image-to-3D only, requires 1-4 externally accessible `image_url` / `image_urls`, and accepts parameter commands only; key controls include `file_format`, `resolution`, `face_count`, `request_type`, and `multi_images_bit`

Example `invoke_agent` payload:

```json
{"provider":"seed3d","input_path":"inbox/cli/session_1/object.png","file_format":"glb","subdivision_level":"medium"}
```

```json
{"provider":"hyper3d","prompt":"full-body sci-fi robot, hard-surface design","file_format":"glb","mesh_mode":"Raw","quality_override":150000}
```

```json
{"provider":"hitem3d","image_url":"https://example.com/front.png","file_format":"glb","resolution":"1536pro","request_type":3}
```

## Deterministic Media Operations

CreativeClaw also exposes deterministic local media-processing experts:

- `ImageBasicOperations`
- `VideoBasicOperations`
- `AudioBasicOperations`

These experts are for workspace-local file inspection and transformation, not for model generation.

System dependency note:

- image operations rely on Pillow only
- video and audio operations require both `ffmpeg` and `ffprobe` on `PATH`
- for a parameter-by-parameter quick reference, see [media_basic_operations.md](media_basic_operations.md)

Recommended `invoke_agent` payloads:

### ImageBasicOperations

Read image metadata:

```json
{"operation":"info","input_path":"inbox/cli/session_1/sample.png"}
```

Crop an image:

```json
{"operation":"crop","input_path":"inbox/cli/session_1/sample.png","left":32,"top":24,"right":640,"bottom":512}
```

Rotate an image:

```json
{"operation":"rotate","input_path":"inbox/cli/session_1/sample.png","degrees":90,"expand":true}
```

Resize an image:

```json
{"operation":"resize","input_path":"inbox/cli/session_1/sample.png","width":1024,"height":1024,"keep_aspect_ratio":true,"resample":"lanczos"}
```

Convert an image:

```json
{"operation":"convert","input_path":"inbox/cli/session_1/sample.png","output_format":"jpg","quality":90}
```

### VideoBasicOperations

Read video metadata:

```json
{"operation":"info","input_path":"inbox/cli/session_1/clip.mp4"}
```

Extract one frame:

```json
{"operation":"extract_frame","input_path":"inbox/cli/session_1/clip.mp4","timestamp":"00:00:01.500","output_format":"png"}
```

Trim one clip:

```json
{"operation":"trim","input_path":"inbox/cli/session_1/clip.mp4","start_time":"00:00:02","duration":"3.0"}
```

Concatenate two clips:

```json
{"operation":"concat","input_paths":["inbox/cli/session_1/part1.mp4","inbox/cli/session_1/part2.mp4"],"output_format":"mp4"}
```

Convert a clip:

```json
{"operation":"convert","input_path":"inbox/cli/session_1/clip.mp4","output_format":"mov"}
```

### AudioBasicOperations

Read audio metadata:

```json
{"operation":"info","input_path":"inbox/cli/session_1/voice.wav"}
```

Trim one clip:

```json
{"operation":"trim","input_path":"inbox/cli/session_1/voice.wav","start_time":"00:00:01","end_time":"00:00:04"}
```

Concatenate two clips:

```json
{"operation":"concat","input_paths":["inbox/cli/session_1/a.wav","inbox/cli/session_1/b.wav"],"output_format":"wav"}
```

Convert audio:

```json
{"operation":"convert","input_path":"inbox/cli/session_1/voice.wav","output_format":"mp3","bitrate":"192k","sample_rate":44100,"channels":2}
```

## Running

### Local CLI

```bash
cd creative_claw
source ./.venv/bin/activate
creative-claw chat cli
```

Single message:

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

Single message with attachments:

```bash
creative-claw chat cli \
  --message "Describe this image and write a better prompt" \
  --attachment /path/to/image.png
```

### Telegram

```bash
creative-claw chat telegram
```

### Web Chat

```bash
creative-claw chat web
```

Open the printed URL in a browser. The first iteration currently supports:

- text chat
- realtime progress updates
- generated artifact preview/download links

### Feishu

```bash
creative-claw chat feishu
```

## Chat Commands

Supported across the CLI chat, local Web chat, Telegram, and Feishu channels:

- `/help`
- `/new`

## Tests

Focused regression suite:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m unittest \
  unit_test.test_orchestrator \
  unit_test.test_runtime_session \
  unit_test.test_feishu_channel \
  unit_test.test_file_tools
```

Quick syntax check for commonly touched files:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m py_compile \
  conf/api.py \
  src/agents/orchestrator/orchestrator_agent.py \
  src/agents/experts/search/tool.py \
  unit_test/test_feishu_channel.py \
  unit_test/test_runtime_session.py
```

## Public Release Checklist

- keep public-facing prompts, comments, and examples in English
- do not document repository-local legacy environment-file setup anymore
- verify documented credentials against the actual runtime code before release
- prefer feature-gated credential checks at call time instead of import-time crashes
