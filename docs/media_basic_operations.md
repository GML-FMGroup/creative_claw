# Media Basic Operations Quick Reference

This document is a user-facing quick reference for CreativeClaw's deterministic local media experts:

- `ImageBasicOperations`
- `VideoBasicOperations`
- `AudioBasicOperations`

These experts work on workspace-local files only. They do not call model providers and they do not overwrite the original file by default.

## Shared Rules

- Use workspace-relative paths such as `inbox/cli/session_1/sample.png`.
- Use `input_path` for one file.
- Use `input_paths` for concatenation or other multi-file operations.
- For file-producing operations, CreativeClaw writes a new suffixed file next to the source file.
- For `info` operations, the expert returns structured JSON-like metadata in `output_text` and `results`.

System dependency note:

- image operations require Pillow
- video and audio operations require both `ffmpeg` and `ffprobe` on `PATH`

## ImageBasicOperations

Supported operations:

- `crop`
- `rotate`
- `flip`
- `info`
- `resize`
- `convert`

### Image Operation Parameters

| Operation | Required parameters | Optional parameters | Result |
|----------|---------------------|---------------------|--------|
| `info` | `operation`, `input_path` | none | metadata text and structured result |
| `crop` | `operation`, `input_path`, `left`, `top`, `right`, `bottom` | none | one new image file |
| `rotate` | `operation`, `input_path`, `degrees` | `expand` | one new image file |
| `flip` | `operation`, `input_path`, `direction` | none | one new image file |
| `resize` | `operation`, `input_path`, `width` or `height` | `keep_aspect_ratio`, `resample` | one new image file |
| `convert` | `operation`, `input_path`, `output_format` | `mode`, `quality` | one new image file |

Allowed values:

- `direction`: `horizontal`, `vertical`
- `resample`: `nearest`, `bilinear`, `bicubic`, `lanczos`
- `output_format`: `png`, `jpg`, `jpeg`, `webp`

Examples:

```json
{"operation":"info","input_path":"inbox/cli/session_1/sample.png"}
```

```json
{"operation":"crop","input_path":"inbox/cli/session_1/sample.png","left":32,"top":24,"right":640,"bottom":512}
```

```json
{"operation":"rotate","input_path":"inbox/cli/session_1/sample.png","degrees":90,"expand":true}
```

```json
{"operation":"flip","input_path":"inbox/cli/session_1/sample.png","direction":"horizontal"}
```

```json
{"operation":"resize","input_path":"inbox/cli/session_1/sample.png","width":1024,"height":1024,"keep_aspect_ratio":true,"resample":"lanczos"}
```

```json
{"operation":"convert","input_path":"inbox/cli/session_1/sample.png","output_format":"jpg","quality":90}
```

Typical output names:

- `sample_crop.png`
- `sample_rotate_90.png`
- `sample_flip_horizontal.png`
- `sample_resize_1024x768.png`
- `sample_convert.jpg`

## VideoBasicOperations

Supported operations:

- `info`
- `extract_frame`
- `trim`
- `concat`
- `convert`

### Video Operation Parameters

| Operation | Required parameters | Optional parameters | Result |
|----------|---------------------|---------------------|--------|
| `info` | `operation`, `input_path` | none | metadata text and structured result |
| `extract_frame` | `operation`, `input_path`, `timestamp` | `output_format` | one new image file |
| `trim` | `operation`, `input_path`, `start_time` plus exactly one of `end_time` or `duration` | none | one new video file |
| `concat` | `operation`, `input_paths` with at least two files | `output_format` | one new video file |
| `convert` | `operation`, `input_path`, `output_format` | `video_codec`, `audio_codec` | one new video file |

Allowed values:

- `output_format` for `extract_frame`: `png`, `jpg`, `jpeg`, `webp`
- `output_format` for video files: `mp4`, `mov`, `mkv`, `webm`

Examples:

```json
{"operation":"info","input_path":"inbox/cli/session_1/clip.mp4"}
```

```json
{"operation":"extract_frame","input_path":"inbox/cli/session_1/clip.mp4","timestamp":"00:00:01.500","output_format":"png"}
```

```json
{"operation":"trim","input_path":"inbox/cli/session_1/clip.mp4","start_time":"00:00:02","duration":"3.0"}
```

```json
{"operation":"concat","input_paths":["inbox/cli/session_1/part1.mp4","inbox/cli/session_1/part2.mp4"],"output_format":"mp4"}
```

```json
{"operation":"convert","input_path":"inbox/cli/session_1/clip.mp4","output_format":"mov"}
```

Typical output names:

- `clip_frame_00_00_01.500.png`
- `clip_trim.mp4`
- `part1_concat.mp4`
- `clip_convert.mov`

Important note for `concat`:

- the current implementation assumes the input clips are already compatible enough for ffmpeg concat copy mode
- if concat fails, convert the clips first so they share container and codec settings

## AudioBasicOperations

Supported operations:

- `info`
- `trim`
- `concat`
- `convert`

### Audio Operation Parameters

| Operation | Required parameters | Optional parameters | Result |
|----------|---------------------|---------------------|--------|
| `info` | `operation`, `input_path` | none | metadata text and structured result |
| `trim` | `operation`, `input_path`, `start_time` plus exactly one of `end_time` or `duration` | none | one new audio file |
| `concat` | `operation`, `input_paths` with at least two files | `output_format` | one new audio file |
| `convert` | `operation`, `input_path`, `output_format` | `sample_rate`, `bitrate`, `channels` | one new audio file |

Allowed values:

- `output_format`: `mp3`, `wav`, `aac`, `m4a`, `flac`, `ogg`

Examples:

```json
{"operation":"info","input_path":"inbox/cli/session_1/voice.wav"}
```

```json
{"operation":"trim","input_path":"inbox/cli/session_1/voice.wav","start_time":"00:00:01","end_time":"00:00:04"}
```

```json
{"operation":"concat","input_paths":["inbox/cli/session_1/a.wav","inbox/cli/session_1/b.wav"],"output_format":"wav"}
```

```json
{"operation":"convert","input_path":"inbox/cli/session_1/voice.wav","output_format":"mp3","bitrate":"192k","sample_rate":44100,"channels":2}
```

Typical output names:

- `voice_trim.wav`
- `a_concat.wav`
- `voice_convert.mp3`

Important note for `concat`:

- the current implementation assumes the input clips are already compatible enough for ffmpeg concat copy mode
- if concat fails, convert the clips first so they share sample rate, channel count, and container format

## When To Use These Experts

Use these experts when:

- you need deterministic local media processing
- you already have the file in the workspace
- you want metadata, trimming, conversion, extraction, or simple geometric image changes

Do not use these experts when:

- you need semantic image understanding
- you need image or video generation from prompts
- you need speech transcription or text-to-speech

For those cases, use the corresponding model-based experts instead.
