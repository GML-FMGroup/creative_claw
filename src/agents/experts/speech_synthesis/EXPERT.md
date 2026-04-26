+++
name = "SpeechSynthesisExpert"
enabled = true
default_provider = "bytedance_tts"
default_model = "seed-tts-2.0"
input_types = ["text", "ssml"]
output_types = ["audio"]
routing_keywords = ["text to speech", "tts", "voiceover", "narration", "ssml", "speaker", "voice"]
parameter_examples = [
  "{'text': 'Hello from Creative Claw.'}",
  '''{'text': '这是一段产品视频解说。', 'voice_name': '解说小明 2.0', 'audio_format': 'mp3'}''',
  '''{'ssml': '<speak>Hello<break time="500ms"/>world</speak>', 'resource_id': 'seed-tts-1.0', 'speaker': 'zh_female_yingyujiaoyu_mars_bigtts', 'audio_format': 'mp3', 'enable_timestamp': true}''',
]
+++

# SpeechSynthesisExpert

## When to Use

Use this expert to generate one speech audio file from plain text or SSML for narration, voiceover, spoken prompts, or dialogue audio.

## Routing Notes

- Pass either `text` or `ssml`; `ssml` takes precedence when both are present.
- Default to Seed TTS 2.0 with Vivi 2.0 (`zh_female_vv_uranus_bigtts`) when the user does not specify a voice.
- Use `speaker`, `voice_type`, or `voice_name` when the user specifies a voice. For Seed TTS 2.0, accept only voices from the official "豆包语音合成模型2.0" list.
- The main agent may choose a Seed TTS 2.0 voice by scene: education or English teaching -> Tina老师 2.0; customer service -> 暖阳女声 2.0; children or audiobook -> 儿童绘本 2.0 or 少儿故事 2.0; video narration -> 解说小明 2.0 or 磁性解说男声/Morgan 2.0; English -> Tim, Dacey, or Stokie.
- Use `audio_format` only for supported formats: `mp3`, `wav`, `flac`, or `pcm`.
- Use `enable_timestamp=true` only when downstream timing metadata is needed.

## Provider Boundaries

- Current integration uses the ByteDance or Volcengine HTTP unidirectional streaming TTS path.
- The default resource id is `seed-tts-2.0`; callers may pass `resource_id` only when they know the target TTS resource.
- Seed TTS 2.0 voice selection is validated against the local 2.0 voice catalog; unknown 2.0 voice names are rejected with a clear error.
- It generates one speech audio file per call and saves it in the workspace.
- It does not transcribe audio, generate subtitle files, clone custom voices, or generate music.

## When Not to Use

Do not use this expert for ASR, subtitle alignment, BGM, songs, or local audio trimming. Use speech recognition, music generation, or audio basic operations as appropriate.
