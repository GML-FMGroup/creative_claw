+++
name = "TextTransformExpert"
enabled = true
input_types = ["text"]
output_types = ["text"]
routing_keywords = ["rewrite", "expand", "compress", "translate", "structure", "title", "script"]
parameter_examples = [
  "{'input_text': 'source text', 'mode': 'rewrite|expand|compress|translate|structure|title|script'}",
  "{'text': 'source text', 'mode': 'translate', 'target_language': 'zh-CN'}",
]
+++

# TextTransformExpert

## When to Use

Use this expert for exactly one atomic text transformation: `rewrite`, `expand`, `compress`, `translate`, `structure`, `title`, or `script`.

## Routing Notes

- Pass source text as `input_text` or `text`, and pass one supported `mode`.
- Use `target_language` for translation requests.
- Use `style` and `constraints` only when the user specifies tone, format, length, or other output requirements.
- Prefer this expert for text cleanup before media generation when the text itself needs rewriting or structuring.

## Provider Boundaries

- Current implementation uses the configured project LLM through Google ADK.
- It returns transformed text only and asks the model not to explain the transformation.
- It validates `mode` strictly; unsupported modes produce an error instead of silently falling back.
- It does not read files, generate media, or perform web research.

## When Not to Use

Do not use this expert for image/video/audio generation, file transcription, search, or long-form creative planning. Use `KnowledgeAgent` when the task is a visual design brief rather than a simple text transform.
