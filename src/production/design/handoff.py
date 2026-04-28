"""Lightweight handoff exports for completed Design production sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.production.design.models import (
    DesignProductionState,
    DesignQcReport,
    HtmlArtifact,
    HtmlValidationReport,
)
from src.production.models import WorkspaceFileRef, utc_now_iso
from src.runtime.workspace import workspace_relative_path


def write_handoff_exports(
    *,
    state: DesignProductionState,
    session_root: Path,
    core_artifacts: list[WorkspaceFileRef],
) -> list[WorkspaceFileRef]:
    """Write deterministic Design spec and handoff manifest files."""
    export_dir = session_root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    spec_path = export_dir / "design_spec.md"
    manifest_path = export_dir / "handoff_manifest.json"

    spec_ref = WorkspaceFileRef(
        name="design_spec.md",
        path=workspace_relative_path(spec_path),
        description="Design handoff specification derived from production state.",
        source=state.production_session.capability,
    )
    manifest_ref = WorkspaceFileRef(
        name="handoff_manifest.json",
        path=workspace_relative_path(manifest_path),
        description="Machine-readable Design handoff manifest.",
        source=state.production_session.capability,
    )
    handoff_refs = [spec_ref, manifest_ref]

    spec_path.write_text(
        _design_spec_markdown(
            state,
            core_artifacts=core_artifacts,
            handoff_artifacts=handoff_refs,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            _handoff_manifest(state, core_artifacts=core_artifacts, handoff_artifacts=handoff_refs),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return handoff_refs


def _handoff_manifest(
    state: DesignProductionState,
    *,
    core_artifacts: list[WorkspaceFileRef],
    handoff_artifacts: list[WorkspaceFileRef],
) -> dict[str, Any]:
    latest_html = _latest_html_artifact(state)
    latest_qc = _latest_qc_report(state)
    latest_validation = _latest_validation_report(state)
    return {
        "schema_version": "0.1.0",
        "generated_at": utc_now_iso(),
        "production_session_id": state.production_session.production_session_id,
        "capability": state.production_session.capability,
        "status": state.status,
        "stage": state.stage,
        "design_genre": state.design_genre,
        "build_mode": state.build_mode,
        "latest_html_artifact_id": latest_html.artifact_id if latest_html is not None else "",
        "latest_html_path": latest_html.path if latest_html is not None else "",
        "quality_status": latest_qc.status if latest_qc is not None else "",
        "validation_status": latest_validation.status if latest_validation is not None else "",
        "brief": state.brief.model_dump(mode="json") if state.brief is not None else None,
        "design_system": state.design_system.model_dump(mode="json") if state.design_system is not None else None,
        "layout_plan": state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None,
        "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
        "html_artifacts": [item.model_dump(mode="json") for item in state.html_artifacts],
        "preview_reports": [item.model_dump(mode="json") for item in state.preview_reports],
        "quality_reports": [item.model_dump(mode="json") for item in state.qc_reports],
        "revision_history": state.revision_history,
        "deliverables": [item.model_dump(mode="json") for item in core_artifacts],
        "handoff_artifacts": [item.model_dump(mode="json") for item in handoff_artifacts],
        "known_limits": [
            "The core Design deliverable is the approved HTML artifact.",
            "P0b-E does not generate PDF, ZIP, Figma, or production-code handoff outputs.",
            "Screenshots are included only when browser preview rendering is available.",
        ],
    }


def _design_spec_markdown(
    state: DesignProductionState,
    *,
    core_artifacts: list[WorkspaceFileRef],
    handoff_artifacts: list[WorkspaceFileRef],
) -> str:
    brief = state.brief
    latest_html = _latest_html_artifact(state)
    latest_qc = _latest_qc_report(state)
    lines = [
        "# Design Handoff Spec",
        "",
        "## Production",
        "",
        f"- Session: {state.production_session.production_session_id}",
        f"- Capability: {state.production_session.capability}",
        f"- Status: {state.status}",
        f"- Stage: {state.stage}",
        f"- Genre: {state.design_genre or ''}",
        f"- Build mode: {state.build_mode}",
        f"- Latest HTML: {latest_html.path if latest_html is not None else ''}",
        "",
        "## Brief",
        "",
        f"- Goal: {brief.goal if brief is not None else ''}",
        f"- Audience: {brief.audience if brief is not None else ''}",
        f"- Primary action: {brief.primary_action if brief is not None else ''}",
        f"- Confirmed: {brief.confirmed if brief is not None else False}",
        "",
        "### Selling Points",
        "",
    ]
    lines.extend(_bullet_list(brief.selling_points if brief is not None else []))
    lines.extend(["", "### Constraints", ""])
    lines.extend(_bullet_list(brief.constraints if brief is not None else []))
    lines.extend(["", "## Design System", ""])
    if state.design_system is None:
        lines.append("- No design system was generated.")
    else:
        lines.append(f"- Source: {state.design_system.source}")
        lines.append(f"- Notes: {state.design_system.notes}")
        lines.append("- Colors:")
        lines.extend(
            f"  - {color.name}: {color.value} ({color.usage})"
            for color in state.design_system.colors
        )
        lines.append("- Typography:")
        lines.extend(
            (
                f"  - {item.role}: {item.font_family}, {item.font_size_px or ''}px, "
                f"weight {item.font_weight}, line-height {item.line_height}"
            )
            for item in state.design_system.typography
        )
    lines.extend(["", "## Layout", ""])
    if state.layout_plan is None:
        lines.append("- No layout plan was generated.")
    else:
        for page in state.layout_plan.pages:
            lines.append(f"### {page.title}")
            lines.append("")
            lines.append(f"- Path: {page.path}")
            lines.append(f"- Status: {page.status}")
            lines.append("- Sections:")
            for section in page.sections:
                lines.append(f"  - {section.section_id}: {section.title} - {section.purpose}")
            lines.append("")
    lines.extend(["## Quality", ""])
    if latest_qc is None:
        lines.append("- No QC report was generated.")
    else:
        lines.append(f"- Status: {latest_qc.status}")
        lines.append(f"- Summary: {latest_qc.summary}")
        lines.append("- Findings:")
        lines.extend(
            f"  - [{finding.severity}] {finding.category}: {finding.summary}"
            for finding in latest_qc.findings
        )
    lines.extend(["", "## Deliverables", ""])
    for artifact in core_artifacts:
        lines.append(f"- {artifact.name}: {artifact.path} - {artifact.description}")
    lines.extend(["", "## Handoff Files", ""])
    for artifact in handoff_artifacts:
        lines.append(f"- {artifact.name}: {artifact.path} - {artifact.description}")
    lines.extend(
        [
            "",
            "## Known Limits",
            "",
            "- The approved HTML artifact is the durable source of truth for this Design production output.",
            "- PDF, ZIP, Figma, and production-code handoff outputs are intentionally outside P0b-E.",
            "- Browser screenshots may be unavailable in environments without browser automation dependencies.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _latest_html_artifact(state: DesignProductionState) -> HtmlArtifact | None:
    approved_or_valid = [
        artifact
        for artifact in state.html_artifacts
        if artifact.status in {"approved", "valid"}
    ]
    if approved_or_valid:
        return approved_or_valid[-1]
    return state.html_artifacts[-1] if state.html_artifacts else None


def _latest_qc_report(state: DesignProductionState) -> DesignQcReport | None:
    return state.qc_reports[-1] if state.qc_reports else None


def _latest_validation_report(state: DesignProductionState) -> HtmlValidationReport | None:
    return state.html_validation_reports[-1] if state.html_validation_reports else None


def _bullet_list(items: list[str]) -> list[str]:
    if not items:
        return ["- None recorded."]
    return [f"- {item}" for item in items]
