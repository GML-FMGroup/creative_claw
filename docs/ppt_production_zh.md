# PPT Production P0 说明

本文说明 CreativeClaw 当前 PPT 产品线的 P0 能力。通用 production 框架请先看 [production_framework_zh.md](production_framework_zh.md)。

## 一句话总结

PPT 生产不是全局 Expert，而是 durable production tool：

```text
User
-> Orchestrator
-> run_ppt_production(...)
-> PPTProductionManager
-> PPTProductionState / Store / Projection
-> final.pptx + previews + quality report
```

P0 目标是先跑通最小闭环：用户给出 brief，系统生成可审阅 outline；用户确认后生成可编辑 PPTX、每页 preview 和质量报告；用户再确认 preview 后完成并投影最终产物到 ADK session state。

## 当前支持

P0 支持：

- 纯文本 brief 生成原生可编辑 `.pptx`。
- `outline_review`：生成前审阅页数、每页标题、purpose、layout 和 bullet。
- `final_preview_review`：生成后审阅 preview PNG 和质量报告。
- `status` / `view` 查询当前状态、中间结果、事件和产物。
- `add_inputs` 记录 PPT 模板、源文档和参考图，并回到 outline review。
- `analyze_revision_impact` 只读分析修改影响范围。
- `apply_revision` 在用户确认后应用修改，并回到 outline review。
- 产物写入 production session 目录，并通过共享 projection 投影最终文件。

P0 暂不支持：

- 真实模板编辑和版式映射。
- PDF/DOCX/TXT 内容抽取和事实引用。
- 页面级局部重生成。
- HTML deck。
- 完整四级 review gate。

这些属于 P1/P2 范围。

## 标准流程

```text
start
  -> ingest_inputs
  -> outline_planning
  -> outline_review
  -> resume approve
  -> deck_spec_planning
  -> native_build
  -> preview_rendering
  -> quality_check
  -> final_preview_review
  -> resume approve
  -> completed
```

## Tool 动作

`run_ppt_production` 支持：

| action | 是否写状态 | 说明 |
| --- | --- | --- |
| `start` | 是 | 创建 PPT production session，并进入 `outline_review`。 |
| `status` | 否 | 返回当前阶段、进度和 active production 指针。 |
| `view` | 否 | 查看 `overview`、`brief`、`inputs`、`outline`、`deck_spec`、`previews`、`quality`、`events`、`artifacts`。 |
| `resume` | 是 | 对 active review 执行 `approve`、`revise` 或 `cancel`。 |
| `add_inputs` | 是 | 追加模板、源文档或参考图，标记下游 stale，并回到 outline review。 |
| `analyze_revision_impact` | 否 | 只读分析用户修改会影响哪些对象。 |
| `apply_revision` | 是 | 用户确认后应用修改，清理下游产物，并回到 outline review。 |

## render_settings

常用字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `target_pages` | `6` | 目标页数，P0 限制为 1-30。 |
| `aspect_ratio` | `16:9` | 支持 `16:9`、`4:3`、`9:16`。 |
| `style_preset` | `business_executive` | 支持 `business_executive`、`pitch_deck`、`educational`、`editorial_visual`。 |
| `pipeline` | `auto` | P0 实际使用 native pipeline；模板和 HTML deck 是后续能力。 |
| `skip_review` | `false` | 为测试或自动化场景跳过 review 直接生成。 |

示例：

```python
await run_ppt_production(
    action="start",
    user_prompt="做一份 6 页的 Q1 业务汇报，给高管看。",
    render_settings={"target_pages": 6, "style_preset": "business_executive"},
)
```

## 输出文件

典型输出目录：

```text
generated/{adk_session_id}/production/{ppt_session_id}/
  state.json
  events.jsonl
  brief.md
  inputs.json
  outline.md
  outline.json
  deck_spec.md
  deck_spec.json
  preview/
    index.json
    slide-01.png
  final/
    final.pptx
  quality_report.md
  quality_report.json
```

`state.json` 是事实源；Markdown/JSON 视图和 preview 是投影。

## 环境与降级

P0 优先使用 `python-pptx` 生成更完整的原生 PPTX。如果运行环境没有 `python-pptx`，会降级到内置最小 OOXML builder，仍输出可编辑 `.pptx`。

Preview 优先使用 LibreOffice + Poppler 渲染真实页面图。如果缺失或渲染失败，会降级到 Pillow 生成确定性 preview PNG。质量报告会记录相关 warning。

## 后续 P1 方向

- 接入文档抽取：PDF/DOCX/TXT -> `DocumentSummary` -> outline/deck spec。
- 接入模板分析：thumbnail/layout/font/palette -> `TemplateSummary`。
- 支持模板编辑：文本、图片、表格、图表替换。
- 支持页面级 stale 和局部重生成。
- 扩展四级 review gate：brief、outline、deck spec、page preview。
