# Short-video Generation 框架细节

本文说明 CreativeClaw 当前 short-video generation 的实现框架。它面向维护者，重点回答当前短视频生产代码如何分层、状态如何流转、review/resume 如何工作、provider 如何选择、以及局部修改如何影响 stale 和重生成。

如果想先理解通用 production 4 层框架，请看 [production_framework_zh.md](production_framework_zh.md)。如果只是想使用短视频能力，请看 [short_video_production_zh.md](short_video_production_zh.md)。

## 当前能力边界

当前短视频实现已经达到 P1e：

- P0a：placeholder 成片闭环。
- P0b：真实 provider 生成闭环。
- P1a：storyboard review。
- P1b：shot-level asset plan 和分段 `shot_review`。
- P1c：局部分段修改和局部重生成。
- P1d：显式 provider/runtime 选择。
- P1e：基础质量报告。

支持的视频类型：

- `product_ad`
- `cartoon_short_drama`
- `social_media_short`

支持的输入：

- 纯文本 brief。
- 文本 brief + workspace-relative 参考图。
- 同一 production session 中追加或替换参考图。

支持的 provider 路由：

- 默认：Seedance 2.0 原生有声视频。
- 快速：Seedance 2.0 fast，保持 `720p`。
- 兼容：Veo + ByteDance / Volcengine TTS。

provider 失败不会自动切换。切换 provider 必须由用户明确要求，并通过 review gate。

## 主链路

短视频生成不走 orchestrator 临时串联多个 expert，而走 typed production tool：

```text
Orchestrator
-> run_short_video_production(...)
-> ShortVideoProductionManager
-> provider runtime / renderer / validator / quality
-> ProductionState
-> final artifacts
```

真实生成的常规流程是：

```text
start
-> storyboard_review
-> resume approve
-> asset_plan_review
-> resume approve
-> provider_generation
-> audio_generation
-> shot_preview_rendering
-> shot_review
-> resume approve
-> rendering
-> validation
-> quality_report
-> completed
```

如果有多个 provider-valid segment，系统会在每个分段生成后返回 `shot_review`，用户确认后再继续下一个分段。对 10 秒内短视频，系统会把 storyboard shots 合并成 provider-valid segment，避免违反 Seedance 最小时长约束。

## Tool Contract

入口是：

```text
src/production/short_video/tool.py
```

工具函数：

```python
run_short_video_production(...)
```

当前支持的 action：

| Action | 是否写状态 | 作用 |
| --- | --- | --- |
| `start` | 是 | 创建 production session，并进入 storyboard review 或 placeholder 成片 |
| `status` | 否 | 读取当前状态摘要 |
| `view` | 否 | 读取只读 production view |
| `resume` | 是 | 从 active breakpoint 继续 |
| `add_reference_assets` | 是 | 同一 session 中追加或替换参考素材 |
| `analyze_revision_impact` | 否 | 分析修改影响范围，不改状态 |
| `apply_revision` | 是 | 用户确认后应用修改，标记 stale，并回到 review |

`tool_context` 由 ADK 注入，不应由用户或模型伪造。工具会从 `tool_context.state` 中读取：

- `sid`
- `turn_index`
- `channel`
- `chat_id`
- `sender_id`
- `uploaded`
- `generated`
- `files_history`

其中 `uploaded` 只用于找到当前输入文件；`files_history` 不参与依赖判断或 stale 判断。

## 核心模块

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
  user_response.py
  prompts/
```

模块职责：

| 模块 | 职责 |
| --- | --- |
| `tool.py` | ADK tool 边界，规范化输入并调用 manager |
| `manager.py` | 状态机、review/resume、reference asset、stale、projection、最终编排 |
| `models.py` | 短视频私有 state、storyboard、asset plan、manifest、timeline、quality report |
| `providers.py` | Seedance 和 Veo+TTS provider runtime |
| `renderer.py` | ffmpeg timeline render |
| `validators.py` | ffprobe render validation |
| `quality.py` | 确定性和业务规则质量报告 |
| `impact.py` | read-only revision impact view |
| `placeholders.py` | P0a 本地 placeholder 资产 |
| `prompt_catalog.py` | Markdown prompt 模板加载和变量校验 |
| `user_response.py` | 用户确认、修改、取消 payload 归一化 |
| `prompts/*.md` | 可调的 provider prompt 模板 |

当前 `manager.py` 仍包含部分 storyboard、asset plan 和 prompt 构造逻辑。长期看，这些更适合拆到 Skill/Playbook/Planning Expert 层；当前实现先保留在 manager 内，是为了保持最小可运行和可测试。

## ProductionState

短视频私有 state 是 `ShortVideoProductionState`。

主要字段：

| 字段 | 作用 |
| --- | --- |
| `brief_summary` | 用户 brief 和后续修改摘要 |
| `reference_assets` | 用户上传或追加的参考素材 |
| `planning_context` | provider、比例、时长、视频类型等规划上下文 |
| `storyboard` | 用户可 review 的分镜结构 |
| `asset_plan` | provider-specific 生成计划 |
| `shot_asset_plans` | provider-executable 分段计划 |
| `shot_artifacts` | 已生成的分段预览 |
| `asset_manifest` | 视频/图片素材 manifest |
| `audio_manifest` | 音频素材 manifest |
| `timeline` | renderer 使用的机械 timeline |
| `render_report` | ffmpeg 渲染结果 |
| `render_validation_report` | ffprobe 校验结果 |
| `quality_report` | 最终质量报告 |

持久化目录结构：

```text
generated/{adk_session_id}/production/
  index.json
  {production_session_id}/
    state.json
    events.jsonl
    brief.md
    storyboard.json
    storyboard.md
    asset_plan.json
    timeline.json
    quality_report.json
    quality_report.md
    assets/
    audio/
    renders/
    final/
```

`state.json` 是事实源 checkpoint。其他 `.md` / `.json` 文件是投影，方便用户查看和工程调试。

## Review 阶段

### storyboard_review

`start(placeholder_assets=false)` 默认先进入 `storyboard_review`。

用户会看到：

- 视频类型。
- 目标时长。
- 已选择或待选择的比例。
- 全局约束。
- 参考素材使用声明。
- 分镜列表。
- 每个镜头的目的、视觉节拍、台词、音频说明和约束。

用户可以：

- `approve`：进入 asset plan review。
- `revise`：更新 storyboard 并继续停在 storyboard review。
- `cancel`：取消 production。

### asset_plan_review

storyboard approve 后进入 `asset_plan_review`。

用户会看到：

- provider 和模型。
- 分辨率。
- 是否原生音频或 TTS。
- 可选比例。
- 参考素材。
- 总体 shot plan。
- provider-valid `shot_asset_plans`。

如果用户没有指定比例，`selected_ratio` 为空，系统必须要求用户选择：

- Seedance：`9:16`、`16:9`、`1:1`
- Veo+TTS：`9:16`、`16:9`

只有比例有效且用户 approve 后，才允许进入真实 provider generation。

### shot_review

asset plan approve 后，manager 生成一个 pending shot segment，然后返回 `shot_review`。

用户会看到：

- 当前 segment index。
- 对应的 storyboard shot ids。
- 预览 MP4 path。
- video asset id 和 audio id。

用户可以：

- approve：继续下一个 segment，或者进入 final render。
- revise：当前分段标记 stale，回到 asset plan review。
- cancel：取消 production。

## Provider Runtime

provider 边界在：

```text
src/production/short_video/providers.py
```

当前有三个关键类：

| 类 | 作用 |
| --- | --- |
| `RoutedShortVideoProviderRuntime` | 根据 asset plan 显式选择 provider，不做静默 fallback |
| `SeedanceNativeAudioProviderRuntime` | Seedance 2.0 原生有声视频 |
| `VeoTtsProviderRuntime` | Veo 生成视频，TTS 生成 voiceover |

默认 provider 是 Seedance：

- 默认模型：`doubao-seedance-2-0-260128`
- fast 模型：`doubao-seedance-2-0-fast-260128`
- fast 分辨率保持 `720p`
- `generate_audio=true`
- audio manifest provider 是 `seedance_native_audio`

Veo+TTS 是显式兼容 runtime：

- provider 在 state 中记录为 `veo`
- video generation 使用 Veo。
- voiceover 使用 ByteDance / Volcengine TTS。
- ratio 只支持 `9:16` 和 `16:9`。
- duration 只支持 `4/6/8` 秒。
- 分辨率固定 `720p`。

provider 失败时：

- manager 返回 `failed`。
- 写入 `ProductionEvent`。
- 不自动切换 provider。
- 用户后续可以明确要求换 provider，再走 review。

## Timeline Render 和 Validation

renderer 在：

```text
src/production/short_video/renderer.py
```

validator 在：

```text
src/production/short_video/validators.py
```

renderer 做两类工作：

- 单分段 preview：把 video asset 和 audio asset mux 成 preview MP4。
- 多分段 final：把已 approve 的 segment previews concat 成 final MP4。

validator 使用 ffprobe 检查：

- duration 大于 0。
- 包含 video stream。
- 包含 audio stream。
- width / height 合法。

renderer 只读取 manifest 中 `status="valid"` 的 asset/audio。如果 timeline 引用了 stale 或 failed 素材，应失败并返回可读错误。

## Quality Report

quality report 在：

```text
src/production/short_video/quality.py
```

最终成片后生成：

- `quality_report.json`
- `quality_report.md`

当前质量报告是确定性和启发式检查，不是昂贵的 LLM-as-judge。

检查项包括：

- final artifact 是否存在。
- render validation 是否通过。
- 时长是否接近计划。
- 分辨率和比例是否匹配。
- 是否有音轨。
- 计划 segment 是否都有 approved preview。
- 如果用户要求无字幕，是否没有 subtitle-like artifact。
- 参考素材是否在 storyboard / asset plan 中声明使用。
- 产品广告是否包含产品露出、卖点覆盖和 CTA 线索。

用户问质量、QC、检查结果时，orchestrator 应使用：

```text
run_short_video_production(action="view", view_type="quality")
```

不能凭聊天记忆编造质量报告。

## Revision 和 Stale

修改流程分两步：

```text
analyze_revision_impact
-> apply_revision
-> review
-> resume approve
-> regenerate
```

`analyze_revision_impact` 是只读的。它返回：

- 用户想改什么。
- 匹配到哪些目标。
- 未匹配目标。
- 可选目标列表。
- 会影响哪些 state 节点。
- 推荐下一步。

`apply_revision` 才会修改 state。它会：

- 记录 revision notes。
- 更新 storyboard 或 asset plan。
- 标记受影响的 generated media、shot artifact、timeline、quality report stale。
- 回到 review checkpoint。

常见目标：

- `brief`
- `storyboard`
- `shot`
- `asset_plan`
- `shot_asset_plan`
- `shot_artifact`
- `voiceover`
- `reference_asset`
- `timeline`
- `artifact`

对于明确的 `shot_asset_plan` 或 `shot_artifact`，当前实现会尽量只影响对应 segment。对于 brief、storyboard、reference asset 这类全局目标，会影响更大范围。

注意：当前 `impact.py` 中的 `recommended_next_action` 文案仍有一处建议使用 `resume(decision=revise)`。当前标准口径应以本文、README 和 orchestrator 指令为准：用户确认修改后使用 `apply_revision`，再等待 review/approve。

## Reference Assets

用户上传或追加的参考图进入 `reference_assets`。

同一 production session 中追加参考图使用：

```text
run_short_video_production(action="add_reference_assets", input_files=...)
```

行为：

- 新增 `ReferenceAssetEntry`。
- 如果用户明确替换旧参考图，旧条目标记为 `replaced`。
- 旧 generated media 标记 stale。
- 清空 timeline / render report / quality report。
- 回到 `storyboard_review`，让用户重新确认参考素材使用方式。

参考图不会直接替代 `ProductionState`，也不会从 `files_history` 推断依赖关系。

## Projection 到 ADK State

短视频 production 只允许投影用户可见信息到 ADK session state。

最终 artifact 完成后投影：

- `generated`
- `new_files`
- `files_history`
- `final_file_paths`
- `active_production_session_id`
- `active_production_stage`
- `active_production_status`

中间事实不投影：

- `asset_manifest`
- `audio_manifest`
- `timeline`
- `render_report`
- `render_validation_report`
- `quality_report`
- provider 原始响应

中间事实只能通过 production view 或 `state.json` 读取。

## Progress Events

短视频内部阶段会发布进度事件，用于飞书等渠道展示。

当前常见阶段：

- Generating Shot Segment
- Preparing Segment Audio
- Shot Segment Ready
- Rendering Final Short Video
- Short Video Completed

这些进度事件是用户界面投影，不是生产事实源。事实源仍是 `ProductionState.production_events` 和 `state.json`。

## Prompt 调优边界

可反复优化的 provider prompt 放在：

```text
src/production/short_video/prompts/
```

当前模板：

- `product_ad_visual.md`
- `cartoon_short_drama_visual.md`
- `social_media_visual.md`
- `native_audio_dialogue.md`
- `native_audio_scene.md`
- `storyboard_instruction.md`
- `shot_segment_visual.md`

适合放进 Markdown 模板：

- 风格指令。
- 镜头画面表达。
- 原生音频指令。
- 对白保留策略。
- 产品广告、卡通短剧、社交媒体短片的 provider prompt 表达。

不适合放进 Markdown 模板：

- stage 流转。
- 是否允许调用 provider。
- stale 判断。
- workspace path 处理。
- provider 参数校验。
- state 持久化。

这些必须留在 Python 代码里，避免 prompt 调优破坏生产流程。

## 当前已知技术债

当前实现是可运行的 P1e，但还有几处需要后续收敛：

1. Skill/Playbook 层未完全拆出。

   Storyboard、asset plan、prompt 构造的一部分仍在 `manager.py`。后续可以逐步引入 `playbooks/short_video/*.md` 或内部 planning expert。

2. 错误模型偏简化。

   当前 `ProductionErrorInfo` 只有 `code`、`message`、`details`。如果要增强 UI、重试和 provider 故障处理，建议补 `stage`、`retryable`、`provider`。

3. `impact.py` 的推荐文案需要统一。

   当前标准流程应是 `analyze_revision_impact -> apply_revision -> review -> resume`。

4. 质量报告仍是轻量启发式。

   当前不做昂贵的 LLM-as-judge，也不做完整视觉一致性判断。后续可以在真实案例稳定后补创意 QC。

## 维护建议

改短视频生产逻辑时，优先按下面判断修改位置：

| 问题类型 | 优先修改位置 |
| --- | --- |
| stage / review / resume 流程错误 | `manager.py` |
| provider 参数或路由错误 | `providers.py` 和 manager 的 provider normalization |
| prompt 质量弱 | `prompts/*.md` |
| prompt 模板变量错误 | `prompt_catalog.py` 和对应单测 |
| final MP4 拼接或播放问题 | `renderer.py` / `validators.py` |
| quality report 缺检查项 | `quality.py` |
| 修改影响范围不对 | `impact.py` 和 manager stale 逻辑 |
| ADK state 投影不对 | `src/production/projection.py` |
| orchestrator 工具路由不对 | `src/agents/orchestrator/orchestrator_agent.py` |

改完后建议至少运行：

```bash
python -m unittest unit_test.test_short_video_prompt_catalog
python -m unittest unit_test.test_short_video_production
python -m unittest unit_test.test_orchestrator
```

如果改了 orchestrator 路由，还应补跑 ADK eval。

