"""Read-only revision impact analysis for Design production."""

from __future__ import annotations

from typing import Any

from src.production.design.models import DesignProductionState
from src.production.models import new_id


def normalize_revision_request(user_response: Any | None) -> dict[str, Any]:
    """Normalize free-form or structured revision input into one dictionary."""
    if isinstance(user_response, dict):
        normalized = dict(user_response)
        normalized.setdefault("notes", "")
        normalized.setdefault("targets", [])
        return normalized
    text = str(user_response or "").strip()
    return {"notes": text, "targets": []}


def build_revision_impact_view(state: DesignProductionState, user_response: Any | None) -> dict[str, Any]:
    """Return a read-only impact view for a requested design revision."""
    request = normalize_revision_request(user_response)
    notes = str(request.get("notes") or "").lower()
    targets = list(request.get("targets") or [])
    affected_section_ids = _affected_sections_from_request(state, notes=notes, targets=targets)
    generic_change = not affected_section_ids
    affected_page_ids = [
        page.page_id
        for page in (state.layout_plan.pages if state.layout_plan is not None else [])
        if generic_change or any(section.section_id in affected_section_ids for section in page.sections)
    ]
    return {
        "view_type": "revision_impact",
        "revision_id": new_id("design_revision"),
        "revision_request": request,
        "state_mutation": "none",
        "summary": "P0 applies confirmed design revisions by returning to design direction review and rebuilding the full page.",
        "affected_brief": generic_change or any(word in notes for word in ("copy", "文案", "audience", "受众")),
        "affected_design_system": generic_change or any(word in notes for word in ("color", "font", "style", "颜色", "字体", "风格")),
        "affected_page_ids": affected_page_ids,
        "affected_section_ids": affected_section_ids,
        "affected_asset_ids": [asset.asset_id for asset in state.reference_assets if generic_change],
        "affected_artifact_ids": [artifact.artifact_id for artifact in state.html_artifacts],
        "recommended_action": "rebuild_page",
        "available_targets": _available_targets(state),
    }


def _affected_sections_from_request(
    state: DesignProductionState,
    *,
    notes: str,
    targets: list[Any],
) -> list[str]:
    explicit_ids = {
        str(target.get("id") or "").strip()
        for target in targets
        if isinstance(target, dict) and str(target.get("id") or "").strip()
    }
    result: list[str] = []
    if state.layout_plan is None:
        return result
    for page in state.layout_plan.pages:
        for section in page.sections:
            title = section.title.lower()
            if section.section_id in explicit_ids or title in notes or any(part in notes for part in title.split()):
                result.append(section.section_id)
    return result


def _available_targets(state: DesignProductionState) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    if state.layout_plan is not None:
        for page in state.layout_plan.pages:
            targets.append({"kind": "page", "id": page.page_id, "label": page.title})
            for section in page.sections:
                targets.append({"kind": "section", "id": section.section_id, "label": section.title})
    for artifact in state.html_artifacts:
        targets.append({"kind": "html_artifact", "id": artifact.artifact_id, "label": artifact.path})
    return targets

