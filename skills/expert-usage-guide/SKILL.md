---
name: expert-usage-guide
description: Use when the task depends on choosing the right Creative Claw expert, composing expert calls, or explaining the usage strategy for image, video, search, grounding, segmentation, prompt extraction, or knowledge experts.
---

# Expert Usage Guide

Use this skill when you need to decide which Creative Claw expert should handle a task, how to structure `invoke_agent(...)` parameters, or how to chain several experts together.

This skill is about routing and strategy. It does not replace the actual expert implementation.

## Core Rule

- Prefer built-in file tools and direct reasoning for ordinary coding, file editing, and lightweight analysis.
- Use an expert only when the task needs a specialized capability that is already productized in the system.
- When an expert has a structured parameter contract, always pass a JSON object string to `invoke_agent`.
- Prefer workspace-relative paths such as `inbox/...` and `generated/...`.

## Expert Selection Strategy

### `ImageGenerationAgent`

Use when:

- The user wants to create new images from text.
- No source image is required as the main input.

Recommended providers:

- `nano_banana`: default choice for general text-to-image generation.
- `seedream`: use when the user explicitly asks for Seedream or the task clearly needs that provider.
- `gpt_image`: use when the user explicitly asks for GPT Image / OpenAI image generation, or when the task should follow OpenAI-native image parameters.

Recommended parameters:

```json
{"prompt":"a cinematic cat poster","provider":"nano_banana"}
```

```json
{"prompt":"a cinematic cat poster","provider":"seedream"}
```

```json
{"prompt":"a cinematic cat poster","provider":"gpt_image","size":"1536x1024","quality":"medium"}
```

Do not use when:

- The user wants to edit an existing image.
- The user first needs image understanding, grounding, segmentation, or prompt extraction.

### `ImageEditingAgent`

Use when:

- The user wants to modify one or more existing images.
- The task is "change this image", "replace background", "make it blue", "edit the uploaded image", and similar.

Recommended parameters:

```json
{"input_path":"inbox/cli/source.png","prompt":["replace the background with a clean white studio"],"provider":"nano_banana"}
```

Use `input_paths` when several images or references are required.

Do not use when:

- The user wants a mask first.
- The user wants pure text-to-image generation.

### `ImageSegmentationAgent`

Use when:

- The user wants a mask, cutout, localized edit, region-only processing, or inpaint-style preparation.
- The task says "segment", "extract the subject", "generate a mask", "keep only the person", or similar.

Recommended parameters:

```json
{"input_path":"inbox/cli/source.png","prompt":"person","threshold":0.2}
```

Key follow-up rule:

- After success, read `current_output.results[0].mask_path`.
- That `mask_path` is a reusable workspace file path for later tools or experts.

Typical chaining:

1. Call `ImageSegmentationAgent`.
2. Read `mask_path`.
3. Pass that workspace path to later editing or file-processing steps.

### `ImageGroundingAgent`

Use when:

- The user needs locations or bounding boxes, not a pixel mask.
- The task is to find where an object is in the image.

Recommended parameters:

```json
{"input_path":"inbox/cli/source.png","prompt":"red handbag"}
```

Prefer this over segmentation when:

- Bbox information is enough.
- The next step only needs rough localization.

### `ImageUnderstandingAgent`

Use when:

- The user wants description, style analysis, OCR, or a combined understanding pass.
- The task says "describe this image", "read the text in this image", "analyze the style", or similar.

Recommended parameters:

```json
{"input_path":"inbox/cli/source.png","mode":"description"}
```

```json
{"input_path":"inbox/cli/source.png","mode":"all"}
```

Prefer this before generation or editing when:

- The image contents are unclear.
- You need to understand a reference before deciding the next step.

### `ImageToPromptAgent`

Use when:

- The user wants a reusable generation prompt inferred from an image.
- The goal is "reverse prompt", "recreate this image", or "write the likely generation prompt".

Recommended parameters:

```json
{"input_path":"inbox/cli/reference.png"}
```

Prefer this over `ImageUnderstandingAgent` when:

- The output should look like a generation-ready prompt, not general analysis text.

### `VideoGenerationAgent`

Use when:

- The user wants a video generated from text or image-guided inputs.

Recommended parameters:

```json
{"prompt":"make a cinematic cat video","provider":"seedance","mode":"prompt"}
```

```json
{"input_path":"inbox/cli/first_frame.png","prompt":"animate this image","provider":"veo","mode":"first_frame"}
```

Do not use when:

- The task is only to generate still images.

### `SearchAgent`

Use when:

- The user needs external references, inspirations, or web facts.

Mode strategy:

- `image`: use for visual references.
- `text`: use for factual or conceptual information.
- `all`: use when both are helpful.

Recommended parameters:

```json
{"query":"editorial fashion poster references","mode":"image"}
```

```json
{"query":"brand campaign visual strategy examples","mode":"text"}
```

### `KnowledgeAgent`

Use when:

- The task needs design thinking, art direction, prompt planning, or creative decomposition before execution.
- The user wants several visual directions, campaign ideas, or prompt sets.

Recommended parameters:

```json
{"prompt":"Design three visual directions for a premium matcha brand campaign"}
```

Prefer this before generation when:

- The task is underspecified.
- The user wants higher-level creative planning, not just direct rendering.

## Common Chaining Patterns

### Reference Image -> Better Prompt -> New Image

1. `ImageToPromptAgent`
2. `ImageGenerationAgent`

### Uploaded Image -> Understand -> Decide Edit Direction

1. `ImageUnderstandingAgent`
2. `ImageEditingAgent`

### Uploaded Image -> Segment Subject -> Local Edit

1. `ImageSegmentationAgent`
2. Read `mask_path`
3. Use `ImageEditingAgent` or built-in file/image tools

### Design Brief -> Creative Directions -> Final Rendering

1. `KnowledgeAgent`
2. `ImageGenerationAgent`

### Need Web References Before Rendering

1. `SearchAgent`
2. `KnowledgeAgent` or `ImageGenerationAgent`

## Routing Heuristics

- If the user says "generate", start from `ImageGenerationAgent`.
- If the user says "edit this image", start from `ImageEditingAgent`.
- If the user says "describe / OCR / analyze style", start from `ImageUnderstandingAgent`.
- If the user says "reverse prompt / recreate from reference", start from `ImageToPromptAgent`.
- If the user says "find where", start from `ImageGroundingAgent`.
- If the user says "mask / cutout / segment", start from `ImageSegmentationAgent`.
- If the user says "video", start from `VideoGenerationAgent`.
- If the user says "search references / web info", start from `SearchAgent`.
- If the user says "give me design directions / creative plan", start from `KnowledgeAgent`.

## Important Guardrails

- Do not call an expert just because one exists; choose it because it is the best fit.
- Do not use `ImageGenerationAgent` when the user clearly needs an image edit.
- Do not use `ImageGroundingAgent` when the next step truly needs a pixel mask; use `ImageSegmentationAgent`.
- Do not pass artifact names or vague labels when a workspace path is available.
- Do not invent unsupported provider names. Use only declared provider values.
