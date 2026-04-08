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
2. Prepare a minimal and correct parameter object.
3. Call `run_expert`.
4. Review the returned result and either continue next turn or call `finish_task`.

## Notes

- Use one expert step at a time unless the user explicitly asks for batch work.
- If better prompting is needed before generation or editing, use `KnowledgeAgent` first.
- If outside references are needed, use `SearchAgent` before image generation or editing.
