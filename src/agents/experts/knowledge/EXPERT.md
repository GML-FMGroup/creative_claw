+++
name = "KnowledgeAgent"
enabled = true
input_types = ["prompt", "image"]
output_types = ["design_plan", "image_prompt"]
routing_keywords = ["design plan", "creative brief", "prompt optimization", "reference analysis", "campaign concept"]
+++

# KnowledgeAgent

## When to Use

Use this expert to turn a creative requirement, with optional reference images, into a professional visual design scheme and one or more refined image-generation prompts.

## Routing Notes

- Use this expert before image generation when the request needs visual design reasoning, product-display planning, marketing-poster direction, or reference-image interpretation.
- Pass the original creative request as `prompt`; pass `input_path` or `input_paths` only when the user provided reference images.
- Use it when the user explicitly asks for multiple design proposals or prompt variants; the task text must specify the requested count.
- Feed the resulting prompt text into `ImageGenerationAgent` or `ImageEditingAgent` only after the design planning step is complete.

## Provider Boundaries

- Current implementation is an LLM planning agent using the configured project LLM through Google ADK.
- It returns text only: design scheme text plus image-generation prompt text.
- It does not create images, edit images, search the web, or inspect non-image files.
- Reference images are provided directly to the LLM for visual analysis.

## When Not to Use

Do not use this expert for ordinary one-step image generation when the user already gave a clear prompt. Do not use it as a substitute for `ImageUnderstandingAgent` when the user only wants OCR, description, or style analysis.
