# Short Video Production

This package implements Creative Claw's durable short-video production product. It is responsible for turning a user brief, optional reference assets, provider settings, and iterative user feedback into a reviewable and resumable short-video workflow.

The product is intentionally built around explicit checkpoints. The system should show useful intermediate results quickly, ask for user confirmation before expensive provider calls, and preserve enough state for later revision rather than treating video generation as a one-shot tool call.

## Product Flow

The normal real-generation flow is:

1. `start`: create a production session from the user brief and optional uploaded files.
2. `storyboard_review`: present the creative structure and wait for user approval or revision.
3. `asset_plan_review`: present provider, model, ratio, reference assets, visual prompt, and audio plan.
4. `shot_review`: generate one provider-valid segment and wait for user approval before continuing.
5. `completed`: render final MP4, persist artifacts, and write a quality report.

Users can also call read-only views during the flow:

- `overview`: current stage, progress, and pending review.
- `brief`: user brief and normalized settings.
- `storyboard`: current storyboard.
- `asset_plan`: provider-specific generation plan.
- `timeline`: render timeline.
- `quality`: final quality report.
- `events`: production event history.
- `artifacts`: generated files and media references.

Revision flow is separate from resume flow:

- `analyze_revision_impact` is read-only and explains what would become stale.
- `apply_revision` mutates state and returns to a review checkpoint.
- `resume` continues generation only after the user has approved the updated checkpoint.

## Package Responsibilities

- `tool.py`: ADK tool boundary for `run_short_video_production`.
- `manager.py`: durable production state machine, review checkpoints, revision handling, projection files, and final orchestration.
- `models.py`: typed production state, storyboard, asset plan, timeline, render, and quality-report models.
- `providers.py`: provider runtime boundary for Seedance native-audio generation and Veo+TTS compatibility generation.
- `prompt_catalog.py`: strict renderer for package-local Markdown prompt templates.
- `prompts/*.md`: frequently tuned short-video provider prompt templates.
- `quality.py`: deterministic and business-rule quality report generation.
- `renderer.py`: deterministic local final-render assembly.
- `validators.py`: rendered media validation.
- `impact.py`: read-only revision impact analysis.
- `placeholders.py`: local placeholder assets for framework validation.
- `user_response.py`: user confirmation and revision payload normalization.

Keep business flow, provider parameter validation, stale-state handling, file resolution, and API calls in Python. Keep tunable creative and provider prompt wording in Markdown templates.

## Current Capabilities

Supported production types:

- `product_ad`
- `cartoon_short_drama`
- `social_media_short`

Supported input shapes:

- Text brief only.
- Text brief plus uploaded reference images or workspace-relative reference paths.

Supported provider routes:

- Default: Seedance 2.0 native video and audio.
- Explicit fast route: Seedance 2.0 fast.
- Explicit compatibility route: Veo video plus ByteDance / Volcengine TTS voiceover.

Implemented control points:

- Storyboard-first review.
- Provider-specific asset-plan review.
- Provider segment generation and `shot_review`.
- Partial segment revision and partial regeneration.
- Explicit provider selection without silent fallback.
- Final quality report as JSON and Markdown.
- Conversation-readable production views.

## Prompt Optimization Entry Points

Most repeated quality tuning should happen in `prompts/*.md`, not inside `manager.py`.

Use this mapping when reviewing failed or weak real-generation examples:

- Product advertising video looks generic, misses selling points, or hides the package:
  edit `prompts/product_ad_visual.md`.
- Cartoon short-drama becomes narration, loses character dialogue, or misses punchline timing:
  edit `prompts/cartoon_short_drama_visual.md` and `prompts/native_audio_dialogue.md`.
- Social-media video has weak opening hook or slow pacing:
  edit `prompts/social_media_visual.md`.
- Seedance reads the task description aloud instead of generating scene audio:
  edit `prompts/native_audio_scene.md`.
- Explicit role dialogue is not spoken by the characters:
  edit `prompts/native_audio_dialogue.md`.
- Approved storyboard details are not reflected in provider prompts:
  edit `prompts/storyboard_instruction.md`.
- One generated segment loses continuity with the full plan:
  edit `prompts/shot_segment_visual.md`.

Do not put these concerns into Markdown templates:

- Which stage comes next.
- Whether provider generation is allowed.
- Which files are stale.
- Provider model, ratio, duration, or resolution validation.
- Workspace path normalization.
- Artifact persistence.

Those remain code responsibilities so prompt edits cannot accidentally bypass review gates or provider constraints.

## Recommended Optimization Loop

When a real example is poor, record the case in this shape:

```text
case: short label
input: user brief and reference-asset shape
observed: what the generated output did wrong
expected: what should happen instead
diagnosis: route, prompt, provider parameter, quality check, or Feishu display
change target: exact file to edit
regression check: unit test, eval case, or live provider case
```

Then apply the smallest matching change:

- Routing or expert choice issue: update orchestrator instructions or expert contracts.
- Production flow issue: update `manager.py`.
- Provider prompt quality issue: update the matching file in `prompts/`.
- Provider capability or parameter issue: update `providers.py` or video-generation capabilities.
- Missing acceptance rule: update `quality.py` and add a test case.
- Confusing status or artifact display: update production views or channel rendering.

After prompt edits, run at least:

```bash
python -m unittest unit_test.test_short_video_prompt_catalog
python -m unittest unit_test.test_short_video_production
```

For routing changes, also run orchestrator and ADK eval coverage before pushing.

## Prompt Template Rules

Prompt templates use strict `{{ variable }}` placeholders. Missing variables raise an error through `prompt_catalog.py`, so prompt edits should fail loudly during tests.

Template names are package-local and cannot be arbitrary file paths. This keeps runtime prompt loading predictable and prevents a conversation-level prompt tweak from reading local files.

When adding a new template:

1. Add a Markdown file under `prompts/`.
2. Use a stable lowercase underscore template name.
3. Keep frontmatter short and descriptive.
4. Render it through `render_prompt_template`.
5. Add or update `unit_test/test_short_video_prompt_catalog.py`.
6. If packaging changes are needed, update `pyproject.toml`.
