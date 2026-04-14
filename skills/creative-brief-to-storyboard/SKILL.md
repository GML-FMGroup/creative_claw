---
name: creative-brief-to-storyboard
description: Use when the user gives a topic, campaign idea, product angle, or raw creative brief and needs it turned into a structured storyboard with hooks, scene beats, narration, and visual intent.
---

# Creative Brief To Storyboard

Use this skill when the user is still at the concept-development stage and needs a production-ready plan before generating media.

## Trigger Cues

- "turn this idea into a storyboard"
- "plan a short video"
- "break this into scenes"
- "how should this ad unfold"
- "give me the hook, beats, and CTA"

## Best For

- short-form videos
- ad concepts
- explainer content
- creator scripts
- converting rough ideas into shot-by-shot structure

## When To Use

- The user has an idea, topic, or campaign objective but not a clean scene plan yet.
- The user needs titles, hooks, scene flow, and narration before media generation.
- The request is still upstream of prompt writing or generation.

## When Not To Use

- The user already has final narration or a finished script.
  Use `narration-to-visual-prompts` instead.
- The user only wants one atomic text operation such as a title or a rewrite.
  Use `TextTransformExpert` directly.
- The user clearly wants direct asset generation now and does not need planning first.

## Inputs To Resolve

- objective
- audience
- platform or format
- duration or scene count
- tone or style
- required claims, keywords, CTA, or brand constraints

If some inputs are missing, infer the smallest reasonable defaults and state them explicitly.

## Workflow

1. Normalize the request into a compact production brief.
2. Choose one clear creative angle instead of mixing several unrelated concepts.
3. Decide storyboard length:
   - 3-5 scenes for short promos or ads
   - 5-8 scenes for explainers, stories, or knowledge content
4. Draft:
   - 2-3 title options
   - one primary hook
   - one-line story arc
   - scene-by-scene storyboard
5. For each scene, provide:
   - purpose
   - narration
   - visual intent
   - on-screen text
   - suggested duration
6. Keep the sequence coherent: hook -> development -> payoff or CTA.
7. Only generate media prompts if the user also asks for prompts or production assets.

## Recommended Expert Usage

- Use `TextTransformExpert` for atomic text jobs:
  - `mode="title"` for title options
  - `mode="script"` for polishing narration
  - `mode="structure"` for cleaning up a messy brief
- Use `KnowledgeAgent` only when the user explicitly wants multiple creative directions or deeper ideation.
- Do not call `ImageGenerationAgent` or `VideoGenerationAgent` during the planning stage unless the user explicitly asks to move into production.

## Handoff Examples

If the brief is messy and needs structuring first:

```json
{"input_text":"launch a spring tea drink for office workers, soothing and premium, vertical short video","mode":"structure"}
```

If the user wants the narration polished after the storyboard is drafted:

```json
{"input_text":"<scene narration draft>","mode":"script","style":"natural spoken delivery","constraints":"keep one scene only"}
```

## Output Contract

Return a compact structured block like this:

```json
{
  "brief_summary": {
    "objective": "...",
    "audience": "...",
    "platform": "9:16 short video",
    "tone": "...",
    "assumptions": ["..."]
  },
  "titles": ["...", "...", "..."],
  "primary_hook": "...",
  "story_arc": "...",
  "scenes": [
    {
      "scene_id": 1,
      "purpose": "...",
      "narration": "...",
      "visual_intent": "...",
      "on_screen_text": "...",
      "duration_seconds": 4
    }
  ]
}
```

## Guardrails

- Keep language consistent with the user's language unless the user requests another output language.
- Avoid repetitive scene openings or template-sounding narration.
- Separate assumptions from confirmed requirements.
- Do not invent factual claims for regulated or evidence-heavy topics.
- Keep the storyboard compact enough that downstream prompt generation remains one-to-one with scenes.
- Prefer one main creative angle. If you provide variants, label them clearly instead of mixing them.
