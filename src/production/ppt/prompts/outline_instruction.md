---
description: PPT outline planning instruction
---
Create a concise PPT outline for this brief:

{{ brief }}

Target pages: {{ target_pages }}
Style preset: {{ style_preset }}

Input context:
{{ input_context }}

Requirements:
- Match the user's language unless the brief explicitly asks otherwise.
- Produce exactly the requested number of slides.
- Use the source-document facts semantically; do not paste them as generic "Source fact" bullets.
- Treat reference_image records as lightweight visual/brand context only; do not infer unseen image details beyond provided names/descriptions.
- Use concise, presentation-ready titles and 2-4 concrete bullet points per slide.
- Choose layouts from: cover, section, content, metric, two_column, closing.
- Use source_refs only when a slide is grounded in a source document input id from the context.
