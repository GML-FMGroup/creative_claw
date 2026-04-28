"""Source-reference helpers for Design production outputs."""

from __future__ import annotations

from pathlib import Path

from src.production.design.models import DesignProductionState, HtmlArtifact, PdfExportReport, PreviewReport
from src.production.models import WorkspaceFileRef


_DERIVED_HANDOFF_NAMES = {
    "qc_report.md",
    "design_system_audit.md",
    "component_inventory.md",
    "component_inventory.json",
    "browser_diagnostics.md",
    "browser_diagnostics.json",
    "artifact_lineage.md",
    "artifact_lineage.json",
    "design_spec.md",
    "handoff_manifest.json",
    "design_tokens.json",
    "design_tokens.css",
    "design_handoff_bundle.zip",
}


def source_ref_details(state: DesignProductionState, source_refs: list[str]) -> list[dict[str, str]]:
    """Resolve Design reference asset ids to readable workspace-safe metadata."""
    assets_by_id = {asset.asset_id: asset for asset in state.reference_assets}
    details: list[dict[str, str]] = []
    for source_ref in source_refs:
        asset = assets_by_id.get(source_ref)
        if asset is None:
            details.append(
                {
                    "asset_id": source_ref,
                    "name": source_ref,
                    "path": "",
                    "kind": "",
                    "source": "",
                    "status": "missing",
                    "description": "",
                }
            )
            continue
        details.append(
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "path": asset.path,
                "kind": asset.kind,
                "source": asset.source,
                "status": asset.status,
                "description": asset.description,
            }
        )
    return details


def source_refs_text(state: DesignProductionState, source_refs: list[str]) -> str:
    """Return compact human-readable source reference labels."""
    details = source_ref_details(state, source_refs)
    if not details:
        return ", ".join(source_refs)
    return ", ".join(_format_source_ref_detail(detail) for detail in details)


def html_artifact_source_refs(state: DesignProductionState, artifact_id: str) -> list[str]:
    """Return source refs for one HTML artifact id."""
    artifact = _html_artifact_by_id(state, artifact_id)
    return list(artifact.depends_on) if artifact is not None else []


def preview_report_source_refs(state: DesignProductionState, report: PreviewReport) -> list[str]:
    """Return source refs inherited by one preview report from its HTML artifact."""
    return html_artifact_source_refs(state, report.artifact_id)


def workspace_file_source_refs(state: DesignProductionState, artifact: WorkspaceFileRef) -> list[str]:
    """Return source refs represented by one final workspace file."""
    html = _html_artifact_by_path(state, artifact.path)
    if html is not None:
        return list(html.depends_on)
    preview = _preview_report_by_screenshot_path(state, artifact.path)
    if preview is not None:
        return preview_report_source_refs(state, preview)
    pdf_report = _pdf_export_report_by_path(state, artifact.path)
    if pdf_report is not None:
        return html_artifact_source_refs(state, pdf_report.artifact_id)
    if artifact.name in _DERIVED_HANDOFF_NAMES:
        latest = latest_html_artifact(state)
        return list(latest.depends_on) if latest is not None else []
    return []


def latest_html_artifact(state: DesignProductionState) -> HtmlArtifact | None:
    """Return the latest approved or valid HTML artifact."""
    approved_or_valid = [
        artifact
        for artifact in state.html_artifacts
        if artifact.status in {"approved", "valid"}
    ]
    if approved_or_valid:
        return approved_or_valid[-1]
    return state.html_artifacts[-1] if state.html_artifacts else None


def _html_artifact_by_id(state: DesignProductionState, artifact_id: str) -> HtmlArtifact | None:
    for artifact in state.html_artifacts:
        if artifact.artifact_id == artifact_id:
            return artifact
    return None


def _html_artifact_by_path(state: DesignProductionState, path: str) -> HtmlArtifact | None:
    for artifact in state.html_artifacts:
        if artifact.path == path:
            return artifact
    return None


def _preview_report_by_screenshot_path(state: DesignProductionState, path: str) -> PreviewReport | None:
    for report in state.preview_reports:
        if report.screenshot_path == path:
            return report
    return None


def _pdf_export_report_by_path(state: DesignProductionState, path: str) -> PdfExportReport | None:
    for report in state.pdf_export_reports:
        if report.pdf_path == path:
            return report
    return None


def _format_source_ref_detail(detail: dict[str, str]) -> str:
    name = Path(detail.get("name") or detail.get("asset_id") or "unknown").name
    asset_id = detail.get("asset_id") or ""
    return f"{name}({asset_id})" if asset_id and asset_id != name else name
