---
name: asset-to-script
description: Use when the user already has images or videos and wants a script, scene plan, or edit-ready narrative built around those existing assets instead of generating everything from scratch.
---

# Asset To Script

Use this skill when the user already has media assets and wants the story to be built around what is available.

## Trigger Cues

- "make a script from these photos"
- "use these clips to build a short video"
- "what story can we tell with these assets"
- "assign my footage to scenes"

## Best For

- product or campaign reels from existing assets
- creator edits from a media folder
- turning photos and clips into a narrated short
- selecting the right asset for each scene before editing

## When To Use

- The user already has images, videos, or both.
- The story or narration should be constrained by the available media.
- Asset selection and sequencing matter more than generating new visuals.

## When Not To Use

- The user has no assets and wants everything generated from scratch.
- The task is only to understand one image or one video.
  Use `ImageUnderstandingAgent` or `VideoUnderstandingExpert` directly.
- The user only needs prompt writing, not asset assignment.

## Workspace Rule

- Prefer workspace-relative paths such as `inbox/...` and `generated/...`.
- When you return assigned assets, preserve the exact path strings so later expert calls can reuse them directly.

## Workflow

1. Build an asset inventory from the provided files.
2. Analyze each asset:
   - `ImageUnderstandingAgent` for image files
   - `VideoUnderstandingExpert` for video files
3. Summarize what each asset can contribute: subject, mood, setting, usable role, limitations.
4. Convert the user's intent into a target structure:
   - objective
   - audience
   - duration
   - tone
   - CTA if relevant
5. Map scenes to assets. Reuse assets only when necessary and say so.
6. Write narration and on-screen intent around the chosen assets instead of inventing unrelated visuals.
7. If there are obvious gaps, list the missing asset types instead of hiding the mismatch.
8. Tag each asset mentally as `hero`, `supporting`, or `filler` so scene assignment stays intentional.

## Recommended Expert Usage

- Use `ImageUnderstandingAgent` with `mode="description"` or `mode="all"` for still assets.
- Use `VideoUnderstandingExpert` with `mode="description"` or `mode="shot_breakdown"` for clips.
- Use `TextTransformExpert` with `mode="script"` to polish the final narration after scene mapping.

## Handoff Examples

For image asset analysis:

```json
{"input_path":"inbox/demo/product.jpg","mode":"description"}
```

For video clip analysis:

```json
{"input_path":"inbox/demo/broll.mp4","mode":"shot_breakdown"}
```

For polishing the final voiceover after scene mapping:

```json
{"input_text":"<draft asset-based narration>","mode":"script","style":"clear, creator-style voiceover"}
```

## Output Contract

Return a structured asset-driven script like this:

```json
{
  "asset_catalog": [
    {
      "path": "inbox/demo/hero.png",
      "type": "image",
      "summary": "...",
      "best_use": "opening shot"
    }
  ],
  "scene_plan": [
    {
      "scene_id": 1,
      "asset_path": "inbox/demo/hero.png",
      "reason": "...",
      "narration": "...",
      "on_screen_text": "...",
      "duration_seconds": 5
    }
  ],
  "gaps": ["..."]
}
```

## Guardrails

- Do not invent assets the user did not provide.
- If an asset analysis is uncertain, say that it is uncertain.
- Preserve exact asset paths in the output.
- Do not force a perfect one-asset-per-scene mapping if the input set is too small; reuse assets intentionally and state the reuse.
- Keep the script faithful to the user intent, not just to the strongest-looking asset.
- If the media set is too weak for the requested outcome, say what additional asset types are needed.
