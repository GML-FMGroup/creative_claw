---
name: creative-image-task
description: Use when a request requires image generation, image editing, image understanding, search, or prompt refinement through existing expert agents.
---

# Creative Image Task Skill

Use this skill when the task should be delegated to an existing image-related expert.

## Expert Selection Guide

1. Use `KnowledgeAgent` for prompt refinement, design plans, and visual strategy.
2. Use `ImageGenerationAgent` for text-only image generation.
3. Use `ImageEditingAgent` when generation depends on one or more reference images.
4. Use `ImageUnderstandingAgent` for description, style analysis, or OCR.
5. Use `SearchAgent` for retrieving visual references or text information.

## Workflow

1. Decide which expert is best suited for the current single step.
2. If the task first needs simple local preprocessing, use `image_crop`, `image_rotate`, or `image_flip` on the input files before calling an expert.
3. Prepare a minimal and correct parameter object.
4. Call `run_expert`.
5. Review the returned result and either continue next turn or call `finish_task`.

## Notes

- Use one expert step at a time unless the user explicitly asks for batch work.
- If better prompting is needed before generation or editing, use `KnowledgeAgent` first.
- If outside references are needed, use `SearchAgent` before image generation or editing.
- Use local image tools for basic file preparation only. Use experts when the task requires semantic generation, editing, understanding, or prompt extraction.
- `image_crop(path, left, top, right, bottom)` is useful for isolating the subject or removing borders before expert calls.
- `image_rotate(path, degrees, expand=True)` is useful for orientation fixes before OCR, understanding, or editing.
- `image_flip(path, direction)` is useful for quick horizontal or vertical mirroring when the task is purely geometric.
