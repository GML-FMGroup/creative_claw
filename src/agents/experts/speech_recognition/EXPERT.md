+++
name = "SpeechRecognitionExpert"
enabled = true
default_task = "auto"
input_types = ["audio", "video"]
output_types = ["transcript", "subtitle_file"]
routing_keywords = ["speech recognition", "transcribe", "transcript", "subtitle", "caption", "srt", "vtt"]
parameter_examples = [
  "{'input_path': 'workspace/path.wav', 'task': 'asr'(optional), 'timestamps': true(optional), 'language': 'en'(optional)}",
  "{'input_path': 'workspace/path.mp4', 'task': 'subtitle', 'subtitle_format': 'srt|vtt'(optional), 'output_path': 'workspace/subtitles.srt'(optional)}",
  "{'input_path': 'workspace/path.mp4', 'task': 'subtitle', 'subtitle_text': 'existing subtitle or transcript text', 'subtitle_format': 'srt|vtt'(optional)}",
  "{'input_paths': ['workspace/a.wav', 'workspace/b.mp4'], 'task': 'asr'(optional), 'timestamps': false(optional)}",
]
+++

# SpeechRecognitionExpert

## When to Use

Use this expert for speech recognition on workspace audio or video files, transcript extraction, timestamped ASR, subtitle file generation, and subtitle timing/alignment.

## Routing Notes

- Use `task=asr` when the user asks for transcript text, speech-to-text, or timestamped transcription.
- Use `task=subtitle` when the user asks for subtitle files, captions, SRT, VTT, or subtitle timing.
- After video generation, route subtitle-file requests here instead of asking a video generation model to produce SRT/VTT.
- When the user provides existing subtitle text that needs timing, pass it as `subtitle_text` or `audio_text`.

## Provider Boundaries

- This expert accepts workspace audio or video files through `input_path` or `input_paths`.
- Subtitle output supports `subtitle_format` values `srt` and `vtt`.
- The subtitle path is written into the workspace and returned in `current_output.results[*].subtitle_path` plus `output_files`.

## When Not to Use

Do not use this expert to synthesize narration, generate music, edit audio files, or generate video. Use `SpeechSynthesisExpert`, `MusicGenerationExpert`, `AudioBasicOperations`, or `VideoGenerationAgent` for those tasks.
