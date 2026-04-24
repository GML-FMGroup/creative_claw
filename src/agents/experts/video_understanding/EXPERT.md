+++
name = "VideoUnderstandingExpert"
enabled = true
input_types = ["video"]
output_types = ["text", "metadata"]
routing_keywords = ["video description", "shot breakdown", "video OCR", "reverse prompt", "analyze video"]
+++

# VideoUnderstandingExpert

## When to Use

Use this expert to analyze one or more workspace videos and return text understanding results for `description`, `shot_breakdown`, `ocr`, or `prompt`.

## Routing Notes

- Use `description` for a concise explanation of scene, subjects, actions, setting, mood, and major visual changes.
- Use `shot_breakdown` for beat-by-beat or shot-by-shot structure.
- Use `ocr` when the user needs readable text visible in the video.
- Use `prompt` when the goal is to reverse engineer a reusable creative prompt or recreation brief from a reference video.
- One mode string can apply to all input videos, or a mode list can match `input_paths` one-to-one.

## Provider Boundaries

- Current implementation uses the configured multimodal project LLM through Google ADK.
- It also appends basic video metadata from local workspace video tools when available.
- It returns text and structured analysis records only; it does not generate, trim, transcode, or subtitle video files.
- OCR quality depends on the configured multimodal model and the legibility of frames.

## When Not to Use

Do not use this expert for local video file operations; use `VideoBasicOperations`. Do not use it for speech transcription or subtitle files; use `SpeechRecognitionExpert` or `SpeechTranscriptionExpert`.
