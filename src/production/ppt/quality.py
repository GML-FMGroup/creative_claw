"""Deterministic quality reporting for PPT production."""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.production.ppt.models import PPTProductionState, PPTQualityCheck, PPTQualityReport
from src.runtime.workspace import resolve_workspace_path


def build_quality_report(state: PPTProductionState, *, report_path: str | None = None) -> PPTQualityReport:
    """Build an explainable quality report from persisted PPT production state."""
    checks = [
        _final_pptx_exists_check(state),
        _slide_count_check(state),
        _outline_check(state),
        _preview_check(state),
        _input_warning_check(state),
        _content_density_check(state),
    ]
    status = _aggregate_status(checks)
    recommendations = _recommendations(checks)
    metrics = _metrics(state)
    return PPTQualityReport(
        status=status,
        summary=_summary_for(status, checks),
        report_path=report_path,
        metrics=metrics,
        checks=checks,
        recommendations=recommendations,
    )


def quality_report_markdown(report: PPTQualityReport | None) -> str:
    """Render a PPT quality report as operator-readable Markdown."""
    if report is None:
        return "# PPT Quality Report\n\nNo quality report has been generated yet.\n"
    lines = [
        "# PPT Quality Report",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        "",
        "## Metrics",
        "",
    ]
    if report.metrics:
        lines.extend(f"- {key}: {value}" for key, value in report.metrics.items())
    else:
        lines.append("- No metrics available.")
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- [{check.status}] {check.check_id}: {check.summary}" for check in report.checks)
    lines.extend(["", "## Recommendations", ""])
    if report.recommendations:
        lines.extend(f"- {item}" for item in report.recommendations)
    else:
        lines.append("- No immediate quality actions found.")
    return "\n".join(lines).rstrip() + "\n"


def _final_pptx_exists_check(state: PPTProductionState) -> PPTQualityCheck:
    path = state.final_artifact.pptx_path if state.final_artifact is not None else ""
    exists = bool(path and resolve_workspace_path(path).is_file()) if path else False
    return _check(
        "final_pptx_exists",
        "structure",
        "pass" if exists else "fail",
        "Final PPTX file exists." if exists else "No final PPTX file is recorded or present on disk.",
        {"path": path},
    )


def _slide_count_check(state: PPTProductionState) -> PPTQualityCheck:
    expected = len(state.deck_spec.slides) if state.deck_spec is not None else 0
    actual = _actual_pptx_slide_count(state)
    if expected <= 0:
        return _check("slide_count", "structure", "fail", "No deck spec slides are available.")
    if actual <= 0:
        return _check("slide_count", "structure", "warning", "Could not inspect generated PPTX slide count.", {"expected": expected, "actual": actual})
    return _check(
        "slide_count",
        "structure",
        "pass" if actual == expected else "warning",
        "Generated PPTX slide count matches the deck spec." if actual == expected else "Generated PPTX slide count differs from the deck spec.",
        {"expected": expected, "actual": actual},
    )


def _outline_check(state: PPTProductionState) -> PPTQualityCheck:
    if state.outline is None or not state.outline.entries:
        return _check("outline_present", "content", "fail", "No outline exists for this PPT production.")
    empty_titles = [entry.sequence_index for entry in state.outline.entries if not entry.title.strip()]
    return _check(
        "outline_present",
        "content",
        "pass" if not empty_titles else "warning",
        "Outline exists and all slides have titles." if not empty_titles else "Some outline entries are missing titles.",
        {"empty_title_slides": empty_titles},
    )


def _preview_check(state: PPTProductionState) -> PPTQualityCheck:
    expected = len(state.deck_spec.slides) if state.deck_spec is not None else 0
    valid = [item for item in state.slide_previews if item.status == "generated" and resolve_workspace_path(item.preview_path).is_file()]
    return _check(
        "preview_images",
        "visual",
        "pass" if expected and len(valid) >= expected else "warning",
        "Preview images were generated for all slides." if expected and len(valid) >= expected else "Preview images are missing or incomplete.",
        {"expected": expected, "actual": len(valid)},
    )


def _input_warning_check(state: PPTProductionState) -> PPTQualityCheck:
    warnings = [item.warning for item in state.inputs if item.warning]
    warnings.extend(state.warnings)
    return _check(
        "input_support",
        "delivery",
        "warning" if warnings else "pass",
        "Some inputs were recorded but not fully used in P0." if warnings else "No input support warnings were recorded.",
        {"warnings": warnings},
    )


def _content_density_check(state: PPTProductionState) -> PPTQualityCheck:
    if state.deck_spec is None:
        return _check("content_density", "content", "not_applicable", "No deck spec exists.")
    dense_slides = [slide.sequence_index for slide in state.deck_spec.slides if len(" ".join(slide.bullets)) > 620 or len(slide.bullets) > 6]
    return _check(
        "content_density",
        "content",
        "warning" if dense_slides else "pass",
        "Some slides may be too dense for presentation use." if dense_slides else "Slide text density is within P0 limits.",
        {"dense_slides": dense_slides},
    )


def _actual_pptx_slide_count(state: PPTProductionState) -> int:
    path = state.final_artifact.pptx_path if state.final_artifact is not None else ""
    if not path:
        return 0
    try:
        from pptx import Presentation

        return len(Presentation(str(resolve_workspace_path(path))).slides)
    except Exception:
        return _zip_slide_count(path)


def _zip_slide_count(path: str) -> int:
    try:
        with zipfile.ZipFile(resolve_workspace_path(path)) as package:
            return len(
                [
                    name
                    for name in package.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                ]
            )
    except Exception:
        return 0


def _metrics(state: PPTProductionState) -> dict[str, object]:
    return {
        "target_pages": state.render_settings.target_pages,
        "outline_slides": len(state.outline.entries) if state.outline is not None else 0,
        "deck_spec_slides": len(state.deck_spec.slides) if state.deck_spec is not None else 0,
        "preview_images": len(state.slide_previews),
        "input_count": len(state.inputs),
        "warnings": len(state.warnings) + len([item for item in state.inputs if item.warning]),
    }


def _aggregate_status(checks: list[PPTQualityCheck]) -> str:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warning" for check in checks):
        return "warning"
    return "pass"


def _summary_for(status: str, checks: list[PPTQualityCheck]) -> str:
    failing = len([check for check in checks if check.status == "fail"])
    warnings = len([check for check in checks if check.status == "warning"])
    if status == "fail":
        return f"PPT generation completed with {failing} failing check(s) and {warnings} warning(s)."
    if status == "warning":
        return f"PPT generation completed with {warnings} warning(s)."
    return "PPT generation completed and deterministic checks passed."


def _recommendations(checks: list[PPTQualityCheck]) -> list[str]:
    recommendations: list[str] = []
    for check in checks:
        if check.status == "pass" or check.status == "not_applicable":
            continue
        if check.check_id == "input_support":
            recommendations.append("If template or source-document fidelity matters, continue with the P1 template/document pipeline.")
        elif check.check_id == "content_density":
            recommendations.append("Split dense slides or shorten bullets before final delivery.")
        elif check.check_id == "preview_images":
            recommendations.append("Install or verify LibreOffice/Poppler if real slide previews are required.")
        else:
            recommendations.append(f"Review check `{check.check_id}` before delivery.")
    return recommendations


def _check(check_id: str, category: str, status: str, summary: str, details: dict[str, object] | None = None) -> PPTQualityCheck:
    return PPTQualityCheck(
        check_id=check_id,
        category=category,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        summary=summary,
        details=details or {},
    )
