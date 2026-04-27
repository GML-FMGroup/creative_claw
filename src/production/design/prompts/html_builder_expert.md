---
description: Design HTML builder structured instruction
---
Build the baseline single-file HTML artifact for this Design production.

Brief JSON:
{{ brief_json }}

Design system JSON:
{{ design_system_json }}

Layout plan JSON:
{{ layout_plan_json }}

Reference assets JSON:
{{ reference_assets_json }}

Requirements:
- Return complete HTML, not Markdown.
- The HTML must be portable and must not reference local absolute paths.
- Inline CSS in a style tag; use only minimal inline JavaScript if needed.
- Use the section ids from the layout plan.
- Include responsive desktop and mobile CSS.
- Do not include fenced code blocks.
- Avoid external runtime dependencies.
