+++
name = "SpeechSynthesisExpert"
enabled = true
default_provider = "bytedance_tts"
default_model = "seed-tts-1.0"
input_types = ["text", "ssml"]
output_types = ["audio"]
routing_keywords = ["text to speech", "tts", "voiceover", "narration", "ssml", "speaker"]
parameter_examples = [
  "{'text': 'Hello from Creative Claw.'}",
  '''{'ssml': '<speak>Hello<break time="500ms"/>world</speak>', 'speaker': 'zh_female_yingyujiaoyu_mars_bigtts', 'audio_format': 'mp3', 'enable_timestamp': true}''',
]
+++

# SpeechSynthesisExpert

## When to Use

Use this expert to generate one speech audio file from plain text or SSML for narration, voiceover, spoken prompts, or dialogue audio.

## Routing Notes

- Pass either `text` or `ssml`; `ssml` takes precedence when both are present.
- Use `speaker` when the user specifies a voice; otherwise the current default speaker is `zh_female_yingyujiaoyu_mars_bigtts`.
- Use `audio_format` only for supported formats: `mp3`, `wav`, `flac`, or `pcm`.
- Use `enable_timestamp=true` only when downstream timing metadata is needed.

## Provider Boundaries

- Current integration uses the ByteDance or Volcengine HTTP unidirectional streaming TTS path.
- The default resource id is `seed-tts-1.0`; callers may pass `resource_id` only when they know the target TTS resource.
- It generates one speech audio file per call and saves it in the workspace.
- It does not transcribe audio, generate subtitle files, clone custom voices, or generate music.

## When Not to Use

Do not use this expert for ASR, subtitle alignment, BGM, songs, or local audio trimming. Use speech recognition, music generation, or audio basic operations as appropriate.
