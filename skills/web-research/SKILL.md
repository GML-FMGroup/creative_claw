---
name: web-research
description: Research a topic on the web using search and fetch tools, then return grounded findings.
---

# Web Research

Use this skill when the user asks for research, comparisons, background knowledge, or external references.

## Workflow

1. Use `web_search` to find promising sources.
2. Fetch the most relevant pages with `web_fetch`.
3. Compare sources instead of relying on one page when the question is important.
4. Extract concrete facts, constraints, and open questions.
5. If the task needs images or creative references, consider `run_expert` with `SearchAgent`.

## Guardrails

- Prefer specific and recent sources when the topic changes quickly.
- Distinguish between confirmed facts and your own inference.
- If web access is blocked or the API key is missing, say so and fall back to local reasoning.
