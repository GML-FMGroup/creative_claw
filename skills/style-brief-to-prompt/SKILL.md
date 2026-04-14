---
name: style-brief-to-prompt
description: Use when the user describes a look, atmosphere, or aesthetic direction in natural language and needs it converted into a clean English prompt fragment or style prefix for image or video generation.
---

# Style Brief To Prompt

Use this skill to turn loose art direction into prompt language that generation models can actually use.

## Trigger Cues

- "translate this style into a prompt"
- "turn this mood board into prompt language"
- "convert this Chinese style brief to English prompt"
- "give me a reusable style prefix"

## Best For

- converting Chinese style notes into English prompts
- building reusable prompt prefixes
- turning mood boards into style language
- separating subject content from style language

## When To Use

- The user mainly cares about look, tone, atmosphere, or art direction.
- The subject prompt already exists or will be written separately.
- You need a reusable style block that can be attached to many prompts.

## When Not To Use

- The user needs full scene planning or storyboard logic.
  Use `creative-brief-to-storyboard`.
- The user wants complete scene prompts from finished narration.
  Use `narration-to-visual-prompts`.
- The user already supplied a final generation-ready prompt and only wants execution.

## Workflow

1. Parse the style brief into concrete dimensions:
   - palette
   - lighting
   - mood
   - composition
   - materials or texture
   - era or cultural cues
   - camera language if video is involved
2. If reference media exists, extract style signals first:
   - `ImageUnderstandingAgent` with `mode="style"` or `mode="prompt"`
   - `VideoUnderstandingExpert` with `mode="prompt"` if the reference is a video
3. Convert the style description into:
   - one concise English prompt prefix
   - one optional negative prompt
   - one short summary of how the style should be applied
4. Keep the style prompt reusable. It should not hard-code the subject unless the user asked for subject and style together.
5. If the user wants multiple directions, return clearly separated variants instead of one overloaded prompt.

## Handoff Examples

For extracting style from a reference image:

```json
{"input_path":"inbox/reference.png","mode":"style"}
```

For combining the style prefix with image generation:

```json
{"prompt":"<subject prompt>, <prompt_prefix_en>","provider":"gpt_image","size":"1536x1024","quality":"medium"}
```

For combining the style prefix with video generation:

```json
{"prompt":"<subject motion prompt>, <prompt_prefix_en>","provider":"veo","mode":"prompt","aspect_ratio":"16:9","resolution":"720p"}
```

## Output Contract

Return a structure like this:

```json
{
  "style_summary": "...",
  "prompt_prefix_en": "...",
  "negative_prompt_en": "...",
  "applicable_to": ["image", "video"],
  "notes": "..."
}
```

## Guardrails

- Prefer attributes, materials, eras, and camera language over direct imitation of living artists.
- Avoid copyrighted character or franchise mimicry when the user only wants a general aesthetic.
- Keep the prompt compact enough to combine with subject prompts later.
- Do not mix incompatible style cues unless the user explicitly wants contrast.
- If the user asks for several directions, keep each variant internally coherent rather than averaging them together.
