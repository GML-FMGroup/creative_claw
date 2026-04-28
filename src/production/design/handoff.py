"""Lightweight handoff exports for completed Design production sessions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from src.production.design.models import (
    DesignProductionState,
    DesignQcReport,
    HtmlArtifact,
    HtmlValidationReport,
)
from src.production.design.quality import quality_report_markdown
from src.production.design.source_refs import (
    latest_html_artifact,
    preview_report_source_refs,
    source_ref_details,
    source_refs_text,
    workspace_file_source_refs,
)
from src.production.models import WorkspaceFileRef, utc_now_iso
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_BUNDLE_NAME = "design_handoff_bundle.zip"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


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
    bundle_path = export_dir / _BUNDLE_NAME

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
    bundle_ref = WorkspaceFileRef(
        name=_BUNDLE_NAME,
        path=workspace_relative_path(bundle_path),
        description="Portable ZIP bundle containing Design handoff deliverables.",
        source=state.production_session.capability,
    )
    handoff_refs = [spec_ref, manifest_ref, bundle_ref]

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
    _write_handoff_bundle(
        state=state,
        bundle_path=bundle_path,
        session_root=session_root,
        artifacts=core_artifacts + [spec_ref, manifest_ref],
    )
    return handoff_refs


def _handoff_manifest(
    state: DesignProductionState,
    *,
    core_artifacts: list[WorkspaceFileRef],
    handoff_artifacts: list[WorkspaceFileRef],
) -> dict[str, Any]:
    latest_html = latest_html_artifact(state)
    latest_qc = _latest_qc_report(state)
    latest_validation = _latest_validation_report(state)
    latest_source_refs = list(latest_html.depends_on) if latest_html is not None else []
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
        "latest_source_refs": latest_source_refs,
        "latest_source_ref_details": source_ref_details(state, latest_source_refs),
        "quality_status": latest_qc.status if latest_qc is not None else "",
        "validation_status": latest_validation.status if latest_validation is not None else "",
        "brief": state.brief.model_dump(mode="json") if state.brief is not None else None,
        "design_system": state.design_system.model_dump(mode="json") if state.design_system is not None else None,
        "layout_plan": state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None,
        "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
        "html_artifacts": [_html_artifact_manifest_item(state, item) for item in state.html_artifacts],
        "preview_reports": [_preview_report_manifest_item(state, item) for item in state.preview_reports],
        "pdf_export_reports": [item.model_dump(mode="json") for item in state.pdf_export_reports],
        "quality_reports": [item.model_dump(mode="json") for item in state.qc_reports],
        "revision_history": state.revision_history,
        "deliverables": [_workspace_file_manifest_item(state, item) for item in core_artifacts],
        "handoff_artifacts": [_workspace_file_manifest_item(state, item) for item in handoff_artifacts],
        "known_limits": [
            "The core Design deliverable is the approved HTML artifact.",
            "PDF is an optional export derived from the approved HTML artifact.",
            "Figma and production-code handoff outputs are intentionally outside P1d.",
            "Screenshots are included only when browser preview rendering is available.",
        ],
    }


def _html_artifact_manifest_item(state: DesignProductionState, artifact: HtmlArtifact) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    payload["source_refs"] = list(artifact.depends_on)
    payload["source_ref_details"] = source_ref_details(state, artifact.depends_on)
    return payload


def _preview_report_manifest_item(state: DesignProductionState, report) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    source_refs = preview_report_source_refs(state, report)
    payload["source_refs"] = source_refs
    payload["source_ref_details"] = source_ref_details(state, source_refs)
    return payload


def _workspace_file_manifest_item(state: DesignProductionState, artifact: WorkspaceFileRef) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    source_refs = workspace_file_source_refs(state, artifact)
    payload["source_refs"] = source_refs
    payload["source_ref_details"] = source_ref_details(state, source_refs)
    return payload


def _design_spec_markdown(
    state: DesignProductionState,
    *,
    core_artifacts: list[WorkspaceFileRef],
    handoff_artifacts: list[WorkspaceFileRef],
) -> str:
    brief = state.brief
    latest_html = latest_html_artifact(state)
    latest_qc = _latest_qc_report(state)
    latest_source_refs = list(latest_html.depends_on) if latest_html is not None else []
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
    lines.extend(["", "## Source References", ""])
    if not state.reference_assets:
        lines.append("- No reference assets were used.")
    else:
        lines.append(f"- Latest HTML sources: {source_refs_text(state, latest_source_refs) if latest_source_refs else 'None'}")
        lines.append("- Reference assets:")
        for asset in state.reference_assets:
            lines.append(f"  - {asset.name} ({asset.asset_id}): {asset.path} - {asset.kind}, {asset.status}")
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
    lines.extend(["## PDF Export", ""])
    if not state.pdf_export_reports:
        lines.append("- No PDF export was requested.")
    else:
        for report in state.pdf_export_reports:
            detail = f"{report.status}: {report.pdf_path or '; '.join(report.issues)}"
            lines.append(f"- {report.report_id}: {detail}")
    lines.extend(["", "## Quality", ""])
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
        source_refs = workspace_file_source_refs(state, artifact)
        source_suffix = f" Sources: {source_refs_text(state, source_refs)}." if source_refs else ""
        lines.append(f"- {artifact.name}: {artifact.path} - {artifact.description}{source_suffix}")
    lines.extend(["", "## Handoff Files", ""])
    for artifact in handoff_artifacts:
        source_refs = workspace_file_source_refs(state, artifact)
        source_suffix = f" Sources: {source_refs_text(state, source_refs)}." if source_refs else ""
        lines.append(f"- {artifact.name}: {artifact.path} - {artifact.description}{source_suffix}")
    lines.extend(
        [
            "",
            "## Known Limits",
            "",
            "- The approved HTML artifact is the durable source of truth for this Design production output.",
            "- PDF export is optional and derived from the approved HTML artifact.",
            "- Figma and production-code handoff outputs are intentionally outside P1d.",
            "- Browser screenshots may be unavailable in environments without browser automation dependencies.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_handoff_bundle(
    *,
    state: DesignProductionState,
    bundle_path: Path,
    session_root: Path,
    artifacts: list[WorkspaceFileRef],
) -> None:
    """Write a stable ZIP containing available handoff deliverable files."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    entries = _bundle_entries(
        state=state,
        bundle_path=bundle_path,
        session_root=session_root,
        artifacts=artifacts,
    )
    with zipfile.ZipFile(bundle_path, "w") as archive:
        for arcname, payload in entries:
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)


def _bundle_entries(
    *,
    state: DesignProductionState,
    bundle_path: Path,
    session_root: Path,
    artifacts: list[WorkspaceFileRef],
) -> list[tuple[str, bytes]]:
    seen_paths: set[Path] = {bundle_path.resolve()}
    seen_arcnames: set[str] = set()
    entries: list[tuple[str, bytes]] = []
    for artifact in artifacts:
        entry = _bundle_entry_for_artifact(
            state=state,
            artifact=artifact,
            session_root=session_root,
            seen_paths=seen_paths,
            seen_arcnames=seen_arcnames,
        )
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda item: item[0])


def _bundle_entry_for_artifact(
    *,
    state: DesignProductionState,
    artifact: WorkspaceFileRef,
    session_root: Path,
    seen_paths: set[Path],
    seen_arcnames: set[str],
) -> tuple[str, bytes] | None:
    try:
        resolved = resolve_workspace_path(artifact.path)
    except ValueError:
        return None
    resolved = resolved.resolve()
    if resolved in seen_paths:
        return None
    arcname = _bundle_arcname(artifact, resolved=resolved, session_root=session_root)
    arcname = _unique_arcname(arcname, seen_arcnames)
    payload = _artifact_payload(state, artifact=artifact, resolved=resolved)
    if payload is None:
        return None
    seen_paths.add(resolved)
    seen_arcnames.add(arcname)
    return arcname, payload


def _artifact_payload(
    state: DesignProductionState,
    *,
    artifact: WorkspaceFileRef,
    resolved: Path,
) -> bytes | None:
    if resolved.exists() and resolved.is_file():
        return resolved.read_bytes()
    if artifact.name == "qc_report.md":
        return quality_report_markdown(_latest_qc_report(state)).encode("utf-8")
    return None


def _bundle_arcname(artifact: WorkspaceFileRef, *, resolved: Path, session_root: Path) -> str:
    try:
        arcname = resolved.relative_to(session_root.resolve()).as_posix()
    except ValueError:
        arcname = Path(artifact.path).name
    return arcname.lstrip("/") or artifact.name


def _unique_arcname(arcname: str, seen_arcnames: set[str]) -> str:
    if arcname not in seen_arcnames:
        return arcname
    path = Path(arcname)
    parent = "" if str(path.parent) == "." else f"{path.parent.as_posix()}/"
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = f"{parent}{stem}_{index}{suffix}"
        if candidate not in seen_arcnames:
            return candidate
        index += 1


def _latest_qc_report(state: DesignProductionState) -> DesignQcReport | None:
    return state.qc_reports[-1] if state.qc_reports else None


def _latest_validation_report(state: DesignProductionState) -> HtmlValidationReport | None:
    return state.html_validation_reports[-1] if state.html_validation_reports else None


def _bullet_list(items: list[str]) -> list[str]:
    if not items:
        return ["- None recorded."]
    return [f"- {item}" for item in items]
