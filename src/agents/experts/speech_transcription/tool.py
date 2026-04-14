"""Compatibility wrappers for the renamed speech recognition helpers."""

from src.agents.experts.speech_recognition.tool import (
    build_subtitle_document,
    describe_media_metadata,
    infer_subtitle_output_path,
    normalize_caption_type,
    normalize_subtitle_format,
    normalize_speech_task,
    normalize_sta_punc_mode,
    parse_bool,
    parse_timestamped_transcript,
    resolve_speech_task,
    resolve_subtitle_format,
    speech_recognition_tool,
    speech_subtitle_tool,
)


async def speech_transcription_tool(*args, **kwargs):
    """Backward-compatible wrapper around `speech_recognition_tool`."""
    kwargs.setdefault("task", "asr")
    return await speech_recognition_tool(*args, **kwargs)
