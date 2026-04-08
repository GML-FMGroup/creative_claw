---
name: summarize
description: Summarize web pages, search results, or local text files into concise takeaways using the current built-in tools.
---

# Summarize

Use this skill when the user wants a concise summary of a webpage, search result, or local text file.

## Workflow

1. If the source is a URL, use `web_fetch` to get the content.
2. If the source is a local file, use `read_file`.
3. If the source is not specified yet, use `web_search` first to find likely candidates.
4. Extract the important facts, decisions, and action items.
5. Keep the summary concise unless the user explicitly asks for depth.

## Guardrails

- Prefer primary sources when possible.
- If the fetched text is noisy or incomplete, say so clearly and summarize only what is reliable.
- Do not fabricate details that are not present in the source text.
