# 短视频 P0 收口记录

本文记录短视频生产能力 P0 的完成状态、验收情况、已知限制和 P1 边界。它是进入 P1 前的交接记录，不替代设计文档。

## 当前结论

短视频 P0 已完成核心闭环：

```text
用户提出短视频需求
-> Orchestrator 路由到 run_short_video_production
-> ShortVideoProductionManager 创建/恢复 production session
-> 返回 asset_plan_review 等待用户确认
-> 用户确认后调用真实 provider
-> 渲染、校验并返回最终 MP4
-> 支持查看状态、修改计划、追加参考素材和继续会话
```

当前默认真实生成路径是 Seedance 2.0 原生有声视频：

- 默认模型：`doubao-seedance-2-0-260128`
- 快速模型：`doubao-seedance-2-0-fast-260128`
- 默认分辨率：`720p`
- 默认音频：Seedance native audio，`generate_audio=true`
- 支持输入：纯文本任务描述，或任务描述加参考图片

Veo+TTS adapter 已实现，但不是默认用户路径：

- `VeoTtsProviderRuntime` 可调用 Veo 生成视频片段，再调用 ByteDance / Volcengine TTS 生成 voiceover。
- 当前限制为 `16:9` / `9:16`，`4/6/8` 秒，`720p`。
- P0 不提供自动 provider 降级，也不在对话里默认暴露 provider 切换。

## P0 已完成能力

- `src/production` 基础设施：production state、session store、owner check、event、artifact projection。
- `ShortVideoProductionManager`：start/status/resume/view/add_reference_assets/analyze_revision_impact/apply_revision。
- 短视频 P0 类型：产品广告、卡通短剧、社交媒体短片。
- 真实生成前 review：默认先返回 `asset_plan_review`，用户确认后才调用真实 provider。
- Seedance 2.0/2.0 fast：支持原生有声视频、参考图、分辨率/模型参数保存。
- 飞书进度：工具级进度和短视频内部阶段进度可以投影到渠道。
- 参考素材迭代：同一会话新增或替换参考图进入同一个 production session。
- 修改闭环：支持影响分析、确认应用修改、重新 review，再由用户确认生成。
- 版本安全：重新生成不会覆盖旧最终产物，旧素材会标记 stale/superseded。
- 文档：README、短视频使用说明、模型/token 映射和 v2 设计文档已同步当前默认路径。

## 验收记录

已完成的自动化验证：

- 短视频相关单测通过。
- Orchestrator 工具路由相关单测通过。
- 全量 unit test 通过。
- `git diff --check` 通过。
- ADK eval 已覆盖短视频 P0 的主要路由、review-before-generation、取消、继续 session、Seedance fast 和参考图路径。

已完成的人工 smoke：

- 两只猫咪对白短剧 case 可用，Seedance 2.0 生成效果可接受。
- 飞书产品图广告短视频 case 大体可跑通。
- 飞书上传参考图曾暴露 `input_files` 字符串路径兼容问题，已修复并补回归测试。

## 已知限制

这些问题不阻塞 P0，后续按 P1/P2 优先级处理：

- P0 是单镜头生产闭环，不做多镜头 storyboard 和一镜一交付。
- P0 不生成多个候选视频供并排选择。
- P0 不自动重试真实 provider，不自动切换 provider。
- P0 不做复杂 creative QC，不做产品一致性自动打分。
- P0 不做结构化 Web Chat UI。
- P0 对参考图的使用以 provider reference input 为主，深度图像理解和产品信息抽取后移。
- P0 不实现 PPT 生产和短剧剧本生产，只保持 production 基础设施可复用。
- ADK Python 2.0 不作为 P0 运行依赖。

## P1 建议边界

P1 可以开始做“质量和可控性”的增强，但仍应保持 ProductionManager 是事实源：

- 多镜头 storyboard 和一镜一交付。
- 粗剪 / 镜头级 review。
- provider 显式选择和失败后可控切换。
- 参考图深度理解、角色/产品 identity bible。
- 镜头级重生成和局部 stale 判断。
- 基础 creative QC 和可解释质量报告。
- 更好的飞书/频道展示，包括中间结果文件和明确确认按钮。

P1 不应直接把短视频主控改成 LLM agent，也不应绕过 `ProductionState` 写第二套生产状态。
