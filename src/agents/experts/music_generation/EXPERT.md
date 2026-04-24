+++
name = "MusicGenerationExpert"
enabled = true
default_provider = "minimax"
default_model = "music-2.5"
input_types = ["prompt", "lyrics"]
output_types = ["audio"]
routing_keywords = ["music", "bgm", "song", "instrumental", "lyrics", "soundtrack"]
+++

# MusicGenerationExpert

## When to Use

Use this expert to generate one music, song-like, soundtrack, or BGM audio clip from text instructions, with optional lyrics.

## Routing Notes

- Use `instrumental=true` and omit `lyrics` for BGM or non-vocal music.
- Provide `lyrics` when the user asks for a song with words.
- Use `audio_format` only for supported formats: `mp3`, `wav`, or `flac`.
- Keep voiceover and spoken narration requests routed to `SpeechSynthesisExpert`, not this expert.

## Provider Boundaries

- Current integration calls MiniMax music generation over HTTP.
- The code default model is `music-2.5`; callers may pass `model`, but the expert should not assume a newer model unless the integration is changed or the caller explicitly requests a supported model.
- The provider returns audio bytes, which are saved as one workspace audio file.
- When `lyrics` are omitted, the current implementation sends an instrumental lyric scaffold to the provider.

## When Not to Use

Do not use this expert for text-to-speech narration, audio trimming, transcription, subtitles, or sound effects inside a generated video. Use the speech, audio, or video experts instead.
