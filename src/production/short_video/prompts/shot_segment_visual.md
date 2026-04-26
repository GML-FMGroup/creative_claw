---
id: short_video.shot_segment_visual
version: 1
description: Provider prompt wrapper for one approved storyboard segment.
---

Generate provider segment {{ segment_index }} for the approved short-video storyboard.

This segment covers storyboard shots {{ covered_shots }}.

Segment storyboard:
{{ shot_parts }}
{{ global_constraints }}

Keep style and continuity aligned with the full asset plan:
{{ full_asset_plan_prompt }}
