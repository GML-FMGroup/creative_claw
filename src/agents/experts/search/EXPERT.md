+++
name = "SearchAgent"
enabled = true
input_types = ["query"]
output_types = ["image", "search_results"]
providers = ["serper", "duckduckgo"]
routing_keywords = ["search", "reference image", "web result", "internet", "lookup", "visual reference"]
+++

# SearchAgent

## When to Use

Use this expert to search the Internet for visual references, text results, or both, based on a user query.

## Routing Notes

- Use `mode=image` when the next step needs downloaded reference images.
- Use `mode=text` when the task needs background information, trends, factual context, or design references in text form.
- Use `mode=all` when both image references and text search results are useful.
- Use optional `count` for image search result count; text search currently requests a fixed set of DuckDuckGo results.

## Provider Boundaries

- Image search uses Serper image search and requires `SERPER_API_KEY`.
- Downloaded image results are converted to PNG workspace files when possible; remote URLs may fail or return non-image content.
- Text search uses DuckDuckGo through `asyncddgs`, currently with `region=cn-zh`, `max_results=20`, and `timelimit=y`.
- This expert retrieves external references; it does not judge truthfulness, cite sources in a final report, or generate media itself.

## When Not to Use

Do not use this expert for private workspace file inspection, deterministic media operations, or final factual answers that require careful source citation. Use the retrieved results as inputs for later reasoning or generation.
