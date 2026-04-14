"""Provider helpers for music generation."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import requests

from conf.api import API_CONFIG
from src.logger import logger

_MINIMAX_MUSIC_URL = "https://api.minimax.io/v1/music_generation"
_DEFAULT_MUSIC_MODEL = "music-2.5"
_SUPPORTED_MUSIC_FORMATS = {"mp3", "wav", "flac"}


def _parse_bool(value: Any) -> bool:
    """Normalize one flexible boolean-like value."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_music_audio_format(value: str) -> str:
    """Return one supported music output format."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _SUPPORTED_MUSIC_FORMATS else "mp3"


def _build_instrumental_lyrics() -> str:
    """Return a compact instrumental lyric scaffold for BGM-like generation."""
    return "[Intro]\n[Inst]\n[Verse]\n[Inst]\n[Hook]\n[Inst]\n[Outro]\n[Inst]"


def _request_music_generation(
    *,
    prompt: str,
    lyrics: str = "",
    instrumental: bool = True,
    audio_format: str = "mp3",
    sample_rate: int = 44100,
    bitrate: int = 256000,
    model: str = _DEFAULT_MUSIC_MODEL,
) -> dict[str, Any]:
    """Call MiniMax music generation over direct HTTP."""
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip() or str(API_CONFIG.MINIMAX_API_KEY).strip()
    if not api_key:
        return {
            "status": "error",
            "message": "MiniMax API key is not configured. Required: MINIMAX_API_KEY.",
            "provider": "minimax",
            "model_name": model,
        }

    normalized_format = normalize_music_audio_format(audio_format)
    use_lyrics = lyrics.strip() or (_build_instrumental_lyrics() if instrumental else _build_instrumental_lyrics())
    payload = {
        "model": model.strip() or _DEFAULT_MUSIC_MODEL,
        "prompt": prompt.strip(),
        "lyrics": use_lyrics,
        "output_format": "hex",
        "audio_setting": {
            "sample_rate": int(sample_rate),
            "bitrate": int(bitrate),
            "format": normalized_format,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _MINIMAX_MUSIC_URL,
            headers=headers,
            json=payload,
            timeout=(30, 300),
        )
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            return {
                "status": "error",
                "message": f"MiniMax music generation HTTP {response.status_code}: {response.text[:500]}",
                "provider": "minimax",
                "model_name": payload["model"],
            }

        base_resp = body.get("base_resp", {}) if isinstance(body, dict) else {}
        if isinstance(base_resp, dict) and int(base_resp.get("status_code", 0) or 0) != 0:
            return {
                "status": "error",
                "message": f"MiniMax music generation failed: {base_resp}",
                "provider": "minimax",
                "model_name": payload["model"],
            }

        audio_hex = str(body.get("data", {}).get("audio", "")).strip() if isinstance(body, dict) else ""
        if not audio_hex:
            return {
                "status": "error",
                "message": "MiniMax music generation returned no audio data.",
                "provider": "minimax",
                "model_name": payload["model"],
            }

        try:
            audio_bytes = bytes.fromhex(audio_hex)
        except ValueError as exc:
            return {
                "status": "error",
                "message": f"MiniMax music generation returned invalid hex audio: {exc}",
                "provider": "minimax",
                "model_name": payload["model"],
            }

        return {
            "status": "success",
            "message": audio_bytes,
            "provider": "minimax",
            "model_name": payload["model"],
            "audio_format": normalized_format,
            "lyrics_used": use_lyrics,
            "instrumental": not lyrics.strip() and instrumental,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "MiniMax music generation failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"MiniMax music generation failed: {type(exc).__name__}: {exc}",
            "provider": "minimax",
            "model_name": model.strip() or _DEFAULT_MUSIC_MODEL,
        }


async def music_generation_tool(
    *,
    prompt: str,
    lyrics: str = "",
    instrumental: bool = True,
    audio_format: str = "mp3",
    sample_rate: int = 44100,
    bitrate: int = 256000,
    model: str = _DEFAULT_MUSIC_MODEL,
) -> dict[str, Any]:
    """Call MiniMax music generation without blocking the event loop."""
    return await asyncio.to_thread(
        _request_music_generation,
        prompt=prompt,
        lyrics=lyrics,
        instrumental=instrumental,
        audio_format=audio_format,
        sample_rate=sample_rate,
        bitrate=bitrate,
        model=model,
    )
