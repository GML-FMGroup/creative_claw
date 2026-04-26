"""Read-only revision impact analysis for short-video production."""

from __future__ import annotations

from typing import Any

from src.production.short_video.models import ShortVideoProductionState
from src.production.short_video.user_response import normalize_user_response


def build_revision_impact_view(
    state: ShortVideoProductionState,
    response: Any,
) -> dict[str, Any]:
    """Build a read-only view describing what a requested revision would affect."""
    response_payload = normalize_user_response(response)
    view = _base_revision_view(state)
    targets = _revision_targets_from_response(response_payload)
    matched_targets, unmatched_targets = _match_revision_targets(state, targets)
    impacted = _revision_impact_entries(state, matched_targets, unmatched_targets)
    view.update(
        {
            "revision_request": {
                "notes": _revision_notes(response_payload),
                "targets": targets,
            },
            "matched_targets": matched_targets,
            "unmatched_targets": unmatched_targets,
            "available_targets": _available_revision_targets(state),
            "impacted": impacted,
            "impact_level": _revision_impact_level(impacted, unmatched_targets),
            "state_mutation": "none",
            "recommended_next_action": _recommended_revision_action(
                impacted,
                unmatched_targets,
            ),
        }
    )
    return view


def _base_revision_view(state: ShortVideoProductionState) -> dict[str, Any]:
    session = state.production_session
    return {
        "view_type": "revision_impact",
        "production_session_id": session.production_session_id,
        "capability": session.capability,
        "status": state.status,
        "stage": state.stage,
        "progress_percent": state.progress_percent,
        "state_ref": f"{session.root_dir}/state.json",
        "project_root": session.root_dir,
    }


def _revision_notes(response: dict[str, Any]) -> str:
    return str(response.get("notes", "") or response.get("message", "") or "").strip()


def _revision_targets_from_response(response: dict[str, Any]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    raw_targets = response.get("targets")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if isinstance(item, dict):
                target = _normalize_revision_target(
                    kind=item.get("kind") or item.get("target_kind"),
                    target_id=item.get("id") or item.get("target_id"),
                    label=item.get("label") or item.get("name"),
                )
            else:
                target = _normalize_revision_target(kind="", target_id=item, label="")
            if target:
                targets.append(target)

    target = _normalize_revision_target(
        kind=response.get("target_kind") or response.get("kind"),
        target_id=response.get("target_id") or response.get("id"),
        label=response.get("target_label") or response.get("label"),
    )
    if target and target not in targets:
        targets.append(target)

    if not targets:
        targets.append({"kind": "production", "id": "", "label": "unspecified revision"})
    return targets


def _normalize_revision_target(
    *,
    kind: Any,
    target_id: Any,
    label: Any,
) -> dict[str, str]:
    normalized_kind = str(kind or "").strip().lower()
    normalized_id = str(target_id or "").strip()
    normalized_label = str(label or "").strip()
    if not normalized_kind and not normalized_id and not normalized_label:
        return {}
    return {
        "kind": normalized_kind or "unknown",
        "id": normalized_id,
        "label": normalized_label,
    }


def _match_revision_targets(
    state: ShortVideoProductionState,
    targets: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    available = _available_revision_targets(state)
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for target in targets:
        match = _match_revision_target(target, available)
        if match is None:
            unmatched.append(target)
        else:
            matched.append(match)
    return matched, unmatched


def _match_revision_target(
    target: dict[str, str],
    available: list[dict[str, str]],
) -> dict[str, str] | None:
    target_kind = target.get("kind", "")
    target_id = target.get("id", "")
    if target_kind == "production":
        return target
    for candidate in available:
        candidate_kind = candidate["kind"]
        candidate_id = candidate["id"]
        if target_id and target_id == candidate_id and target_kind in {"", "unknown", candidate_kind}:
            return candidate
        if target_kind == candidate_kind and not target_id:
            return candidate
    return None


def _available_revision_targets(state: ShortVideoProductionState) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = [
        {"kind": "brief", "id": "brief", "label": "Short-video brief"},
    ]
    if state.storyboard is not None:
        targets.append(
            {
                "kind": "storyboard",
                "id": state.storyboard.storyboard_id,
                "label": "Current storyboard",
            }
        )
        targets.extend(
            {
                "kind": "shot",
                "id": shot.shot_id,
                "label": f"Storyboard shot {shot.sequence_index}: {shot.purpose}",
            }
            for shot in state.storyboard.shots
        )
        if any(shot.dialogue_lines or shot.audio_notes for shot in state.storyboard.shots):
            targets.append(
                {
                    "kind": "voiceover",
                    "id": state.storyboard.storyboard_id,
                    "label": "Storyboard dialogue and audio notes",
                }
            )
    if state.asset_plan is not None:
        targets.extend(
            [
                {
                    "kind": "asset_plan",
                    "id": state.asset_plan.plan_id,
                    "label": "Current asset plan",
                },
                {
                    "kind": "shot",
                    "id": state.asset_plan.shot_plan.shot_id,
                    "label": "Current P0b shot plan",
                },
                {
                    "kind": "voiceover",
                    "id": state.asset_plan.shot_plan.shot_id,
                    "label": "Current shot voiceover",
                },
            ]
        )
    targets.extend(
        {
            "kind": "shot_asset_plan",
            "id": plan.shot_asset_plan_id,
            "label": f"Provider segment {plan.segment_index}",
        }
        for plan in state.shot_asset_plans
    )
    targets.extend(
        {
            "kind": "shot_artifact",
            "id": artifact.shot_artifact_id,
            "label": f"Generated segment {artifact.segment_index}",
        }
        for artifact in state.shot_artifacts
    )
    targets.extend(
        {
            "kind": "reference_asset",
            "id": reference.reference_asset_id,
            "label": str(reference.metadata.get("name", "") or reference.role),
        }
        for reference in state.reference_assets
    )
    if state.timeline is not None:
        targets.append(
            {
                "kind": "timeline",
                "id": state.timeline.timeline_id,
                "label": "Rendered timeline",
            }
        )
    targets.extend(
        {
            "kind": "artifact",
            "id": artifact.path,
            "label": artifact.name,
        }
        for artifact in state.artifacts
    )
    return targets


def _revision_impact_entries(
    state: ShortVideoProductionState,
    matched_targets: list[dict[str, str]],
    unmatched_targets: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if unmatched_targets and not matched_targets:
        return []

    target_kinds = {target.get("kind", "") for target in matched_targets}
    if not matched_targets or "production" in target_kinds:
        target_kinds = {
            "brief",
            "storyboard",
            "asset_plan",
            "shot",
            "voiceover",
            "reference_asset",
            "shot_asset_plan",
            "shot_artifact",
        }

    target_ids_by_kind = _target_ids_by_kind(matched_targets)
    targeted_segment_plan_ids = _targeted_segment_plan_ids(state, target_ids_by_kind)
    segment_scoped = bool(target_kinds & {"shot_asset_plan", "shot_artifact"}) and not (
        target_kinds & {"brief", "storyboard", "asset_plan", "reference_asset"}
    )

    impacted: list[dict[str, Any]] = []
    if target_kinds & {"brief", "storyboard", "shot", "voiceover", "reference_asset"}:
        if state.storyboard is not None:
            impacted.append(
                {
                    "kind": "storyboard",
                    "id": state.storyboard.storyboard_id,
                    "current_status": state.storyboard.status,
                    "would_change": "rebuild_or_reapprove",
                    "reason": "Accepted revisions are normalized into the reviewed storyboard before provider planning.",
                }
            )
    if target_kinds & {"brief", "asset_plan", "shot", "voiceover", "reference_asset", "shot_asset_plan"}:
        if state.asset_plan is not None:
            impacted.append(
                {
                    "kind": "asset_plan",
                    "id": state.asset_plan.plan_id,
                    "current_status": state.asset_plan.status,
                    "would_change": "rebuild_or_reapprove",
                    "reason": "Accepted revisions are normalized back into the reviewed asset plan.",
                }
            )
    if target_kinds & {"brief", "storyboard", "asset_plan", "shot", "voiceover", "reference_asset", "shot_asset_plan"}:
        impacted.extend(
            {
                "kind": "shot_asset_plan",
                "id": plan.shot_asset_plan_id,
                "current_status": plan.status,
                "would_change": "rebuild_or_reapprove",
                "reason": "Provider segment plans are derived from the storyboard and asset plan.",
            }
            for plan in state.shot_asset_plans
            if not segment_scoped or plan.shot_asset_plan_id in targeted_segment_plan_ids
        )
    if target_kinds & {"reference_asset"}:
        target_ids = {
            target.get("id", "")
            for target in matched_targets
            if target.get("kind") == "reference_asset"
        }
        impacted.extend(
            {
                "kind": "reference_asset",
                "id": reference.reference_asset_id,
                "current_status": reference.status,
                "would_change": "replace_or_reclassify",
                "path": reference.path,
                "reason": "Reference asset changes should be applied through add_reference_assets.",
            }
            for reference in state.reference_assets
            if not target_ids or reference.reference_asset_id in target_ids
        )

    shot_artifact_impacted = bool(
        target_kinds
        & {"brief", "storyboard", "asset_plan", "shot", "voiceover", "reference_asset", "shot_asset_plan", "shot_artifact"}
    )
    if shot_artifact_impacted:
        impacted.extend(
            {
                "kind": "shot_artifact",
                "id": artifact.shot_artifact_id,
                "current_status": artifact.status,
                "would_change": "mark_stale_before_regeneration",
                "path": artifact.preview_path,
                "reason": "Generated segment previews depend on provider segment plans and generated media.",
            }
            for artifact in state.shot_artifacts
            if (
                not segment_scoped
                or artifact.shot_asset_plan_id in targeted_segment_plan_ids
                or artifact.shot_artifact_id in target_ids_by_kind.get("shot_artifact", set())
            )
        )

    video_impacted = bool(target_kinds & {"brief", "storyboard", "asset_plan", "shot", "reference_asset", "shot_asset_plan", "shot_artifact"})
    audio_impacted = bool(target_kinds & {"brief", "storyboard", "asset_plan", "shot", "voiceover", "shot_asset_plan", "shot_artifact"})
    timeline_impacted = bool(target_kinds & {"timeline"})
    artifact_impacted = bool(target_kinds & {"artifact"})

    impacted_video_asset_ids = _targeted_video_asset_ids(state, targeted_segment_plan_ids) if segment_scoped else set()
    impacted_audio_ids = _targeted_audio_ids(state, targeted_segment_plan_ids) if segment_scoped else set()

    if video_impacted:
        impacted.extend(
            {
                "kind": "video_asset",
                "id": asset.asset_id,
                "current_status": asset.status,
                "would_change": "mark_stale_before_regeneration",
                "path": asset.path,
                "reason": "The generated visual asset depends on the reviewed plan or references.",
            }
            for asset in state.asset_manifest
            if asset.kind == "video"
            if not segment_scoped or asset.asset_id in impacted_video_asset_ids
        )
    if audio_impacted:
        impacted.extend(
            {
                "kind": "audio_asset",
                "id": audio.audio_id,
                "current_status": audio.status,
                "would_change": "mark_stale_before_regeneration",
                "path": audio.path,
                "reason": "The voiceover depends on the brief, shot plan, or voiceover text.",
            }
            for audio in state.audio_manifest
            if not segment_scoped or audio.audio_id in impacted_audio_ids
        )

    media_impacted = any(item["kind"] in {"video_asset", "audio_asset"} for item in impacted)
    if state.timeline is not None and (media_impacted or timeline_impacted):
        impacted.append(
            {
                "kind": "timeline",
                "id": state.timeline.timeline_id,
                "current_status": "valid",
                "would_change": "rebuild",
                "reason": "The render timeline references media that would change.",
            }
        )
    if state.artifacts and (media_impacted or timeline_impacted or artifact_impacted):
        impacted.extend(
            {
                "kind": "final_artifact",
                "id": artifact.path,
                "current_status": "valid",
                "would_change": "may_become_stale",
                "path": artifact.path,
                "reason": "The final deliverable is derived from the impacted timeline or media.",
            }
            for artifact in state.artifacts
        )
    return impacted


def _target_ids_by_kind(targets: list[dict[str, str]]) -> dict[str, set[str]]:
    ids_by_kind: dict[str, set[str]] = {}
    for target in targets:
        kind = str(target.get("kind", "") or "").strip()
        target_id = str(target.get("id", "") or "").strip()
        if kind:
            ids_by_kind.setdefault(kind, set())
            if target_id:
                ids_by_kind[kind].add(target_id)
    return ids_by_kind


def _targeted_segment_plan_ids(
    state: ShortVideoProductionState,
    target_ids_by_kind: dict[str, set[str]],
) -> set[str]:
    plan_ids = set(target_ids_by_kind.get("shot_asset_plan", set()))
    artifact_ids = target_ids_by_kind.get("shot_artifact", set())
    if artifact_ids:
        plan_ids.update(
            artifact.shot_asset_plan_id
            for artifact in state.shot_artifacts
            if artifact.shot_artifact_id in artifact_ids
        )
    return plan_ids


def _targeted_video_asset_ids(
    state: ShortVideoProductionState,
    segment_plan_ids: set[str],
) -> set[str]:
    asset_ids: set[str] = set()
    for artifact in state.shot_artifacts:
        if artifact.shot_asset_plan_id not in segment_plan_ids:
            continue
        asset_ids.add(artifact.video_asset_id)
        preview_asset_id = str(artifact.metadata.get("preview_video_asset_id") or "").strip()
        if preview_asset_id:
            asset_ids.add(preview_asset_id)
    return asset_ids


def _targeted_audio_ids(
    state: ShortVideoProductionState,
    segment_plan_ids: set[str],
) -> set[str]:
    return {
        artifact.audio_id
        for artifact in state.shot_artifacts
        if artifact.shot_asset_plan_id in segment_plan_ids
    }


def _revision_impact_level(
    impacted: list[dict[str, Any]],
    unmatched_targets: list[dict[str, str]],
) -> str:
    if unmatched_targets and not impacted:
        return "target_unmatched"
    impacted_kinds = {item.get("kind", "") for item in impacted}
    if impacted_kinds & {"video_asset", "audio_asset", "timeline", "final_artifact"}:
        return "generated_outputs_would_be_stale"
    if impacted:
        return "planning_only"
    return "no_known_impact"


def _recommended_revision_action(
    impacted: list[dict[str, Any]],
    unmatched_targets: list[dict[str, str]],
) -> str:
    if unmatched_targets and not impacted:
        return "Ask the user to choose one of the available_targets before applying a revision."
    if any(item.get("kind") == "reference_asset" for item in impacted):
        return 'Use action="add_reference_assets" for reference changes.'
    return 'Use action="resume" with decision="revise" only after the user confirms this impact.'
