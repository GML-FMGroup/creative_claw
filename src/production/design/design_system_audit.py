"""Deterministic DesignSystemSpec audit rules."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from src.production.design.models import (
    DesignSystemAuditFinding,
    DesignSystemAuditReport,
    DesignSystemSpec,
)


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CORE_COLOR_NAMES = {"primary", "accent", "ink", "surface"}
_CORE_TYPE_ROLES = {"display", "body"}


def audit_design_system(design_system: DesignSystemSpec | None) -> DesignSystemAuditReport:
    """Run non-blocking deterministic checks over one design system."""
    if design_system is None:
        return DesignSystemAuditReport(
            status="fail",
            summary="No design system is available for audit.",
            findings=[
                DesignSystemAuditFinding(
                    severity="error",
                    category="coverage",
                    target="design_system",
                    summary="DesignSystemSpec is missing.",
                    recommendation="Generate a design system before handoff.",
                )
            ],
            metrics={},
        )

    findings: list[DesignSystemAuditFinding] = []
    color_names = [color.name for color in design_system.colors]
    typography_roles = [item.role for item in design_system.typography]
    hex_colors = [_parse_hex_color(color.value) for color in design_system.colors]
    valid_hex_colors = [color for color in hex_colors if color is not None]

    _audit_name_duplicates(findings, "color", color_names)
    _audit_name_duplicates(findings, "typography", typography_roles)
    _audit_color_coverage(findings, color_names, design_system)
    _audit_color_values(findings, design_system)
    _audit_color_contrast(findings, design_system)
    _audit_palette_variety(findings, valid_hex_colors)
    _audit_typography(findings, design_system)
    _audit_spacing_and_radii(findings, design_system)
    _audit_component_tokens(findings, design_system)

    status = _status_for_findings(findings)
    metrics = {
        "color_count": len(design_system.colors),
        "valid_hex_color_count": len(valid_hex_colors),
        "typography_count": len(design_system.typography),
        "spacing_token_count": len(design_system.spacing),
        "radius_token_count": len(design_system.radii),
        "shadow_token_count": len(design_system.shadows),
        "component_token_count": len(design_system.component_tokens),
        "finding_counts": _finding_counts(findings),
    }
    return DesignSystemAuditReport(
        design_system_id=design_system.design_system_id,
        status=status,
        summary=_summary_for_status(status, findings),
        findings=findings,
        metrics=metrics,
    )


def design_system_audit_markdown(report: DesignSystemAuditReport | None) -> str:
    """Render a Design system audit report as Markdown."""
    if report is None:
        return "# Design System Audit\n\nNo design system audit has been generated.\n"
    lines = [
        "# Design System Audit",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        f"- Report ID: {report.report_id}",
        f"- Design system ID: {report.design_system_id or 'n/a'}",
        "",
        "## Metrics",
        "",
    ]
    if not report.metrics:
        lines.append("- None recorded.")
    else:
        for key, value in sorted(report.metrics.items()):
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("No findings.")
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.severity.upper()} - {finding.category}",
                "",
                f"- Target: {finding.target or 'n/a'}",
                f"- Summary: {finding.summary}",
                f"- Recommendation: {finding.recommendation or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _audit_name_duplicates(findings: list[DesignSystemAuditFinding], group: str, names: Iterable[str]) -> None:
    normalized_names = [_normalize_name(name) for name in names if str(name).strip()]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in normalized_names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    for duplicate in sorted(duplicates):
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="naming",
                target=f"{group}.{duplicate}",
                summary=f"Duplicate {group} token name detected: {duplicate}.",
                recommendation="Use unique token names so downstream CSS variables remain stable.",
            )
        )


def _audit_color_coverage(
    findings: list[DesignSystemAuditFinding],
    color_names: list[str],
    design_system: DesignSystemSpec,
) -> None:
    if len(design_system.colors) < 3:
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="coverage",
                target="colors",
                summary="Design system has fewer than three color tokens.",
                recommendation="Include at least primary, text/ink, and surface/background colors.",
            )
        )
    normalized = {_normalize_name(name) for name in color_names}
    missing = sorted(_CORE_COLOR_NAMES - normalized)
    if missing:
        findings.append(
            DesignSystemAuditFinding(
                severity="info",
                category="coverage",
                target="colors",
                summary=f"Common color token names are missing: {', '.join(missing)}.",
                recommendation="Consider naming core tokens primary, accent, ink, and surface for easier handoff.",
            )
        )


def _audit_color_values(findings: list[DesignSystemAuditFinding], design_system: DesignSystemSpec) -> None:
    for color in design_system.colors:
        value = str(color.value or "").strip()
        if not value:
            findings.append(
                DesignSystemAuditFinding(
                    severity="error",
                    category="color",
                    target=f"color.{color.name}",
                    summary="Color token has an empty value.",
                    recommendation="Provide a valid CSS color value.",
                )
            )
            continue
        if value.startswith("#") and not _HEX_COLOR_RE.match(value):
            findings.append(
                DesignSystemAuditFinding(
                    severity="error",
                    category="color",
                    target=f"color.{color.name}",
                    summary=f"Color token has an invalid hex value: {value}.",
                    recommendation="Use #RGB or #RRGGBB for hex colors.",
                )
            )


def _audit_color_contrast(findings: list[DesignSystemAuditFinding], design_system: DesignSystemSpec) -> None:
    colors_by_name = {_normalize_name(color.name): _parse_hex_color(color.value) for color in design_system.colors}
    ink = colors_by_name.get("ink") or colors_by_name.get("text")
    surface = colors_by_name.get("surface") or colors_by_name.get("background")
    if ink is None or surface is None:
        return
    ratio = _contrast_ratio(ink, surface)
    if ratio < 4.5:
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="color",
                target="color.ink_on_surface",
                summary=f"Ink/surface contrast ratio is {ratio:.2f}:1.",
                recommendation="Use at least 4.5:1 contrast for normal body text.",
            )
        )


def _audit_palette_variety(findings: list[DesignSystemAuditFinding], colors: list[tuple[int, int, int]]) -> None:
    if len(colors) < 3:
        return
    hues = [_rgb_to_hue(color) for color in colors[:4]]
    if max(hues) - min(hues) <= 18:
        findings.append(
            DesignSystemAuditFinding(
                severity="info",
                category="color",
                target="colors",
                summary="First color tokens are very close in hue.",
                recommendation="Check whether the palette is too one-note for the intended design genre.",
            )
        )


def _audit_typography(findings: list[DesignSystemAuditFinding], design_system: DesignSystemSpec) -> None:
    if not design_system.typography:
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="typography",
                target="typography",
                summary="No typography tokens were provided.",
                recommendation="Provide display and body typography roles for handoff.",
            )
        )
        return
    roles = {_normalize_name(item.role) for item in design_system.typography}
    missing_roles = sorted(_CORE_TYPE_ROLES - roles)
    if missing_roles:
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="typography",
                target="typography",
                summary=f"Common typography roles are missing: {', '.join(missing_roles)}.",
                recommendation="Include display and body roles so downstream implementations have clear defaults.",
            )
        )
    for item in design_system.typography:
        if item.font_size_px is not None and item.font_size_px <= 0:
            findings.append(
                DesignSystemAuditFinding(
                    severity="error",
                    category="typography",
                    target=f"typography.{item.role}",
                    summary="Typography token has a non-positive font size.",
                    recommendation="Use positive pixel font sizes.",
                )
            )
        if item.font_size_px is not None and item.font_size_px > 72:
            findings.append(
                DesignSystemAuditFinding(
                    severity="info",
                    category="typography",
                    target=f"typography.{item.role}",
                    summary=f"Typography token uses a large {item.font_size_px}px font size.",
                    recommendation="Confirm the type scale is intentional for the target viewport.",
                )
            )


def _audit_spacing_and_radii(findings: list[DesignSystemAuditFinding], design_system: DesignSystemSpec) -> None:
    if not design_system.spacing:
        findings.append(
            DesignSystemAuditFinding(
                severity="warning",
                category="spacing",
                target="spacing",
                summary="No spacing tokens were provided.",
                recommendation="Provide spacing tokens for repeatable layout rhythm.",
            )
        )
    if not design_system.radii:
        findings.append(
            DesignSystemAuditFinding(
                severity="info",
                category="spacing",
                target="radii",
                summary="No radius tokens were provided.",
                recommendation="Provide radius tokens when the visual system uses rounded components.",
            )
        )
    for name, value in design_system.radii.items():
        px = _parse_px(value)
        if px is not None and px > 8:
            findings.append(
                DesignSystemAuditFinding(
                    severity="info",
                    category="spacing",
                    target=f"radii.{name}",
                    summary=f"Radius token {name} is {px:g}px.",
                    recommendation="Keep radii at 8px or less unless the brief explicitly calls for softer UI.",
                )
            )


def _audit_component_tokens(findings: list[DesignSystemAuditFinding], design_system: DesignSystemSpec) -> None:
    if not design_system.component_tokens:
        findings.append(
            DesignSystemAuditFinding(
                severity="info",
                category="components",
                target="component_tokens",
                summary="No component-level tokens were provided.",
                recommendation="Add component tokens for buttons, cards, forms, or navigation when relevant.",
            )
        )


def _status_for_findings(findings: list[DesignSystemAuditFinding]) -> str:
    if any(finding.severity == "error" for finding in findings):
        return "fail"
    if any(finding.severity == "warning" for finding in findings):
        return "warning"
    return "pass"


def _summary_for_status(status: str, findings: list[DesignSystemAuditFinding]) -> str:
    if status == "pass":
        return "Design system passed deterministic token audit."
    if status == "warning":
        return "Design system audit found non-blocking handoff warnings."
    return "Design system audit found token errors that should be corrected."


def _finding_counts(findings: list[DesignSystemAuditFinding]) -> dict[str, int]:
    counts = {"info": 0, "warning": 0, "error": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _parse_hex_color(value: str) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    if not _HEX_COLOR_RE.match(text):
        return None
    hex_value = text.lstrip("#")
    if len(hex_value) == 3:
        hex_value = "".join(char * 2 for char in hex_value)
    return int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16)


def _contrast_ratio(foreground: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    lighter = max(_relative_luminance(foreground), _relative_luminance(background))
    darker = min(_relative_luminance(foreground), _relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for channel in color:
        srgb = channel / 255
        channels.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _rgb_to_hue(color: tuple[int, int, int]) -> float:
    red, green, blue = [channel / 255 for channel in color]
    max_value = max(red, green, blue)
    min_value = min(red, green, blue)
    delta = max_value - min_value
    if delta == 0:
        return 0.0
    if max_value == red:
        hue = 60 * (((green - blue) / delta) % 6)
    elif max_value == green:
        hue = 60 * (((blue - red) / delta) + 2)
    else:
        hue = 60 * (((red - green) / delta) + 4)
    return hue if not math.isnan(hue) else 0.0


def _parse_px(value: str) -> float | None:
    text = str(value or "").strip().lower()
    match = re.match(r"^(-?\d+(?:\.\d+)?)px$", text)
    return float(match.group(1)) if match else None
