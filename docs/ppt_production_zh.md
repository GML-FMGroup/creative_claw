# PPT Production 说明

本文说明 CreativeClaw 当前 PPT 产品线能力。通用 production 框架请先看 [production_framework_zh.md](production_framework_zh.md)。

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

当前目标是在 P0/P1a 闭环上推进 P1b deck spec review：用户给出 brief，系统生成可审阅 outline；用户确认 outline 后生成可审阅 `DeckSpec`；用户确认 deck spec 后生成可编辑 PPTX、每页 preview 和质量报告；用户再确认 preview 后完成并投影最终产物到 ADK session state。若用户附加 TXT/MD/DOCX 源文档或 PPTX 模板，系统会先生成轻量 `DocumentSummary` / `TemplateSummary`，再把可用上下文纳入 outline 和 deck spec。

## 当前支持

当前支持：

- 纯文本 brief 生成原生可编辑 `.pptx`。
- `outline_review`：审阅页数、每页标题、purpose、layout 和 bullet。
- `deck_spec_review`：生成 PPTX 前审阅每页可执行内容，包括标题、正文、layout、visual notes 和 speaker notes。
- `final_preview_review`：生成后审阅 preview PNG 和质量报告。
- `status` / `view` 查询当前状态、中间结果、事件和产物。
- `add_inputs` 追加 PPT 模板、源文档和参考图，并回到 outline review。
- TXT/MD/DOCX 源文档轻量抽取，生成 `DocumentSummary`，并把关键事实注入 outline。
- PPTX 模板轻量分析，生成 `TemplateSummary`，包括 slide/layout/master/theme/media 等结构信号。
- `analyze_revision_impact` 只读分析修改影响范围。
- `apply_revision` 在用户确认后应用修改，并回到 outline review。
- 产物写入 production session 目录，并通过共享 projection 投影最终文件。

当前暂不支持：

- 真实模板编辑和版式映射。
- PDF 内容抽取和事实引用。
- 页面级局部重生成。
- HTML deck。
- `brief_review` 和页面级 `page_preview_review`。

这些属于 P1/P2 范围。

## 标准流程

```text
start
  -> ingest_inputs
  -> outline_planning
  -> outline_review
  -> resume approve
  -> deck_spec_planning
  -> deck_spec_review
  -> resume approve
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
| `view` | 否 | 查看 `overview`、`brief`、`inputs`、`document_summary`、`template_summary`、`outline`、`deck_spec`、`previews`、`quality`、`events`、`artifacts`。 |
| `resume` | 是 | 对 active review 执行 `approve`、`revise` 或 `cancel`；当前 review 可能是 `outline_review`、`deck_spec_review` 或 `final_preview_review`。 |
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
| `pipeline` | `auto` | 当前实际使用 native pipeline；模板编辑和 HTML deck 是后续能力。 |
| `deck_spec_review` | `true` | 是否在 outline 通过后暂停到 `deck_spec_review`。 |
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
  document_summary.md
  document_summary.json
  template_summary.md
  template_summary.json
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

原生 PPTX 生成优先使用 `python-pptx`。如果运行环境没有 `python-pptx`，会降级到内置最小 OOXML builder，仍输出可编辑 `.pptx`。

源文档抽取在 P1a 采用轻量、低依赖策略：TXT/MD 直接读取，DOCX 通过标准库解析 OOXML 文本，PDF 暂时显式降级并提示用户提供 TXT/MD/DOCX。PPTX 模板分析同样通过标准库读取 OOXML package，只做结构摘要，不修改模板。

Preview 优先使用 LibreOffice + Poppler 渲染真实页面图。如果缺失或渲染失败，会降级到 Pillow 生成确定性 preview PNG。质量报告会记录相关 warning。

## 后续方向

- 接入更完整的 PDF 抽取和引用定位。
- 扩展模板分析：thumbnail、版式占位符、字体和配色策略。
- 支持模板编辑：文本、图片、表格、图表替换。
- 支持页面级 stale 和局部重生成。
- 扩展剩余 review gate：brief review、页面级 page preview review。
