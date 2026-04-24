+++
name = "VideoGenerationAgent"
enabled = true
default_provider = "seedance"
input_types = ["prompt", "image", "video"]
output_types = ["video"]
routing_keywords = ["video", "animation", "native audio", "dialogue", "ambience", "subtitle", "srt", "caption"]
+++

# VideoGenerationAgent

## When to Use

Use this expert for text-to-video, image-guided video, first-frame plus last-frame video, Kling multi-reference image-to-video, and Veo video extension workflows.

## Routing Notes

- Use `seedance` as the default when the user only asks for video generation and gives no explicit audio requirement.
- Prefer `veo` when the user asks for native audio, dialogue, ambience, music, or sound effects in the generated video. Audio should be described in the prompt, not passed as a separate file.
- Use `kling` `multi_reference` when the user provides 2-4 reference images and wants visual consistency across references.
- If the user asks for subtitle files, captions, SRT/VTT, or transcript output, generate or obtain the video first and then route to speech recognition or subtitle tools.

## Provider Boundaries

- `seedance` uses `doubao-seedance-1-0-pro-250528`; treat current output as visual-only and do not promise synchronized audio or subtitle files.
- `veo` uses `veo-3.1-generate-preview`; it supports native synchronized audio from prompt cues such as dialogue, ambience, music, and sound effects, but it does not return structured subtitle files.
- `kling` basic routes default to `kling-v3`; current Creative Claw integration does not expose native audio controls, so treat Kling output as visual-only for audio/subtitle routing.
- `kling` `multi_reference` uses `kling-v1-6`; treat it as visual-only and use it for 2-4 workspace reference images.

## When Not to Use

Do not use this expert as the final step for subtitle-file creation. Use `SpeechRecognitionExpert` after the video is available.
