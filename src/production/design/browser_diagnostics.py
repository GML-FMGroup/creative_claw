"""Deterministic diagnostics for Design browser preview and export outputs."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Literal, cast

from src.production.design.browser_environment import (
    BROWSER_REMEDIATION,
    browser_environment_metadata,
    browser_environment_recommendation,
    classify_browser_environment_issue,
)
from src.production.design.models import (
    BrowserDiagnosticsFinding,
    BrowserDiagnosticsReport,
    DesignProductionState,
    HtmlArtifact,
    PdfExportReport,
    PreviewReport,
)
from src.production.design.source_refs import latest_html_artifact


def build_browser_diagnostics(
    state: DesignProductionState,
    *,
    artifact: HtmlArtifact | None = None,
) -> BrowserDiagnosticsReport:
    """Build a stable diagnostics report from preview and PDF export facts."""
    target_artifact = artifact or latest_html_artifact(state)
    artifact_id = target_artifact.artifact_id if target_artifact is not None else ""
    preview_reports = _preview_reports_for_artifact(state, artifact_id)
    pdf_reports = _pdf_reports_for_artifact(state, artifact_id)
    findings: list[BrowserDiagnosticsFinding] = []

    if target_artifact is None:
        findings.append(
            _finding(
                severity="warning",
                category="artifact",
                target="html",
                summary="No HTML artifact is available for browser diagnostics.",
                recommendation="Build a valid HTML artifact before relying on browser preview or export outputs.",
            )
        )

    findings.extend(_preview_findings(preview_reports))
    findings.extend(_pdf_findings(pdf_reports))
    metrics = _metrics(preview_reports=preview_reports, pdf_reports=pdf_reports, findings=findings)
    status = _status(findings)
    return BrowserDiagnosticsReport(
        artifact_id=artifact_id,
        status=status,
        summary=_summary(status=status, metrics=metrics),
        findings=findings,
        metrics=metrics,
    )


def browser_diagnostics_markdown(report: BrowserDiagnosticsReport | None) -> str:
    """Render the latest browser diagnostics report as Markdown."""
    lines = ["# Browser Diagnostics", ""]
    if report is None:
        lines.append("No browser diagnostics report was generated.")
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            f"- Report ID: {report.report_id}",
            f"- Artifact ID: {report.artifact_id}",
            f"- Status: {report.status}",
            f"- Summary: {report.summary}",
            "",
            "## Metrics",
            "",
        ]
    )
    for key in sorted(report.metrics):
        lines.append(f"- {key}: {report.metrics[key]}")
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("- No browser diagnostics findings.")
    for finding in report.findings:
        target = f" ({finding.target})" if finding.target else ""
        lines.append(f"- [{finding.severity}] {finding.category}{target}: {finding.summary}")
        if finding.recommendation:
            lines.append(f"  Recommendation: {finding.recommendation}")
    return "\n".join(lines).rstrip() + "\n"


def browser_diagnostics_json(report: BrowserDiagnosticsReport | None) -> str:
    """Render one browser diagnostics report as stable JSON."""
    payload: dict[str, Any] | None = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _preview_findings(preview_reports: list[PreviewReport]) -> list[BrowserDiagnosticsFinding]:
    findings: list[BrowserDiagnosticsFinding] = []
    if not preview_reports:
        return [
            _finding(
                severity="warning",
                category="preview",
                target="preview",
                summary="No browser preview report is available for the latest HTML artifact.",
                recommendation="Run HTML preview rendering before final handoff when visual QA matters.",
            )
        ]

    for report in preview_reports:
        target = f"preview:{report.viewport}"
        if _preview_is_unavailable(report):
            issue_text = _issue_text(report.issues)
            findings.append(
                _finding(
                    severity="warning",
                    category="environment",
                    target=target,
                    summary=f"Browser preview is unavailable for {report.viewport}.",
                    recommendation=browser_environment_recommendation(issue_text) or BROWSER_REMEDIATION,
                    evidence=_preview_evidence(report),
                )
            )
            continue
        if not report.screenshot_path:
            findings.append(
                _finding(
                    severity="warning",
                    category="artifact",
                    target=target,
                    summary=f"Preview screenshot is missing for {report.viewport}.",
                    recommendation="Inspect preview renderer output and rerun browser preview.",
                    evidence=_preview_evidence(report),
                )
            )
        if report.console_errors:
            findings.append(
                _finding(
                    severity="warning",
                    category="preview",
                    target=target,
                    summary=f"Browser console reported {len(report.console_errors)} error(s).",
                    recommendation="Fix runtime JavaScript or asset errors before treating the preview as final.",
                    evidence={"console_errors": report.console_errors[:5], "report_id": report.report_id},
                )
            )
        if report.network_failures:
            findings.append(
                _finding(
                    severity="warning",
                    category="preview",
                    target=target,
                    summary=f"Browser preview recorded {len(report.network_failures)} network failure(s).",
                    recommendation="Remove broken external dependencies or bundle required assets into the workspace.",
                    evidence={"network_failures": report.network_failures[:5], "report_id": report.report_id},
                )
            )
        for issue in report.issues:
            findings.append(_preview_issue_finding(report, issue))
    return findings


def _pdf_findings(pdf_reports: list[PdfExportReport]) -> list[BrowserDiagnosticsFinding]:
    findings: list[BrowserDiagnosticsFinding] = []
    if not pdf_reports:
        return [
            _finding(
                severity="info",
                category="pdf",
                target="pdf",
                summary="No PDF export report is present; this is expected when PDF was not requested.",
                recommendation="Request the pdf export option when a fixed-layout PDF deliverable is needed.",
            )
        ]

    for report in pdf_reports:
        target = f"pdf:{report.report_id}"
        if report.status == "exported":
            if not report.pdf_path:
                findings.append(
                    _finding(
                        severity="error",
                        category="artifact",
                        target=target,
                        summary="PDF export is marked exported but no PDF path was recorded.",
                        recommendation="Rerun PDF export and verify the generated artifact path.",
                        evidence=_pdf_evidence(report),
                    )
                )
            continue
        if report.status == "unavailable":
            issue_text = _issue_text(report.issues)
            findings.append(
                _finding(
                    severity="warning",
                    category="environment",
                    target=target,
                    summary="PDF export is unavailable in the current browser environment.",
                    recommendation=browser_environment_recommendation(issue_text) or BROWSER_REMEDIATION,
                    evidence=_pdf_evidence(report),
                )
            )
            continue
        findings.append(
            _finding(
                severity="error",
                category="pdf",
                target=target,
                summary="PDF export failed.",
                recommendation="Inspect the PDF export issue details and rerun export from the approved HTML.",
                evidence=_pdf_evidence(report),
            )
        )
    return findings


def _preview_issue_finding(report: PreviewReport, issue: str) -> BrowserDiagnosticsFinding:
    issue_lower = issue.lower()
    severity = "warning"
    recommendation = "Review the rendered page in browser preview before final approval."
    if "browser preview failed" in issue_lower or "screenshot file is empty" in issue_lower:
        severity = "error"
        recommendation = "Rerun browser preview after fixing the preview environment or generated HTML."
    if "horizontal overflow" in issue_lower:
        recommendation = "Adjust responsive CSS so the page fits within the target viewport width."
    return _finding(
        severity=severity,
        category="preview",
        target=f"preview:{report.viewport}",
        summary=issue,
        recommendation=recommendation,
        evidence=_preview_evidence(report),
    )


def _metrics(
    *,
    preview_reports: list[PreviewReport],
    pdf_reports: list[PdfExportReport],
    findings: list[BrowserDiagnosticsFinding],
) -> dict[str, Any]:
    finding_counts = Counter(finding.severity for finding in findings)
    return {
        "preview_report_count": len(preview_reports),
        "preview_valid_count": len([report for report in preview_reports if report.valid]),
        "screenshot_count": len([report for report in preview_reports if report.screenshot_path]),
        "preview_unavailable_count": len([report for report in preview_reports if _preview_is_unavailable(report)]),
        "pdf_export_report_count": len(pdf_reports),
        "pdf_exported_count": len([report for report in pdf_reports if report.status == "exported"]),
        "pdf_unavailable_count": len([report for report in pdf_reports if report.status == "unavailable"]),
        "pdf_failed_count": len([report for report in pdf_reports if report.status == "failed"]),
        "browser_environment_status": _browser_environment_status(
            preview_reports=preview_reports,
            pdf_reports=pdf_reports,
        ),
        "browser_environment_remediation": _browser_environment_remediation(
            preview_reports=preview_reports,
            pdf_reports=pdf_reports,
        ),
        "finding_counts": {
            "info": finding_counts.get("info", 0),
            "warning": finding_counts.get("warning", 0),
            "error": finding_counts.get("error", 0),
        },
    }


def _summary(*, status: str, metrics: dict[str, Any]) -> str:
    if status == "fail":
        return (
            "Browser diagnostics found blocking preview or export failures "
            f"({metrics['finding_counts']['error']} error finding(s))."
        )
    if status == "warning":
        return (
            "Browser diagnostics found non-blocking preview or export warnings "
            f"({metrics['finding_counts']['warning']} warning finding(s))."
        )
    return "Browser preview and export diagnostics are ready with no warning or error findings."


def _status(findings: list[BrowserDiagnosticsFinding]) -> str:
    severities = {finding.severity for finding in findings}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "warning"
    return "ready"


def _preview_reports_for_artifact(state: DesignProductionState, artifact_id: str) -> list[PreviewReport]:
    if not artifact_id:
        return list(state.preview_reports)
    return [report for report in state.preview_reports if report.artifact_id == artifact_id]


def _pdf_reports_for_artifact(state: DesignProductionState, artifact_id: str) -> list[PdfExportReport]:
    if not artifact_id:
        return list(state.pdf_export_reports)
    return [report for report in state.pdf_export_reports if report.artifact_id == artifact_id]


def _preview_is_unavailable(report: PreviewReport) -> bool:
    if report.layout_metrics.get("preview") == "unavailable":
        return True
    return classify_browser_environment_issue(_issue_text(report.issues)) is not None


def _preview_evidence(report: PreviewReport) -> dict[str, Any]:
    evidence = {
        "report_id": report.report_id,
        "viewport": report.viewport,
        "issues": report.issues[:5],
        "screenshot_path": report.screenshot_path,
    }
    evidence.update(browser_environment_metadata(_issue_text(report.issues)))
    return evidence


def _pdf_evidence(report: PdfExportReport) -> dict[str, Any]:
    evidence = {
        "report_id": report.report_id,
        "status": report.status,
        "pdf_path": report.pdf_path,
        "issues": report.issues[:5],
    }
    evidence.update(browser_environment_metadata(_issue_text(report.issues)))
    return evidence


def _browser_environment_status(
    *,
    preview_reports: list[PreviewReport],
    pdf_reports: list[PdfExportReport],
) -> str:
    if any(_preview_is_unavailable(report) for report in preview_reports):
        return "unavailable"
    if any(report.status == "unavailable" for report in pdf_reports):
        return "unavailable"
    if not preview_reports:
        return "unchecked"
    return "ready"


def _browser_environment_remediation(
    *,
    preview_reports: list[PreviewReport],
    pdf_reports: list[PdfExportReport],
) -> str:
    for issue_text in [_issue_text(report.issues) for report in preview_reports]:
        recommendation = browser_environment_recommendation(issue_text)
        if recommendation:
            return recommendation
    for issue_text in [_issue_text(report.issues) for report in pdf_reports]:
        recommendation = browser_environment_recommendation(issue_text)
        if recommendation:
            return recommendation
    return ""


def _issue_text(issues: list[str]) -> str:
    return "\n".join(issues)


def _finding(
    *,
    severity: str,
    category: str,
    target: str,
    summary: str,
    recommendation: str = "",
    evidence: dict[str, Any] | None = None,
) -> BrowserDiagnosticsFinding:
    return BrowserDiagnosticsFinding(
        severity=cast(Literal["info", "warning", "error"], severity),
        category=cast(Literal["preview", "pdf", "environment", "artifact"], category),
        target=target,
        summary=summary,
        recommendation=recommendation,
        evidence=evidence or {},
    )
