"""Typed models for Design production state and HTML artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.production.models import ProductionState, WorkspaceFileRef, new_id, utc_now_iso


DesignGenre = Literal[
    "landing_page",
    "ui_design",
    "product_detail_page",
    "micro_site",
    "one_pager",
    "prototype",
    "wireframe",
]
DesignBuildMode = Literal["single_html", "multi_html"]
DesignArtifactStatus = Literal["draft", "valid", "stale", "failed", "approved"]
ReferenceAssetStatus = Literal["valid", "stale", "replaced", "failed"]


class DesignBrief(BaseModel):
    """User-reviewable design brief for one production session."""

    brief_id: str = Field(default_factory=lambda: new_id("design_brief"))
    version: int = 1
    design_genre: DesignGenre = "landing_page"
    goal: str = ""
    audience: str = ""
    primary_action: str = ""
    selling_points: list[str] = Field(default_factory=list)
    content_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    device_targets: list[Literal["desktop", "mobile", "tablet"]] = Field(default_factory=lambda: ["desktop", "mobile"])
    confirmed: bool = False
    notes: str = ""


class ReferenceAssetEntry(BaseModel):
    """User-provided or generated asset tracked by a design production session."""

    asset_id: str = Field(default_factory=lambda: new_id("design_asset"))
    version: int = 1
    kind: Literal[
        "logo",
        "screenshot",
        "product_photo",
        "reference_image",
        "generated_image",
        "font_file",
        "css_token",
        "other",
    ] = "other"
    path: str = ""
    name: str = ""
    source: Literal["user_upload", "generated", "extracted", "placeholder"] = "user_upload"
    description: str = ""
    extracted_metadata: dict[str, Any] = Field(default_factory=dict)
    derived_from: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: ReferenceAssetStatus = "valid"
    stale_reason: str = ""
    added_turn_index: int = 0


class DesignTokenColor(BaseModel):
    """One named color token for a generated design system."""

    name: str
    value: str
    usage: str = ""


class DesignTokenTypography(BaseModel):
    """One typography token for a generated design system."""

    role: str
    font_family: str
    font_size_px: int | None = None
    font_weight: str = ""
    line_height: str = ""


class DesignSystemSpec(BaseModel):
    """Design tokens and visual rules used by the HTML builder."""

    design_system_id: str = Field(default_factory=lambda: new_id("design_system"))
    version: int = 1
    source: Literal["generated", "extracted", "mixed", "placeholder"] = "generated"
    colors: list[DesignTokenColor] = Field(default_factory=list)
    typography: list[DesignTokenTypography] = Field(default_factory=list)
    spacing: dict[str, str] = Field(default_factory=dict)
    radii: dict[str, str] = Field(default_factory=dict)
    shadows: dict[str, str] = Field(default_factory=dict)
    component_tokens: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class DesignSystemAuditFinding(BaseModel):
    """One deterministic design-system audit finding."""

    finding_id: str = Field(default_factory=lambda: new_id("design_system_audit_finding"))
    severity: Literal["info", "warning", "error"]
    category: Literal["coverage", "naming", "color", "typography", "spacing", "components"]
    target: str = ""
    summary: str
    recommendation: str = ""


class DesignSystemAuditReport(BaseModel):
    """Deterministic audit report for a DesignSystemSpec."""

    report_id: str = Field(default_factory=lambda: new_id("design_system_audit"))
    design_system_id: str = ""
    status: Literal["pass", "warning", "fail"] = "pass"
    summary: str = ""
    findings: list[DesignSystemAuditFinding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class ComponentInventoryItem(BaseModel):
    """One implementation-facing component inventory item."""

    item_id: str = Field(default_factory=lambda: new_id("component_inventory_item"))
    name: str
    category: Literal[
        "section",
        "navigation",
        "button",
        "card",
        "metric",
        "form",
        "media",
        "tokenized_component",
        "html_structure",
        "other",
    ] = "other"
    source: Literal["layout_plan", "design_system", "html_artifact"]
    page_id: str = ""
    section_id: str = ""
    selector: str = ""
    role: str = ""
    description: str = ""
    source_refs: list[str] = Field(default_factory=list)
    token_refs: list[str] = Field(default_factory=list)
    responsive_notes: str = ""
    implementation_notes: list[str] = Field(default_factory=list)


class ComponentInventoryReport(BaseModel):
    """Deterministic component inventory derived from Design production state."""

    report_id: str = Field(default_factory=lambda: new_id("component_inventory"))
    artifact_id: str = ""
    layout_plan_id: str = ""
    design_system_id: str = ""
    status: Literal["ready", "partial", "empty"] = "ready"
    summary: str = ""
    items: list[ComponentInventoryItem] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class LayoutSection(BaseModel):
    """One stable design section that can later be regenerated independently."""

    section_id: str = Field(default_factory=lambda: new_id("section"))
    title: str
    purpose: str = ""
    content: list[str] = Field(default_factory=list)
    required_asset_ids: list[str] = Field(default_factory=list)
    missing_asset_briefs: list[str] = Field(default_factory=list)
    responsive_notes: str = ""
    expert_hints: dict[str, Any] = Field(default_factory=dict)


class PageBlueprint(BaseModel):
    """User-reviewable page plan before HTML generation."""

    page_id: str = Field(default_factory=lambda: new_id("page"))
    title: str
    path: str = "index.html"
    sections: list[LayoutSection] = Field(default_factory=list)
    device_targets: list[str] = Field(default_factory=lambda: ["desktop", "mobile"])
    version: int = 1
    status: Literal["draft", "approved", "stale"] = "draft"


class LayoutPlan(BaseModel):
    """Collection of pages and global layout notes."""

    layout_plan_id: str = Field(default_factory=lambda: new_id("layout_plan"))
    version: int = 1
    pages: list[PageBlueprint] = Field(default_factory=list)
    global_notes: str = ""


class HtmlArtifact(BaseModel):
    """Generated HTML artifact tracked by DesignProductionState."""

    artifact_id: str = Field(default_factory=lambda: new_id("html_artifact"))
    page_id: str
    variant_id: str | None = None
    version: int = 1
    path: str
    builder: Literal["placeholder", "HtmlBuilderExpert.baseline", "HtmlBuilderExpert.variant"]
    section_fragments: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    status: DesignArtifactStatus = "draft"
    stale_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class HtmlValidationReport(BaseModel):
    """Static validation result for one generated HTML artifact."""

    report_id: str = Field(default_factory=lambda: new_id("html_validation"))
    artifact_id: str
    path: str
    status: Literal["valid", "invalid"] = "valid"
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now_iso)


class ViewportSpec(BaseModel):
    """Viewport used for browser preview rendering."""

    name: Literal["desktop", "mobile", "tablet"]
    width: int
    height: int


class PreviewReport(BaseModel):
    """Browser preview result for one HTML artifact."""

    report_id: str = Field(default_factory=lambda: new_id("preview_report"))
    artifact_id: str
    viewport: Literal["desktop", "mobile", "tablet"]
    screenshot_path: str = ""
    console_errors: list[str] = Field(default_factory=list)
    network_failures: list[str] = Field(default_factory=list)
    layout_metrics: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True
    issues: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class PdfExportReport(BaseModel):
    """PDF export result for one HTML artifact."""

    report_id: str = Field(default_factory=lambda: new_id("pdf_export"))
    artifact_id: str
    source_html_path: str
    pdf_path: str = ""
    status: Literal["exported", "unavailable", "failed"] = "exported"
    issues: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class BrowserDiagnosticsFinding(BaseModel):
    """One deterministic finding for browser-dependent Design outputs."""

    finding_id: str = Field(default_factory=lambda: new_id("browser_diagnostics_finding"))
    severity: Literal["info", "warning", "error"]
    category: Literal["preview", "pdf", "environment", "artifact"]
    target: str = ""
    summary: str
    recommendation: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class BrowserDiagnosticsReport(BaseModel):
    """Browser preview and export diagnostics derived from Design production facts."""

    report_id: str = Field(default_factory=lambda: new_id("browser_diagnostics"))
    artifact_id: str = ""
    status: Literal["ready", "warning", "fail"] = "ready"
    summary: str = ""
    findings: list[BrowserDiagnosticsFinding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class ArtifactLineageItem(BaseModel):
    """One HTML artifact lineage entry with linked report ids."""

    item_id: str = Field(default_factory=lambda: new_id("artifact_lineage_item"))
    artifact_id: str
    page_id: str = ""
    path: str = ""
    version: int = 1
    status: DesignArtifactStatus
    builder: str = ""
    build_mode: str = ""
    revision_id: str = ""
    replaces_artifact_ids: list[str] = Field(default_factory=list)
    replaced_by_artifact_id: str = ""
    stale_reason: str = ""
    source_refs: list[str] = Field(default_factory=list)
    report_refs: dict[str, list[str]] = Field(default_factory=dict)
    artifact_refs: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ArtifactLineageReport(BaseModel):
    """State-derived lineage report for Design HTML artifacts and attached reports."""

    report_id: str = Field(default_factory=lambda: new_id("artifact_lineage"))
    latest_artifact_id: str = ""
    status: Literal["ready", "partial", "empty"] = "ready"
    summary: str = ""
    items: list[ArtifactLineageItem] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class DesignQcFinding(BaseModel):
    """One explainable quality finding for a design artifact."""

    finding_id: str = Field(default_factory=lambda: new_id("qc_finding"))
    severity: Literal["info", "warning", "error"]
    category: Literal["brief_fit", "visual", "responsive", "content", "accessibility", "technical"]
    target: str = ""
    summary: str
    recommendation: str = ""


class DesignQcReport(BaseModel):
    """Quality report for generated HTML design artifacts."""

    report_id: str = Field(default_factory=lambda: new_id("design_qc"))
    artifact_ids: list[str] = Field(default_factory=list)
    status: Literal["pass", "warning", "fail"] = "pass"
    summary: str = ""
    findings: list[DesignQcFinding] = Field(default_factory=list)
    report_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class DesignProductionState(ProductionState):
    """Persisted state for Design production."""

    design_genre: DesignGenre | None = None
    build_mode: DesignBuildMode = "single_html"
    brief: DesignBrief | None = None
    reference_assets: list[ReferenceAssetEntry] = Field(default_factory=list)
    design_system: DesignSystemSpec | None = None
    design_system_audit_reports: list[DesignSystemAuditReport] = Field(default_factory=list)
    component_inventory_reports: list[ComponentInventoryReport] = Field(default_factory=list)
    layout_plan: LayoutPlan | None = None
    variation_plan: dict[str, Any] | None = None
    html_artifacts: list[HtmlArtifact] = Field(default_factory=list)
    html_validation_reports: list[HtmlValidationReport] = Field(default_factory=list)
    preview_reports: list[PreviewReport] = Field(default_factory=list)
    pdf_export_reports: list[PdfExportReport] = Field(default_factory=list)
    browser_diagnostics_reports: list[BrowserDiagnosticsReport] = Field(default_factory=list)
    artifact_lineage_reports: list[ArtifactLineageReport] = Field(default_factory=list)
    qc_reports: list[DesignQcReport] = Field(default_factory=list)
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    export_artifacts: list[WorkspaceFileRef] = Field(default_factory=list)
    requested_exports: list[str] = Field(default_factory=list)
