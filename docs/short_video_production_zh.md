# 短视频生产 P1e 使用说明

本文档记录当前短视频生产 P1e 的可用范围、运行方式和验收检查项。P1e 在 P0 真实生成闭环、P1a storyboard 审阅、P1b 分段确认、P1c 局部重生成、P1d 显式 provider/runtime 选择基础上，增加了基础质量报告：用户给出任务描述后，系统先返回 storyboard，再返回 provider 资产计划；用户确认资产计划后，系统生成可预览的短视频分段，等待用户确认后再继续生成后续分段或合成最终 MP4。最终成片后，系统会落盘可解释质量报告，帮助用户决定是否继续修改。

## 能力范围

当前 P1e 支持三类短视频：

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
- 这条路径不是默认用户路径，但用户可以在对话里明确选择 Veo 或 Veo+TTS。
- 当前限制：只支持 `16:9` / `9:16`，时长 `4/6/8` 秒，分辨率固定 `720p`。
- provider 失败不会自动切换到另一家模型；是否切换必须由用户确认。

当前 P1e 已做真实 provider 分段生成、`shot_review` 暂停、当前分段修改、显式 `shot_asset_plan` / `shot_artifact` 目标的局部过期和局部重生成、Seedance / Seedance fast / Veo+TTS 的显式选择，以及基础质量报告。仍未做跨分段视觉一致性增强、完整 rough cut 编辑台和昂贵的 LLM-as-judge 创意评分，这些属于后续质量迭代优化。

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
8. 用户确认修改后的资产计划后，系统只重生成被修改的分段；其他已确认分段继续保持可用。
9. 用户在最终成片后明确修改某个已生成分段时，orchestrator 应先调用 `analyze_revision_impact` 展示影响范围；用户确认后再用 `apply_revision` 让对应 `shot_asset_plan` / `shot_artifact` 过期，并等待资产计划确认后局部重生成。

## 使用 Seedance 2.0 fast

用户可以在自然语言里明确要求快版：

```text
用 Seedance 2.0 fast 给我做一个 9:16 社交媒体短片，主题是新品咖啡上市。
需要有轻快背景音乐和女生口播。
先给我计划，确认后再生成。
```

当前 fast 模型会保持 `720p`，因为 Seedance 2.0 fast 不支持 `1080p`。

## 使用 Veo+TTS 兼容 runtime

用户可以在自然语言里明确要求 Veo 或 Veo+TTS：

```text
用 Veo+TTS 做一个 8 秒 9:16 产品广告短视频，画面用 Veo，中文口播用 TTS。
先给我 storyboard 和资产计划，确认后再生成。
```

预期行为：

- 资产计划中的 provider 会显示为 `veo`，并标注 `Veo + ByteDance TTS`。
- 可选比例只显示 `9:16` 和 `16:9`，不会展示 Seedance 才支持的 `1:1`。
- 当前兼容 runtime 固定 `720p`，provider 分段只支持 `4/6/8` 秒。
- 如果 Veo 或 TTS 失败，系统返回可读错误，不自动切回 Seedance。

## 查看质量报告

最终 MP4 完成后，系统会在生产目录写出：

- `quality_report.json`：结构化质量报告，适合后续工具读取。
- `quality_report.md`：人可读质量报告，适合飞书或 CLI 展示。

对话里可以直接问：

```text
这个短视频的质量报告给我看一下。
```

预期行为：

- Orchestrator 调用 `run_short_video_production(action="view", view_type="quality")`。
- 报告包含结构检查：文件存在、可播放、时长、分辨率/比例、音轨、分段数量。
- 报告包含业务规则检查：无字幕请求、语音/口播请求、参考图使用声明、产品露出、卖点覆盖和 CTA。
- 当前质量报告是确定性和启发式检查，不等同于模型主观审美评分。

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
- 在 `shot_review` 或最终成片后要求“只改这一段”时，未受影响的分段不应被标记为 stale。
- 最终回复包含 MP4 产物路径。
- 对话中继续提出修改时，系统先分析影响范围或返回更新后的计划，不直接覆盖旧成片。

## 当前 P1e 验收样例

建议每次改短视频策略后至少跑这三类：

- 卡通短剧：两只猫咪对白，要求无字幕、有软萌语音。
- 产品广告：智能保温杯或咖啡新品，要求 8 秒、9:16、有口播和音效。
- 社交媒体短片：小红书/抖音风格，要求强开头钩子和快节奏。
- Provider 选择：同一产品广告分别明确要求 Seedance fast 和 Veo+TTS，检查资产计划和实际 provider 路由不同。
- 质量报告：猫咪 case 检查“无字幕”和“需要语音”；产品广告 case 检查产品露出、卖点覆盖和 CTA。

如果真实模型输出质量不稳定，优先调 storyboard、资产计划中的 `visual_prompt` 和原生音频指令，不要绕回“视频 + 单独 TTS 解说”的默认链路。

## Prompt 调优入口

短视频生产里会反复优化的 provider prompt 已经独立为 Markdown 模板，位置在：

```text
src/production/short_video/prompts/
```

当前主要模板包括：

- `product_ad_visual.md`：产品广告视觉 prompt。
- `cartoon_short_drama_visual.md`：卡通短剧视觉 prompt。
- `social_media_visual.md`：社交媒体短片视觉 prompt。
- `native_audio_dialogue.md`：有明确角色对白时的 Seedance 原生音频指令。
- `native_audio_scene.md`：没有明确对白时的场景音频指令。
- `storyboard_instruction.md`：把已确认 storyboard 注入 provider prompt 的模板。
- `shot_segment_visual.md`：单个分段生成时的 provider prompt 包装模板。

这些文件适合承载可反复调参的创意策略。状态机、provider 参数校验、文件路径处理、stale / 重生成规则仍然保留在 Python 代码里，避免 prompt 修改破坏生产流程。

P0 收口状态见 [short_video_p0_completion_zh.md](short_video_p0_completion_zh.md)。
