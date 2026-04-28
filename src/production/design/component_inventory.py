"""Deterministic component inventory extraction for Design handoff."""

from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.production.design.models import (
    ComponentInventoryItem,
    ComponentInventoryReport,
    DesignProductionState,
    HtmlArtifact,
)
from src.runtime.workspace import resolve_workspace_path


_SIGNIFICANT_TAGS = {"a", "button", "footer", "form", "header", "img", "main", "nav", "section"}
_CLASS_SKIP_PREFIXES = {"is", "has", "u", "js"}


def build_component_inventory(
    state: DesignProductionState,
    *,
    artifact: HtmlArtifact | None = None,
) -> ComponentInventoryReport:
    """Build an implementation-facing component inventory from current Design state."""
    latest_artifact = artifact or (state.html_artifacts[-1] if state.html_artifacts else None)
    html_summary = _summarize_html_components(latest_artifact)
    items = [
        *_layout_component_items(state),
        *_design_system_component_items(state),
        *_html_component_items(html_summary),
    ]
    items = _dedupe_items(items)
    status = _status(state=state, artifact=latest_artifact, items=items)
    metrics = {
        "item_count": len(items),
        "layout_component_count": sum(1 for item in items if item.source == "layout_plan"),
        "design_system_component_count": sum(1 for item in items if item.source == "design_system"),
        "html_component_count": sum(1 for item in items if item.source == "html_artifact"),
        "category_counts": _counter_dict(item.category for item in items),
        "html_tag_counts": html_summary.tag_counts,
        "html_class_counts": html_summary.class_counts,
        "html_id_count": len(html_summary.ids),
    }
    return ComponentInventoryReport(
        artifact_id=latest_artifact.artifact_id if latest_artifact is not None else "",
        layout_plan_id=state.layout_plan.layout_plan_id if state.layout_plan is not None else "",
        design_system_id=state.design_system.design_system_id if state.design_system is not None else "",
        status=status,
        summary=_summary(status=status, items=items),
        items=items,
        metrics=metrics,
    )


def component_inventory_json(report: ComponentInventoryReport | None) -> str:
    """Render one component inventory report as stable JSON."""
    payload = report.model_dump(mode="json") if report is not None else None
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def component_inventory_markdown(report: ComponentInventoryReport | None) -> str:
    """Render one component inventory report as Markdown."""
    if report is None:
        return "# Component Inventory\n\nNo component inventory has been generated.\n"
    lines = [
        "# Component Inventory",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        f"- Report ID: {report.report_id}",
        f"- Artifact ID: {report.artifact_id or 'n/a'}",
        f"- Layout plan ID: {report.layout_plan_id or 'n/a'}",
        f"- Design system ID: {report.design_system_id or 'n/a'}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in sorted(report.metrics.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Items", ""])
    if not report.items:
        lines.append("- No component inventory items were detected.")
    for item in report.items:
        lines.extend(
            [
                f"### {item.name}",
                "",
                f"- Category: {item.category}",
                f"- Source: {item.source}",
                f"- Selector: {item.selector or 'n/a'}",
                f"- Role: {item.role or 'n/a'}",
                f"- Description: {item.description or 'n/a'}",
                f"- Source refs: {', '.join(item.source_refs) if item.source_refs else 'none'}",
                f"- Token refs: {', '.join(item.token_refs) if item.token_refs else 'none'}",
                f"- Responsive notes: {item.responsive_notes or 'n/a'}",
            ]
        )
        if item.implementation_notes:
            lines.append("- Implementation notes:")
            lines.extend(f"  - {note}" for note in item.implementation_notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _layout_component_items(state: DesignProductionState) -> list[ComponentInventoryItem]:
    if state.layout_plan is None:
        return []
    items: list[ComponentInventoryItem] = []
    for page in state.layout_plan.pages:
        for section in page.sections:
            category = _category_from_text(section.title, section.purpose)
            items.append(
                ComponentInventoryItem(
                    name=section.title,
                    category=category if category != "tokenized_component" else "section",
                    source="layout_plan",
                    page_id=page.page_id,
                    section_id=section.section_id,
                    selector=f"#{section.section_id}",
                    role=section.purpose,
                    description=_compact_text(section.content),
                    source_refs=list(section.required_asset_ids),
                    token_refs=_token_refs_for_category(state, category),
                    responsive_notes=section.responsive_notes,
                    implementation_notes=_implementation_notes_from_section(section.expert_hints),
                )
            )
    return items


def _design_system_component_items(state: DesignProductionState) -> list[ComponentInventoryItem]:
    if state.design_system is None:
        return []
    items: list[ComponentInventoryItem] = []
    for name, value in sorted(state.design_system.component_tokens.items()):
        items.append(
            ComponentInventoryItem(
                name=str(name),
                category="tokenized_component",
                source="design_system",
                selector=_selector_from_name(str(name)),
                role="Component token group",
                description=_value_summary(value),
                token_refs=[f"component.{name}"],
                implementation_notes=["Use these token values as component implementation defaults."],
            )
        )
    return items


def _html_component_items(summary: "_HtmlSummary") -> list[ComponentInventoryItem]:
    items: list[ComponentInventoryItem] = []
    for class_name, count in sorted(summary.class_counts.items()):
        if not _is_component_like_class(class_name, count):
            continue
        category = _category_from_text(class_name, "")
        items.append(
            ComponentInventoryItem(
                name=_title_from_slug(class_name),
                category=category,
                source="html_artifact",
                selector=f".{class_name}",
                role=f"Detected HTML class used {count} time(s).",
                description="Class-level component hook detected in generated HTML.",
                implementation_notes=["Confirm this selector remains stable before downstream implementation reuse."],
            )
        )
    for tag_name, count in sorted(summary.tag_counts.items()):
        if tag_name not in _SIGNIFICANT_TAGS:
            continue
        items.append(
            ComponentInventoryItem(
                name=_title_from_slug(tag_name),
                category=_category_from_text(tag_name, ""),
                source="html_artifact",
                selector=tag_name,
                role=f"Detected semantic tag used {count} time(s).",
                description="Semantic HTML structure detected in generated artifact.",
            )
        )
    return items


def _dedupe_items(items: list[ComponentInventoryItem]) -> list[ComponentInventoryItem]:
    deduped: list[ComponentInventoryItem] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        key = (item.source, item.page_id, item.section_id, item.selector or _slug(item.name))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _status(
    *,
    state: DesignProductionState,
    artifact: HtmlArtifact | None,
    items: list[ComponentInventoryItem],
) -> str:
    if not items:
        return "empty"
    if artifact is None or state.layout_plan is None or state.design_system is None:
        return "partial"
    return "ready"


def _summary(*, status: str, items: list[ComponentInventoryItem]) -> str:
    if status == "empty":
        return "No component inventory items were detected."
    if status == "partial":
        return f"Component inventory is partial with {len(items)} detected item(s)."
    return f"Component inventory is ready with {len(items)} detected item(s)."


def _summarize_html_components(artifact: HtmlArtifact | None) -> "_HtmlSummary":
    if artifact is None:
        return _HtmlSummary()
    try:
        path = resolve_workspace_path(artifact.path)
    except ValueError:
        return _HtmlSummary()
    if not path.exists() or not path.is_file():
        return _HtmlSummary()
    parser = _ComponentHtmlParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.summary()


def _category_from_text(name: str, purpose: str) -> str:
    text = f"{name} {purpose}".lower()
    if any(term in text for term in ("nav", "menu", "header")):
        return "navigation"
    if any(term in text for term in ("button", "cta", "action")):
        return "button"
    if any(term in text for term in ("card", "panel", "tile", "block")):
        return "card"
    if any(term in text for term in ("metric", "stat", "kpi")):
        return "metric"
    if any(term in text for term in ("form", "input", "field")):
        return "form"
    if any(term in text for term in ("image", "media", "photo", "video")):
        return "media"
    if any(term in text for term in ("section", "hero", "feature", "testimonial", "pricing", "footer", "main")):
        return "section"
    return "other"


def _token_refs_for_category(state: DesignProductionState, category: str) -> list[str]:
    if state.design_system is None:
        return []
    refs: list[str] = []
    if category in {"button", "card", "metric", "form", "navigation"}:
        if category in state.design_system.component_tokens:
            refs.append(f"component.{category}")
        if category == "button" and "button" in state.design_system.component_tokens:
            refs.append("component.button")
    if state.design_system.colors:
        refs.extend(f"color.{color.name}" for color in state.design_system.colors[:2])
    if state.design_system.typography:
        refs.extend(f"typography.{item.role}" for item in state.design_system.typography[:2])
    return sorted(set(refs))


def _implementation_notes_from_section(expert_hints: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for key, value in sorted(expert_hints.items()):
        if isinstance(value, (str, int, float, bool)):
            notes.append(f"{key}: {value}")
        elif isinstance(value, list):
            notes.append(f"{key}: {', '.join(str(item) for item in value[:5])}")
    return notes


def _compact_text(items: list[str]) -> str:
    text = "; ".join(item.strip() for item in items if item.strip())
    return text[:240]


def _value_summary(value: Any) -> str:
    if isinstance(value, dict):
        parts = [f"{key}={_scalar_or_type(child)}" for key, child in sorted(value.items())[:8]]
        return ", ".join(parts)
    return str(value)


def _scalar_or_type(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return type(value).__name__


def _selector_from_name(name: str) -> str:
    slug = _slug(name)
    return f".{slug}" if slug else ""


def _title_from_slug(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", value) if part) or value


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")


def _is_component_like_class(class_name: str, count: int) -> bool:
    normalized = _slug(class_name).replace("-", "_")
    if not normalized:
        return False
    first = normalized.split("_")[0]
    if first in _CLASS_SKIP_PREFIXES:
        return False
    if any(term in normalized for term in ("button", "card", "hero", "metric", "panel", "section", "nav", "form", "media")):
        return True
    return count >= 2 and len(normalized) >= 4


def _counter_dict(values) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


class _HtmlSummary:
    """Small HTML structure summary for component inventory heuristics."""

    def __init__(self) -> None:
        self.tag_counts: dict[str, int] = {}
        self.class_counts: dict[str, int] = {}
        self.ids: list[str] = []


class _ComponentHtmlParser(HTMLParser):
    """Collect tag, class, and id signals from generated HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._tag_counts: Counter[str] = Counter()
        self._class_counts: Counter[str] = Counter()
        self._ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect one HTML start tag."""
        tag_name = tag.lower()
        self._tag_counts[tag_name] += 1
        for key, value in attrs:
            if key == "id" and value:
                self._ids.append(value)
            if key == "class" and value:
                for class_name in value.split():
                    self._class_counts[class_name] += 1

    def summary(self) -> _HtmlSummary:
        """Return a stable summary of collected HTML component signals."""
        summary = _HtmlSummary()
        summary.tag_counts = dict(sorted(self._tag_counts.items()))
        summary.class_counts = dict(sorted(self._class_counts.items()))
        summary.ids = sorted(self._ids)
        return summary
