# CreativeClaw Development Guide

This document is for contributors, maintainers, and advanced users who want implementation details.

If you only want to try the product, start from [../README.md](../README.md).

## Architecture

CreativeClaw is a channel-oriented creative agent system built on Google's Agent Development Kit (ADK).

Core pieces:

- `Orchestrator`: the primary user-facing agent
- `invoke_agent(agent_name, prompt)`: the expert delegation entrypoint
- `runtime/expert_dispatcher.py`: normalizes expert parameters, creates child sessions, runs experts, and merges results back
- `workspace/`: the filesystem source of truth for uploaded and generated files
- channel adapters: Local CLI, local Web chat, Telegram, and Feishu

Workspace behavior:

- uploaded files are staged into `workspace/inbox/...`
- generated outputs are written into `workspace/generated/...`

## Included Channels

- Unified local CLI: `creative-claw chat local`
- Unified local Web chat: `creative-claw chat web`
- Unified Telegram runner: `creative-claw chat telegram`
- Unified Feishu runner: `creative-claw chat feishu`

Module fallback before installing the console script:

- `python -m src.creative_claw_cli chat local`
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
cp .env.template .env
```

If you already have the repository-local virtual environment, reuse it instead of recreating it.

Important:

- `.env` is ignored by git and should never be committed
- only `.env.template` should be committed
- rotate any secret that was ever shared outside your machine

## Credential Matrix

The default orchestrator model in `conf/jsons/system.json` is `openai/gpt-5.4`, so `OPENAI_API_KEY` is the only required credential for a minimal text-first setup.

Feature-specific capabilities require additional keys only when those capabilities are used:

| Env var | Required when | Used by | Official URL |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Required for the default orchestrator model | Main orchestrator and any feature using the default system model | [OpenAI API keys](https://platform.openai.com/api-keys) |
| `GOOGLE_API_KEY` | Required for Gemini-backed image and VEO paths | `ImageGenerationAgent`, `ImageEditingAgent`, `ImageUnderstandingAgent`, `ImageToPromptAgent`, `VideoGenerationAgent` (`veo`) | [Google AI Studio API keys](https://aistudio.google.com/app/apikey) |
| `ARK_API_KEY` | Optional | Seedream image generation, image editing, and `VideoGenerationAgent` (`seedance`) | [Volcengine Ark console](https://console.volcengine.com/ark) |
| `DDS_API_KEY` | Optional | `ImageGroundingAgent` via DeepDataSpace DINO-XSeek | [DeepDataSpace cloud console](https://cloud.deepdataspace.com/) |
| `SERPER_API_KEY` | Optional | `SearchAgent` image mode | [Serper](https://serper.dev/) |
| `BRAVE_API_KEY` | Optional | Built-in `web_search` tool | [Brave Search API](https://api.search.brave.com/app/keys) |
| `TELEGRAM_BOT_TOKEN` | Required only for Telegram channel | `creative-claw chat telegram` | [Telegram Bot token guide](https://core.telegram.org/bots/tutorial#obtain-your-bot-token) |
| `TELEGRAM_ALLOW_FROM` | Recommended for Telegram channel | Telegram allowlist | [Telegram Bot API docs](https://core.telegram.org/bots/api) |
| `FEISHU_APP_ID` | Required only for Feishu channel | `creative-claw chat feishu` | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_APP_SECRET` | Required only for Feishu channel | `creative-claw chat feishu` | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_ENCRYPT_KEY` | Optional, only if Feishu event encryption is enabled in the platform | Feishu event subscription security | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_VERIFICATION_TOKEN` | Optional, only if token verification is enabled in the platform | Feishu event subscription verification | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_ALLOW_FROM` | Recommended for Feishu channel | Feishu allowlist | [Feishu Open Platform](https://open.feishu.cn/app) |

Notes:

- `SERPER_API_KEY` and `BRAVE_API_KEY` power different search paths
- `GOOGLE_API_KEY` is not required for a minimal text-only run
- `DASHSCOPE_API_KEY` is not required by the current tracked runtime paths

## Web Chat Notes

The local Web chat channel is configured through these optional environment variables:

| Env var | Default | Purpose |
| --- | --- | --- |
| `WEB_HOST` | `127.0.0.1` | Host interface for the local Web chat server |
| `WEB_PORT` | `18900` | Port for the local Web chat server |
| `WEB_TITLE` | `CreativeClaw Web Chat` | Browser page title shown in the UI |
| `WEB_OPEN_BROWSER` | `false` | Whether to try opening the browser automatically on startup |

CLI flags can override these values for one run:

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

## Feishu Notes

For the current implementation:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are the main required values
- `FEISHU_ENCRYPT_KEY` and `FEISHU_VERIFICATION_TOKEN` are not required for a basic test setup
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

- `seedance`: default provider, requires `ARK_API_KEY`
- `veo`: Google VEO provider, requires `GOOGLE_API_KEY`

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
{"input_path":"inbox/local/session_1/cat.png","prompt":"Animate this cat blinking and turning toward the camera","provider":"veo","mode":"first_frame","aspect_ratio":"9:16","resolution":"720p"}
```

## Running

### Local CLI

```bash
cd creative_claw
source ./.venv/bin/activate
creative-claw chat local
```

Single message:

```bash
creative-claw chat local --message "Generate a poster-style cat image"
```

Single message with attachments:

```bash
creative-claw chat local \
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

Supported across the local CLI, local Web chat, Telegram, and Feishu channels:

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
- commit only `.env.template`, never a real `.env`
- verify documented credentials against the actual runtime code before release
- prefer feature-gated credential checks at call time instead of import-time crashes
