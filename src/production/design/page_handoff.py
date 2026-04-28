"""Deterministic page and variant handoff readiness reports for Design."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from src.production.design.models import (
    DesignProductionState,
    HtmlArtifact,
    PageBlueprint,
    PageHandoffItem,
    PageHandoffReport,
)


_READY_ARTIFACT_STATUSES = {"approved", "valid"}


def build_page_handoff(state: DesignProductionState) -> PageHandoffReport:
    """Build a state-derived handoff readiness report for planned pages and variants."""
    layout_plan = state.layout_plan
    pages = list(layout_plan.pages) if layout_plan is not None else []
    items: list[PageHandoffItem] = []
    for page in pages:
        for variant_id in _variant_ids_for_page(state, page.page_id):
            items.append(_handoff_item(state=state, page=page, variant_id=variant_id))

    metrics = _metrics(state=state, items=items, pages=pages)
    status = _report_status(items)
    return PageHandoffReport(
        layout_plan_id=layout_plan.layout_plan_id if layout_plan is not None else "",
        build_mode=state.build_mode,
        status=status,
        summary=_summary(status=status, metrics=metrics),
        items=items,
        metrics=metrics,
    )


def page_handoff_json(report: PageHandoffReport | None) -> str:
    """Render one page handoff report as stable JSON."""
    payload: dict[str, Any] | None = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def page_handoff_markdown(report: PageHandoffReport | None) -> str:
    """Render one page handoff report as Markdown."""
    lines = ["# Page Handoff", ""]
    if report is None:
        lines.append("No page handoff report was generated.")
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            f"- Report ID: {report.report_id}",
            f"- Layout plan ID: {report.layout_plan_id or 'n/a'}",
            f"- Build mode: {report.build_mode}",
            f"- Status: {report.status}",
            f"- Summary: {report.summary}",
            "",
            "## Metrics",
            "",
        ]
    )
    for key, value in sorted(report.metrics.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Pages", ""])
    if not report.items:
        lines.append("- No planned pages were available.")
    for item in report.items:
        variant_label = item.variant_id or "default"
        lines.extend(
            [
                f"### {item.page_title or item.page_id} ({variant_label})",
                "",
                f"- Page ID: {item.page_id or 'n/a'}",
                f"- Path: {item.page_path or 'n/a'}",
                f"- Status: {item.status}",
                f"- Artifact ID: {item.artifact_id or 'none'}",
                f"- Artifact path: {item.artifact_path or 'none'}",
                f"- Artifact status: {item.artifact_status or 'none'}",
                f"- Builder: {item.builder or 'none'}",
            ]
        )
        if item.report_refs:
            lines.append("- Report refs:")
            for name, values in sorted(item.report_refs.items()):
                lines.append(f"  - {name}: {', '.join(values) if values else 'none'}")
        if item.artifact_refs:
            lines.append("- Artifact refs:")
            for name, values in sorted(item.artifact_refs.items()):
                lines.append(f"  - {name}: {', '.join(values) if values else 'none'}")
        if item.source_refs:
            lines.append(f"- Source refs: {', '.join(item.source_refs)}")
        if item.notes:
            lines.append("- Notes:")
            lines.extend(f"  - {note}" for note in item.notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _handoff_item(*, state: DesignProductionState, page: PageBlueprint, variant_id: str) -> PageHandoffItem:
    artifact = _latest_artifact_for_page_variant(state, page_id=page.page_id, variant_id=variant_id)
    report_refs = _report_refs(state, artifact.artifact_id) if artifact is not None else {}
    artifact_refs = _artifact_refs(state, artifact.artifact_id) if artifact is not None else {}
    notes = _item_notes(state=state, page=page, variant_id=variant_id, artifact=artifact)
    return PageHandoffItem(
        page_id=page.page_id,
        page_title=page.title,
        page_path=page.path,
        variant_id=variant_id,
        status=_item_status(state=state, artifact=artifact),
        artifact_id=artifact.artifact_id if artifact is not None else "",
        artifact_path=artifact.path if artifact is not None else "",
        artifact_status=artifact.status if artifact is not None else None,
        artifact_version=artifact.version if artifact is not None else None,
        builder=artifact.builder if artifact is not None else "",
        report_refs=report_refs,
        artifact_refs=artifact_refs,
        source_refs=list(artifact.depends_on) if artifact is not None else [],
        notes=notes,
    )


def _variant_ids_for_page(state: DesignProductionState, page_id: str) -> list[str]:
    planned = _planned_variant_ids(state)
    artifact_variants = {
        _normalize_variant_id(artifact.variant_id)
        for artifact in state.html_artifacts
        if artifact.page_id == page_id
    }
    if not planned:
        planned = ["default"]
    return sorted(set(planned) | artifact_variants)


def _planned_variant_ids(state: DesignProductionState) -> list[str]:
    raw = state.variation_plan
    if raw is None:
        return []
    candidates: list[Any]
    if isinstance(raw, dict):
        value = raw.get("variants") or raw.get("variant_ids") or raw.get("variant_plan") or []
        candidates = value if isinstance(value, list) else [value]
    elif isinstance(raw, list):
        candidates = raw
    else:
        candidates = [raw]
    variant_ids = [_variant_id_from_candidate(candidate) for candidate in candidates]
    return sorted({variant_id for variant_id in variant_ids if variant_id})


def _variant_id_from_candidate(candidate: Any) -> str:
    if isinstance(candidate, str):
        return _normalize_variant_id(candidate)
    if isinstance(candidate, dict):
        for key in ("variant_id", "id", "name", "label"):
            value = str(candidate.get(key, "") or "").strip()
            if value:
                return _normalize_variant_id(value)
    return ""


def _latest_artifact_for_page_variant(
    state: DesignProductionState,
    *,
    page_id: str,
    variant_id: str,
) -> HtmlArtifact | None:
    candidates = [
        artifact
        for artifact in state.html_artifacts
        if artifact.page_id == page_id and _normalize_variant_id(artifact.variant_id) == variant_id
    ]
    active = [artifact for artifact in candidates if artifact.status in _READY_ARTIFACT_STATUSES]
    if active:
        return active[-1]
    return candidates[-1] if candidates else None


def _item_status(*, state: DesignProductionState, artifact: HtmlArtifact | None) -> str:
    if artifact is None:
        return "missing"
    if artifact.status == "stale":
        return "stale"
    if artifact.status not in _READY_ARTIFACT_STATUSES:
        return "partial"
    validation = _latest_validation_report(state, artifact.artifact_id)
    if validation is None or validation.status != "valid":
        return "partial"
    preview_reports = _preview_reports(state, artifact.artifact_id)
    if not preview_reports or any(not report.valid for report in preview_reports):
        return "partial"
    qc_report = _latest_quality_report(state, artifact.artifact_id)
    if qc_report is not None and qc_report.status == "fail":
        return "partial"
    accessibility = _latest_accessibility_report(state, artifact.artifact_id)
    if accessibility is not None and accessibility.status == "fail":
        return "partial"
    diagnostics = _latest_browser_diagnostics_report(state, artifact.artifact_id)
    if diagnostics is not None and diagnostics.status == "fail":
        return "partial"
    return "ready"


def _item_notes(
    *,
    state: DesignProductionState,
    page: PageBlueprint,
    variant_id: str,
    artifact: HtmlArtifact | None,
) -> list[str]:
    notes: list[str] = []
    if variant_id != "default":
        notes.append(f"Variant '{variant_id}' is tracked separately from the default page handoff.")
    if artifact is None:
        notes.append("No HTML artifact was generated for this planned page and variant.")
        if state.build_mode == "single_html":
            notes.append("Current build mode is single_html; later multi-page generation should fill this slot.")
        return notes
    if artifact.status not in _READY_ARTIFACT_STATUSES:
        notes.append(f"HTML artifact status is {artifact.status}.")
    validation = _latest_validation_report(state, artifact.artifact_id)
    if validation is None:
        notes.append("No HTML validation report is linked to this artifact.")
    elif validation.status != "valid":
        notes.append("HTML validation did not pass for this artifact.")
    preview_reports = _preview_reports(state, artifact.artifact_id)
    if not preview_reports:
        notes.append("No browser preview reports are linked to this artifact.")
    elif any(not report.valid for report in preview_reports):
        notes.append("At least one browser preview report is invalid.")
    qc_report = _latest_quality_report(state, artifact.artifact_id)
    if qc_report is not None and qc_report.status == "fail":
        notes.append("Design quality report failed for this artifact.")
    accessibility = _latest_accessibility_report(state, artifact.artifact_id)
    if accessibility is not None and accessibility.status == "fail":
        notes.append("Accessibility report failed for this artifact.")
    diagnostics = _latest_browser_diagnostics_report(state, artifact.artifact_id)
    if diagnostics is not None and diagnostics.status == "fail":
        notes.append("Browser diagnostics failed for this artifact.")
    if page.status == "stale":
        notes.append("The planned page is marked stale.")
    return notes


def _report_refs(state: DesignProductionState, artifact_id: str) -> dict[str, list[str]]:
    return {
        "html_validation_report_ids": [
            report.report_id for report in state.html_validation_reports if report.artifact_id == artifact_id
        ],
        "component_inventory_report_ids": [
            report.report_id for report in state.component_inventory_reports if report.artifact_id == artifact_id
        ],
        "design_system_extraction_report_ids": [
            report.report_id for report in state.design_system_extraction_reports if report.artifact_id == artifact_id
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
    state: DesignProductionState,
    items: list[PageHandoffItem],
    pages: list[PageBlueprint],
) -> dict[str, Any]:
    status_counts = Counter(item.status for item in items)
    variant_ids = sorted({item.variant_id for item in items})
    planned_page_ids = {page.page_id for page in pages}
    item_artifact_ids = {item.artifact_id for item in items if item.artifact_id}
    return {
        "planned_page_count": len(pages),
        "planned_variant_count": len(variant_ids),
        "handoff_item_count": len(items),
        "ready_item_count": status_counts.get("ready", 0),
        "partial_item_count": status_counts.get("partial", 0),
        "missing_item_count": status_counts.get("missing", 0),
        "stale_item_count": status_counts.get("stale", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "variant_ids": variant_ids,
        "planned_page_ids": sorted(planned_page_ids),
        "artifact_count": len(state.html_artifacts),
        "covered_artifact_count": len(item_artifact_ids),
        "unplanned_artifact_count": len(
            [
                artifact
                for artifact in state.html_artifacts
                if artifact.page_id not in planned_page_ids or artifact.artifact_id not in item_artifact_ids
            ]
        ),
    }


def _report_status(items: list[PageHandoffItem]) -> str:
    if not items:
        return "empty"
    if all(item.status == "ready" for item in items):
        return "ready"
    return "partial"


def _summary(*, status: str, metrics: dict[str, Any]) -> str:
    if status == "empty":
        return "No planned pages are available for handoff readiness reporting."
    ready = metrics.get("ready_item_count", 0)
    total = metrics.get("handoff_item_count", 0)
    missing = metrics.get("missing_item_count", 0)
    partial = metrics.get("partial_item_count", 0)
    if status == "ready":
        return f"All {total} planned page/variant handoff item(s) are ready."
    return f"{ready}/{total} planned page/variant handoff item(s) are ready; {partial} partial and {missing} missing."


def _normalize_variant_id(value: str | None) -> str:
    return str(value or "default").strip() or "default"


def _latest_validation_report(state: DesignProductionState, artifact_id: str):
    matches = [report for report in state.html_validation_reports if report.artifact_id == artifact_id]
    return matches[-1] if matches else None


def _preview_reports(state: DesignProductionState, artifact_id: str):
    return [report for report in state.preview_reports if report.artifact_id == artifact_id]


def _latest_quality_report(state: DesignProductionState, artifact_id: str):
    matches = [report for report in state.qc_reports if artifact_id in report.artifact_ids]
    return matches[-1] if matches else None


def _latest_accessibility_report(state: DesignProductionState, artifact_id: str):
    matches = [report for report in state.accessibility_reports if report.artifact_id == artifact_id]
    return matches[-1] if matches else None


def _latest_browser_diagnostics_report(state: DesignProductionState, artifact_id: str):
    matches = [report for report in state.browser_diagnostics_reports if report.artifact_id == artifact_id]
    return matches[-1] if matches else None
