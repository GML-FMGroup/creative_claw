"""Provider runtime boundary for short-video production."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    ReferenceAssetEntry,
    ShortVideoAssetPlan,
    ShortVideoRenderSettings,
)


class ShortVideoProviderError(RuntimeError):
    """Raised when a short-video provider cannot complete a generation step."""


class ShortVideoProviderRuntime(Protocol):
    """Runtime boundary for provider-backed video and voiceover generation."""

    def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        reference_assets: list[ReferenceAssetEntry],
    ) -> AssetManifestEntry:
        """Generate one provider-backed video clip."""
        ...

    def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
    ) -> AudioManifestEntry:
        """Generate one provider-backed voiceover track."""
        ...


class UnavailableShortVideoProviderRuntime:
    """Default runtime used until real Veo and TTS adapters are wired in."""

    def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        reference_assets: list[ReferenceAssetEntry],
    ) -> AssetManifestEntry:
        """Fail clearly instead of silently falling back to placeholder media."""
        raise ShortVideoProviderError(
            "P0b provider runtime is not configured yet. "
            "Approve/review is implemented; real Veo generation will be wired in the next provider integration step."
        )

    def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
    ) -> AudioManifestEntry:
        """Fail clearly instead of returning silent audio as a success path."""
        raise ShortVideoProviderError(
            "P0b TTS runtime is not configured yet. "
            "Real speech synthesis will be wired in the next provider integration step."
        )
