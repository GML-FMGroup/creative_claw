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

Legacy compatibility wrappers are still available under `apps/`.

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

Useful config sections:

- `workspace`: runtime file root
- `llm.provider` / `llm.model`: default text model selection
- `providers.*`: credentials and API base settings for text LLM providers
- `services.*`: extra keys for image/video/search integrations
- `channels.*`: Telegram, Feishu, and Web channel defaults

Credential resolution rule:

- `conf.json` is the primary source of truth
- if an API key field in `conf.json` is an empty string, runtime falls back to the matching environment variable
- this fallback applies to key-like secret fields, not to general settings such as `workspace` or `api_base`
- after config load, runtime also syncs configured secrets back into process environment variables for SDK compatibility

Current provider env-fallback coverage:

- auto-fallback is implemented for `openai`, `anthropic`, `gemini`, `groq`, `deepseek`, `dashscope`, `zhipu`, `moonshot`, `minimax`, `mistral`, `stepfun`, and `qianfan`
- `gemini` accepts `GOOGLE_API_KEY` as the primary env var and also accepts `GEMINI_API_KEY` as a compatibility alias
- providers such as `openrouter`, `vllm`, `ollama`, `siliconflow`, `volcengine`, `byteplus`, `azure_openai`, and `custom` can still use `providers.<name>.api_key` from `conf.json`, but `apply_env_fallbacks()` does not currently auto-import them from provider-specific environment variables

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
    "brave_api_key": ""
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
| `services.ark_api_key` | Volcengine Ark key | Seedream and Seedance paths |
| `services.dds_api_key` | DeepDataSpace key | Image grounding and image segmentation |
| `services.serper_api_key` | Serper key | `SearchAgent` image mode |
| `services.brave_api_key` | Brave search key | Built-in web search tool |
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

- `services.ark_api_key`: Seedream image generation, image editing, and `VideoGenerationAgent` (`seedance`)
- `services.dds_api_key`: `ImageGroundingAgent` and `ImageSegmentationAgent`
- `services.serper_api_key`: `SearchAgent` image mode
- `services.brave_api_key`: built-in `web_search` tool
- `providers.gemini.api_key`: Gemini-backed image and VEO paths

Additional compatibility aliases used by runtime code:

- `ImageGroundingAgent`: accepts `DDS_API_KEY`, `DDS_TOKEN`, and `DINO_XSEEK_TOKEN`
- `ImageSegmentationAgent`: accepts `DDS_API_KEY` and `DDS_TOKEN`

Credentials not stored in `conf.json` today:

- `ThreeDGenerationAgent` (`hy3d`) reads Tencent Cloud credentials directly from environment variables:
  - `TENCENTCLOUD_SECRET_ID`
  - `TENCENTCLOUD_SECRET_KEY`
  - optional `TENCENTCLOUD_SESSION_TOKEN`
  - optional `TENCENTCLOUD_REGION`

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

`VideoGenerationAgent` supports two providers:

- `seedance`: default provider, requires `services.ark_api_key`
- `veo`: Google VEO provider, requires `providers.gemini.api_key`

Supported modes:

- `prompt`
- `first_frame`
- `first_frame_and_last_frame`
- `reference_asset`
- `reference_style`

Example `invoke_agent` payloads:

```json
{"prompt":"A cinematic orange cat surfing on neon waves at sunset","provider":"seedance","mode":"prompt","aspect_ratio":"16:9"}
```

```json
{"input_path":"inbox/cli/session_1/cat.png","prompt":"Animate this cat blinking and turning toward the camera","provider":"veo","mode":"first_frame","aspect_ratio":"9:16","resolution":"720p"}
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
