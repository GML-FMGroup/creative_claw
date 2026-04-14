"""Tool helpers for speech recognition and subtitle generation."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.agents.invocation_context import InvocationContext

from src.agents.experts.speech_recognition.volcengine_client import VolcengineSpeechClient
from src.logger import logger
from src.runtime.workspace import generated_session_dir, resolve_workspace_path, workspace_relative_path
from src.tools.builtin_tools import BuiltinToolbox

_SUPPORTED_TASKS = {"auto", "asr", "subtitle"}
_SUPPORTED_SUBTITLE_FORMATS = {"srt", "vtt"}
_SUPPORTED_CAPTION_TYPES = {"auto", "speech", "singing"}
_SUPPORTED_STA_PUNC_MODES = {"1", "2", "3"}
_TIMESTAMP_LINE_RE = re.compile(r"^\[(?P<timestamp>\d{2}:\d{2}(?::\d{2})?[.,]\d{1,3})\]\s*(?P<text>.*)$")
_ASR_LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "en": "en-US",
    "en-us": "en-US",
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ko": "ko-KR",
    "ko-kr": "ko-KR",
    "yue": "yue-CN",
    "yue-cn": "yue-CN",
}
_SUBTITLE_LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "en": "en-US",
    "en-us": "en-US",
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ko": "ko-KR",
    "ko-kr": "ko-KR",
    "yue": "yue",
    "yue-cn": "yue",
}


@dataclass(slots=True, frozen=True)
class TranscriptSegment:
    """One timestamped transcript segment."""

    start_seconds: float
    text: str
    end_seconds: float | None = None


@dataclass(slots=True, frozen=True)
class PreparedMedia:
    """One local media payload normalized for the speech services."""

    input_path: str
    prepared_path: Path
    mime_type: str
    media_bytes: bytes


def describe_media_metadata(input_path: str) -> str:
    """Return a compact metadata summary for one audio or video file."""
    toolbox = BuiltinToolbox()
    mime_type, _ = mimetypes.guess_type(input_path)
    raw_result = (
        toolbox.video_info(input_path)
        if mime_type and mime_type.startswith("video/")
        else toolbox.audio_info(input_path)
    )
    result_text = str(raw_result).strip()
    if not result_text or result_text.startswith("Error"):
        return "Basic media info unavailable."
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return f"Basic media info unavailable: {result_text}"

    if mime_type and mime_type.startswith("video/"):
        return (
            "Basic media info: "
            f"duration_seconds={payload.get('duration_seconds')}, "
            f"size={payload.get('width')}x{payload.get('height')}, "
            f"fps={payload.get('fps')}, "
            f"video_codec={payload.get('video_codec')}, "
            f"audio_codec={payload.get('audio_codec')}."
        )
    return (
        "Basic media info: "
        f"duration_seconds={payload.get('duration_seconds')}, "
        f"sample_rate={payload.get('sample_rate')}, "
        f"channels={payload.get('channels')}, "
        f"codec={payload.get('codec')}."
    )


def parse_bool(value: Any) -> bool:
    """Normalize one flexible boolean-like value."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def normalize_speech_task(value: Any) -> str:
    """Return one supported speech task."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _SUPPORTED_TASKS else "auto"


def normalize_subtitle_format(value: Any) -> str:
    """Return one supported subtitle document format."""
    normalized = str(value or "").strip().lower()
    if normalized.startswith("."):
        normalized = normalized[1:]
    return normalized if normalized in _SUPPORTED_SUBTITLE_FORMATS else "srt"


def normalize_caption_type(value: Any) -> str:
    """Return one supported subtitle caption type."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _SUPPORTED_CAPTION_TYPES else "auto"


def normalize_sta_punc_mode(value: Any) -> str:
    """Return one supported automatic subtitle timing punctuation mode."""
    normalized = str(value or "").strip()
    return normalized if normalized in _SUPPORTED_STA_PUNC_MODES else ""


def resolve_speech_task(parameters: dict[str, Any]) -> str:
    """Choose the effective speech task from explicit and inferred parameters."""
    task = normalize_speech_task(parameters.get("task", "auto"))
    if task != "auto":
        return task

    subtitle_text = str(parameters.get("subtitle_text", parameters.get("audio_text", ""))).strip()
    if subtitle_text:
        return "subtitle"

    subtitle_format = str(parameters.get("subtitle_format", "")).strip()
    if subtitle_format:
        return "subtitle"

    output_path = str(parameters.get("output_path", "")).strip().lower()
    if output_path.endswith(".srt") or output_path.endswith(".vtt"):
        return "subtitle"

    return "asr"


def resolve_subtitle_format(parameters: dict[str, Any]) -> str:
    """Choose the effective subtitle format from explicit and inferred parameters."""
    subtitle_format = str(parameters.get("subtitle_format", "")).strip()
    if subtitle_format:
        return normalize_subtitle_format(subtitle_format)

    output_path = str(parameters.get("output_path", "")).strip().lower()
    if output_path.endswith(".vtt"):
        return "vtt"
    return "srt"


async def speech_recognition_tool(
    ctx: InvocationContext,
    input_path: str,
    *,
    language: str = "",
    timestamps: bool = False,
    task: str = "asr",
    enable_itn: bool = True,
    enable_punc: bool = True,
    enable_ddc: bool = False,
    enable_speaker_info: bool = False,
) -> dict[str, Any]:
    """Recognize speech from one workspace media file via Volcengine big-ASR."""
    return await asyncio.to_thread(
        _speech_recognition_sync,
        ctx,
        input_path,
        language,
        timestamps,
        task,
        enable_itn,
        enable_punc,
        enable_ddc,
        enable_speaker_info,
    )


async def speech_subtitle_tool(
    ctx: InvocationContext,
    input_path: str,
    *,
    language: str = "",
    subtitle_format: str = "srt",
    caption_type: str = "auto",
    subtitle_text: str = "",
    sta_punc_mode: str = "",
    words_per_line: int | None = None,
    max_lines: int | None = None,
    use_itn: bool = True,
    use_punc: bool = True,
    use_ddc: bool = False,
    with_speaker_info: bool = False,
    use_capitalize: bool = True,
) -> dict[str, Any]:
    """Generate subtitles or align provided subtitle text for one workspace media file."""
    return await asyncio.to_thread(
        _speech_subtitle_sync,
        ctx,
        input_path,
        language,
        subtitle_format,
        caption_type,
        subtitle_text,
        sta_punc_mode,
        words_per_line,
        max_lines,
        use_itn,
        use_punc,
        use_ddc,
        with_speaker_info,
        use_capitalize,
    )


def parse_timestamped_transcript(text: str) -> list[TranscriptSegment]:
    """Parse timestamped transcript text into ordered segments."""
    segments: list[TranscriptSegment] = []
    current_start: float | None = None
    current_lines: list[str] = []

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TIMESTAMP_LINE_RE.match(line)
        if match:
            if current_start is not None:
                merged = " ".join(part for part in current_lines if part).strip()
                if merged:
                    segments.append(TranscriptSegment(start_seconds=current_start, text=merged))
            current_start = _parse_timestamp_to_seconds(match.group("timestamp"))
            current_lines = [match.group("text").strip()]
            continue
        if current_start is not None:
            current_lines.append(line)

    if current_start is not None:
        merged = " ".join(part for part in current_lines if part).strip()
        if merged:
            segments.append(TranscriptSegment(start_seconds=current_start, text=merged))
    return segments


def build_subtitle_document(
    transcription_text: str,
    subtitle_format: str = "srt",
    utterances: list[dict[str, Any]] | None = None,
) -> str:
    """Render one subtitle document from utterances or timestamped transcript text."""
    normalized_format = normalize_subtitle_format(subtitle_format)
    segments = _segments_from_utterances(utterances) if utterances else parse_timestamped_transcript(transcription_text)
    if not segments:
        raise ValueError(
            "Subtitle generation requires utterances or timestamped transcript lines in the format [MM:SS.mmm] text."
        )

    lines: list[str] = ["WEBVTT", ""] if normalized_format == "vtt" else []
    for index, segment in enumerate(segments, start=1):
        next_start = segments[index].start_seconds if index < len(segments) else None
        end_seconds = segment.end_seconds if segment.end_seconds is not None else _infer_segment_end(segment, next_start)
        start_text = _format_subtitle_timestamp(segment.start_seconds, normalized_format)
        end_text = _format_subtitle_timestamp(end_seconds, normalized_format)
        if normalized_format == "srt":
            lines.extend([str(index), f"{start_text} --> {end_text}", segment.text, ""])
        else:
            lines.extend([f"{start_text} --> {end_text}", segment.text, ""])

    document = "\n".join(lines).rstrip()
    return document + "\n"


def infer_subtitle_output_path(input_path: str, subtitle_format: str) -> str:
    """Infer a subtitle filename from one input media path."""
    suffix = normalize_subtitle_format(subtitle_format)
    return f"{Path(input_path).stem}.{suffix}"


def _speech_recognition_sync(
    ctx: InvocationContext,
    input_path: str,
    language: str,
    timestamps: bool,
    task: str,
    enable_itn: bool,
    enable_punc: bool,
    enable_ddc: bool,
    enable_speaker_info: bool,
) -> dict[str, Any]:
    """Run one blocking big-ASR request and normalize the result."""
    normalized_task = "subtitle" if normalize_speech_task(task) == "subtitle" else "asr"
    effective_timestamps = timestamps or normalized_task == "subtitle"
    prepared_media: PreparedMedia | None = None
    client: VolcengineSpeechClient | None = None
    resolved_path = None
    try:
        resolved_path = resolve_workspace_path(input_path)
        prepared_media = _prepare_media_for_volcengine(ctx, input_path, suffix="speech_asr")
        client = VolcengineSpeechClient()
        response = client.recognize_flash(
            user_id=str(ctx.session.user_id),
            media_bytes=prepared_media.media_bytes,
            language=_normalize_language_code(language, target="asr"),
            enable_itn=enable_itn,
            enable_punc=enable_punc,
            enable_ddc=enable_ddc,
            enable_speaker_info=enable_speaker_info,
        )
        utterances = response.get("utterances", [])
        transcription_text = (
            _format_utterances_with_timestamps(utterances)
            if effective_timestamps and utterances
            else str(response.get("text", "")).strip()
        )
        if not transcription_text:
            return {
                "status": "error",
                "message": "Speech recognition returned empty text.",
                "input_path": workspace_relative_path(resolved_path),
                "provider": str(response.get("provider", "")).strip(),
                "model_name": str(response.get("model_name", "")).strip(),
                "task": normalized_task,
                "timestamps": effective_timestamps,
            }

        basic_info = describe_media_metadata(workspace_relative_path(resolved_path))
        return {
            "status": "success",
            "message": f"{transcription_text}\n\n{basic_info}",
            "transcription_text": transcription_text,
            "basic_info": basic_info,
            "input_path": workspace_relative_path(resolved_path),
            "provider": str(response.get("provider", "")).strip(),
            "model_name": str(response.get("model_name", "")).strip(),
            "task": normalized_task,
            "timestamps": effective_timestamps,
            "utterances": utterances,
            "audio_duration_ms": response.get("audio_duration_ms"),
            "request_id": str(response.get("request_id", "")).strip(),
            "log_id": str(response.get("log_id", "")).strip(),
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "speech recognition failed: input_path={} resolved_path={} task={} error_type={} error={!r}",
            input_path,
            resolved_path or "<unresolved>",
            normalized_task,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": (
                f"Speech recognition failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}'): {type(exc).__name__}: {exc}"
            ),
            "input_path": str(input_path),
            "provider": "volcengine_bigasr_flash",
            "model_name": "volc.bigasr.auc_turbo",
            "task": normalized_task,
            "timestamps": effective_timestamps,
        }
    finally:
        if client is not None:
            client.close()
        _cleanup_prepared_media(prepared_media)


def _speech_subtitle_sync(
    ctx: InvocationContext,
    input_path: str,
    language: str,
    subtitle_format: str,
    caption_type: str,
    subtitle_text: str,
    sta_punc_mode: str,
    words_per_line: int | None,
    max_lines: int | None,
    use_itn: bool,
    use_punc: bool,
    use_ddc: bool,
    with_speaker_info: bool,
    use_capitalize: bool,
) -> dict[str, Any]:
    """Run one blocking subtitle generation or subtitle alignment request."""
    prepared_media: PreparedMedia | None = None
    client: VolcengineSpeechClient | None = None
    resolved_path = None
    normalized_subtitle_format = normalize_subtitle_format(subtitle_format)
    normalized_caption_type = normalize_caption_type(caption_type)
    normalized_subtitle_text = str(subtitle_text or "").strip()
    try:
        resolved_path = resolve_workspace_path(input_path)
        prepared_media = _prepare_media_for_volcengine(ctx, input_path, suffix="speech_subtitle")
        client = VolcengineSpeechClient()
        if normalized_subtitle_text:
            response = client.align_subtitles(
                media_bytes=prepared_media.media_bytes,
                mime_type=prepared_media.mime_type,
                subtitle_text=normalized_subtitle_text,
                caption_type="speech" if normalized_caption_type == "auto" else normalized_caption_type,
                sta_punc_mode=normalize_sta_punc_mode(sta_punc_mode),
            )
        else:
            response = client.generate_subtitles(
                media_bytes=prepared_media.media_bytes,
                mime_type=prepared_media.mime_type,
                language=_normalize_language_code(language, target="subtitle"),
                caption_type=normalized_caption_type,
                words_per_line=words_per_line,
                max_lines=max_lines,
                use_itn=use_itn,
                use_punc=use_punc,
                use_ddc=use_ddc,
                with_speaker_info=with_speaker_info,
                use_capitalize=use_capitalize,
            )

        utterances = response.get("utterances", [])
        subtitle_content = build_subtitle_document(
            response.get("text", ""),
            subtitle_format=normalized_subtitle_format,
            utterances=utterances,
        )
        transcription_text = _format_utterances_with_timestamps(utterances)
        basic_info = describe_media_metadata(workspace_relative_path(resolved_path))
        return {
            "status": "success",
            "message": f"Subtitle generation completed for {workspace_relative_path(resolved_path)}.",
            "transcription_text": transcription_text,
            "basic_info": basic_info,
            "input_path": workspace_relative_path(resolved_path),
            "provider": str(response.get("provider", "")).strip(),
            "model_name": str(response.get("model_name", "")).strip(),
            "task": "subtitle",
            "timestamps": True,
            "utterances": utterances,
            "audio_duration_ms": response.get("audio_duration_ms"),
            "subtitle_content": subtitle_content,
            "subtitle_format": normalized_subtitle_format,
            "subtitle_backend": str(response.get("provider", "")).strip(),
            "caption_type": normalized_caption_type,
            "job_id": str(response.get("job_id", "")).strip(),
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "speech subtitle failed: input_path={} resolved_path={} error_type={} error={!r}",
            input_path,
            resolved_path or "<unresolved>",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": (
                f"Speech subtitle failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}'): {type(exc).__name__}: {exc}"
            ),
            "input_path": str(input_path),
            "provider": "volcengine_subtitle_generation",
            "model_name": "volcengine_vc",
            "task": "subtitle",
            "timestamps": True,
            "subtitle_format": normalized_subtitle_format,
        }
    finally:
        if client is not None:
            client.close()
        _cleanup_prepared_media(prepared_media)


def _prepare_media_for_volcengine(ctx: InvocationContext, input_path: str, *, suffix: str) -> PreparedMedia:
    """Convert one workspace audio or video file into mono 16 kHz WAV for Volcengine APIs."""
    resolved_path = resolve_workspace_path(input_path)
    session_dir = generated_session_dir(ctx.session.id)
    with tempfile.NamedTemporaryFile(
        suffix=f"_{suffix}.wav",
        dir=session_dir,
        delete=False,
    ) as temp_file:
        prepared_path = Path(temp_file.name)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(resolved_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(prepared_path),
    ]
    _run_subprocess_checked(command, timeout=600)
    return PreparedMedia(
        input_path=workspace_relative_path(resolved_path),
        prepared_path=prepared_path.resolve(),
        mime_type="audio/wav",
        media_bytes=prepared_path.read_bytes(),
    )


def _run_subprocess_checked(args: list[str], *, timeout: int) -> None:
    """Run one subprocess command and raise a readable error on failure."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout} seconds") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or f"Command failed with exit code {completed.returncode}")


def _cleanup_prepared_media(prepared_media: PreparedMedia | None) -> None:
    """Best-effort cleanup of one transient prepared media file."""
    if prepared_media is None:
        return
    prepared_media.prepared_path.unlink(missing_ok=True)


def _segments_from_utterances(utterances: list[dict[str, Any]] | None) -> list[TranscriptSegment]:
    """Convert timed utterances into subtitle segments."""
    if not utterances:
        return []
    segments: list[TranscriptSegment] = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        start_time = _safe_int(item.get("start_time"))
        end_time = _safe_int(item.get("end_time"))
        if not text or start_time is None:
            continue
        segments.append(
            TranscriptSegment(
                start_seconds=start_time / 1000.0,
                end_seconds=(end_time / 1000.0) if end_time is not None else None,
                text=text,
            )
        )
    return segments


def _format_utterances_with_timestamps(utterances: list[dict[str, Any]]) -> str:
    """Format timed utterances into the legacy timestamped transcript text form."""
    lines: list[str] = []
    for segment in _segments_from_utterances(utterances):
        timestamp = _format_compact_timestamp(segment.start_seconds)
        lines.append(f"[{timestamp}] {segment.text}")
    return "\n".join(lines).strip()


def _format_compact_timestamp(seconds: float) -> str:
    """Format one transcript timestamp using the compact legacy style."""
    safe_seconds = max(0.0, float(seconds))
    total_milliseconds = int(round(safe_seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def _parse_timestamp_to_seconds(timestamp: str) -> float:
    """Parse one transcript timestamp string into seconds."""
    normalized = str(timestamp).strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported timestamp format: {timestamp}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _infer_segment_end(segment: TranscriptSegment, next_start: float | None) -> float:
    """Infer one cue end time from transcript spacing and text length."""
    estimated_duration = min(6.0, max(1.2, len(segment.text) / 12.0))
    if next_start is None:
        return segment.start_seconds + estimated_duration
    if next_start <= segment.start_seconds:
        return segment.start_seconds + estimated_duration
    return max(segment.start_seconds + 0.6, next_start - 0.01)


def _format_subtitle_timestamp(seconds: float, subtitle_format: str) -> str:
    """Format one cue boundary for SRT or VTT output."""
    safe_seconds = max(0.0, float(seconds))
    total_milliseconds = int(round(safe_seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    separator = "," if subtitle_format == "srt" else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


def _normalize_language_code(language: str, *, target: str) -> str:
    """Normalize one loose language hint for the selected backend."""
    normalized = str(language or "").strip()
    if not normalized:
        return ""
    key = normalized.lower()
    if target == "subtitle":
        return _SUBTITLE_LANGUAGE_ALIASES.get(key, normalized)
    return _ASR_LANGUAGE_ALIASES.get(key, normalized)


def _safe_int(value: Any) -> int | None:
    """Convert one loosely typed integer-like value when possible."""
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
