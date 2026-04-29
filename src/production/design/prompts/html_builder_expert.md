---
description: Design HTML builder structured instruction
---
Build the complete HTML artifact for the target page in this Design production.

Build mode:
{{ build_mode }}

Brief JSON:
{{ brief_json }}

Design system JSON:
{{ design_system_json }}

Layout plan JSON:
{{ layout_plan_json }}

Reference assets JSON:
{{ reference_assets_json }}

Revision request JSON:
{{ revision_request_json }}

Revision impact JSON:
{{ revision_impact_json }}

Validation feedback JSON:
{{ validation_feedback_json }}

Multi-page shared HTML context:
{{ shared_html_context }}

Previous HTML summary:
{{ previous_html_summary }}

Requirements:
- Return complete HTML, not Markdown.
- The HTML must be portable and must not reference local absolute paths.
- Inline CSS in a style tag; use only minimal inline JavaScript if needed.
- The layout plan contains the target page for this build call; use that page's path, title, and section ids.
- Use the section ids from the target page layout plan.
- For every referenced asset, use its `html_src` value exactly. Session assets live in `../assets/` relative to this HTML file.
- Do not copy workspace-relative asset paths from `path` into HTML. Use `html_src` for `src`, CSS `url()`, or other asset references.
- Do not use external scripts, stylesheets, fonts, images, or CDN resources. Navigation links may use `#section-id` or local `.html` page filenames.
- Include responsive desktop and mobile CSS.
- If build mode is revision, preserve unaffected sections unless the revision impact says they are affected.
- If build mode is revision, make the requested changes visible in the returned HTML.
- If validation feedback JSON contains issues, repair all listed issues in the returned HTML.
- If multi-page shared HTML context is not `(none)`, keep header, navigation, footer, naming, and repeated component markup consistent with that context.
- Do not include fenced code blocks.
- Avoid external runtime dependencies.
