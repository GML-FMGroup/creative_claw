"""Provider helpers for speech synthesis."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any

import requests

from conf.api import API_CONFIG
from src.agents.experts.speech_synthesis.voice_catalog import (
    DEFAULT_SEED_TTS_1_VOICE_TYPE,
    SEED_TTS_2_RESOURCE_ID,
    is_seed_tts_2_resource,
    resolve_seed_tts_2_voice,
    unknown_seed_tts_2_voice_message,
)
from src.logger import logger

_BYTEDANCE_TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
_DEFAULT_RESOURCE_ID = SEED_TTS_2_RESOURCE_ID
_SUPPORTED_AUDIO_FORMATS = {"mp3", "wav", "flac", "pcm"}


def _parse_bool(value: Any) -> bool:
    """Normalize one flexible boolean-like value."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_speech_audio_format(value: str) -> str:
    """Return one supported speech synthesis output format."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _SUPPORTED_AUDIO_FORMATS else "mp3"


def _build_additions(
    *,
    explicit_language: str = "",
    latex_parser: str = "",
    enable_timestamp: bool = False,
) -> str:
    """Serialize the optional ByteDance additions payload."""
    additions = {
        "disable_markdown_filter": True,
        "enable_timestamp": enable_timestamp,
    }
    if explicit_language.strip():
        additions["explicit_language"] = explicit_language.strip()
    if latex_parser.strip():
        additions["latex_parser"] = latex_parser.strip()
    return json.dumps(additions, ensure_ascii=True, separators=(",", ":"))


def _select_requested_voice(*, speaker: str = "", voice_type: str = "", voice_name: str = "") -> str:
    """Return the first explicit voice selector supplied by the caller."""
    return str(speaker or "").strip() or str(voice_type or "").strip() or str(voice_name or "").strip()


def _resolve_speaker_for_resource(
    *,
    resource_id: str,
    speaker: str = "",
    voice_type: str = "",
    voice_name: str = "",
) -> tuple[str, str]:
    """Return the API speaker value and a display name for one TTS resource."""
    requested_voice = _select_requested_voice(speaker=speaker, voice_type=voice_type, voice_name=voice_name)
    if is_seed_tts_2_resource(resource_id):
        resolved_voice = resolve_seed_tts_2_voice(requested_voice)
        if resolved_voice is None:
            raise ValueError(unknown_seed_tts_2_voice_message(requested_voice))
        return resolved_voice.voice_type, resolved_voice.display_name

    return requested_voice or DEFAULT_SEED_TTS_1_VOICE_TYPE, ""


def _parse_stream_json_line(chunk: str | bytes) -> dict[str, Any] | None:
    """Parse one JSON or SSE data line from the ByteDance TTS stream."""
    if isinstance(chunk, bytes):
        chunk = chunk.decode("utf-8")
    line = chunk.strip()
    if not line or line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
        return None
    if line.startswith("data:"):
        line = line.removeprefix("data:").strip()
    if not line or line == "[DONE]":
        return None
    return json.loads(line)


def _request_bytedance_speech_synthesis(
    *,
    user_id: str,
    text: str = "",
    ssml: str = "",
    speaker: str = "",
    voice_type: str = "",
    voice_name: str = "",
    resource_id: str = "",
    audio_format: str = "mp3",
    sample_rate: int = 24000,
    explicit_language: str = "",
    enable_timestamp: bool = False,
    latex_parser: str = "",
) -> dict[str, Any]:
    """Call ByteDance TTS over the unidirectional HTTP streaming API."""
    app_id = os.environ.get("VOLCENGINE_APPID", "").strip() or str(API_CONFIG.VOLCENGINE_APPID).strip()
    access_token = os.environ.get("VOLCENGINE_ACCESS_TOKEN", "").strip() or str(
        API_CONFIG.VOLCENGINE_ACCESS_TOKEN
    ).strip()
    current_resource_id = resource_id.strip() or _DEFAULT_RESOURCE_ID
    try:
        current_speaker, current_voice_name = _resolve_speaker_for_resource(
            resource_id=current_resource_id,
            speaker=speaker,
            voice_type=voice_type,
            voice_name=voice_name,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "provider": "bytedance_tts",
            "model_name": current_resource_id,
        }

    if not app_id or not access_token:
        return {
            "status": "error",
            "message": "ByteDance TTS credentials are not configured. Required: VOLCENGINE_APPID and VOLCENGINE_ACCESS_TOKEN.",
            "provider": "bytedance_tts",
            "model_name": current_resource_id,
            "speaker": current_speaker,
            "voice_name": current_voice_name,
        }

    normalized_format = normalize_speech_audio_format(audio_format)
    payload = {
        "user": {"uid": user_id or "creative_claw_user"},
        "req_params": {
            "speaker": current_speaker,
            "audio_params": {
                "format": normalized_format,
                "sample_rate": int(sample_rate),
                "enable_timestamp": enable_timestamp,
            },
            "additions": _build_additions(
                explicit_language=explicit_language,
                latex_parser=latex_parser,
                enable_timestamp=enable_timestamp,
            ),
        },
    }
    if ssml.strip():
        payload["req_params"]["ssml"] = ssml.strip()
    else:
        payload["req_params"]["text"] = text.strip()

    headers = {
        "X-Api-App-Id": app_id,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": current_resource_id,
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }

    session = requests.Session()
    response = None
    try:
        response = session.post(
            _BYTEDANCE_TTS_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(30, 300),
        )
        if response.status_code >= 400:
            return {
                "status": "error",
                "message": f"ByteDance TTS HTTP {response.status_code}: {response.text[:500]}",
                "provider": "bytedance_tts",
                "model_name": headers["X-Api-Resource-Id"],
            }

        audio_data = bytearray()
        sentence_events: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        log_id = str(response.headers.get("X-Tt-Logid", "")).strip()

        for chunk in response.iter_lines(decode_unicode=True):
            try:
                data = _parse_stream_json_line(chunk)
            except json.JSONDecodeError as exc:
                return {
                    "status": "error",
                    "message": f"ByteDance TTS returned invalid JSON chunk: {exc}",
                    "provider": "bytedance_tts",
                    "model_name": headers["X-Api-Resource-Id"],
                    "log_id": log_id,
                }
            if data is None:
                continue

            code = int(data.get("code", 0) or 0)
            if code == 0 and data.get("data"):
                audio_data.extend(base64.b64decode(data["data"]))
                continue
            if code == 0 and data.get("sentence"):
                sentence_events.append(data["sentence"])
                continue
            if code == 20000000:
                if isinstance(data.get("usage"), dict):
                    usage = data["usage"]
                break
            if code > 0:
                return {
                    "status": "error",
                    "message": f"ByteDance TTS stream returned error payload: {data}",
                    "provider": "bytedance_tts",
                    "model_name": headers["X-Api-Resource-Id"],
                    "log_id": log_id,
                }

        if not audio_data:
            return {
                "status": "error",
                "message": "ByteDance TTS completed without audio data.",
                "provider": "bytedance_tts",
                "model_name": headers["X-Api-Resource-Id"],
                "log_id": log_id,
            }

        return {
            "status": "success",
            "message": bytes(audio_data),
            "provider": "bytedance_tts",
            "model_name": headers["X-Api-Resource-Id"],
            "speaker": payload["req_params"]["speaker"],
            "voice_name": current_voice_name,
            "audio_format": normalized_format,
            "usage": usage,
            "sentences": sentence_events,
            "log_id": log_id,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "ByteDance TTS failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"ByteDance TTS failed: {type(exc).__name__}: {exc}",
            "provider": "bytedance_tts",
            "model_name": current_resource_id,
            "speaker": current_speaker,
            "voice_name": current_voice_name,
        }
    finally:
        if response is not None:
            response.close()
        session.close()


async def speech_synthesis_tool(
    *,
    user_id: str,
    text: str = "",
    ssml: str = "",
    speaker: str = "",
    voice_type: str = "",
    voice_name: str = "",
    resource_id: str = "",
    audio_format: str = "mp3",
    sample_rate: int = 24000,
    explicit_language: str = "",
    enable_timestamp: bool = False,
    latex_parser: str = "",
) -> dict[str, Any]:
    """Call ByteDance TTS without blocking the event loop."""
    return await asyncio.to_thread(
        _request_bytedance_speech_synthesis,
        user_id=user_id,
        text=text,
        ssml=ssml,
        speaker=speaker,
        voice_type=voice_type,
        voice_name=voice_name,
        resource_id=resource_id,
        audio_format=audio_format,
        sample_rate=sample_rate,
        explicit_language=explicit_language,
        enable_timestamp=enable_timestamp,
        latex_parser=latex_parser,
    )
