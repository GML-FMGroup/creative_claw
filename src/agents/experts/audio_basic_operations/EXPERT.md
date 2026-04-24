+++
name = "AudioBasicOperations"
enabled = true
input_types = ["audio"]
output_types = ["audio", "metadata"]
routing_keywords = ["audio info", "trim audio", "concat audio", "convert audio", "sample rate", "bitrate"]
+++

# AudioBasicOperations

## When to Use

Use this expert for deterministic local audio operations inside the workspace: `info`, `trim`, `concat`, and `convert`.

## Routing Notes

- Use `info` when duration, sample rate, channel count, or codec details are needed before another step.
- Use `trim` to cut one audio clip by `start_time` plus either `end_time` or `duration`.
- Use `concat` only when input clips are compatible enough for the local concat path.
- Use `convert` for audio format conversion with optional `sample_rate`, `bitrate`, and `channels`.

## Provider Boundaries

- This is a deterministic local operation wrapper around built-in workspace audio tools.
- `info` returns structured metadata and does not create a new audio file.
- File-producing operations save new workspace files and leave original inputs unchanged.
- It does not transcribe speech, synthesize narration, or generate music.

## When Not to Use

Do not use this expert for ASR, subtitle files, text-to-speech, or music generation. Route those tasks to `SpeechRecognitionExpert`, `SpeechTranscriptionExpert`, `SpeechSynthesisExpert`, or `MusicGenerationExpert`.
