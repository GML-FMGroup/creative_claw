---
description: Design layout planner structured instruction
---
Create a page-aware layout plan for this Design production.

Requested build mode:
{{ requested_build_mode }}

Design settings JSON:
{{ design_settings_json }}

Requested page specs JSON:
{{ requested_pages_json }}

Brief JSON:
{{ brief_json }}

Design system JSON:
{{ design_system_json }}

Reference assets JSON:
{{ reference_assets_json }}

Genre playbook:
{{ playbook_text }}

Requirements:
- If requested build mode is `single_html`, produce exactly one page with path `index.html` unless the design settings explicitly request another safe `.html` path.
- If requested build mode is `multi_html`, produce one `PageBlueprint` per requested page spec when specs are provided; preserve each requested page title and safe relative `.html` path.
- If requested build mode is `multi_html` and no page specs are provided, produce a cohesive 2-5 page site map with `index.html` as the first page.
- For every page, include enough page-specific section content for a complete first HTML build.
- Use stable, lowercase section ids suitable for HTML anchors.
- Prefer page-scoped section ids when multiple pages may share similar section names.
- Keep desktop and mobile responsiveness explicit in section notes.
