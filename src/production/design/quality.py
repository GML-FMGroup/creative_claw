"""Deterministic quality checks for Design production."""

from __future__ import annotations

from src.production.design.models import (
    DesignBrief,
    DesignQcFinding,
    DesignQcReport,
    HtmlArtifact,
    HtmlValidationReport,
    LayoutPlan,
    PreviewReport,
)


def build_quality_report(
    *,
    artifact: HtmlArtifact,
    validation_report: HtmlValidationReport,
    preview_reports: list[PreviewReport],
    brief: DesignBrief | None,
    layout_plan: LayoutPlan | None,
    expert_report: DesignQcReport | None = None,
) -> DesignQcReport:
    """Build a P0 quality report from hard facts plus optional expert guidance."""
    findings: list[DesignQcFinding] = []
    for issue in validation_report.issues:
        findings.append(
            DesignQcFinding(
                severity="error",
                category="technical",
                target=validation_report.path,
                summary=issue,
                recommendation="Fix the generated HTML before treating it as a valid design artifact.",
            )
        )
    for warning in validation_report.warnings:
        findings.append(
            DesignQcFinding(
                severity="warning",
                category="technical",
                target=validation_report.path,
                summary=warning,
                recommendation="Review whether this weakens portability or maintainability.",
            )
        )

    for report in preview_reports:
        for issue in report.issues:
            findings.append(
                DesignQcFinding(
                    severity="warning",
                    category="responsive",
                    target=report.viewport,
                    summary=issue,
                    recommendation="Run browser preview again after fixing local browser dependencies or layout issues.",
                )
            )
        for console_error in report.console_errors:
            findings.append(
                DesignQcFinding(
                    severity="error",
                    category="technical",
                    target=report.viewport,
                    summary=f"Console error: {console_error}",
                    recommendation="Fix client-side errors before approval.",
                )
            )
        for failure in report.network_failures:
            findings.append(
                DesignQcFinding(
                    severity="warning",
                    category="technical",
                    target=report.viewport,
                    summary=f"Network failure: {failure}",
                    recommendation="Avoid external dependencies or provide fallbacks.",
                )
            )

    if brief is None:
        findings.append(
            DesignQcFinding(
                severity="warning",
                category="brief_fit",
                target="brief",
                summary="No design brief is attached to this production state.",
                recommendation="Generate or confirm a brief before real design generation.",
            )
        )
    if layout_plan is None or not layout_plan.pages:
        findings.append(
            DesignQcFinding(
                severity="error",
                category="content",
                target="layout_plan",
                summary="No layout plan is attached to this production state.",
                recommendation="Generate a page blueprint before HTML generation.",
            )
        )

    deterministic_has_error = any(finding.severity == "error" for finding in findings)
    findings.extend(_supplemental_expert_findings(expert_report))

    status = "pass"
    if deterministic_has_error:
        status = "fail"
    elif any(finding.severity == "warning" for finding in findings):
        status = "warning"
    summary = {
        "pass": "HTML design passed deterministic P0 checks.",
        "warning": "HTML design completed with warnings that should be reviewed.",
        "fail": "HTML design failed deterministic P0 checks.",
    }[status]
    return DesignQcReport(
        artifact_ids=[artifact.artifact_id],
        status=status,
        summary=summary,
        findings=findings,
    )


def _supplemental_expert_findings(expert_report: DesignQcReport | None) -> list[DesignQcFinding]:
    """Return expert findings normalized so they cannot create hard failures."""
    if expert_report is None:
        return []
    if not expert_report.findings and expert_report.status != "pass":
        return [
            DesignQcFinding(
                severity="warning",
                category="visual",
                target="DesignQCExpert",
                summary=expert_report.summary or "DesignQCExpert reported a quality concern.",
                recommendation="Review the generated HTML and request a revision if the concern is visible.",
            )
        ]

    normalized: list[DesignQcFinding] = []
    for finding in expert_report.findings:
        severity = "warning" if finding.severity == "error" else finding.severity
        normalized.append(
            DesignQcFinding(
                severity=severity,
                category=finding.category,
                target=finding.target,
                summary=finding.summary,
                recommendation=finding.recommendation,
            )
        )
    if expert_report.status != "pass" and not any(finding.severity == "warning" for finding in normalized):
        normalized.append(
            DesignQcFinding(
                severity="warning",
                category="visual",
                target="DesignQCExpert",
                summary=expert_report.summary or "DesignQCExpert reported a quality concern.",
                recommendation="Review the generated HTML and request a revision if the concern is visible.",
            )
        )
    return normalized


def quality_report_markdown(report: DesignQcReport | None) -> str:
    """Render one quality report as human-readable Markdown."""
    if report is None:
        return "# Design QC Report\n\nNo quality report has been generated.\n"
    lines = [
        "# Design QC Report",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        f"- Report ID: {report.report_id}",
        "",
        "## Findings",
        "",
    ]
    if not report.findings:
        lines.append("No findings.")
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.severity.upper()} · {finding.category}",
                "",
                f"- Target: {finding.target or 'n/a'}",
                f"- Summary: {finding.summary}",
                f"- Recommendation: {finding.recommendation or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
