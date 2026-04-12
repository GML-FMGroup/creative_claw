<div align="center">
  <img src="asset/logo-2.png" alt="CreativeClaw" width="420">
  <h1>CreativeClaw</h1>
  <p><strong>简体中文</strong> · <a href="README.md">English</a></p>
  <p><strong> 对话式创意生成，你的个人创意助理。</strong></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
    <img src="https://img.shields.io/badge/google--adk-1.29.0-green" alt="Google ADK">
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Web%20%7C%20Telegram%20%7C%20Feishu-orange" alt="Channels">
  </p>
</div>

CreativeClaw 可以把自然语言请求转成创意产出。 你可以通过对话让它生成图像、视频、分析参考图、重写提示词、搜索辅助信息等。 最简单的方式就是本地 CLI：配一个 API Key，跑一条命令就能开始。

## 为什么用 CreativeClaw
- **同一个界面下面使用众多提供商的模型**： 当前图像生成可以使用 nano banana、seedance ，视频生成可以使用 Veo、seedance。
- **基于对话的反复迭代**：可以先发参考图让它分析，再继续追问和修改。
- **面向创意工作流**：图像生成、图像编辑、图像理解、提示词提取、目标定位、搜索、视频生成都是一等能力。
- **支持聊天工具接入**：可以先从 CLI 开始，后续再接本地网页、Telegram 或飞书。
- **可通过 skill 扩展**：本地 skill 可以继续教它新流程，比如 MiniMax CLI skill。
- **基于coding的图像操作**：生成基于OpenCV的代码来批量处理图像、视频等素材。

## 你可以拿它做什么

- 根据一句话生成海报风、产品风、概念风图片
- 对已有图片做修改或变体
- 理解参考图的内容或风格
- 把参考图转成更好的生成提示词
- 在图中做目标定位
- 搜索资料、灵感和补充信息
- 根据文本或参考图生成短视频
- 通过 `mmx` 使用 MiniMax 的模型，尤其是视频、音乐、语音模型。

## 快速开始

最推荐的入门方式是 CLI 聊天入口。

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

### 2. 先填写最少必需的 API Key

默认配置下，先填这个就可以体验：

```env
OPENAI_API_KEY="your_api_key_here"
```

说明：

- 这已经足够体验默认的本地聊天流程。
- 图片、视频、搜索、特定 provider 等功能，只有你用到时才需要补充额外 key。
- 更完整的配置说明见 [docs/development.md](docs/development.md)。

### 3. 开始聊天

如果你已经执行过 `pip install -e .`，就可以直接使用 console script：

```bash
creative-claw chat cli
```

如果你还没有安装这个 console script，就先这样运行：

```bash
python -m src.creative_claw_cli chat cli
```

也可以直接发一条单次请求：

```bash
creative-claw chat cli --message "Generate a poster-style cat image"
```

或者带图提问：

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

## 支持的渠道

CreativeClaw 当前支持：

- **CLI Chat**：最适合第一次上手
- **本地 Web Chat**：浏览器里聊天，能直接看到进度和产物预览
- **Telegram**：在 Telegram 里使用
- **飞书**：在飞书里使用

### 本地 Web Chat

```bash
creative-claw chat web
```

默认会监听在 `http://127.0.0.1:18900`。

也可以显式指定：

```bash
creative-claw chat web --host 127.0.0.1 --port 18900 --title "CreativeClaw Web Chat"
```

### Telegram

在 `.env` 填好 Telegram 相关配置后：

```bash
creative-claw chat telegram
```

### 飞书

在 `.env` 填好飞书相关配置后：

```bash
creative-claw chat feishu
```

说明：

- `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是主要必需项。
- `FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN` 一般 **不需要**。只有你在飞书平台里开启了对应安全配置时才需要填写。
- Web Chat 也支持通过环境变量配置：`WEB_HOST`、`WEB_PORT`、`WEB_TITLE`、`WEB_OPEN_BROWSER`。
- 过渡期间，`apps/art_cli.py`、`apps/run_telegram.py`、`apps/run_feishu.py` 仍然保留为兼容包装层。

## MiniMax CLI Skill

CreativeClaw 现在内置了一个项目级的 MiniMax skill：`skills/minimax-cli-skill/SKILL.md`。

适合这些场景：

- 你明确想用 MiniMax 或 `mmx`
- 你想用 MiniMax 生成音乐
- 你想用 MiniMax 做语音合成
- 你需要走 MiniMax 的文件上传或 `file_id` 相关流程

MiniMax CLI 需要鉴权。对 agent 场景，推荐直接用 API Key 登录：

```bash
# Install CLI globally for terminal use
npm install -g mmx-cli
# Authenticate
mmx auth login --api-key sk-xxxxx
mmx auth status --output json --non-interactive
```

通常只有你明确需要 MiniMax 特定能力时，才需要用这条 skill，比如音乐、语音或 `mmx` 专属流程。

## 适合什么人

如果你想要下面这些体验，CreativeClaw 会比较适合：

- 一个偏创意工作的 AI 助手，尤其适合图片和提示词相关任务
- 一个命令行优先、但可以继续接聊天渠道的使用方式
- 一个可以先直接用起来，之后再慢慢扩展的系统
- 一个后面能继续长出更复杂工作流的工具

## 更多文档

- [docs/development.md](docs/development.md)：架构、环境、凭证、测试、开发者说明

## 当前状态

CreativeClaw 还在持续迭代中。当前最适合的使用方式是：

- 先从 CLI Chat 开始
- 先跑图片和提示词相关流程
- 只开启你真的需要的 provider 和聊天渠道

如果你想要最顺畅的第一次体验，建议先从 `creative-claw chat cli` 和 `OPENAI_API_KEY` 开始，跑通后再逐步增加其他能力。
