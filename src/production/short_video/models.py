"""Typed models for short-video production state and timeline rendering."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.production.models import ProductionState


class ShortVideoRenderSettings(BaseModel):
    """Deterministic render settings for one short-video output."""

    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    width: int = 1280
    height: int = 720
    fps: int = 24
    background_color: str = "#111827"


class ReferenceAssetEntry(BaseModel):
    """User-provided reference asset tracked by a short-video production session."""

    reference_asset_id: str
    version: int = 1
    path: str
    source: Literal["user_upload"] = "user_upload"
    role: Literal["product", "person", "style", "unknown"] = "unknown"
    analysis_summary: str = ""
    added_turn_index: int
    status: Literal["valid", "stale", "replaced"] = "valid"
    replaced_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetManifestEntry(BaseModel):
    """Generated or user-provided visual asset tracked by production state."""

    asset_id: str
    version: int = 1
    kind: Literal["image", "video"]
    path: str
    source: Literal["user_upload", "placeholder", "expert", "cache"]
    provider: str = ""
    prompt_ref: str | None = None
    status: Literal["valid", "stale", "failed"] = "valid"
    derived_from: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    stale_reason: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioManifestEntry(BaseModel):
    """Generated or user-provided audio asset tracked by production state."""

    audio_id: str
    version: int = 1
    kind: Literal["voiceover", "bgm", "silent"]
    path: str
    source: Literal["user_upload", "placeholder", "expert", "cache"]
    provider: str = ""
    status: Literal["valid", "stale", "failed"] = "valid"
    derived_from: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    stale_reason: str = ""
    duration_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoClip(BaseModel):
    """One visual clip in a short-video timeline."""

    clip_id: str
    asset_id: str
    start_seconds: float
    duration_seconds: float
    source_in_seconds: float = 0
    fit: Literal["cover", "contain"] = "cover"
    background_color: str = "#000000"
    pad_mode: Literal["freeze_last_frame", "black", "none"] = "freeze_last_frame"


class VideoTrack(BaseModel):
    """A sequence of visual clips rendered in order."""

    track_id: str
    clips: list[VideoClip] = Field(default_factory=list)


class AudioClip(BaseModel):
    """One audio clip in a short-video timeline."""

    clip_id: str
    audio_id: str
    start_seconds: float
    duration_seconds: float
    source_in_seconds: float = 0


class AudioTrack(BaseModel):
    """A sequence of audio clips rendered in order."""

    track_id: str
    kind: Literal["voiceover", "bgm", "silent"]
    clips: list[AudioClip] = Field(default_factory=list)
    gain_db: float = 0


class ShortVideoTimeline(BaseModel):
    """Mechanical render plan consumed by the short-video timeline renderer."""

    timeline_id: str
    duration_seconds: float
    render_settings: ShortVideoRenderSettings = Field(default_factory=ShortVideoRenderSettings)
    video_tracks: list[VideoTrack] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)


class RenderReport(BaseModel):
    """Metadata produced by a timeline render."""

    output_path: str
    duration_seconds: float
    width: int | None = None
    height: int | None = None
    video_codec: str = ""
    audio_codec: str = ""
    command_summary: str = ""


class RenderValidationReport(BaseModel):
    """Validation result for one rendered short-video file."""

    status: Literal["valid", "invalid"]
    path: str
    duration_seconds: float = 0
    width: int | None = None
    height: int | None = None
    has_video: bool = False
    has_audio: bool = False
    issues: list[str] = Field(default_factory=list)


class ShortVideoProductionState(ProductionState):
    """Persisted state for short-video production."""

    brief_summary: str = ""
    reference_assets: list[ReferenceAssetEntry] = Field(default_factory=list)
    asset_manifest: list[AssetManifestEntry] = Field(default_factory=list)
    audio_manifest: list[AudioManifestEntry] = Field(default_factory=list)
    timeline: ShortVideoTimeline | None = None
    render_report: RenderReport | None = None
    render_validation_report: RenderValidationReport | None = None

