"""Provider runtime boundary for short-video production."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.agents.experts.speech_synthesis import tool as speech_tools
from src.agents.experts.video_generation import tool as video_tools
from src.production.models import ProductionOwnerRef
from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    ReferenceAssetEntry,
    ShortVideoAssetPlan,
    ShortVideoRenderSettings,
)
from src.runtime.workspace import workspace_relative_path


class ShortVideoProviderError(RuntimeError):
    """Raised when a short-video provider cannot complete a generation step."""


class ShortVideoProviderRuntime(Protocol):
    """Runtime boundary for provider-backed video and voiceover generation."""

    async def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        reference_assets: list[ReferenceAssetEntry],
        owner_ref: ProductionOwnerRef,
    ) -> AssetManifestEntry:
        """Generate one provider-backed video clip."""
        ...

    async def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        owner_ref: ProductionOwnerRef,
    ) -> AudioManifestEntry:
        """Generate one provider-backed voiceover track."""
        ...


class UnavailableShortVideoProviderRuntime:
    """Default runtime used until real Veo and TTS adapters are wired in."""

    async def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        reference_assets: list[ReferenceAssetEntry],
        owner_ref: ProductionOwnerRef,
    ) -> AssetManifestEntry:
        """Fail clearly instead of silently falling back to placeholder media."""
        raise ShortVideoProviderError(
            "This short-video manager instance was configured without a video provider runtime."
        )

    async def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        owner_ref: ProductionOwnerRef,
    ) -> AudioManifestEntry:
        """Fail clearly instead of returning silent audio as a success path."""
        raise ShortVideoProviderError(
            "This short-video manager instance was configured without a TTS provider runtime."
        )


class VeoTtsProviderRuntime:
    """Provider runtime that uses CreativeClaw's existing Veo and TTS tools."""

    _VEO_SUPPORTED_RATIOS = {"16:9", "9:16"}
    _VEO_SUPPORTED_DURATIONS = {4, 6, 8}

    async def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        reference_assets: list[ReferenceAssetEntry],
        owner_ref: ProductionOwnerRef,
    ) -> AssetManifestEntry:
        """Generate one Veo clip and save it inside the production session."""
        ratio = asset_plan.selected_ratio or render_settings.aspect_ratio
        if ratio not in self._VEO_SUPPORTED_RATIOS:
            raise ShortVideoProviderError(
                f"Veo currently supports aspect_ratio 16:9 or 9:16 in this adapter; got {ratio}."
            )
        duration_seconds = int(asset_plan.duration_seconds)
        if duration_seconds not in self._VEO_SUPPORTED_DURATIONS:
            raise ShortVideoProviderError(
                f"Veo currently supports duration_seconds 4, 6, or 8 in this adapter; got {duration_seconds}."
            )

        input_paths = [item.path for item in reference_assets if item.status == "valid"][:3]
        mode = "reference_asset" if input_paths else "prompt"
        result = await video_tools.veo_video_generation_tool(
            asset_plan.shot_plan.visual_prompt,
            input_paths=input_paths or None,
            mode=mode,
            aspect_ratio=ratio,
            resolution="720p",
            duration_seconds=duration_seconds,
        )
        if result.get("status") != "success":
            raise ShortVideoProviderError(str(result.get("message", "Veo generation failed.")))

        video_bytes = result.get("message")
        if not isinstance(video_bytes, bytes):
            raise ShortVideoProviderError("Veo returned a success response without video bytes.")

        output_path = session_root / "assets" / f"{asset_plan.plan_id}_veo.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(video_bytes)
        return AssetManifestEntry(
            asset_id=f"{asset_plan.plan_id}_video",
            kind="video",
            path=workspace_relative_path(output_path),
            source="expert",
            provider=str(result.get("provider", "") or "veo"),
            prompt_ref=asset_plan.plan_id,
            duration_seconds=duration_seconds,
            width=render_settings.width,
            height=render_settings.height,
            derived_from=asset_plan.reference_asset_ids,
            metadata={"model_name": str(result.get("model_name", "") or "")},
        )

    async def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan: ShortVideoAssetPlan,
        render_settings: ShortVideoRenderSettings,
        owner_ref: ProductionOwnerRef,
    ) -> AudioManifestEntry:
        """Generate one TTS voiceover and save it inside the production session."""
        user_id = owner_ref.sender_id or owner_ref.chat_id or "creative_claw_user"
        result = await speech_tools.speech_synthesis_tool(
            user_id=user_id,
            text=asset_plan.shot_plan.voiceover_text,
            audio_format="mp3",
        )
        if result.get("status") != "success":
            raise ShortVideoProviderError(str(result.get("message", "TTS generation failed.")))

        audio_bytes = result.get("message")
        if not isinstance(audio_bytes, bytes):
            raise ShortVideoProviderError("TTS returned a success response without audio bytes.")

        output_path = session_root / "audio" / f"{asset_plan.plan_id}_voiceover.mp3"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)
        return AudioManifestEntry(
            audio_id=f"{asset_plan.plan_id}_voiceover",
            kind="voiceover",
            path=workspace_relative_path(output_path),
            source="expert",
            provider=str(result.get("provider", "") or "bytedance_tts"),
            duration_seconds=asset_plan.duration_seconds,
            metadata={
                "model_name": str(result.get("model_name", "") or ""),
                "speaker": str(result.get("speaker", "") or ""),
                "log_id": str(result.get("log_id", "") or ""),
            },
        )
