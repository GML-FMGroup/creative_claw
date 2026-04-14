---
name: creative-qc
description: Use when the user wants to review or sanity-check a storyboard, prompt pack, script, or generated asset set for consistency, platform fit, and creative quality before final delivery or generation.
---

# Creative QC

Use this skill to review creative work before handoff, generation, or publishing.

## Trigger Cues

- "review this storyboard"
- "sanity-check these prompts"
- "is this ready to generate"
- "spot issues before we publish"
- "quality-check this creative pack"

## Best For

- storyboard review
- prompt pack review
- script cleanup
- checking generated assets against plan
- catching mismatch before spending more generation budget

## When To Use

- The user wants review, risk detection, or final sanity-checking.
- There is already a concrete artifact to inspect.
- You need to compare plan vs execution, or prompt vs output.

## When Not To Use

- The user wants first-pass ideation or prompt creation, not review.
- There is no concrete artifact yet.
- The user already asked for direct rewriting instead of review-first feedback.

## Workflow

1. Identify the artifact type:
   - brief
   - storyboard
   - prompt pack
   - generated images
   - generated videos
2. Run structural checks:
   - language consistency
   - count alignment
   - one-to-one mapping between scenes and prompts
   - hook strength and opening variety
   - CTA presence when the content is promotional
   - platform fit such as `9:16`, `16:9`, short-form pacing, or still-image use
3. Run creative checks:
   - visual coherence
   - tone consistency
   - prompt specificity
   - repeated or generic phrasing
4. If real assets are provided, compare them against the plan:
   - `ImageUnderstandingAgent` for images
   - `VideoUnderstandingExpert` for videos
5. Return prioritized issues and concrete fixes.
6. Only rewrite automatically if the user asks for direct revision. Otherwise review first.

## Severity Rules

- `pass`: no material blockers, only optional polish
- `revise`: usable direction but there are issues that should be fixed before generation or handoff
- `block`: high-risk mismatch, missing inputs, or structural failure that should stop the next step

## Handoff Examples

If a prompt pack needs source comparison first:

```json
{"input_path":"inbox/reference.png","mode":"prompt"}
```

If a generated video needs intent comparison:

```json
{"input_path":"inbox/output.mp4","mode":"shot_breakdown"}
```

If the QC result says the script itself should be revised:

```json
{"input_text":"<script to revise>","mode":"rewrite","constraints":"fix repetition and keep the original meaning"}
```

## Output Contract

Return a QC summary like this:

```json
{
  "status": "pass",
  "critical_issues": [],
  "major_issues": [],
  "minor_issues": [],
  "fix_plan": [
    "..."
  ],
  "residual_risks": [
    "..."
  ]
}
```

## Guardrails

- Distinguish objective mismatch from subjective taste.
- Review first; do not silently rewrite the user's material unless asked.
- If there are no issues, say what you checked so the pass result is meaningful.
- When reviewing prompts, keep model compatibility in mind: concise, unambiguous, and medium-appropriate prompts usually beat overloaded prompts.
- Prioritize findings that would waste generation budget, break platform fit, or create scene-to-prompt mismatch.
