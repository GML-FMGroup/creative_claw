---
description: Design brief structured planning instruction
---
Create a production-ready Design brief from the user request.

Design genre: {{ design_genre }}

User request:
{{ user_prompt }}

Design settings JSON:
{{ design_settings_json }}

Reference assets JSON:
{{ reference_assets_json }}

Genre playbook:
{{ playbook_text }}

Requirements:
- Keep the brief practical for a single-file HTML design artifact.
- Preserve explicit user constraints.
- Set device targets to desktop and mobile unless the request clearly says otherwise.
- Do not invent unavailable brand assets.
