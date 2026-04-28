# Design P1 收口记录

本文记录 Design production 产品线 P1 的完成状态、验收范围、验证命令、已知限制和 P2 边界。它是 P1 交接记录，不替代源码内的状态模型、运行时实现或 eval 配置。

## 当前结论

Design P1 已完成可用闭环。当前 Design 是 CreativeClaw 中面向 HTML 设计产物的 durable production 产品线，不是一次性图片生成、PPTX 生成或浏览器截图工具链。

核心事实源仍然是 `DesignProductionState`，ADK session state、Markdown/JSON 报告、HTML、PDF、ZIP 和 screenshots 都是从 production state 投影或派生出来的产物。

主流程如下：

```text
用户提出 HTML-centered Design 需求
-> Orchestrator 路由到 run_design_production
-> DesignProductionManager 创建/恢复 production session
-> 非 placeholder 路径先返回 design_direction_review
-> 用户确认设计方向后生成 HTML，并进入 preview_review
-> 用户可以查看 preview、quality、design system、components、lineage、pages 等视图
-> 用户可以确认完成，或先分析/应用 targeted revision
-> 最终确认后投影 HTML、报告、tokens、manifest、ZIP、可选 PDF 等交付物
```

## P1 已完成能力

- P1a handoff bundle ZIP：最终确认后生成 `exports/design_handoff_bundle.zip`，打包 HTML、报告、tokens、manifest、spec 和可用 preview。
- P1b source references：HTML artifact、preview、views、manifest 和 spec 都能携带 workspace-relative source ref details。
- P1c review quality metadata：`preview_review.metadata` 提供 delivery、preview、quality、source reference 摘要，客户端不需要重新解析全量状态。
- P1d optional PDF export：用户显式请求时导出 `exports/design.pdf`；浏览器依赖缺失时记录非阻塞 PDF report。
- P1e design token handoff：从 `DesignSystemSpec` 导出 `exports/design_tokens.json` 和 `exports/design_tokens.css`。
- P1f design system audit：生成 `reports/design_system_audit.*`，检查 token coverage、命名、颜色、对比度、排版、spacing/radius 和 component tokens。
- P1g component inventory：从 layout、tokens 和 HTML 生成 `reports/component_inventory.*`。
- P1h browser diagnostics：统一 preview/PDF 浏览器依赖诊断，生成 `reports/browser_diagnostics.*`，明确 Playwright/Chromium remediation。
- P1i artifact lineage：生成 `reports/artifact_lineage.*`，串联 artifact、revision、report、preview、PDF 和 stale/replaced 关系。
- P1j accessibility lint：生成 `reports/accessibility_report.*`，覆盖 lang/title/viewport、landmarks、heading、alt text、accessible names、form labels 和 click handlers。
- P1k design-system extraction：从 HTML/CSS 提取 CSS variables、颜色、typography、spacing、radius、shadow、breakpoints 和 selectors，生成 `reports/design_system_extraction.*`。
- P1l page handoff：生成 `reports/page_handoff.*`，说明每个 planned page / variant 的 handoff readiness。
- P1m multi-page build foundation：`design_settings.build_mode="multi_html"` 支持按页面生成、验证、preview、QC 和 handoff。
- P1n multi-page revisions：多页面 targeted revision 只 stale/rebuild 受影响页面，未受影响页面保持 active。
- P1o browser readiness：preview 和 PDF 共用浏览器环境分类，依赖缺失不会阻塞 HTML 交付。
- P1p multi-page expert planning：真实 `LayoutPlannerExpert` 能接收 build mode 和 requested page specs。
- P1q multi-page eval routing：orchestrator 指令和 ADK eval 覆盖多页面 microsite routing，避免被压成单页 landing page。
- P1r strict ADK schemas：Design 内部 structured-output experts 使用 ADK-facing strict schema，再转换回生产模型，避免 strict JSON schema 后端拒绝自由 dict。

## 用户可用范围

当前适合使用 Design production 的请求：

- HTML landing page。
- HTML UI mockup / dashboard。
- HTML product detail page。
- HTML microsite / multi-page website。
- one-pager、prototype、wireframe 这类以 HTML 为核心的设计产物。

当前不应使用 Design production 的请求：

- 单张 PNG/JPG 海报或图片生成，这类应走图片工作流。
- 可编辑 PPTX deck，这类应走 PPT production。
- 视频、音频、3D 或纯搜索任务。

## 常用操作

启动非 placeholder Design production：

```python
await run_design_production(
    action="start",
    user_prompt="Design a multi-page HTML microsite for an AI support SaaS.",
    placeholder_design=False,
    design_settings={
        "build_mode": "multi_html",
        "pages": [
            {"title": "Home", "path": "index.html"},
            {"title": "Product", "path": "product.html"},
            {"title": "Pricing", "path": "pricing.html"}
        ]
    },
    tool_context=tool_context,
)
```

查看中间状态：

```python
await run_design_production(action="view", view_type="preview", tool_context=tool_context)
await run_design_production(action="view", view_type="quality", tool_context=tool_context)
await run_design_production(action="view", view_type="design_system", tool_context=tool_context)
await run_design_production(action="view", view_type="components", tool_context=tool_context)
await run_design_production(action="view", view_type="lineage", tool_context=tool_context)
await run_design_production(action="view", view_type="pages", tool_context=tool_context)
```

分析 targeted revision 影响：

```python
await run_design_production(
    action="analyze_revision_impact",
    user_response={
        "notes": "Make the product page hero more product-led.",
        "targets": [{"kind": "page", "id": "page_product"}]
    },
    tool_context=tool_context,
)
```

应用已确认的 revision：

```python
await run_design_production(
    action="apply_revision",
    user_response={
        "notes": "Make the product page hero more product-led.",
        "targets": [{"kind": "page", "id": "page_product"}]
    },
    tool_context=tool_context,
)
```

## 验收记录

P1 收口前，主线已完成这些自动化验证路径：

- Design 单测覆盖 placeholder、非 placeholder、review/resume、multi-page、targeted revision、PDF、source refs、metadata、audit、inventory、diagnostics、lineage、accessibility、design-system extraction、page handoff、strict ADK schemas。
- Design eval asset 单测覆盖 evalset/config schema、Design rubrics、multi-page case、非 Design 边界 stub。
- 相关回归套件覆盖 Design、PPT、production session isolation、orchestrator、step events、Design eval assets 和 short-video eval assets。
- 全量 `unittest discover unit_test` 已在允许本地端口绑定的环境通过。
- `git diff --check` 已通过。

本次 P1 收口应至少运行：

```bash
.venv/bin/python -m json.tool tests/eval/evalsets/design_p0_evalset.json
.venv/bin/python -m json.tool tests/eval/eval_config.json
.venv/bin/python -m py_compile unit_test/test_design_production.py unit_test/test_design_adk_eval_assets.py unit_test/test_design_p1_completion_doc.py
.venv/bin/python -m unittest unit_test.test_design_p1_completion_doc
.venv/bin/python -m unittest unit_test.test_design_production
.venv/bin/python -m unittest unit_test.test_design_adk_eval_assets unit_test.test_short_video_adk_eval_assets
.venv/bin/python -m unittest unit_test.test_design_production unit_test.test_ppt_production unit_test.test_production_session_isolation unit_test.test_orchestrator unit_test.test_step_events unit_test.test_design_adk_eval_assets unit_test.test_short_video_adk_eval_assets
.venv/bin/python -m unittest discover unit_test
git diff --check
```

Live ADK eval 会调用配置的 agent model 和 judge model，需要可用模型凭证和网络。完整 Design eval 命令：

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/design_p0_evalset.json \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

如果只想先验证 P1q/P1r 最关键路径，可以跑多页面 case：

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/design_p0_evalset.json:start_multi_page_microsite_preserves_pages \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

## 已知限制

这些限制不阻塞 P1，可留到真实使用反馈后进入 P2：

- 当前 targeted revision 是 page-level rebuild，不是只重写一个 section fragment。
- 当前不做 Figma 文件生成、Figma Code Connect 或生产代码仓库 handoff。
- 当前 Design 视觉质量主要依赖 prompts、playbooks、deterministic checks 和 supplemental QC，还没有更强的多样式候选、并排评审或专业视觉 LLM-as-judge。
- 当前 asset pipeline 仍偏轻量：能保留 source refs，但不会自动生成完整 brand kit、icon system、产品图变体或图像派生关系图。
- PDF、screenshots 和 browser diagnostics 都是 HTML 的派生物；浏览器环境缺失时不会阻塞 HTML 交付。
- ADK eval 是行为验收，不替代真实项目中对最终 HTML 视觉质量、品牌一致性和可访问性的人工 review。

## P2 建议边界

建议先真实使用 P1 一段时间，再根据问题集中进入 P2。优先级建议如下：

1. Section-level regeneration：把当前 page-level revision 收敛到 section-fragment 级重生成和局部拼装。
2. Figma / code handoff：从现有 HTML、tokens、component inventory 和 page handoff 派生 Figma 或生产代码交接。
3. 视觉质量增强：增加更多 genre playbooks、style presets、candidate comparison、视觉质量 eval 和更强的 DesignQC。
4. Asset pipeline：系统化处理 logo、产品图、reference image、generated image、icon 和 brand token 的派生、过期和复用。
5. 使用体验打磨：为 Web/Feishu 客户端提供更清晰的 preview review、page-level revision、artifact bundle 和 remediation 展示。

P2 仍应遵守 P1 的核心边界：`DesignProductionManager` 和 `DesignProductionState` 是事实源；ADK state 只是投影；HTML 是核心交付载体；截图、PDF、图片和 ZIP 都不能替代 HTML。
