"""Placeholder asset generation for short-video P0a."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from src.production.models import new_id
from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    ShortVideoRenderSettings,
)
from src.runtime.workspace import workspace_relative_path


class PlaceholderAssetFactory:
    """Create deterministic local placeholder assets for P0a production runs."""

    def create(
        self,
        *,
        session_root: Path,
        render_settings: ShortVideoRenderSettings,
        duration_seconds: float,
    ) -> tuple[list[AssetManifestEntry], list[AudioManifestEntry]]:
        """Create one placeholder image, video clip, and silent audio clip."""
        assets_dir = session_root / "assets"
        audio_dir = session_root / "audio"
        assets_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        image_path = assets_dir / "placeholder_keyframe.png"
        video_path = assets_dir / "placeholder_clip.mp4"
        audio_path = audio_dir / "placeholder_silence.m4a"

        self._create_placeholder_image(image_path, render_settings=render_settings)
        self._create_placeholder_video(
            video_path,
            render_settings=render_settings,
            duration_seconds=duration_seconds,
        )
        self._create_silent_audio(audio_path, duration_seconds=duration_seconds)

        video_asset = AssetManifestEntry(
            asset_id=new_id("asset_video"),
            kind="video",
            path=workspace_relative_path(video_path),
            source="placeholder",
            provider="local_ffmpeg",
            duration_seconds=duration_seconds,
            width=render_settings.width,
            height=render_settings.height,
            metadata={"purpose": "P0a placeholder video clip"},
        )
        image_asset = AssetManifestEntry(
            asset_id=new_id("asset_image"),
            kind="image",
            path=workspace_relative_path(image_path),
            source="placeholder",
            provider="local_pillow",
            width=render_settings.width,
            height=render_settings.height,
            metadata={"purpose": "P0a placeholder keyframe"},
        )
        audio_asset = AudioManifestEntry(
            audio_id=new_id("audio_silent"),
            kind="silent",
            path=workspace_relative_path(audio_path),
            source="placeholder",
            provider="local_ffmpeg",
            duration_seconds=duration_seconds,
            metadata={"purpose": "P0a silent audio bed"},
        )
        return [image_asset, video_asset], [audio_asset]

    @staticmethod
    def _create_placeholder_image(
        output_path: Path,
        *,
        render_settings: ShortVideoRenderSettings,
    ) -> None:
        image = Image.new("RGB", (render_settings.width, render_settings.height), color=(17, 24, 39))
        draw = ImageDraw.Draw(image)
        accent_height = max(8, render_settings.height // 40)
        draw.rectangle((0, 0, render_settings.width, accent_height), fill=(56, 189, 248))
        draw.rectangle(
            (
                render_settings.width // 10,
                render_settings.height // 3,
                render_settings.width * 9 // 10,
                render_settings.height * 2 // 3,
            ),
            outline=(148, 163, 184),
            width=max(2, render_settings.width // 240),
        )
        image.save(output_path)

    @staticmethod
    def _create_placeholder_video(
        output_path: Path,
        *,
        render_settings: ShortVideoRenderSettings,
        duration_seconds: float,
    ) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x111827:s={render_settings.width}x{render_settings.height}:d={duration_seconds}",
            "-vf",
            f"fps={render_settings.fps},format=yuv420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        _run_media_command(command, timeout=120)

    @staticmethod
    def _create_silent_audio(output_path: Path, *, duration_seconds: float) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            str(duration_seconds),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
        _run_media_command(command, timeout=120)


def _run_media_command(command: list[str], *, timeout: int) -> None:
    """Run one local media command and raise a readable failure."""
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

