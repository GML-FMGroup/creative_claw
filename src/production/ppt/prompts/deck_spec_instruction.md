---
description: PPT deck spec planning instruction
---
Convert the approved outline into a deck spec.

Outline:
{{ outline }}

Render settings:
{{ render_settings }}

Input context:
{{ input_context }}

Requirements:
- Preserve the outline narrative and slide sequence.
- Match the outline/user language unless explicitly asked otherwise.
- Write concise slide copy that is ready to render in editable PPT shapes.
- Use reference_image records only as visual/brand direction from provided metadata; do not claim image contents that are not described.
- For metric slides, provide at most three short metric labels or quantified statements.
- For content and two_column slides, provide concise bullets that fit on a slide.
- Add explicit visual_notes for each slide without inventing unavailable source evidence.
- Preserve valid source_refs from the outline when the slide uses source material.
