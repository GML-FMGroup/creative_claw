---
id: short_video.storyboard_expert
version: 1
description: Structured short-video storyboard planning instruction
---

Create a reviewable short-video storyboard from the user request.

Video type: {{ video_type }}
Selected aspect ratio: {{ selected_ratio }}
Target duration seconds: {{ duration_seconds }}

User request:
{{ user_prompt }}

Reference assets JSON:
{{ reference_assets_json }}

Baseline storyboard JSON:
{{ baseline_storyboard_json }}

Requirements:
- Preserve concrete user instructions, roles, dialogue, pacing cues, product benefits, platform style, and explicit constraints.
- Convert stage directions such as pauses, stares, reactions, falls, hooks, camera moves, and CTA requests into readable shot fields.
- Dialogue lines must stay as dialogue_lines, with speaker labels when the user provided them.
- Do not invent unsupported claims, brands, product benefits, visual identities, or reference assets.
- Use only reference_asset_ids that appear in Reference assets JSON.
- Make durations sum to the target duration. For social shorts, keep the opening hook near 2 seconds unless the user requests a different hook length.
- Prefer 3 to 5 shots. For target durations over 15 seconds, avoid shots shorter than 4 seconds when possible.
- Keep each purpose and visual_beat concise enough for user review, but specific to this user request.
- Keep global constraints practical for downstream video generation, not generic process narration.
- Return only the structured schema fields.
