"""Deterministic lineage reports for Design HTML artifacts."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from src.production.design.models import (
    ArtifactLineageItem,
    ArtifactLineageReport,
    DesignProductionState,
    HtmlArtifact,
)
from src.production.design.source_refs import latest_html_artifact


def build_artifact_lineage(state: DesignProductionState) -> ArtifactLineageReport:
    """Build a state-derived lineage report for generated HTML artifacts."""
    latest_artifact = latest_html_artifact(state)
    latest_artifact_id = latest_artifact.artifact_id if latest_artifact is not None else ""
    items = [
        _lineage_item(
            state=state,
            artifact=artifact,
            artifact_index=index,
            latest_artifact_id=latest_artifact_id,
        )
        for index, artifact in enumerate(state.html_artifacts)
    ]
    status = _status(items=items, latest_artifact_id=latest_artifact_id)
    metrics = _metrics(items=items, state=state, latest_artifact_id=latest_artifact_id)
    return ArtifactLineageReport(
        latest_artifact_id=latest_artifact_id,
        status=status,
        summary=_summary(status=status, metrics=metrics),
        items=items,
        metrics=metrics,
    )


def artifact_lineage_json(report: ArtifactLineageReport | None) -> str:
    """Render one artifact lineage report as stable JSON."""
    payload: dict[str, Any] | None = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def artifact_lineage_markdown(report: ArtifactLineageReport | None) -> str:
    """Render one artifact lineage report as Markdown."""
    lines = ["# Artifact Lineage", ""]
    if report is None:
        lines.append("No artifact lineage report was generated.")
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            f"- Status: {report.status}",
            f"- Summary: {report.summary}",
            f"- Latest artifact ID: {report.latest_artifact_id or 'n/a'}",
            f"- Report ID: {report.report_id}",
            "",
            "## Metrics",
            "",
        ]
    )
    for key, value in sorted(report.metrics.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Artifacts", ""])
    if not report.items:
        lines.append("- No HTML artifacts were generated.")
    for item in report.items:
        lines.extend(
            [
                f"### {item.path or item.artifact_id}",
                "",
                f"- Artifact ID: {item.artifact_id}",
                f"- Page ID: {item.page_id or 'n/a'}",
                f"- Version: {item.version}",
                f"- Status: {item.status}",
                f"- Builder: {item.builder or 'n/a'}",
                f"- Build mode: {item.build_mode or 'n/a'}",
                f"- Revision ID: {item.revision_id or 'n/a'}",
                f"- Replaces: {', '.join(item.replaces_artifact_ids) if item.replaces_artifact_ids else 'none'}",
                f"- Replaced by: {item.replaced_by_artifact_id or 'none'}",
                f"- Source refs: {', '.join(item.source_refs) if item.source_refs else 'none'}",
            ]
        )
        if item.stale_reason:
            lines.append(f"- Stale reason: {item.stale_reason}")
        if item.report_refs:
            lines.append("- Report refs:")
            for name, values in sorted(item.report_refs.items()):
                lines.append(f"  - {name}: {', '.join(values) if values else 'none'}")
        if item.artifact_refs:
            lines.append("- Artifact refs:")
            for name, values in sorted(item.artifact_refs.items()):
                lines.append(f"  - {name}: {', '.join(values) if values else 'none'}")
        if item.notes:
            lines.append("- Notes:")
            lines.extend(f"  - {note}" for note in item.notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _lineage_item(
    *,
    state: DesignProductionState,
    artifact: HtmlArtifact,
    artifact_index: int,
    latest_artifact_id: str,
) -> ArtifactLineageItem:
    artifact_ids_before = [
        item.artifact_id
        for item in state.html_artifacts[:artifact_index]
        if item.status in {"stale", "valid", "approved"}
    ]
    build_mode = str(artifact.metadata.get("build_mode") or _build_mode_from_builder(artifact.builder))
    replaces = artifact_ids_before if build_mode == "revision" else []
    replaced_by = latest_artifact_id if artifact.status == "stale" and latest_artifact_id != artifact.artifact_id else ""
    report_refs = _report_refs(state, artifact.artifact_id)
    artifact_refs = _artifact_refs(state, artifact.artifact_id)
    return ArtifactLineageItem(
        artifact_id=artifact.artifact_id,
        page_id=artifact.page_id,
        path=artifact.path,
        version=artifact.version,
        status=artifact.status,
        builder=artifact.builder,
        build_mode=build_mode,
        revision_id=str(artifact.metadata.get("revision_id") or ""),
        replaces_artifact_ids=replaces,
        replaced_by_artifact_id=replaced_by,
        stale_reason=artifact.stale_reason,
        source_refs=list(artifact.depends_on),
        report_refs=report_refs,
        artifact_refs=artifact_refs,
        notes=_notes(artifact=artifact, report_refs=report_refs, artifact_refs=artifact_refs),
    )


def _report_refs(state: DesignProductionState, artifact_id: str) -> dict[str, list[str]]:
    return {
        "html_validation_report_ids": [
            report.report_id for report in state.html_validation_reports if report.artifact_id == artifact_id
        ],
        "component_inventory_report_ids": [
            report.report_id for report in state.component_inventory_reports if report.artifact_id == artifact_id
        ],
        "accessibility_report_ids": [
            report.report_id for report in state.accessibility_reports if report.artifact_id == artifact_id
        ],
        "preview_report_ids": [
            report.report_id for report in state.preview_reports if report.artifact_id == artifact_id
        ],
        "browser_diagnostics_report_ids": [
            report.report_id for report in state.browser_diagnostics_reports if report.artifact_id == artifact_id
        ],
        "quality_report_ids": [
            report.report_id for report in state.qc_reports if artifact_id in report.artifact_ids
        ],
        "pdf_export_report_ids": [
            report.report_id for report in state.pdf_export_reports if report.artifact_id == artifact_id
        ],
    }


def _artifact_refs(state: DesignProductionState, artifact_id: str) -> dict[str, list[str]]:
    return {
        "preview_screenshot_paths": [
            report.screenshot_path
            for report in state.preview_reports
            if report.artifact_id == artifact_id and report.screenshot_path
        ],
        "pdf_paths": [
            report.pdf_path
            for report in state.pdf_export_reports
            if report.artifact_id == artifact_id and report.pdf_path
        ],
    }


def _metrics(
    *,
    items: list[ArtifactLineageItem],
    state: DesignProductionState,
    latest_artifact_id: str,
) -> dict[str, Any]:
    status_counts = Counter(item.status for item in items)
    builder_counts = Counter(item.builder for item in items)
    return {
        "artifact_count": len(items),
        "latest_artifact_id": latest_artifact_id,
        "active_artifact_count": len([item for item in items if item.status in {"approved", "valid"}]),
        "stale_artifact_count": status_counts.get("stale", 0),
        "failed_artifact_count": status_counts.get("failed", 0),
        "revision_count": len(state.revision_history),
        "status_counts": dict(sorted(status_counts.items())),
        "builder_counts": dict(sorted(builder_counts.items())),
        "report_counts": {
            "html_validation": len(state.html_validation_reports),
            "component_inventory": len(state.component_inventory_reports),
            "accessibility": len(state.accessibility_reports),
            "preview": len(state.preview_reports),
            "browser_diagnostics": len(state.browser_diagnostics_reports),
            "quality": len(state.qc_reports),
            "pdf_export": len(state.pdf_export_reports),
        },
    }


def _status(*, items: list[ArtifactLineageItem], latest_artifact_id: str) -> str:
    if not items:
        return "empty"
    if not latest_artifact_id:
        return "partial"
    return "ready"


def _summary(*, status: str, metrics: dict[str, Any]) -> str:
    artifact_count = int(metrics.get("artifact_count") or 0)
    revision_count = int(metrics.get("revision_count") or 0)
    if status == "empty":
        return "No HTML artifacts are available for lineage."
    if status == "partial":
        return f"Artifact lineage is partial across {artifact_count} artifact(s)."
    return f"Artifact lineage is ready across {artifact_count} artifact(s) and {revision_count} revision(s)."


def _build_mode_from_builder(builder: str) -> str:
    if builder == "HtmlBuilderExpert.variant":
        return "revision"
    if builder == "HtmlBuilderExpert.baseline":
        return "baseline"
    return builder or ""


def _notes(
    *,
    artifact: HtmlArtifact,
    report_refs: dict[str, list[str]],
    artifact_refs: dict[str, list[str]],
) -> list[str]:
    notes: list[str] = []
    if artifact.status == "stale" and artifact.stale_reason:
        notes.append("Artifact was superseded by a later build.")
    if not report_refs.get("html_validation_report_ids"):
        notes.append("No validation report is linked to this artifact.")
    if not report_refs.get("accessibility_report_ids"):
        notes.append("No accessibility report is linked to this artifact.")
    if not report_refs.get("quality_report_ids"):
        notes.append("No quality report is linked to this artifact.")
    if not artifact_refs.get("preview_screenshot_paths"):
        notes.append("No preview screenshots are linked to this artifact.")
    return notes
