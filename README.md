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
 - 2026-04-14: Support HY 3D generation.
 - 2026-04-13: Support 20 LLM providers. Support image segmentation.
 - 2026-04-12: Release v0.1.1. Supports basic image and video operations, with chat-based usage through Web, CLI, and Feishu.

## ✨ Key Features of CreativeClaw

- **Built for creative workflows**: image generation, image editing, image understanding, prompt extraction, grounding, search, and video generation are first-class capabilities.
- **Supports multiple models and providers**: image and video flows can use different providers so you can balance quality, speed, and cost.
- **Iterative through conversation**: send a reference image for analysis, then keep asking follow-up questions, editing, and refining prompts.
- **Extensible by design**: skills let you add specialized workflows such as MiniMax CLI.
- **Coding-based asset processing**: besides generating content directly, it can also help process assets in batches through OpenCV / Python scripts.

## 🏗️ Architecture

The following diagram shows the high-level architecture of CreativeClaw, including the orchestrator, expert agents, skills, and channel integrations.

![CreativeClaw architecture](asset/framework.png)

## 🤖 Supported Models

### 🖼️ Image Generation

- Nano Banana Pro
- Seedream
- GPT-image

### 🎬 Video Generation

- Seedance
- Veo

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
- [docs/model_and_token_map.md](docs/model_and_token_map.md): model names, mapped experts, and token application links

## 🛠️ TODO

- [ ] Support more image-generation and video-generation models
- [ ] Add more creativity-related skills
- [x] Support more LLM providers
- [ ] Support more channels
