+++
name = "VideoBasicOperations"
enabled = true
input_types = ["video"]
output_types = ["video", "image", "metadata"]
routing_keywords = ["video info", "extract frame", "trim video", "concat video", "convert video", "transcode"]
+++

# VideoBasicOperations

## When to Use

Use this expert for deterministic local video operations inside the workspace: `info`, `extract_frame`, `trim`, `concat`, and `convert`.

## Routing Notes

- Use `info` before generation, extension, transcription, or editing when video duration, resolution, frame rate, or codecs matter.
- Use `extract_frame` to create a still image from a timestamp for image analysis, editing, or image-to-video workflows.
- Use `trim` to cut one clip by `start_time` plus either `end_time` or `duration`.
- Use `concat` only when clips are compatible enough for the local concat path.
- Use `convert` for container or codec conversion with optional `video_codec` and `audio_codec`.

## Provider Boundaries

- This is a deterministic local operation wrapper around built-in workspace video tools.
- `info` returns structured metadata and does not create a new video file.
- File-producing operations save new workspace files and leave original inputs unchanged.
- It does not understand video content semantically and does not generate new footage with an AI model.

## When Not to Use

Do not use this expert for text-to-video, video extension, video OCR, shot analysis, speech transcription, or subtitle generation. Route those tasks to the corresponding video generation, video understanding, or speech experts.
