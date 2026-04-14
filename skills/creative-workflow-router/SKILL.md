---
name: creative-workflow-router
description: Use when a creative request could map to several different skills or experts and you need to choose the smallest correct workflow, required inputs, and next execution path.
---

# Creative Workflow Router

Use this skill when the user request is underspecified or spans several creative stages at once.

## Trigger Cues

- "what should we do first"
- "which workflow fits this request"
- "route this creative task"
- "I have some assets and an idea, now what"
- "should this be storyboard, prompt, or generation"

## Main Goal

Choose the smallest correct route instead of jumping straight into the wrong expert or over-planning a simple request.

## When To Use

- The request could reasonably map to several different skills or experts.
- Inputs are mixed, incomplete, or ambiguous.
- The task spans planning, prompting, generation, and review, and you need the right starting point.

## When Not To Use

- The user explicitly named the exact expert or skill they want and the intent is already clear.
- The task is one atomic expert operation with no routing ambiguity.

## Route Options

- `brief_to_storyboard`: the user has an idea and needs scene planning first
- `narration_to_visual_prompts`: the user already has script or narration
- `asset_to_script`: the user already has photos or clips
- `style_to_prompt`: the user mainly needs visual style conversion
- `direct_image_generation`: the user clearly wants still-image generation now
- `direct_video_generation`: the user clearly wants video generation now
- `reference_analysis`: the user mainly wants to understand a reference image or video first
- `creative_qc`: the user wants review, checking, or refinement before handoff

## Fast Routing Heuristics

- Idea only -> `brief_to_storyboard`
- Script or narration already exists -> `narration_to_visual_prompts`
- Existing media folder -> `asset_to_script`
- Style language only -> `style_to_prompt`
- Final still prompt -> `direct_image_generation`
- Final motion prompt or image-guided video request -> `direct_video_generation`
- Existing asset needs interpretation first -> `reference_analysis`
- Existing plan or output needs checking -> `creative_qc`

## Workflow

1. Identify the user's primary goal:
   - ideate
   - structure
   - prompt
   - generate
   - review
2. Identify available inputs:
   - brief
   - script
   - image assets
   - video assets
   - style references
   - finished outputs to review
3. Select one primary route and, if needed, one secondary route.
4. State:
   - chosen route
   - why it fits
   - what inputs are still missing
   - which expert or skill should run next
5. If the user has already asked to proceed and the next step is low-risk, execute the first useful step instead of stopping at the routing explanation.

## Recommended Expert Usage

- Use `expert-usage-guide` when the next step involves choosing between existing experts.
- Prefer direct reasoning plus skills for planning.
- Use experts only when the task crosses into specialized generation, analysis, or file-based media work.

## Handoff Examples

For direct image generation:

```json
{"prompt":"<final prompt>","provider":"nano_banana","aspect_ratio":"16:9"}
```

For reference analysis before planning:

```json
{"input_path":"inbox/reference.mp4","mode":"prompt"}
```

For direct video generation:

```json
{"prompt":"<final video prompt>","provider":"seedance","mode":"prompt","aspect_ratio":"9:16","resolution":"720p"}
```

## Output Contract

Return a compact routing decision like this:

```json
{
  "primary_route": "brief_to_storyboard",
  "secondary_route": "narration_to_visual_prompts",
  "reason": "...",
  "available_inputs": ["brief", "style_notes"],
  "missing_inputs": ["reference_asset"],
  "next_steps": [
    "Run creative-brief-to-storyboard",
    "Then run narration-to-visual-prompts"
  ]
}
```

## Guardrails

- Do not over-route simple tasks that can be handled by one expert directly.
- Do not send a pure planning request into generation unless the user clearly asked for assets now.
- Do not ask for more inputs if safe defaults are enough for the next useful step.
- Prefer one clean route over a sprawling multi-expert chain.
- Bias toward executing the first safe step when the user already asked to proceed.
