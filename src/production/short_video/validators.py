"""Rendered short-video validation helpers."""

from __future__ import annotations

import json
import subprocess

from src.production.short_video.models import RenderValidationReport
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class RenderValidator:
    """Validate that a rendered MP4 is playable and structurally complete."""

    def validate(self, path: str) -> RenderValidationReport:
        """Validate one rendered workspace video file with ffprobe."""
        resolved = resolve_workspace_path(path)
        issues: list[str] = []
        payload = _probe_media(resolved)
        video_stream = _find_stream(payload, "video")
        audio_stream = _find_stream(payload, "audio")
        duration_seconds = _duration_seconds(payload)
        width = _safe_int(video_stream.get("width")) if video_stream else None
        height = _safe_int(video_stream.get("height")) if video_stream else None

        if duration_seconds <= 0:
            issues.append("Rendered video duration must be greater than zero.")
        if video_stream is None:
            issues.append("Rendered video must contain a video stream.")
        if audio_stream is None:
            issues.append("Rendered video must contain an audio stream.")
        if width is None or height is None or width <= 0 or height <= 0:
            issues.append("Rendered video width and height must be positive.")

        return RenderValidationReport(
            status="invalid" if issues else "valid",
            path=workspace_relative_path(resolved),
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            has_video=video_stream is not None,
            has_audio=audio_stream is not None,
            issues=issues,
        )


def _probe_media(path) -> dict:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Required executable not found: ffprobe") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or f"ffprobe failed with exit code {completed.returncode}")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned an unexpected metadata payload")
    return payload


def _find_stream(payload: dict, codec_type: str) -> dict | None:
    for stream in payload.get("streams", []):
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return None


def _duration_seconds(payload: dict) -> float:
    duration = str(payload.get("format", {}).get("duration", "")).strip()
    try:
        return float(duration)
    except ValueError:
        return 0.0


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

