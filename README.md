<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw</h1>
  <p><a href="README_zh.md">简体中文</a> · <strong>English</strong></p>
  <p><strong>Conversational creative generation, your personal creative assistant.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw is a creative agent built on Google ADK. It brings chat, image generation, image understanding, prompt optimization, search, video generation, and multi-channel access into one workflow so you can keep iterating on a creative task without switching tools at every step.

If you only want to get started quickly, begin from the CLI: prepare one API key, run one command, and start chatting.

## Why CreativeClaw

- **Built for creative workflows**: image generation, image editing, image understanding, prompt extraction, grounding, search, and video generation are first-class capabilities.
- **Supports multiple models and providers**: image and video flows can use different providers so you can balance quality, speed, and cost.
- **Good for iterative work**: send a reference image, ask for analysis, then keep refining the prompt or the output in follow-up turns.
- **One capability set, multiple surfaces**: start from the CLI, then add local Web chat, Telegram, or Feishu when needed.
- **Extensible by design**: skills let you plug in specialized workflows such as MiniMax CLI.
- **Not only generation**: besides creating content directly, it can also help produce OpenCV / Python scripts for batch processing of image and video assets.

## What You Can Do

- Generate poster-style, product-style, or concept-style images from text
- Edit, expand, restyle, or vary an existing image
- Analyze the content, composition, and style of a reference image
- Turn a reference image into a stronger generation prompt
- Ground objects inside an image
- Search for references, ideas, and supporting information
- Generate short videos from text or image-guided prompts
- Produce scripts for batch image or video asset processing
- Use `mmx` for MiniMax-specific workflows, especially video, music, speech, and file upload

## Quick Start

### 1. Set up the environment

```bash
git clone https://github.com/GML-FMGroup/creative_claw.git
cd creative_claw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.template .env
```

### 2. Add the minimum required API key

For the default setup, this is enough:

```env
OPENAI_API_KEY="your_api_key_here"
```

Notes:

- This is enough to try the default CLI chat flow.
- Image, video, search, and some provider-specific capabilities only need extra credentials when you actually use them.
- For the full environment and credential matrix, see [docs/development.md](docs/development.md).

### 3. Start chatting

If you already ran `pip install -e .`, you can use the installed command directly:

```bash
creative-claw chat cli
```

If you have not installed the console script yet, use the module entrypoint:

```bash
python -m src.creative_claw_cli chat cli
```

You can also send a single one-off request:

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

Ask with an image attachment:

```bash
creative-claw chat cli \
  --message "Describe this image and write a better prompt for recreating it" \
  --attachment ./example.png
```

## Common Usage

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

## Supported Channels

CreativeClaw currently supports:

- **CLI Chat**: the best place to start
- **Local Web Chat**: browser-based chat with realtime progress and artifact previews
- **Telegram**: chat from Telegram
- **Feishu**: chat from Feishu

### Local Web Chat

```bash
creative-claw chat web
```

The default address is `http://127.0.0.1:18900`.

You can also override it explicitly:

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

### Telegram

After setting the Telegram-related variables in `.env`:

```bash
creative-claw chat telegram
```

### Feishu

After setting the Feishu-related variables in `.env`:

```bash
creative-claw chat feishu
```

Additional notes:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are the main Feishu credentials.
- `FEISHU_ENCRYPT_KEY` and `FEISHU_VERIFICATION_TOKEN` are only needed if the matching security options are enabled in the Feishu platform.
- Web chat can also be configured through environment variables: `WEB_HOST`, `WEB_PORT`, `WEB_TITLE`, and `WEB_OPEN_BROWSER`.

## MiniMax CLI Skill

CreativeClaw ships with a project-level MiniMax skill at `skills/minimax-cli-skill/SKILL.md`.

Use it when:

- you explicitly want MiniMax or `mmx`
- you want MiniMax music generation
- you want MiniMax speech synthesis
- you need MiniMax file upload or `file_id`-based follow-up workflows

For agent-style usage, API key login is the recommended setup:

```bash
npm install -g mmx-cli
mmx auth login --api-key sk-xxxxx
mmx auth status --output json --non-interactive
```

In practice, you only need this skill when you explicitly want MiniMax-specific capabilities.

## Who This Is For

CreativeClaw is a good fit if you want:

- a creative AI assistant for image, video, and prompt-heavy work
- to start from the command line, then add Web or chat channels later
- to get something working quickly and expand models, providers, and workflows gradually
- to keep multi-step creative tasks inside one conversation

## More Docs

- [docs/development.md](docs/development.md): architecture, environment, credentials, tests, and development notes

## Current Status

CreativeClaw is still evolving. The smoothest way to use it today is:

- start with `creative-claw chat cli`
- begin with image, reference-analysis, and prompt-related workflows
- enable only the providers and channels you actually need

For the smoothest first run, start with `OPENAI_API_KEY` and the CLI chat, then expand from there.
