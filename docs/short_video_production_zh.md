# 短视频生产 P1b 使用说明

本文档记录当前短视频生产 P1b 的可用范围、运行方式和验收检查项。P1b 在 P0 真实生成闭环和 P1a storyboard 审阅基础上，增加了 provider 分段计划和首段成片确认：用户给出任务描述后，系统先返回 storyboard，再返回 provider 资产计划；用户确认资产计划后，系统只生成一个可预览的短视频分段，等待用户确认后再继续生成后续分段或合成最终 MP4。

## 能力范围

当前 P1b 支持三类短视频：

- 产品广告短片
- 卡通短剧
- 社交媒体短片

当前默认视频模型链路：

- 默认模型：`doubao-seedance-2-0-260128`
- 快速模型：`doubao-seedance-2-0-fast-260128`
- 默认分辨率：`720p`
- 默认音频：Seedance 原生有声视频，`generate_audio=true`
- 支持输入：纯文本任务描述，或任务描述加参考图片

兼容路径：

- `VeoTtsProviderRuntime` 已实现，可以使用 Veo 生成视频片段，再使用 ByteDance / Volcengine TTS 生成 voiceover。
- 这条路径当前不是默认用户路径，也没有做成对话里可直接切换的公开选项。
- 当前限制：只支持 `16:9` / `9:16`，时长 `4/6/8` 秒，分辨率固定 `720p`。

当前 P1b-1 已做真实 provider 分段生成和 `shot_review` 暂停。仍未做自动质量评分、精细局部镜头重生成、跨分段视觉一致性增强和完整 rough cut 编辑台，这些属于后续 P1c/P1e 优化。

## 运行前准备

短视频真实生成需要 Ark API Key：

```bash
export ARK_API_KEY="your_ark_api_key"
```

如果通过飞书运行，并且本机网络使用 SOCKS 代理，需要确保依赖已安装：

```bash
pip install "python-socks[asyncio]"
```

项目依赖文件已经包含这个依赖，新环境执行 `pip install -r requirements.txt` 即可。

## CLI 文本生成短视频

启动 CLI：

```bash
creative-claw chat cli
```

示例提示词：

```text
给我做一个 8 秒以内的短视频，是关于两只猫咪的对话。
猫A: 你妈妈一个月赚多少钱？诚实说。
猫B: 嗯嗯。。两万五
猫A: 这么多
猫B: 你不是说“乘十”说吗

两只猫咪对视停顿 1 秒，然后爆笑摔倒。
不用显示字幕，但是需要有语音。
语音风格软萌萌可爱。
先给我计划，等我确认后再生成。
```

预期流程：

1. 系统返回 `storyboard_review`，展示视频类型、镜头结构、角色/产品约束、台词和音频要求。
2. 用户确认或修改 storyboard，例如回复 `确认 storyboard`。
3. 系统返回 `asset_plan_review`，展示比例、Seedance 模型、参考素材、镜头 prompt 和音频计划。
4. 用户再次确认，例如回复 `确认，9:16`。
5. 系统调用 Seedance 生成第一个 provider 分段，并返回 `shot_review` 和预览 MP4。
6. 用户确认分段后，系统继续下一个分段；如果已经没有待生成分段，则合成、校验并返回最终 MP4 路径。
7. 用户在 `shot_review` 阶段提出修改时，系统会把当前分段标记为 stale，并回到 `asset_plan_review`，不会直接悄悄重生成。

## 使用 Seedance 2.0 fast

用户可以在自然语言里明确要求快版：

```text
用 Seedance 2.0 fast 给我做一个 9:16 社交媒体短片，主题是新品咖啡上市。
需要有轻快背景音乐和女生口播。
先给我计划，确认后再生成。
```

当前 fast 模型会保持 `720p`，因为 Seedance 2.0 fast 不支持 `1080p`。

## 带参考图生成短视频

CLI 带图示例：

```bash
creative-claw chat cli \
  --message "基于这张产品图做一个 8 秒 9:16 广告短视频，突出高级感和开箱瞬间。先给我计划，确认后再生成。" \
  --attachment ./product.png
```

预期行为：

- 参考图进入短视频生产 session 的 `reference_assets`。
- Storyboard 和资产计划中会显示参考图数量。
- 用户确认后，Seedance 以 `reference_image` 方式使用参考图。

## 飞书验收检查项

启动飞书：

```bash
creative-claw chat feishu
```

验收时建议检查：

- 发送短视频任务后，飞书里出现进度卡，而不是长时间无反馈。
- 首次回复停在 storyboard 确认，不直接生成真实视频。
- 确认 storyboard 后，第二次停在资产计划确认。
- 确认资产计划后，进度卡出现生成分段、准备分段音频、分段预览已就绪等阶段，并在最终成品前停在 `shot_review`。
- 确认 `shot_review` 后，系统继续后续分段或进入最终渲染完成。
- 最终回复包含 MP4 产物路径。
- 对话中继续提出修改时，系统先分析影响范围或返回更新后的计划，不直接覆盖旧成片。

## 当前 P1b 验收样例

建议每次改短视频策略后至少跑这三类：

- 卡通短剧：两只猫咪对白，要求无字幕、有软萌语音。
- 产品广告：智能保温杯或咖啡新品，要求 8 秒、9:16、有口播和音效。
- 社交媒体短片：小红书/抖音风格，要求强开头钩子和快节奏。

如果真实模型输出质量不稳定，优先调 storyboard、资产计划中的 `visual_prompt` 和原生音频指令，不要绕回“视频 + 单独 TTS 解说”的默认链路。

P0 收口状态见 [short_video_p0_completion_zh.md](short_video_p0_completion_zh.md)。
