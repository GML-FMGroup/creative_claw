"""Deterministic timeline renderer for short-video production."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    RenderReport,
    ShortVideoTimeline,
)
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class TimelineRenderer:
    """Render a minimal short-video timeline into one MP4 file."""

    def render(
        self,
        *,
        timeline: ShortVideoTimeline,
        asset_manifest: list[AssetManifestEntry],
        audio_manifest: list[AudioManifestEntry],
        output_path: Path,
    ) -> RenderReport:
        """Render a single-clip timeline using the referenced valid video and audio assets."""
        video_clip = _require_single_video_clip(timeline)
        audio_clip = _require_single_audio_clip(timeline)
        video_asset = _find_valid_video_asset(asset_manifest, video_clip.asset_id)
        audio_asset = _find_valid_audio_asset(audio_manifest, audio_clip.audio_id)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(resolve_workspace_path(video_asset.path)),
            "-i",
            str(resolve_workspace_path(audio_asset.path)),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(timeline.duration_seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        _run_media_command(command, timeout=180)
        metadata = _probe_media(output_path)
        video_stream = _find_stream(metadata, "video")
        audio_stream = _find_stream(metadata, "audio")
        return RenderReport(
            output_path=workspace_relative_path(output_path),
            duration_seconds=_duration_seconds(metadata),
            width=_safe_int(video_stream.get("width")) if video_stream else None,
            height=_safe_int(video_stream.get("height")) if video_stream else None,
            video_codec=str(video_stream.get("codec_name", "")).strip() if video_stream else "",
            audio_codec=str(audio_stream.get("codec_name", "")).strip() if audio_stream else "",
            command_summary="ffmpeg mux video and audio to timeline duration",
        )


def _require_single_video_clip(timeline: ShortVideoTimeline):
    if len(timeline.video_tracks) != 1 or len(timeline.video_tracks[0].clips) != 1:
        raise ValueError("Short-video renderer requires exactly one video track with one clip.")
    return timeline.video_tracks[0].clips[0]


def _require_single_audio_clip(timeline: ShortVideoTimeline):
    if len(timeline.audio_tracks) != 1 or len(timeline.audio_tracks[0].clips) != 1:
        raise ValueError("Short-video renderer requires exactly one audio track with one clip.")
    return timeline.audio_tracks[0].clips[0]


def _find_valid_video_asset(
    asset_manifest: list[AssetManifestEntry],
    asset_id: str,
) -> AssetManifestEntry:
    for asset in asset_manifest:
        if asset.asset_id == asset_id and asset.kind == "video" and asset.status == "valid":
            return asset
    raise ValueError(f"Timeline references missing or invalid video asset: {asset_id}")


def _find_valid_audio_asset(
    audio_manifest: list[AudioManifestEntry],
    audio_id: str,
) -> AudioManifestEntry:
    for audio in audio_manifest:
        if audio.audio_id == audio_id and audio.status == "valid":
            return audio
    raise ValueError(f"Timeline references missing or invalid audio asset: {audio_id}")


def _run_media_command(command: list[str], *, timeout: int) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout} seconds") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or f"Command failed with exit code {completed.returncode}")


def _probe_media(path: Path) -> dict:
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
