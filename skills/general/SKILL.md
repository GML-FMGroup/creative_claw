---
name: general
description: General task execution skill for deciding the next best step without creating a full upfront plan.
---

# General Skill

Use this skill for ordinary requests when no more specific skill is required.

## Workflow

1. Focus on the next best step only.
2. Do not create a full multi-step plan unless the user explicitly asks for one.
3. Prefer direct execution over abstract planning.
4. If local files are involved, inspect them with `list_dir` and `read_file` before changing anything.
5. If the task needs lightweight image preprocessing on local files, use `image_crop`, `image_rotate`, or `image_flip`.
6. If shell execution is clearly the fastest safe path, use `exec`.
7. If the task needs external information, use `web_search` and `web_fetch`.
8. If the task is already complete, call `finish_task`.

## Principles

- Keep changes small and reviewable.
- Do not invent file contents or skill contents when you can read them directly.
- Re-check the latest state after each meaningful action.
- For local image tools, write derived outputs with the returned suffixed file path instead of overwriting the original by default.
