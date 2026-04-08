---
name: code-edit
description: Use when the task requires inspecting and editing local project files.
---

# Code Edit Skill

Use this skill for code or text changes inside the project workspace.

## Workflow

1. Use `list_dir` to locate candidate files when the path is unclear.
2. Use `read_file` before editing.
3. Prefer the smallest safe change.
4. Use `edit_file` for targeted replacements.
5. Use `write_file` only when creating a new file or replacing the full file is clearly simpler.
6. Re-read important files after edits to verify the result.
7. If you need a command-line check, use `exec`.

## Guardrails

- Do not overwrite large files blindly when a targeted edit is enough.
- If exact replacement fails because the text is not unique, read more context and try again.
- Prefer readable, minimal edits over clever rewrites.
