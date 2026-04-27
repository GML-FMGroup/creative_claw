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
        targets.extend({"kind": "slide", "id": entry.slide_id, "label": f"Slide {entry.sequence_index}: {entry.title}"} for entry in state.outline.entries)
    if state.deck_spec is not None:
        targets.append({"kind": "deck_spec", "id": state.deck_spec.deck_spec_id, "label": "Current deck spec"})
    targets.extend({"kind": "artifact", "id": artifact.path, "label": artifact.name} for artifact in state.artifacts)
    return targets


def _match_targets(targets: list[dict[str, str]], available: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for target in targets:
        if target.get("kind") == "production":
            matched.append(target)
            continue
        match = next(
            (
                item
                for item in available
                if (target.get("id") and target.get("id") == item.get("id"))
                or (target.get("kind") == item.get("kind") and not target.get("id"))
            ),
            None,
        )
        if match is None:
            unmatched.append(target)
        else:
            matched.append(match)
    return matched, unmatched


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
        ]
    if "slide" in target_kinds:
        return [
            {"kind": "slide", "id": item.get("id", ""), "would_change": "rebuild_slide_and_downstream"}
            for item in matched
            if item.get("kind") == "slide"
        ]
    return [{"kind": "deck", "id": state.production_session.production_session_id, "would_change": "review_required"}]
