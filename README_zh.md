<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw</h1>
  <p><strong>简体中文</strong> · <a href="README.md">English</a></p>
  <p><strong>对话式创意生成，你的个人创意助理。</strong></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw 是一个基于 Google ADK 的创意 Agent。它把对话、图像生成、图像理解、提示词优化、搜索、视频生成和多渠道接入放在同一个工作流里，让你可以围绕一个创意任务连续迭代，而不是每一步都换一个工具。

如果你只想先跑起来，最简单的方式就是从 CLI 开始：准备一个 API Key，执行一条命令，就可以开始聊天。

## 为什么用 CreativeClaw

- **面向创意工作流**：图像生成、图像编辑、图像理解、提示词提取、目标定位、搜索、视频生成都是一等能力。
- **支持多种模型与提供商**：图像和视频相关能力可以接不同 provider，方便按质量、速度和成本选择。
- **适合反复迭代**：可以先发参考图让它分析，再继续追问、改图、补提示词。
- **同一套能力，多种入口**：先从 CLI 开始，后续可以接本地 Web、Telegram 和飞书。
- **可继续扩展**：通过 skills 可以把更多专用流程接进来，比如 MiniMax CLI。
- **不仅能生成，还能做处理**：除了直接生成内容，也可以让它帮你产出用于批量处理素材的 OpenCV / Python 脚本。

## 你可以拿它做什么

- 根据一句话生成海报风、产品风、概念风图片
- 对已有图片做修改、扩图、风格变化或变体
- 分析参考图的内容、构图和风格
- 把参考图转成更好的生成提示词
- 在图中做目标定位
- 搜索资料、灵感和补充信息
- 根据文本或参考图生成短视频
- 生成用于批量处理图像或视频素材的脚本
- 通过 `mmx` 走 MiniMax 相关流程，尤其是视频、音乐、语音和文件上传

## 快速开始

### 1. 初始化环境

```bash
git clone https://github.com/GML-FMGroup/creative_claw.git
cd creative_claw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.template .env
```

### 2. 填写最少必需的 API Key

默认配置下，先填这个就够了：

```env
OPENAI_API_KEY="your_api_key_here"
```

说明：

- 这已经足够体验默认的 CLI 聊天流程。
- 图片、视频、搜索和某些特定 provider 只在用到时才需要额外凭证。
- 更完整的环境与凭证说明见 [docs/development.md](docs/development.md)。

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

## 常见用法

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

## 支持的接入渠道

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

在 `.env` 里填好 Telegram 相关配置后：

```bash
creative-claw chat telegram
```

### 飞书

在 `.env` 里填好飞书相关配置后：

```bash
creative-claw chat feishu
```

补充说明：

- `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是飞书接入的主要必填项。
- `FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN` 只有在飞书平台里开启对应安全选项时才需要。
- Web Chat 也支持通过环境变量配置：`WEB_HOST`、`WEB_PORT`、`WEB_TITLE`、`WEB_OPEN_BROWSER`。

## MiniMax CLI Skill

CreativeClaw 内置了一个项目级 MiniMax skill：`skills/minimax-cli-skill/SKILL.md`。

适合这些场景：

- 你明确想用 MiniMax 或 `mmx`
- 你想用 MiniMax 生成音乐
- 你想用 MiniMax 做语音合成
- 你需要走 MiniMax 的文件上传或 `file_id` 相关流程

对 agent 场景，推荐直接用 API Key 登录：

```bash
npm install -g mmx-cli
mmx auth login --api-key sk-xxxxx
mmx auth status --output json --non-interactive
```

通常只有你明确需要 MiniMax 特定能力时，才需要启用这条 skill。

## 适合什么人

CreativeClaw 比较适合这些使用方式：

- 想要一个面向图片、视频和提示词任务的创意型 AI 助手
- 想先从命令行开始，再按需接入 Web 或聊天渠道
- 想先快速跑通，再逐步补充更多模型、provider 和工作流
- 想把多步骤创意任务收拢到同一个对话里完成

## 更多文档

- [docs/development.md](docs/development.md)：架构、环境、凭证、测试和开发说明

## 当前状态

CreativeClaw 还在持续迭代中。当前最顺手的使用方式是：

- 先从 `creative-claw chat cli` 开始
- 先跑图片、参考图理解和提示词相关流程
- 只开启你当前真正需要的 provider 和聊天渠道

如果你想要最顺畅的第一次体验，建议先从 `OPENAI_API_KEY` 和 CLI Chat 开始，跑通后再逐步增加其他能力。
