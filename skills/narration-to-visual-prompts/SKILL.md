---
name: narration-to-visual-prompts
description: Use when the user already has script, narration, or storyboard text and needs aligned image or video prompts that are generation-ready and mapped one-to-one to the source beats.
---

# Narration To Visual Prompts

Use this skill to convert spoken or written narrative beats into prompts that can directly feed image or video generation.

## Trigger Cues

- "turn this script into prompts"
- "convert each scene into image prompts"
- "write shot prompts from my narration"
- "make generation prompts from this storyboard"

## Best For

- scene-by-scene image prompts
- shot-by-shot video prompts
- turning scripts into visual prompt packs
- adapting Chinese narration into English model prompts

## When To Use

- The user already has narration, scenes, storyboard text, or a script.
- The next step is image or video generation, but the prompts are not ready yet.
- The user needs exact one-to-one mapping between narrative beats and visual prompts.

## When Not To Use

- The user only has a loose idea and no scene structure yet.
  Use `creative-brief-to-storyboard` first.
- The user only needs a style prefix or aesthetic conversion.
  Use `style-brief-to-prompt`.
- The user explicitly asks for immediate direct generation and already gave a final prompt.

## Workflow

1. Normalize the input into discrete narration units.
2. Decide the medium:
   - default to `image` for still scenes or frame-based output
   - use `video` when motion, camera movement, or transitions matter
3. Capture style constraints from the user, brand brief, or reference assets.
4. Generate one prompt per narration unit. Keep count alignment exact.
5. Write prompts in English for model compatibility, even if the source narration is in another language.
6. Add optional negative prompt or camera notes only when they materially improve execution.
7. If the user asks for final media, feed the reviewed prompts into `ImageGenerationAgent` or `VideoGenerationAgent`.

## Recommended Expert Usage

- If the user supplies a reference image, use `ImageUnderstandingAgent` with `mode="style"` or `mode="prompt"` first.
- If the user supplies a reference video, use `VideoUnderstandingExpert` with `mode="prompt"` or `mode="shot_breakdown"` first.
- Use `TextTransformExpert` with `mode="structure"` only if the source narration is messy and needs cleanup before prompting.

## Handoff Examples

For reference-style extraction before prompt writing:

```json
{"input_path":"inbox/reference.png","mode":"style"}
```

For direct image generation after the prompt pack is approved:

```json
{"prompt":"<subject + prompt_prefix_en + prompt_en>","provider":"nano_banana","aspect_ratio":"16:9"}
```

For direct video generation after the motion prompts are approved:

```json
{"prompt":"<video prompt_en>","provider":"seedance","mode":"prompt","aspect_ratio":"9:16","resolution":"720p"}
```

## Output Contract

Return a structured prompt pack like this:

```json
{
  "style_prefix_en": "...",
  "items": [
    {
      "index": 1,
      "source_narration": "...",
      "medium": "image",
      "prompt_en": "...",
      "negative_prompt_en": "...",
      "camera_or_motion_notes": "..."
    }
  ]
}
```

## Guardrails

- Keep one prompt for each narration unit. No count drift.
- Do not mix still-image language and motion-camera language carelessly.
- Do not add visual elements that contradict the narration.
- Keep the final prompts in English, but keep surrounding explanations in the user's language when replying directly.
- If the user asks for multiple visual directions, label the variants clearly instead of blending styles into one muddy prompt.
- Prefer specific visual verbs and materials over vague adjectives like "beautiful" or "nice".
