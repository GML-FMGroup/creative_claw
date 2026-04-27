"""Typed models for PPT production state and artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.production.models import ProductionState, new_id, utc_now_iso


PPTInputRole = Literal["template_pptx", "source_doc", "reference_image", "unknown"]
PPTPipeline = Literal["auto", "native", "template", "html_deck"]
PPTAspectRatio = Literal["16:9", "4:3", "9:16"]
PPTStylePreset = Literal[
    "business_executive",
    "pitch_deck",
    "educational",
    "editorial_visual",
]
PPTLayoutType = Literal["cover", "section", "content", "metric", "two_column", "closing"]


class PPTRenderSettings(BaseModel):
    """Deterministic render and planning settings for a PPT production run."""

    target_pages: int = 6
    aspect_ratio: PPTAspectRatio = "16:9"
    style_preset: PPTStylePreset = "business_executive"
    pipeline: PPTPipeline = "auto"
    template_edit_mode: str = "auto"
    brief_review: bool = False
    deck_spec_review: bool = True
    skip_review: bool = False


class IngestEntry(BaseModel):
    """One normalized user input tracked by a PPT production session."""

    input_id: str = Field(default_factory=lambda: new_id("ppt_input"))
    path: str
    name: str
    role: PPTInputRole
    added_turn_index: int
    status: Literal["valid", "unsupported", "stale", "replaced"] = "valid"
    warning: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSummary(BaseModel):
    """Extracted source-document context used by PPT planning."""

    summary_id: str = Field(default_factory=lambda: new_id("document_summary"))
    source_input_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    salient_facts: list[str] = Field(default_factory=list)
    status: Literal["not_started", "unsupported", "ready", "failed"] = "not_started"
    warnings: list[str] = Field(default_factory=list)
    document_count: int = 0
    extracted_character_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class TemplateSummary(BaseModel):
    """Extracted PPT template structure used by PPT planning."""

    summary_id: str = Field(default_factory=lambda: new_id("template_summary"))
    template_input_id: str = ""
    summary: str = ""
    layout_count: int = 0
    status: Literal["not_started", "unsupported", "ready", "failed"] = "not_started"
    warnings: list[str] = Field(default_factory=list)
    slide_count: int = 0
    master_count: int = 0
    media_count: int = 0
    theme_count: int = 0
    detected_fonts: list[str] = Field(default_factory=list)
    detected_colors: list[str] = Field(default_factory=list)
    sample_text: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PPTOutlineEntry(BaseModel):
    """One reviewable slide-level outline item."""

    slide_id: str = Field(default_factory=lambda: new_id("slide"))
    sequence_index: int
    title: str
    purpose: str
    layout_type: PPTLayoutType = "content"
    bullet_points: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    source_refs: list[str] = Field(default_factory=list)
    status: Literal["draft", "approved", "stale"] = "draft"


class PPTOutline(BaseModel):
    """User-reviewable outline for a PPT deck."""

    outline_id: str = Field(default_factory=lambda: new_id("ppt_outline"))
    title: str = "Presentation Outline"
    target_pages: int = 6
    entries: list[PPTOutlineEntry] = Field(default_factory=list)
    status: Literal["draft", "approved", "stale"] = "draft"


class DeckSlide(BaseModel):
    """Executable slide specification derived from an approved outline."""

    slide_id: str
    sequence_index: int
    title: str
    layout_type: PPTLayoutType = "content"
    bullets: list[str] = Field(default_factory=list)
    visual_notes: str = ""
    speaker_notes: str = ""
    status: Literal["draft", "approved", "generated", "stale"] = "draft"


class DeckSpec(BaseModel):
    """Executable deck specification consumed by PPTX builders."""

    deck_spec_id: str = Field(default_factory=lambda: new_id("deck_spec"))
    title: str = "CreativeClaw Deck"
    slides: list[DeckSlide] = Field(default_factory=list)
    status: Literal["draft", "approved", "stale"] = "draft"


class SlidePreview(BaseModel):
    """Preview image and optional segment path for one rendered slide."""

    preview_id: str = Field(default_factory=lambda: new_id("slide_preview"))
    slide_id: str
    sequence_index: int
    preview_path: str
    segment_path: str = ""
    status: Literal["generated", "approved", "stale", "failed"] = "generated"
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinalArtifact(BaseModel):
    """Final PPT output paths for one production run."""

    final_artifact_id: str = Field(default_factory=lambda: new_id("final_ppt"))
    pptx_path: str = ""
    pdf_path: str = ""
    preview_paths: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class PPTQualityCheck(BaseModel):
    """One deterministic PPT quality check result."""

    check_id: str
    category: Literal["structure", "content", "visual", "delivery"]
    status: Literal["pass", "warning", "fail", "not_applicable"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class PPTQualityReport(BaseModel):
    """Explainable quality report for a generated PPT deck."""

    report_id: str = Field(default_factory=lambda: new_id("ppt_quality_report"))
    created_at: str = Field(default_factory=utc_now_iso)
    status: Literal["pass", "warning", "fail"] = "pass"
    summary: str = ""
    report_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    checks: list[PPTQualityCheck] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class PPTProductionState(ProductionState):
    """Persisted state for PPT production."""

    brief_summary: str = ""
    render_settings: PPTRenderSettings = Field(default_factory=PPTRenderSettings)
    inputs: list[IngestEntry] = Field(default_factory=list)
    template_summary: TemplateSummary | None = None
    document_summary: DocumentSummary | None = None
    outline: PPTOutline | None = None
    deck_spec: DeckSpec | None = None
    slide_previews: list[SlidePreview] = Field(default_factory=list)
    final_artifact: FinalArtifact | None = None
    quality_report: PPTQualityReport | None = None
    stale_items: list[str] = Field(default_factory=list)
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
