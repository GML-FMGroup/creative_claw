"""Read-only revision impact analysis for PPT production."""

from __future__ import annotations

from typing import Any

from src.production.ppt.models import PPTProductionState
from src.production.ppt.user_response import normalize_user_response


def build_revision_impact_view(state: PPTProductionState, response: Any) -> dict[str, Any]:
    """Build a read-only view describing what a requested PPT revision would affect."""
    payload = normalize_user_response(response)
    targets = _targets_from_response(payload)
    available = _available_targets(state)
    matched, unmatched = _match_targets(targets, available)
    impacted = _impacted_entries(state, matched, unmatched)
    stale_items = _stale_items_from_impacted(impacted)
    return {
        "view_type": "revision_impact",
        "production_session_id": state.production_session.production_session_id,
        "capability": state.production_session.capability,
        "status": state.status,
        "stage": state.stage,
        "progress_percent": state.progress_percent,
        "state_ref": f"{state.production_session.root_dir}/state.json",
        "revision_request": {
            "notes": str(payload.get("notes", "") or payload.get("message", "") or "").strip(),
            "targets": targets,
        },
        "matched_targets": matched,
        "unmatched_targets": unmatched,
        "available_targets": available,
        "impacted": impacted,
        "stale_items": stale_items,
        "impact_level": "unknown" if unmatched and not matched else ("deck" if not matched else "targeted"),
        "state_mutation": "none",
        "recommended_next_action": "apply_revision after user confirmation" if not unmatched or matched else "choose an available target",
    }


def _targets_from_response(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_targets = payload.get("targets")
    targets: list[dict[str, str]] = []
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if isinstance(item, dict):
                targets.append(_target(item.get("kind") or item.get("target_kind"), item.get("id") or item.get("target_id"), item.get("label") or item.get("name")))
            else:
                targets.append(_target("", item, ""))
    single = _target(payload.get("target_kind") or payload.get("kind"), payload.get("target_id") or payload.get("id"), payload.get("label") or payload.get("target_label"))
    if any(single.values()) and single not in targets:
        targets.append(single)

    slide_number = payload.get("slide_number") or payload.get("slide") or payload.get("sequence_index") or payload.get("slide_index")
    if slide_number not in (None, ""):
        slide_target = _target("slide", slide_number, f"Slide {slide_number}")
        if slide_target not in targets:
            targets.append(slide_target)
    return [item for item in targets if any(item.values())] or [{"kind": "production", "id": "", "label": "unspecified revision"}]


def _target(kind: Any, target_id: Any, label: Any) -> dict[str, str]:
    return {
        "kind": str(kind or "").strip().lower() or "unknown",
        "id": str(target_id or "").strip(),
        "label": str(label or "").strip(),
    }


def _available_targets(state: PPTProductionState) -> list[dict[str, str]]:
    targets = [{"kind": "brief", "id": "brief", "label": "PPT brief"}]
    if state.outline is not None:
        targets.append({"kind": "outline", "id": state.outline.outline_id, "label": "Current outline"})
        targets.extend(
            {
                "kind": "outline_entry",
                "id": entry.slide_id,
                "label": f"Outline slide {entry.sequence_index}: {entry.title}",
                "sequence_index": str(entry.sequence_index),
            }
            for entry in state.outline.entries
        )
    if state.deck_spec is not None:
        targets.append({"kind": "deck_spec", "id": state.deck_spec.deck_spec_id, "label": "Current deck spec"})
        targets.extend(
            {
                "kind": "deck_slide",
                "id": slide.slide_id,
                "label": f"Deck slide {slide.sequence_index}: {slide.title}",
                "sequence_index": str(slide.sequence_index),
            }
            for slide in state.deck_spec.slides
        )
    targets.extend({"kind": "artifact", "id": artifact.path, "label": artifact.name} for artifact in state.artifacts)
    return targets


def _match_targets(targets: list[dict[str, str]], available: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for target in targets:
        if target.get("kind") == "production":
            matched.append(target)
            continue
        match = _match_one_target(target, available)
        if match is None:
            unmatched.append(target)
        else:
            matched.append(match)
    return matched, unmatched


def _match_one_target(target: dict[str, str], available: list[dict[str, str]]) -> dict[str, str] | None:
    target_kind = target.get("kind", "")
    target_id = target.get("id", "")
    if target_kind in {"slide", "unknown"} and target_id:
        return _match_slide_alias(target_id, available)
    if target_kind and target_id:
        kind_match = _match_by_kind_and_id(target_kind, target_id, available)
        if kind_match is not None:
            return kind_match
    if target_id:
        return next((item for item in available if target_id == item.get("id")), None)
    if target_kind:
        return next((item for item in available if target_kind == item.get("kind")), None)
    return None


def _match_by_kind_and_id(target_kind: str, target_id: str, available: list[dict[str, str]]) -> dict[str, str] | None:
    return next(
        (
            item
            for item in available
            if item.get("kind") == target_kind
            and target_id in {item.get("id", ""), item.get("sequence_index", "")}
        ),
        None,
    )


def _match_slide_alias(target_id: str, available: list[dict[str, str]]) -> dict[str, str] | None:
    deck_match = next((item for item in available if item.get("kind") == "deck_slide" and target_id in {item.get("id", ""), item.get("sequence_index", "")}), None)
    if deck_match is not None:
        return deck_match
    return next((item for item in available if item.get("kind") == "outline_entry" and target_id in {item.get("id", ""), item.get("sequence_index", "")}), None)


def _impacted_entries(state: PPTProductionState, matched: list[dict[str, str]], unmatched: list[dict[str, str]]) -> list[dict[str, Any]]:
    if unmatched and not matched:
        return []
    target_kinds = {item.get("kind", "") for item in matched}
    if not matched or "production" in target_kinds or target_kinds & {"brief", "outline"}:
        return [
            {"kind": "outline", "id": state.outline.outline_id if state.outline else "", "would_change": "rebuild_or_reapprove"},
            {"kind": "deck_spec", "id": state.deck_spec.deck_spec_id if state.deck_spec else "", "would_change": "rebuild"},
            {"kind": "previews", "id": "slide_previews", "would_change": "regenerate"},
            {"kind": "final", "id": state.final_artifact.pptx_path if state.final_artifact else "", "would_change": "regenerate"},
            {"kind": "quality", "id": state.quality_report.report_id if state.quality_report else "", "would_change": "rerun"},
        ]
    if "outline_entry" in target_kinds:
        return [
            {"kind": "outline_entry", "id": item.get("id", ""), "would_change": "edit_outline_entry_and_rebuild_downstream"}
            for item in matched
            if item.get("kind") == "outline_entry"
        ] + [
            {"kind": "deck_spec", "id": state.deck_spec.deck_spec_id if state.deck_spec else "", "would_change": "rebuild"},
            {"kind": "previews", "id": "slide_previews", "would_change": "regenerate"},
            {"kind": "final", "id": state.final_artifact.pptx_path if state.final_artifact else "", "would_change": "regenerate"},
            {"kind": "quality", "id": state.quality_report.report_id if state.quality_report else "", "would_change": "rerun"},
        ]
    if "deck_slide" in target_kinds:
        return [
            {"kind": "deck_slide", "id": item.get("id", ""), "would_change": "edit_deck_slide_and_regenerate_downstream"}
            for item in matched
            if item.get("kind") == "deck_slide"
        ] + [
            {"kind": "previews", "id": "slide_previews", "would_change": "regenerate"},
            {"kind": "final", "id": state.final_artifact.pptx_path if state.final_artifact else "", "would_change": "regenerate"},
            {"kind": "quality", "id": state.quality_report.report_id if state.quality_report else "", "would_change": "rerun"},
        ]
    return [{"kind": "deck", "id": state.production_session.production_session_id, "would_change": "review_required"}]


def _stale_items_from_impacted(impacted: list[dict[str, Any]]) -> list[str]:
    stale_items: list[str] = []
    for item in impacted:
        kind = str(item.get("kind", "") or "")
        identifier = str(item.get("id", "") or "")
        if kind in {"outline_entry", "deck_slide"} and identifier:
            stale_items.append(f"{kind}:{identifier}")
        elif kind == "previews":
            stale_items.append(identifier or "slide_previews")
        elif kind:
            stale_items.append(kind)
    return stale_items
