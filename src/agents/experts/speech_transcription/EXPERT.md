+++
name = "SpeechTranscriptionExpert"
enabled = true
default_task = "auto"
input_types = ["audio", "video"]
output_types = ["transcript", "subtitle_file"]
routing_keywords = ["transcribe", "transcript", "speech to text", "subtitle", "srt", "vtt"]
+++

# SpeechTranscriptionExpert

## When to Use

Use this compatibility alias for transcript extraction, timestamped ASR, and subtitle-file workflows when the caller already refers to `SpeechTranscriptionExpert`.

## Routing Notes

- Prefer `task=asr` for plain transcript or speech-to-text requests.
- Use `task=subtitle` for SRT/VTT subtitle files or caption timing.
- For new routing rules, treat this expert as the same capability family as `SpeechRecognitionExpert`.

## Provider Boundaries

- This class subclasses `SpeechRecognitionExpert`; it is not a separate provider.
- It accepts the same workspace audio/video inputs and subtitle parameters as `SpeechRecognitionExpert`.

## When Not to Use

Do not use this alias for text-to-speech, music generation, audio trimming/conversion, or video generation.
