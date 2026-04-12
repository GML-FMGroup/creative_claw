<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw：你的个人创意助理</h1>
  <p><strong>简体中文</strong> · <a href="README.md">English</a></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw 是一个基于多自主智能体的创意 Agent，把对话、图像生成、图像理解、提示词优化、搜索、视频生成和多渠道接入放在同一个工作流里，让你可以围绕一个创意任务连续迭代，而不是每一步都换一个工具。

## 📰 News
 - 2026-04-13: 增加支持的 LLM provider数量到20个；支持图像分割。
 - 2026-04-12: v0.1.1，支持基本的图像、视频操作，支持 web、cli、飞书以对话形式使用。


## ✨ CreativeClaw 的特性

- **面向创意工作流**：图像生成、图像编辑、图像理解、提示词提取、目标定位、搜索、视频生成都是一等能力。
- **支持多种模型与提供商**：图像和视频相关能力可以接不同 provider，方便按质量、速度和成本选择。
- **基于对话的反复迭代**：可以先发参考图让它分析，再继续追问、改图、补提示词。
- **可继续扩展**：通过 skills 可以把更多专用流程接进来，比如 MiniMax CLI。
- **基于coding的素材处理**：除了直接生成内容，也可以让它帮你用 OpenCV / Python 脚本 来批量处理素材。

## 🤖 支持模型

### 🖼️ 图像生成
 - Nano Banana Pro
 - Seedream
 - GPT-image

### 🎬 视频生成
 - Seedance
 - Veo

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
- 读取顺序是：`conf.json` 优先；如果某个 API key 在 `conf.json` 里是空字符串，运行时会回退到同名环境变量。
- 第一轮文本 LLM provider 已支持：`openai`、`anthropic`、`gemini`、`openrouter`、`deepseek`、`groq`、`zhipu`、`dashscope`、`vllm`、`ollama`、`moonshot`、`minimax`、`mistral`、`stepfun`、`siliconflow`、`volcengine`、`byteplus`、`qianfan`、`azure_openai`、`custom`。
- 更完整的环境与凭证说明见 [docs/development.md](docs/development.md)。

完整模板参考：

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
  }
}
```

常用字段解释：

- `workspace`：所有上传文件和生成产物的根目录。
- `llm.provider`：默认文本模型提供商，orchestrator 和文本专家默认走这里。
- `llm.model`：默认模型名。大多数情况下只写 provider 内部模型名，不需要再手动写前缀。
- `providers.<name>.api_key`：对应 provider 的认证 key。
- `providers.<name>.api_base`：OpenAI 兼容接口或代理地址，常见于 `custom`、`azure_openai`、私有网关。
- `providers.<name>.api_version`：主要给 `azure_openai` 这类 provider 使用。
- `providers.<name>.extra_headers`：给需要额外 header 的网关或代理用。
- `ollama.api_base` 默认会预填本地地址 `http://localhost:11434/v1`，适合本机 Ollama 直接接入。
- `openrouter.api_base`、`azure_openai.api_base`、`custom.api_base` 会在 `init` 时直接写进模板，便于照着改。
- `services.*`：非文本 LLM 能力依赖的额外服务 key，比如图像、视频、搜索。
- `channels.*`：Telegram、飞书、本地 Web 的默认启动参数。

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
mmx auth status --output json --non-interactive
```
> Requires [Node.js](https://nodejs.org) 18+

> **Requires a MiniMax Token Plan** — [Global](https://platform.minimax.io/subscribe/token-plan) · [CN](https://platform.minimaxi.com/subscribe/token-plan)




## 📚 更多文档

- [docs/development.md](docs/development.md)：架构、环境、凭证、测试和开发说明
- [docs/model_and_token_map.md](docs/model_and_token_map.md)：模型名、对应 expert 和 token 申请链接

## 🛠️ TODO
 - 支持更多图像生成、视频生成模型
 - 增加更多创意相关 skill
 - 支持更多LLM provider
 - 支持更多 channel
