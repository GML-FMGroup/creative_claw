+++
name = "AudioBasicOperations"
enabled = true
input_types = ["audio"]
output_types = ["audio", "metadata"]
routing_keywords = ["audio info", "trim audio", "concat audio", "convert audio", "sample rate", "bitrate"]
parameter_examples = [
  "{'operation': 'info', 'input_path': 'workspace/path.wav'}",
  "{'operation': 'trim', 'input_path': 'workspace/path.wav', 'start_time': '00:00:01', 'end_time': '00:00:03'}",
  "{'operation': 'trim', 'input_path': 'workspace/path.wav', 'start_time': '00:00:01', 'duration': '2.0'}",
  "{'operation': 'concat', 'input_paths': ['workspace/a.wav', 'workspace/b.wav'], 'output_format': 'mp3|wav|aac|m4a|flac|ogg'(optional)}",
  "{'operation': 'convert', 'input_path': 'workspace/path.wav', 'output_format': 'mp3|wav|aac|m4a|flac|ogg', 'sample_rate': 44100(optional), 'bitrate': '192k'(optional), 'channels': 2(optional)}",
]
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

Do not use this expert for ASR, subtitle files, text-to-speech, or music generation. Route those tasks to `SpeechRecognitionExpert`, `SpeechSynthesisExpert`, or `MusicGenerationExpert`.
