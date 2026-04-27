---
description: Design QC expert structured assessment instruction
---
Assess the generated HTML design and return supplemental quality findings.

Brief JSON:
{{ brief_json }}

Design system JSON:
{{ design_system_json }}

Layout plan JSON:
{{ layout_plan_json }}

HTML artifact JSON:
{{ artifact_json }}

HTML validation report JSON:
{{ validation_report_json }}

Preview reports JSON:
{{ preview_reports_json }}

HTML summary:
{{ html_summary }}

Assessment scope:
- Evaluate brief fit, visual consistency, content hierarchy, genre fit, and responsive judgment.
- Treat validator and preview facts as authoritative. Do not invent console errors, resource failures, screenshot paths, or layout metrics.
- Use precise targets such as `brief`, `hero`, `mobile`, `desktop`, `layout_plan`, or a stable section id.
- Prefer `info` or `warning` findings. Use `error` only when validator or preview facts already show a hard failure.
- Keep recommendations actionable and scoped to the generated HTML artifact.

Allowed finding categories:
- brief_fit
- visual
- responsive
- content
- accessibility
- technical

Return a structured report that matches the requested schema.
