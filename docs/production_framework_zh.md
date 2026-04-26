# CreativeClaw Production 框架说明

本文说明 CreativeClaw 当前推荐使用的 production 框架。它面向维护者和后续业务线开发者，重点解释为什么要有 production 框架、4 层职责如何划分，以及短视频、PPT、短剧剧本等业务线如何复用同一套基础设施。

如果只想运行短视频能力，请先看 [short_video_production_zh.md](short_video_production_zh.md)。如果要看当前短视频生成实现细节，请看 [short_video_generation_framework_zh.md](short_video_generation_framework_zh.md)。

## 一句话总结

CreativeClaw 的复杂生产类任务不应该由主 orchestrator 临时串多个 expert 完成，而应该走 typed production tool：

```text
User
-> Orchestrator
-> run_xxx_production(...)
-> ProductionManager
-> Skill / Prompt / Playbook
-> Expert
-> Tool
-> ProductionState / Store
-> User-visible artifacts
```

这里的核心是：

- `Orchestrator` 负责用户入口和路由。
- `ProductionManager` 负责业务线状态机、review/resume、版本和产物投影。
- `Skill / Prompt / Playbook` 负责业务方法论和可调策略。
- `Expert` 负责专业内容产出。
- `Tool` 负责确定性执行。
- `ProductionState` 是唯一生产事实源，横切整个流程。

## 为什么需要 Production 框架

CreativeClaw 里有两类任务。

第一类是原子任务，例如：

- 生成一张图。
- 裁剪一个视频。
- 识别一段语音。
- 搜索一批网页。

这类任务可以直接由 orchestrator 调用 built-in tool 或 expert。

第二类是生产任务，例如：

- 生成可反复修改的短视频。
- 后续生成可审阅的 PPT。
- 后续生成可分阶段修改的短剧剧本。

这类任务不是一次模型调用。它们通常需要：

- 多轮用户确认。
- 中间结果可查看。
- 用户局部修改。
- 旧产物保留，新版本生成。
- 失败可恢复。
- 最终产物投影回对话和渠道。

如果把这些逻辑都塞进 orchestrator，会让 orchestrator 变成万能 workflow engine，后续每加一个业务线都会变重。因此 production 框架把“生产控制”从“用户入口”里拆出来。

## Orchestrator 入口

Orchestrator 不算 production 内核 4 层的一部分。它是用户入口，是 ADK `LlmAgent`，负责理解用户请求、选择能力、调用工具，并把结果解释给用户。

负责：

- 判断用户是要普通 expert 能力，还是要进入某个 production 业务线。
- 调用业务线 typed tool，例如 `run_short_video_production(...)`。
- 展示 `message`、`review_payload`、`view` 和最终 artifacts。
- 收集用户确认、修改或取消意见。
- 用户中途问杂项问题时，继续保持 active production session 指针。

不负责：

- 维护 production session 内部状态。
- 直接判断哪些素材 stale。
- 直接拼接多步视频、PPT 或剧本生成流程。
- 绕过 ProductionManager 写生产状态。

## 4 层生产内核

Production 内核的 4 层是：

```text
ProductionManager
-> Skill / Prompt / Playbook
-> Expert
-> Tool
```

这 4 层处理生产控制、业务方法论、专业内容产出和确定性执行。

### 1. ProductionManager

ProductionManager 是业务线负责人。它是普通 Python runtime service，不是 ADK subagent，也不是 `AgentTool`。

当前短视频业务线的实现是 `ShortVideoProductionManager`，通过 `run_short_video_production(...)` 暴露给 orchestrator。

负责：

- 创建和恢复 production session。
- 管理业务阶段，例如 `storyboard_review`、`asset_plan_review`、`shot_review`、`completed`。
- 保存 `ProductionState`。
- 产生 typed review payload。
- 处理 `resume`、`view`、`add_reference_assets`、`analyze_revision_impact`、`apply_revision`。
- 决定 stale 范围和局部重生成范围。
- 调用 provider runtime、renderer、validator 等下游能力。
- 将最终 artifact 投影到 ADK session state。

不负责：

- 自由聊天。
- 替代 orchestrator 跟用户做开放问答。
- 绕过 review 大量调用真实 provider。
- 把多个业务线塞进一个无类型 manager。

### 2. Skill / Prompt / Playbook

这一层表达业务方法论。它回答“这个类型的作品应该怎么做”，而不是“当前 session 状态是什么”。

短视频里这一层包括：

- `src/production/short_video/prompts/*.md` 中的 provider prompt 模板。
- 当前代码里用于生成 storyboard、asset plan 和 prompt 的规则。
- 未来可以拆出的 `playbooks/short_video/*.md`，例如 product ad、cartoon short-drama、social-media short。

负责：

- 场景 SOP。
- 镜头结构建议。
- 风格、台词、音频和质量策略。
- 可反复调优的 prompt 文案。
- 不同业务类型的差异化规则。

不负责：

- 保存 production state。
- 判断 owner 权限。
- 直接调用 provider。
- 直接写 manifest、timeline 或 artifact。
- 决定 stale 状态。

当前短视频实现里，这一层还没有完全独立出来。部分 storyboard、asset plan 和 prompt 构造逻辑仍在 `manager.py` 中，这是当前实现和理想分层之间的主要差异。后续优化方向是把业务 SOP 和创意规划逐步迁到 playbook、prompt catalog 或内部 planning expert 中，让 manager 更专注于状态机。

### 3. Expert

Expert 是专业内容产出者。它可以是 ADK agent，也可以是 production 内部调用的 provider/runtime 边界。

已有 expert 包括：

- 图像生成、图像编辑、图像理解。
- 视频生成、视频理解。
- 语音合成、语音识别。
- 文本处理、音乐生成。
- 本地媒体基础操作。

负责：

- 生成或分析专业内容。
- 返回文件、文本或结构化结果。
- 把 provider 能力包装成可测试接口。

不负责：

- 维护长流程 production session。
- 决定 review/resume 阶段。
- 管理用户确认点。
- 直接把中间素材写进 ADK session state。

当前短视频真实生成通过 provider runtime 调用已有视频和语音工具。默认路径是 Seedance 2.0 原生有声视频，兼容路径是 Veo+TTS。

### 4. Tool

Tool 是确定性执行能力。它不做创意判断。

负责：

- 文件读写。
- workspace 路径解析。
- ffmpeg 渲染。
- ffprobe 校验。
- 图片、视频、音频基础处理。
- artifact record 构造。

不负责：

- 写广告结构。
- 判断剧情节奏。
- 决定 provider 策略。
- 修改 production 事实源。

## ProductionState 是唯一事实源

`ProductionState` 是 production 框架最重要的约束。

它保存：

- production session 元数据。
- 当前状态和阶段。
- active breakpoint。
- production events。
- 最终 artifacts。
- 业务线自己的 state，例如短视频的 storyboard、asset plan、manifest、timeline、quality report。

ADK session state 只保存用户可见投影：

- `active_production_session_id`
- `active_production_stage`
- `active_production_status`
- `generated`
- `new_files`
- `files_history`
- `final_file_paths`

人可读文件也是投影，不是事实源：

```text
ProductionState
-> state.json
-> brief.md
-> storyboard.json / storyboard.md
-> asset_plan.json
-> timeline.json
-> quality_report.json / quality_report.md
-> final artifacts
```

如果用户要修改中间结果，正确路径是：

```text
User natural language
-> Orchestrator
-> typed production action
-> ProductionManager validation
-> ProductionState update
-> projection files regenerated
```

不能让 LLM 直接改 `state.json` 或把人可读文件当作恢复来源。

## 通信对象

Production 框架主要使用几类 typed 对象通信：

| 对象 | 作用 |
| --- | --- |
| `ProductionRunResult` | 每次 production tool 调用返回的统一结果 |
| `ReviewPayload` | 等待用户确认的结构化 review |
| `ProductionBreakpoint` | 当前暂停点 |
| `ProductionEvent` | 生产事件和审计记录 |
| `ProductionErrorInfo` | 当前实现中的结构化错误 |
| `WorkspaceFileRef` | workspace-relative artifact 引用 |

当前 `ProductionErrorInfo` 比设计中的错误模型简化，只包含 `code`、`message`、`details`。后续如果要增强 UI 和恢复能力，建议补上 `stage`、`retryable`、`provider` 等一等字段。

## 标准动作

不同业务线可以有不同 tool，但动作风格应保持一致。

短视频当前支持：

```text
start
status
resume
view
add_reference_assets
analyze_revision_impact
apply_revision
```

推荐语义：

- `start`：创建 production session，并停在第一个 review。
- `status`：读取当前状态摘要。
- `view`：读取只读视图，不修改状态。
- `resume`：从 active breakpoint 继续，通常处理 approve / revise / cancel。
- `analyze_revision_impact`：只读影响分析，不修改状态。
- `apply_revision`：用户确认后应用修改，标记 stale，并返回 review。
- `add_reference_assets`：同一 production session 内追加或替换参考素材。

## 目录和模块约定

共享 production 基础设施放在：

```text
src/production/
  models.py
  errors.py
  session_store.py
  projection.py
```

业务线私有实现放在：

```text
src/production/{capability}/
```

例如短视频：

```text
src/production/short_video/
  tool.py
  manager.py
  models.py
  providers.py
  renderer.py
  validators.py
  quality.py
  impact.py
  placeholders.py
  prompt_catalog.py
  prompts/
```

后续 PPT 或短剧剧本不应复用短视频私有 schema，而应复用共享 production 基础设施，再定义自己的业务 state、review payload、quality check 和 projection。

## 后续演进原则

新增 production 业务线时，优先保持这些原则：

- 先做最小可运行闭环。
- 真实 provider 调用前必须有 review。
- `ProductionState` 是唯一事实源。
- ADK state 只保存投影。
- 人可读文件只用于查看和调试。
- provider 失败不自动静默降级。
- 局部重生成必须基于 state 中的依赖信息，不基于聊天记忆或 `files_history` 猜测。
- ProductionManager 不直接变成 LLM agent。

当前短视频实现已经跑通了框架，但也暴露了下一步要做的拆分方向：把 Skill/Playbook/Planning 逻辑从 manager 中逐步移出，让 4 层边界更清楚。
