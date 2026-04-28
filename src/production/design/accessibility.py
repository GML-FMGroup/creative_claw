"""Deterministic accessibility checks for generated Design HTML."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from src.production.design.models import (
    AccessibilityFinding,
    AccessibilityReport,
    DesignProductionState,
    HtmlArtifact,
)
from src.production.design.source_refs import latest_html_artifact
from src.runtime.workspace import resolve_workspace_path


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "summary"}
_FORM_TAGS = {"input", "select", "textarea"}
_VOID_TEXT_TAGS = {"img", "input", "meta", "link", "br", "hr", "source", "track", "area", "base", "col", "embed", "param", "wbr"}


def build_accessibility_report(
    state: DesignProductionState,
    *,
    artifact: HtmlArtifact | None = None,
) -> AccessibilityReport:
    """Build a deterministic accessibility report for one generated HTML artifact."""
    target_artifact = artifact or latest_html_artifact(state)
    if target_artifact is None:
        finding = _finding(
            severity="warning",
            category="document",
            target="html",
            summary="No HTML artifact is available for accessibility checks.",
            recommendation="Build a valid HTML artifact before relying on accessibility handoff signals.",
        )
        return AccessibilityReport(
            status="warning",
            summary="Accessibility checks could not inspect a generated HTML artifact.",
            findings=[finding],
            metrics={"finding_counts": {"warning": 1}},
        )

    try:
        html = resolve_workspace_path(target_artifact.path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        finding = _finding(
            severity="error",
            category="document",
            target=target_artifact.path,
            summary=f"Accessibility checks could not read the HTML artifact: {type(exc).__name__}.",
            recommendation="Regenerate the HTML artifact or verify the workspace-relative artifact path.",
        )
        return AccessibilityReport(
            artifact_id=target_artifact.artifact_id,
            path=target_artifact.path,
            status="fail",
            summary="Accessibility checks failed because the HTML artifact could not be read.",
            findings=[finding],
            metrics={"finding_counts": {"error": 1}},
        )

    summary = _parse_accessibility(html)
    findings = [
        *_document_findings(summary),
        *_landmark_findings(summary),
        *_heading_findings(summary),
        *_media_findings(summary),
        *_control_findings(summary),
        *_form_findings(summary),
        *_keyboard_findings(summary),
    ]
    metrics = _metrics(summary=summary, findings=findings)
    status = _status(findings)
    return AccessibilityReport(
        artifact_id=target_artifact.artifact_id,
        path=target_artifact.path,
        status=status,
        summary=_summary(status=status, metrics=metrics),
        findings=findings,
        metrics=metrics,
    )


def accessibility_report_json(report: AccessibilityReport | None) -> str:
    """Render one accessibility report as stable JSON."""
    payload: dict[str, Any] | None = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def accessibility_report_markdown(report: AccessibilityReport | None) -> str:
    """Render one accessibility report as Markdown."""
    lines = ["# Accessibility Report", ""]
    if report is None:
        lines.append("No accessibility report was generated.")
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            f"- Report ID: {report.report_id}",
            f"- Artifact ID: {report.artifact_id or 'n/a'}",
            f"- Path: {report.path or 'n/a'}",
            f"- Status: {report.status}",
            f"- Summary: {report.summary}",
            "",
            "## Metrics",
            "",
        ]
    )
    for key, value in sorted(report.metrics.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("- No accessibility findings.")
    for finding in report.findings:
        target = f" ({finding.target})" if finding.target else ""
        lines.append(f"- [{finding.severity}] {finding.category}{target}: {finding.summary}")
        if finding.recommendation:
            lines.append(f"  Recommendation: {finding.recommendation}")
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class _ElementText:
    tag: str
    attrs: dict[str, str]
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


@dataclass
class _Heading:
    tag: str
    text: str
    attrs: dict[str, str]

    @property
    def level(self) -> int:
        return int(self.tag[1])


@dataclass
class _Control:
    tag: str
    attrs: dict[str, str]
    text: str = ""


@dataclass
class _FormControl:
    tag: str
    attrs: dict[str, str]
    wrapped_by_label: bool = False


@dataclass
class _AccessibilitySummary:
    tag_counts: dict[str, int]
    html_lang: str = ""
    title: str = ""
    has_viewport_meta: bool = False
    landmark_counts: dict[str, int] = field(default_factory=dict)
    headings: list[_Heading] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    controls: list[_Control] = field(default_factory=list)
    form_controls: list[_FormControl] = field(default_factory=list)
    label_for_ids: set[str] = field(default_factory=set)
    onclick_elements: list[dict[str, str]] = field(default_factory=list)


class _AccessibilityParser(HTMLParser):
    """Collect the small HTML facts needed for static accessibility checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag_counts: Counter[str] = Counter()
        self.html_lang = ""
        self.title_parts: list[str] = []
        self.has_viewport_meta = False
        self.landmark_counts: Counter[str] = Counter()
        self.headings: list[_Heading] = []
        self.images: list[dict[str, str]] = []
        self.controls: list[_Control] = []
        self.form_controls: list[_FormControl] = []
        self.label_for_ids: set[str] = set()
        self.onclick_elements: list[dict[str, str]] = []
        self._text_stack: list[_ElementText] = []
        self._title_depth = 0
        self._label_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self.tag_counts[tag] += 1
        if tag == "html":
            self.html_lang = attr_map.get("lang", "").strip()
        if tag == "title":
            self._title_depth += 1
        if tag == "meta" and attr_map.get("name", "").strip().lower() == "viewport":
            self.has_viewport_meta = True
        if tag in {"main", "nav", "header", "footer", "aside"}:
            self.landmark_counts[tag] += 1
        if tag in {"section", "form"}:
            explicit_role = attr_map.get("role", "").strip().lower()
            if explicit_role:
                self.landmark_counts[f"role:{explicit_role}"] += 1
        if tag == "img":
            self.images.append(attr_map)
        if tag == "label":
            self._label_depth += 1
            label_for = attr_map.get("for", "").strip()
            if label_for:
                self.label_for_ids.add(label_for)
        if tag in _FORM_TAGS:
            self.form_controls.append(_FormControl(tag=tag, attrs=attr_map, wrapped_by_label=self._label_depth > 0))
        if tag in {"a", "button"} or tag in _HEADING_TAGS or tag == "label":
            self._text_stack.append(_ElementText(tag=tag, attrs=attr_map))
        elif tag not in _VOID_TEXT_TAGS:
            self._text_stack.append(_ElementText(tag=tag, attrs=attr_map))
        if tag in {"a", "button"} and tag in _VOID_TEXT_TAGS:
            self.controls.append(_Control(tag=tag, attrs=attr_map))
        if "onclick" in attr_map and tag not in _INTERACTIVE_TAGS:
            self.onclick_elements.append({"tag": tag, **attr_map})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "label" and self._label_depth:
            self._label_depth -= 1

        for index in range(len(self._text_stack) - 1, -1, -1):
            if self._text_stack[index].tag != tag:
                continue
            element = self._text_stack.pop(index)
            if tag in _HEADING_TAGS:
                self.headings.append(_Heading(tag=tag, text=element.text, attrs=element.attrs))
            elif tag in {"a", "button"}:
                self.controls.append(_Control(tag=tag, attrs=element.attrs, text=element.text))
            return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._title_depth:
            self.title_parts.append(data)
        for element in self._text_stack:
            element.text_parts.append(data)

    def summary(self) -> _AccessibilitySummary:
        return _AccessibilitySummary(
            tag_counts=dict(sorted(self.tag_counts.items())),
            html_lang=self.html_lang,
            title=" ".join(" ".join(self.title_parts).split()),
            has_viewport_meta=self.has_viewport_meta,
            landmark_counts=dict(sorted(self.landmark_counts.items())),
            headings=self.headings,
            images=self.images,
            controls=self.controls,
            form_controls=self.form_controls,
            label_for_ids=self.label_for_ids,
            onclick_elements=self.onclick_elements,
        )


def _parse_accessibility(html: str) -> _AccessibilitySummary:
    parser = _AccessibilityParser()
    parser.feed(html)
    parser.close()
    return parser.summary()


def _document_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    if not summary.html_lang:
        findings.append(
            _finding(
                severity="warning",
                category="document",
                target="html",
                summary="The html element is missing a lang attribute.",
                recommendation="Set a stable document language, for example `<html lang=\"en\">`.",
            )
        )
    if not summary.title:
        findings.append(
            _finding(
                severity="warning",
                category="document",
                target="title",
                summary="The document is missing a non-empty title.",
                recommendation="Add a concise title that names the page or product.",
            )
        )
    if not summary.has_viewport_meta:
        findings.append(
            _finding(
                severity="warning",
                category="document",
                target="meta[name=viewport]",
                summary="The document is missing a viewport meta tag.",
                recommendation="Add `<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">` for responsive previews.",
            )
        )
    return findings


def _landmark_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    if summary.landmark_counts.get("main", 0) == 0:
        findings.append(
            _finding(
                severity="warning",
                category="landmark",
                target="main",
                summary="No main landmark was detected.",
                recommendation="Wrap the primary page content in a `<main>` element.",
            )
        )
    if not any(summary.landmark_counts.get(name, 0) for name in ("nav", "header", "footer", "aside")):
        findings.append(
            _finding(
                severity="info",
                category="landmark",
                target="landmarks",
                summary="Only the main content landmark was detected.",
                recommendation="Use semantic landmarks such as header, nav, and footer when the page structure includes them.",
            )
        )
    return findings


def _heading_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    if not summary.headings:
        return [
            _finding(
                severity="warning",
                category="heading",
                target="headings",
                summary="No heading elements were detected.",
                recommendation="Add a clear h1 and hierarchical section headings.",
            )
        ]
    if summary.headings[0].level != 1:
        findings.append(
            _finding(
                severity="warning",
                category="heading",
                target=summary.headings[0].tag,
                summary="The first heading is not an h1.",
                recommendation="Start the document heading outline with one h1.",
                evidence={"first_heading_text": summary.headings[0].text[:120]},
            )
        )
    h1_count = sum(1 for heading in summary.headings if heading.level == 1)
    if h1_count == 0:
        findings.append(
            _finding(
                severity="warning",
                category="heading",
                target="h1",
                summary="No h1 heading was detected.",
                recommendation="Add one h1 that names the page purpose.",
            )
        )
    previous_level = summary.headings[0].level
    for heading in summary.headings[1:]:
        if heading.level > previous_level + 1:
            findings.append(
                _finding(
                    severity="warning",
                    category="heading",
                    target=heading.tag,
                    summary=f"Heading level jumps from h{previous_level} to {heading.tag}.",
                    recommendation="Avoid skipped heading levels so assistive technology users can scan the outline.",
                    evidence={"heading_text": heading.text[:120]},
                )
            )
        previous_level = heading.level
    return findings


def _media_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    for index, attrs in enumerate(summary.images, start=1):
        if _is_hidden(attrs):
            continue
        if "alt" not in attrs:
            findings.append(
                _finding(
                    severity="error",
                    category="media",
                    target=_target("img", attrs, index=index),
                    summary="An image is missing an alt attribute.",
                    recommendation="Add meaningful alt text, or use `alt=\"\"` for decorative images.",
                    evidence=_attrs_evidence(attrs),
                )
            )
    return findings


def _control_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    for index, control in enumerate(summary.controls, start=1):
        if _control_has_name(control):
            continue
        findings.append(
            _finding(
                severity="warning",
                category="control",
                target=_target(control.tag, control.attrs, index=index),
                summary=f"A {control.tag} control has no accessible name.",
                recommendation="Add visible text, aria-label, aria-labelledby, or a title attribute.",
                evidence=_attrs_evidence(control.attrs),
            )
        )
    for index, control in enumerate([item for item in summary.controls if item.tag == "a"], start=1):
        href = control.attrs.get("href", "").strip()
        if not href:
            findings.append(
                _finding(
                    severity="warning",
                    category="control",
                    target=_target("a", control.attrs, index=index),
                    summary="A link is missing an href value.",
                    recommendation="Use a real destination or a button element for in-page actions.",
                    evidence=_attrs_evidence(control.attrs),
                )
            )
        elif href.lower().startswith("javascript:"):
            findings.append(
                _finding(
                    severity="warning",
                    category="control",
                    target=_target("a", control.attrs, index=index),
                    summary="A link uses a javascript href.",
                    recommendation="Use semantic buttons for scripted actions and keep links as navigation.",
                    evidence={"href": href[:160]},
                )
            )
    return findings


def _form_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    for index, control in enumerate(summary.form_controls, start=1):
        control_type = control.attrs.get("type", "").strip().lower()
        if control_type == "hidden":
            continue
        if _form_control_has_label(control, summary.label_for_ids):
            continue
        findings.append(
            _finding(
                severity="warning",
                category="form",
                target=_target(control.tag, control.attrs, index=index),
                summary=f"A {control.tag} form control has no durable accessible label.",
                recommendation="Associate a label with the control or add aria-label/aria-labelledby.",
                evidence=_attrs_evidence(control.attrs),
            )
        )
    return findings


def _keyboard_findings(summary: _AccessibilitySummary) -> list[AccessibilityFinding]:
    findings: list[AccessibilityFinding] = []
    for index, attrs in enumerate(summary.onclick_elements, start=1):
        role = attrs.get("role", "").strip()
        tabindex = attrs.get("tabindex", "").strip()
        if role and tabindex:
            continue
        tag = attrs.get("tag", "element")
        findings.append(
            _finding(
                severity="warning",
                category="keyboard",
                target=_target(tag, attrs, index=index),
                summary="A non-semantic element has an onclick handler without both role and tabindex.",
                recommendation="Use a semantic button/link or provide keyboard semantics and focus handling.",
                evidence=_attrs_evidence(attrs),
            )
        )
    return findings


def _metrics(*, summary: _AccessibilitySummary, findings: list[AccessibilityFinding]) -> dict[str, Any]:
    finding_counts = Counter(finding.severity for finding in findings)
    missing_alt_count = sum(1 for attrs in summary.images if "alt" not in attrs and not _is_hidden(attrs))
    unlabeled_form_count = sum(
        1
        for item in summary.form_controls
        if item.attrs.get("type", "").strip().lower() != "hidden"
        and not _form_control_has_label(item, summary.label_for_ids)
    )
    unnamed_control_count = sum(1 for control in summary.controls if not _control_has_name(control))
    h1_count = sum(1 for heading in summary.headings if heading.level == 1)
    return {
        "finding_counts": dict(sorted(finding_counts.items())),
        "tag_counts": summary.tag_counts,
        "landmark_counts": summary.landmark_counts,
        "heading_count": len(summary.headings),
        "h1_count": h1_count,
        "image_count": len(summary.images),
        "image_missing_alt_count": missing_alt_count,
        "control_count": len(summary.controls),
        "unnamed_control_count": unnamed_control_count,
        "form_control_count": len(summary.form_controls),
        "unlabeled_form_control_count": unlabeled_form_count,
        "onclick_nonsemantic_count": len(summary.onclick_elements),
        "has_html_lang": bool(summary.html_lang),
        "has_title": bool(summary.title),
        "has_viewport_meta": summary.has_viewport_meta,
    }


def _status(findings: list[AccessibilityFinding]) -> str:
    if any(finding.severity == "error" for finding in findings):
        return "fail"
    if any(finding.severity == "warning" for finding in findings):
        return "warning"
    return "pass"


def _summary(*, status: str, metrics: dict[str, Any]) -> str:
    if status == "pass":
        return "Accessibility lint passed for the generated HTML artifact."
    finding_counts = metrics.get("finding_counts", {})
    warning_count = int(finding_counts.get("warning") or 0)
    error_count = int(finding_counts.get("error") or 0)
    if status == "fail":
        return f"Accessibility lint found {error_count} error(s) and {warning_count} warning(s)."
    return f"Accessibility lint found {warning_count} non-blocking warning(s)."


def _finding(
    *,
    severity: str,
    category: str,
    target: str,
    summary: str,
    recommendation: str = "",
    evidence: dict[str, Any] | None = None,
) -> AccessibilityFinding:
    return AccessibilityFinding(
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        target=target,
        summary=summary,
        recommendation=recommendation,
        evidence=evidence or {},
    )


def _control_has_name(control: _Control) -> bool:
    return bool(
        control.text.strip()
        or control.attrs.get("aria-label", "").strip()
        or control.attrs.get("aria-labelledby", "").strip()
        or control.attrs.get("title", "").strip()
    )


def _form_control_has_label(control: _FormControl, label_for_ids: set[str]) -> bool:
    control_id = control.attrs.get("id", "").strip()
    return bool(
        control.wrapped_by_label
        or (control_id and control_id in label_for_ids)
        or control.attrs.get("aria-label", "").strip()
        or control.attrs.get("aria-labelledby", "").strip()
        or control.attrs.get("title", "").strip()
    )


def _is_hidden(attrs: dict[str, str]) -> bool:
    return attrs.get("aria-hidden", "").strip().lower() == "true" or attrs.get("role", "").strip().lower() == "presentation"


def _target(tag: str, attrs: dict[str, str], *, index: int) -> str:
    if attrs.get("id"):
        return f"{tag}#{attrs['id']}"
    class_name = attrs.get("class", "").strip().split()
    if class_name:
        return f"{tag}.{class_name[0]}"
    return f"{tag}[{index}]"


def _attrs_evidence(attrs: dict[str, str]) -> dict[str, Any]:
    keys = ("id", "class", "role", "href", "src", "type", "name", "aria-label", "aria-labelledby", "title")
    return {key: attrs[key] for key in keys if attrs.get(key)}
