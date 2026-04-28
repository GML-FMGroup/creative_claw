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

当前目标是在 P0/P1 闭环上推进 targeted revision 和页面级中间产物重生成：用户给出 brief，系统默认生成可审阅 outline；如果启动时传 `render_settings.brief_review=true`，系统会先暂停到 `brief_review`，让用户确认方向后再生成 outline。用户确认 outline 后生成可审阅 `DeckSpec`；用户确认 deck spec 后生成可编辑 PPTX、每页 preview、每页单独 PPTX segment 和质量报告；用户再确认 preview 后完成并投影最终产物到 ADK session state。若用户附加 TXT/MD/DOCX/PDF 源文档或 PPTX 模板，系统会先生成轻量 `DocumentSummary` / `TemplateSummary`，再把可用上下文纳入 brief review、outline 和 deck spec。用户后续明确修改某一页或某个 outline item 时，系统会优先做目标级 stale 标记；对 deck slide 级修改，可以先只重建 stale 页面的 preview 和单页 segment，再等用户确认后重建完整 final PPTX。

## 当前支持

当前支持：

- 纯文本 brief 生成原生可编辑 `.pptx`。
- `brief_review`：可选的 pre-outline 审阅，用于确认 brief、目标页数、比例、风格、输入摘要和 warning。
- `outline_review`：审阅页数、每页标题、purpose、layout 和 bullet。
- `deck_spec_review`：生成 PPTX 前审阅每页可执行内容，包括标题、正文、layout、visual notes 和 speaker notes。
- `page_preview_review`：targeted deck slide 修改并局部重建后，只审阅被重建的页面 preview 和单页 PPTX segment。
- `final_preview_review`：生成后审阅 preview PNG、每页可编辑 PPTX segment 和质量报告。
- `status` / `view` 查询当前状态、中间结果、事件和产物。
- `add_inputs` 追加 PPT 模板、源文档和参考图，并回到 outline review。
- TXT/MD/DOCX/PDF 源文档轻量抽取，生成 `DocumentSummary`，并把关键事实注入 outline；PDF 仅支持可抽取文本层，不包含 OCR 或复杂版面理解。
- PPTX 模板轻量分析，生成 `TemplateSummary`，包括 slide/layout/master/theme/media 等结构信号。
- `analyze_revision_impact` 只读分析修改影响范围，支持 `target_kind` / `target_id` / `slide_number` 定位。
- `apply_revision` 在用户确认后应用修改：outline item 修改回到 `outline_review`，deck spec slide 修改回到 `deck_spec_review`，并把目标页 preview 标记为 stale；无法确定目标时回到整套 outline review。
- `regenerate_stale_segments` 只重建 stale 页面的 preview PNG 和单页 `.pptx` segment，随后进入 `page_preview_review`，不直接更新最终 `final.pptx`。
- `render_manifest` 投影把 final PPTX、preview、单页 segment、quality report、输入、设置和 stale 状态集中成一份交付清单。
- 产物写入 production session 目录，并通过共享 projection 投影最终文件。

当前暂不支持：

- 真实模板编辑和版式映射。
- 扫描件 PDF OCR、复杂 PDF 版面/表格抽取和精确事实引用。
- 把页面级局部重生成结果回写到既有最终 PPTX。当前只重建单页 preview 和 segment，完整 `final.pptx` 仍通过后续 approve 全量重建。
- HTML deck。

这些属于 P1/P2 范围。

## 标准流程

```text
start
  -> ingest_inputs
  -> brief_review (optional: render_settings.brief_review=true)
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

targeted deck slide 修改后的页面级重建流：

```text
apply_revision
  -> deck_spec_review
  -> regenerate_stale_segments
  -> page_preview_review
  -> resume approve
  -> deck_spec_review
  -> resume approve
  -> native_build
  -> final_preview_review
```

## Tool 动作

`run_ppt_production` 支持：

| action | 是否写状态 | 说明 |
| --- | --- | --- |
| `start` | 是 | 创建 PPT production session；默认进入 `outline_review`，当 `render_settings.brief_review=true` 时先进入 `brief_review`。 |
| `status` | 否 | 返回当前阶段、进度和 active production 指针。 |
| `view` | 否 | 查看 `overview`、`brief`、`inputs`、`document_summary`、`template_summary`、`outline`、`deck_spec`、`previews`、`manifest`、`quality`、`events`、`artifacts`。 |
| `resume` | 是 | 对 active review 执行 `approve`、`revise` 或 `cancel`；当前 review 可能是 `brief_review`、`outline_review`、`deck_spec_review`、`page_preview_review` 或 `final_preview_review`。 |
| `add_inputs` | 是 | 追加模板、源文档或参考图，标记下游 stale，并回到 outline review。 |
| `analyze_revision_impact` | 否 | 只读分析用户修改会影响哪些对象。 |
| `apply_revision` | 是 | 用户确认后应用修改，按目标清理下游产物；outline 目标回到 `outline_review`，deck slide 目标回到 `deck_spec_review`，未指定目标时回到 `outline_review`。 |
| `regenerate_stale_segments` | 是 | 只重建 stale 页面对应的 preview PNG 和单页 PPTX segment，并进入 `page_preview_review`；`final.pptx` 与 quality report 仍保持 stale，等待后续 approve 后全量重建。 |

## 修改与 stale 语义

推荐流程是先只读分析，再由用户确认后应用：

```text
analyze_revision_impact
  -> user confirms
  -> apply_revision
  -> review updated outline or deck spec
  -> resume approve
```

目标定位规则：

P1d/P1e/P1f/P1g 页面级 stale 语义：targeted deck slide 修改后，`slide_previews` 不再被整体清空；目标页 preview 会保留路径并变为 `stale`，未受影响页面保持 `generated`。每条 preview 还会带 `segment_path`，指向对应单页可编辑 `.pptx` segment。执行 `regenerate_stale_segments` 后，目标页的 `preview/slide-XX.png` 和 `segments/slide-XX.pptx` 会基于最新 deck slide 重建，preview 状态回到 `generated`，并进入只包含重建页面的 `page_preview_review`。用户 approve `page_preview_review` 后，系统返回 `deck_spec_review`；完整 `final.pptx` 和 quality report 仍被视为 stale，直到用户 approve deck spec 后通过正常生成流程重建。


- 如果用户说“第 2 页”这类页码，orchestrator 可以传 `slide_number=2`。当 `DeckSpec` 已存在时优先匹配 `deck_slide`；否则匹配 outline entry。
- 如果已经知道 review payload 里的 item id，优先传 `target_kind="deck_slide"` / `target_kind="outline_entry"` 和对应 `target_id`。
- `analyze_revision_impact` 返回 `matched_targets`、`unmatched_targets`、`impacted` 和 `stale_items`，不会修改状态。
- deck slide 级修改只更新对应 `DeckSlide` 的 bullets / speaker notes，保留已有 preview 记录，把目标页 preview 标记为 `stale`，清空 final artifact 和 quality report，并暂停回 `deck_spec_review`。
- `regenerate_stale_segments` 只清理已重建页面的 `slide_preview:<id>` stale 标记，保留 `deck_slide:<id>`、`final` 和 `quality` stale 标记。用户 approve `page_preview_review` 后，已审阅页面的 `deck_slide:<id>` stale 标记会被清理，但 `final` / `quality` 仍保留；这是为了避免用户误以为完整 PPTX 已更新。
- 在 `page_preview_review` 上执行 `analyze_revision_impact`、`apply_revision` 或选择 revise 时，如果用户没有显式提供 `target_id` / `slide_number` / `targets`，系统会默认把修改作用于当前正在审阅的页面，避免退化成整套 outline 重建。
- outline entry 级修改只更新对应 `PPTOutlineEntry`，清空 deck spec、previews、final artifact 和 quality report，并暂停回 `outline_review`。
- 未指定目标或目标不够明确时，沿用安全 fallback：把 revision notes 追加到 brief，重建 outline，并清空所有下游产物。

## render_settings

常用字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `target_pages` | `6` | 目标页数，P0 限制为 1-30。 |
| `aspect_ratio` | `16:9` | 支持 `16:9`、`4:3`、`9:16`。 |
| `style_preset` | `business_executive` | 支持 `business_executive`、`pitch_deck`、`educational`、`editorial_visual`。 |
| `pipeline` | `auto` | 当前实际使用 native pipeline；模板编辑和 HTML deck 是后续能力。 |
| `brief_review` | `false` | 是否在 outline 生成前暂停到 `brief_review`，用于先确认方向和输入摘要。 |
| `deck_spec_review` | `true` | 是否在 outline 通过后暂停到 `deck_spec_review`。 |
| `skip_review` | `false` | 为测试或自动化场景跳过 review 直接生成。 |

示例：

```python
await run_ppt_production(
    action="start",
    user_prompt="做一份 6 页的 Q1 业务汇报，给高管看。",
    render_settings={"target_pages": 6, "style_preset": "business_executive", "brief_review": True},
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
  segments/
    slide-01.pptx
  final/
    final.pptx
  render_manifest.md
  render_manifest.json
  quality_report.md
  quality_report.json
```

`state.json` 是事实源；Markdown/JSON 视图、preview 和 render manifest 都是投影，可由 state 重建。`view_type="manifest"` 会返回同一份结构化清单，适合 App 或 CLI 一次性读取交付路径。质量报告会做确定性结构/内容/交付检查；当源文档成功抽取出 `salient_facts` 时，还会检查至少有一个源事实进入 outline 或 deck spec，未覆盖时给 warning，便于人工复核事实遗漏风险。

P1e/P1f/P1g segment 产物是中间产物：`SlidePreview.segment_path` 会记录每页单独的可编辑 `.pptx`，`regenerate_stale_segments` 会覆盖 stale 页面的 segment，并通过 `page_preview_review` 暴露给用户确认，但它们不会作为最终交付文件写入 `final_file_paths`。用户需要检查页面级状态时，通过 `view_type="previews"` 查看；需要同时查看 final、preview、segment 和 quality 路径时，通过 `view_type="manifest"` 查看。

## 环境与降级

原生 PPTX 生成优先使用 `python-pptx`。如果运行环境没有 `python-pptx`，会降级到内置最小 OOXML builder，仍输出可编辑 `.pptx`。

源文档抽取采用轻量、低依赖策略：TXT/MD 直接读取，DOCX 通过标准库解析 OOXML 文本，PDF 通过标准库读取常见文本层；扫描件、加密 PDF、复杂编码或复杂版面仍会显式降级并提示用户提供 TXT/MD/DOCX。PPTX 模板分析同样通过标准库读取 OOXML package，只做结构摘要，不修改模板。

Preview 优先使用 LibreOffice + Poppler 渲染真实页面图。如果缺失或渲染失败，会降级到 Pillow 生成确定性 preview PNG。质量报告会记录相关 warning。

## 后续方向

- 接入更完整的 PDF OCR、表格/版面抽取和引用定位。
- 扩展模板分析：thumbnail、版式占位符、字体和配色策略。
- 支持模板编辑：文本、图片、表格、图表替换。
- 支持把已重建的单页 segment 安全合入完整 PPTX。
- 扩展模板编辑前的版式/视觉策略 review gate。
