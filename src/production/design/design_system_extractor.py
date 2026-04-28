"""Deterministic design-system extraction from generated Design HTML/CSS."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.production.design.models import (
    DesignProductionState,
    DesignSystemExtractionReport,
    ExtractedDesignSelector,
    ExtractedDesignToken,
    HtmlArtifact,
)
from src.runtime.workspace import resolve_workspace_path


_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_CSS_VARIABLE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
_FUNCTION_COLOR_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\([^)]{3,120}\)", re.IGNORECASE)
_MEDIA_RE = re.compile(r"@media\s*([^{]+)\{", re.IGNORECASE)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;{}]+)", re.IGNORECASE)

_SPACING_PROPERTIES = {
    "gap",
    "grid-gap",
    "inset",
    "margin",
    "margin-block",
    "margin-bottom",
    "margin-inline",
    "margin-left",
    "margin-right",
    "margin-top",
    "padding",
    "padding-block",
    "padding-bottom",
    "padding-inline",
    "padding-left",
    "padding-right",
    "padding-top",
    "top",
    "right",
    "bottom",
    "left",
}
_RADIUS_PROPERTIES = {"border-radius", "border-bottom-left-radius", "border-bottom-right-radius", "border-top-left-radius", "border-top-right-radius"}
_SHADOW_PROPERTIES = {"box-shadow", "text-shadow"}


@dataclass
class _CssRule:
    selector: str
    declarations: dict[str, str]
    variables: dict[str, str] = field(default_factory=dict)


def build_design_system_extraction(
    state: DesignProductionState,
    *,
    artifact: HtmlArtifact | None = None,
) -> DesignSystemExtractionReport:
    """Extract design-system usage facts from generated HTML and CSS."""
    latest_artifact = artifact or (state.html_artifacts[-1] if state.html_artifacts else None)
    html = _read_html(latest_artifact)
    css_blocks = _style_blocks(html)
    css = _strip_comments("\n".join(css_blocks))
    css_rules = _css_rules(css)
    tokens = _dedupe_tokens(
        [
            *_design_system_tokens(state, css=css),
            *_css_variable_tokens(css=css, rules=css_rules),
            *_css_value_tokens(css=css, rules=css_rules),
            *_media_tokens(css=css),
        ]
    )
    selectors = _selector_summaries(css_rules=css_rules, css=css)
    status = _status(artifact=latest_artifact, css=css, tokens=tokens, selectors=selectors)
    metrics = _metrics(
        css_blocks=css_blocks,
        css_rules=css_rules,
        tokens=tokens,
        selectors=selectors,
    )
    return DesignSystemExtractionReport(
        artifact_id=latest_artifact.artifact_id if latest_artifact is not None else "",
        path=latest_artifact.path if latest_artifact is not None else "",
        design_system_id=state.design_system.design_system_id if state.design_system is not None else "",
        status=status,
        summary=_summary(status=status, metrics=metrics),
        tokens=tokens,
        selectors=selectors,
        metrics=metrics,
    )


def design_system_extraction_json(report: DesignSystemExtractionReport | None) -> str:
    """Render one design-system extraction report as stable JSON."""
    payload = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def design_system_extraction_markdown(report: DesignSystemExtractionReport | None) -> str:
    """Render one design-system extraction report as Markdown."""
    if report is None:
        return "# Design System Extraction\n\nNo design-system extraction has been generated.\n"
    lines = [
        "# Design System Extraction",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        f"- Report ID: {report.report_id}",
        f"- Artifact ID: {report.artifact_id or 'n/a'}",
        f"- Artifact path: {report.path or 'n/a'}",
        f"- Design system ID: {report.design_system_id or 'n/a'}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in sorted(report.metrics.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Tokens", ""])
    if not report.tokens:
        lines.append("- No design-system tokens or CSS style facts were detected.")
    for token in report.tokens:
        refs = ", ".join(token.selector_refs) if token.selector_refs else "none"
        notes = "; ".join(token.notes) if token.notes else "none"
        lines.extend(
            [
                f"### {token.name}",
                "",
                f"- Category: {token.category}",
                f"- Source: {token.source}",
                f"- Value: {token.value}",
                f"- Usage count: {token.usage_count}",
                f"- Selector refs: {refs}",
                f"- Notes: {notes}",
                "",
            ]
        )
    lines.extend(["## Selectors", ""])
    if not report.selectors:
        lines.append("- No CSS selectors were detected.")
    for selector in report.selectors:
        refs = ", ".join(selector.token_refs) if selector.token_refs else "none"
        props = ", ".join(selector.properties) if selector.properties else "none"
        lines.extend(
            [
                f"### {selector.selector}",
                "",
                f"- Kind: {selector.kind}",
                f"- Declaration count: {selector.declaration_count}",
                f"- Token refs: {refs}",
                f"- Properties: {props}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _read_html(artifact: HtmlArtifact | None) -> str:
    if artifact is None or not artifact.path:
        return ""
    try:
        path = resolve_workspace_path(artifact.path)
    except ValueError:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _style_blocks(html: str) -> list[str]:
    return [match.strip() for match in _STYLE_BLOCK_RE.findall(html or "") if match.strip()]


def _strip_comments(css: str) -> str:
    return _CSS_COMMENT_RE.sub("", css or "")


def _css_rules(css: str) -> list[_CssRule]:
    rules: list[_CssRule] = []
    for raw_selector, raw_declarations in _CSS_RULE_RE.findall(css):
        selector = _clean_selector(raw_selector)
        if not selector or _is_at_rule_selector(selector):
            continue
        declarations, variables = _parse_declarations(raw_declarations)
        if not declarations and not variables:
            continue
        rules.append(_CssRule(selector=selector, declarations=declarations, variables=variables))
    return rules


def _parse_declarations(raw_declarations: str) -> tuple[dict[str, str], dict[str, str]]:
    declarations: dict[str, str] = {}
    variables: dict[str, str] = {}
    for chunk in raw_declarations.split(";"):
        if ":" not in chunk:
            continue
        name, value = chunk.split(":", 1)
        prop = name.strip().lower()
        cleaned_value = _clean_value(value)
        if not prop or not cleaned_value:
            continue
        if prop.startswith("--"):
            variables[prop] = cleaned_value
        else:
            declarations[prop] = cleaned_value
    return declarations, variables


def _design_system_tokens(state: DesignProductionState, *, css: str) -> list[ExtractedDesignToken]:
    if state.design_system is None:
        return []
    tokens: list[ExtractedDesignToken] = []
    for color in state.design_system.colors:
        value = _clean_value(color.value)
        tokens.append(
            ExtractedDesignToken(
                name=f"color.{color.name}",
                category="color",
                value=value,
                source="design_system",
                usage_count=_count_value_usage(css, value),
                notes=[color.usage] if color.usage else [],
            )
        )
    for item in state.design_system.typography:
        value = _clean_value(
            ", ".join(
                part
                for part in (
                    item.font_family,
                    f"{item.font_size_px}px" if item.font_size_px is not None else "",
                    f"weight {item.font_weight}" if item.font_weight else "",
                    f"line-height {item.line_height}" if item.line_height else "",
                )
                if part
            )
        )
        tokens.append(
            ExtractedDesignToken(
                name=f"typography.{item.role}",
                category="typography",
                value=value,
                source="design_system",
                usage_count=_count_value_usage(css, item.font_family),
            )
        )
    tokens.extend(_dict_design_system_tokens("spacing", state.design_system.spacing, css=css))
    tokens.extend(_dict_design_system_tokens("radius", state.design_system.radii, css=css))
    tokens.extend(_dict_design_system_tokens("shadow", state.design_system.shadows, css=css))
    return tokens


def _dict_design_system_tokens(category: str, values: dict[str, Any], *, css: str) -> list[ExtractedDesignToken]:
    tokens: list[ExtractedDesignToken] = []
    for name, raw_value in sorted(values.items()):
        value = _clean_value(raw_value)
        if not value:
            continue
        tokens.append(
            ExtractedDesignToken(
                name=f"{category}.{name}",
                category=category,  # type: ignore[arg-type]
                value=value,
                source="design_system",
                usage_count=_count_value_usage(css, value),
            )
        )
    return tokens


def _css_variable_tokens(*, css: str, rules: list[_CssRule]) -> list[ExtractedDesignToken]:
    refs_by_var: dict[str, set[str]] = defaultdict(set)
    values_by_var: dict[str, str] = {}
    for rule in rules:
        for name, value in rule.variables.items():
            refs_by_var[name].add(rule.selector)
            values_by_var.setdefault(name, value)
        for value in rule.declarations.values():
            for var_name in _CSS_VARIABLE_RE.findall(value):
                refs_by_var[var_name].add(rule.selector)
    tokens: list[ExtractedDesignToken] = []
    for name in sorted(set(values_by_var) | set(refs_by_var)):
        tokens.append(
            ExtractedDesignToken(
                name=name,
                category="css_variable",
                value=values_by_var.get(name, ""),
                source="html_css",
                usage_count=_var_usage_count(css, name),
                selector_refs=sorted(refs_by_var.get(name, set())),
            )
        )
    return tokens


def _css_value_tokens(*, css: str, rules: list[_CssRule]) -> list[ExtractedDesignToken]:
    tokens: list[ExtractedDesignToken] = []
    tokens.extend(_color_tokens(css=css, rules=rules))
    tokens.extend(_font_tokens(css=css, rules=rules))
    tokens.extend(_property_value_tokens("spacing", _SPACING_PROPERTIES, rules=rules, css=css))
    tokens.extend(_property_value_tokens("radius", _RADIUS_PROPERTIES, rules=rules, css=css))
    tokens.extend(_property_value_tokens("shadow", _SHADOW_PROPERTIES, rules=rules, css=css))
    return tokens


def _color_tokens(*, css: str, rules: list[_CssRule]) -> list[ExtractedDesignToken]:
    values = sorted({_normalize_color(match) for match in _HEX_COLOR_RE.findall(css)} | {_clean_value(match) for match in _FUNCTION_COLOR_RE.findall(css)})
    return [
        ExtractedDesignToken(
            name=f"color.{value}",
            category="color",
            value=value,
            source="html_css",
            usage_count=_count_value_usage(css, value),
            selector_refs=_selector_refs_for_value(rules, value),
        )
        for value in values
        if value
    ]


def _font_tokens(*, css: str, rules: list[_CssRule]) -> list[ExtractedDesignToken]:
    values = sorted({_clean_value(match) for match in _FONT_FAMILY_RE.findall(css)})
    return [
        ExtractedDesignToken(
            name=f"font-family.{index}",
            category="typography",
            value=value,
            source="html_css",
            usage_count=_count_value_usage(css, value),
            selector_refs=_selector_refs_for_value(rules, value),
        )
        for index, value in enumerate(values, start=1)
        if value
    ]


def _property_value_tokens(
    category: str,
    properties: set[str],
    *,
    rules: list[_CssRule],
    css: str,
) -> list[ExtractedDesignToken]:
    refs: dict[str, set[str]] = defaultdict(set)
    prop_refs: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        for prop, value in rule.declarations.items():
            if prop not in properties:
                continue
            refs[value].add(rule.selector)
            prop_refs[value].add(prop)
    tokens: list[ExtractedDesignToken] = []
    for value in sorted(refs):
        name_suffix = _slug(value)[:48] or str(len(tokens) + 1)
        tokens.append(
            ExtractedDesignToken(
                name=f"{category}.{name_suffix}",
                category=category,  # type: ignore[arg-type]
                value=value,
                source="html_css",
                usage_count=_count_value_usage(css, value),
                selector_refs=sorted(refs[value]),
                notes=[f"properties: {', '.join(sorted(prop_refs[value]))}"],
            )
        )
    return tokens


def _media_tokens(*, css: str) -> list[ExtractedDesignToken]:
    values = sorted({_clean_value(match) for match in _MEDIA_RE.findall(css)})
    return [
        ExtractedDesignToken(
            name=f"breakpoint.{_slug(value) or index}",
            category="breakpoint",
            value=value,
            source="html_css",
            usage_count=css.count(value),
            selector_refs=[f"@media {value}"],
        )
        for index, value in enumerate(values, start=1)
        if value
    ]


def _selector_summaries(*, css_rules: list[_CssRule], css: str) -> list[ExtractedDesignSelector]:
    selectors: list[ExtractedDesignSelector] = []
    media_conditions = sorted({_clean_value(match) for match in _MEDIA_RE.findall(css)})
    for condition in media_conditions:
        selectors.append(
            ExtractedDesignSelector(
                selector=f"@media {condition}",
                kind="media_query",
                declaration_count=0,
                token_refs=[],
                properties=[],
            )
        )
    for rule in sorted(css_rules, key=lambda item: item.selector):
        properties = sorted([*rule.declarations.keys(), *rule.variables.keys()])
        selectors.append(
            ExtractedDesignSelector(
                selector=rule.selector,
                kind=_selector_kind(rule.selector),
                declaration_count=len(properties),
                token_refs=_selector_token_refs(rule),
                properties=properties,
            )
        )
    return selectors


def _selector_token_refs(rule: _CssRule) -> list[str]:
    refs = set(rule.variables)
    for value in rule.declarations.values():
        refs.update(_CSS_VARIABLE_RE.findall(value))
        refs.update(_normalize_color(match) for match in _HEX_COLOR_RE.findall(value))
        refs.update(_clean_value(match) for match in _FUNCTION_COLOR_RE.findall(value))
    return sorted(ref for ref in refs if ref)


def _dedupe_tokens(tokens: list[ExtractedDesignToken]) -> list[ExtractedDesignToken]:
    merged: dict[tuple[str, str, str, str], ExtractedDesignToken] = {}
    for token in tokens:
        key = (token.source, token.category, token.name, token.value)
        existing = merged.get(key)
        if existing is None:
            token.selector_refs = sorted(set(token.selector_refs))
            token.notes = [note for note in token.notes if note]
            merged[key] = token
            continue
        existing.usage_count += token.usage_count
        existing.selector_refs = sorted(set(existing.selector_refs) | set(token.selector_refs))
        existing.notes = sorted(set(existing.notes) | {note for note in token.notes if note})
    return [merged[key] for key in sorted(merged)]


def _metrics(
    *,
    css_blocks: list[str],
    css_rules: list[_CssRule],
    tokens: list[ExtractedDesignToken],
    selectors: list[ExtractedDesignSelector],
) -> dict[str, Any]:
    token_category_counts = Counter(token.category for token in tokens)
    token_source_counts = Counter(token.source for token in tokens)
    selector_kind_counts = Counter(selector.kind for selector in selectors)
    return {
        "style_block_count": len(css_blocks),
        "css_rule_count": len(css_rules),
        "token_count": len(tokens),
        "selector_count": len(selectors),
        "css_variable_count": token_category_counts.get("css_variable", 0),
        "color_count": token_category_counts.get("color", 0),
        "typography_count": token_category_counts.get("typography", 0),
        "spacing_count": token_category_counts.get("spacing", 0),
        "radius_count": token_category_counts.get("radius", 0),
        "shadow_count": token_category_counts.get("shadow", 0),
        "breakpoint_count": token_category_counts.get("breakpoint", 0),
        "token_category_counts": dict(sorted(token_category_counts.items())),
        "token_source_counts": dict(sorted(token_source_counts.items())),
        "selector_kind_counts": dict(sorted(selector_kind_counts.items())),
    }


def _status(
    *,
    artifact: HtmlArtifact | None,
    css: str,
    tokens: list[ExtractedDesignToken],
    selectors: list[ExtractedDesignSelector],
) -> str:
    if artifact is None:
        return "empty"
    if not css.strip() and not tokens:
        return "empty"
    if not css.strip() or not selectors:
        return "partial"
    return "ready"


def _summary(*, status: str, metrics: dict[str, Any]) -> str:
    token_count = int(metrics.get("token_count") or 0)
    selector_count = int(metrics.get("selector_count") or 0)
    if status == "empty":
        return "No generated HTML/CSS was available for design-system extraction."
    if status == "partial":
        return f"Design-system extraction is partial with {token_count} token(s) and {selector_count} selector(s)."
    return f"Design-system extraction is ready with {token_count} token(s) and {selector_count} selector(s)."


def _selector_refs_for_value(rules: list[_CssRule], value: str) -> list[str]:
    refs: set[str] = set()
    normalized = value.lower()
    for rule in rules:
        for declaration_value in [*rule.declarations.values(), *rule.variables.values()]:
            if normalized and normalized in declaration_value.lower():
                refs.add(rule.selector)
    return sorted(refs)


def _count_value_usage(css: str, value: str) -> int:
    if not css or not value:
        return 0
    return css.lower().count(value.lower())


def _var_usage_count(css: str, name: str) -> int:
    if not css or not name:
        return 0
    pattern = re.compile(rf"var\(\s*{re.escape(name)}(?:\s*,|\s*\))", re.IGNORECASE)
    return len(pattern.findall(css))


def _selector_kind(selector: str) -> str:
    value = selector.strip()
    if value.startswith("@media"):
        return "media_query"
    if "," in value or " " in value or ">" in value or ":" in value or "[" in value:
        return "compound"
    if value.startswith("."):
        return "class"
    if value.startswith("#"):
        return "id"
    if re.match(r"^[a-z][a-z0-9-]*$", value, re.IGNORECASE):
        return "element"
    return "compound"


def _clean_selector(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_color(value: str) -> str:
    raw = _clean_value(value)
    return raw.lower() if raw.startswith("#") else raw


def _is_at_rule_selector(selector: str) -> bool:
    lowered = selector.lower()
    return (
        lowered.startswith("media ")
        or lowered.startswith("supports ")
        or lowered.startswith("keyframes ")
        or lowered.startswith("-webkit-keyframes ")
        or lowered.startswith("font-face")
        or lowered.startswith("page ")
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "value"
