"""Projection helpers from production state into ADK session state."""

from __future__ import annotations

from collections.abc import MutableMapping

from src.production.models import ProductionState, WorkspaceFileRef
from src.runtime.workspace import build_workspace_file_record


def project_final_artifacts_to_adk_state(
    state: MutableMapping[str, object],
    *,
    production_state: ProductionState,
    artifacts: list[WorkspaceFileRef] | None = None,
) -> list[dict[str, object]]:
    """Project final production artifacts into user-visible ADK session state."""
    selected_artifacts = artifacts if artifacts is not None else production_state.artifacts
    current_turn = int(state.get("turn_index", 0) or 0)
    current_step = int(state.get("step", 0) or 0)
    file_records: list[dict[str, object]] = []
    for artifact in selected_artifacts:
        record = build_workspace_file_record(
            artifact.path,
            description=artifact.description,
            source=artifact.source or production_state.production_session.capability,
            name=artifact.name,
            turn=current_turn,
            step=current_step,
        )
        file_records.append(record)

    if file_records:
        generated = list(state.get("generated") or [])
        generated.extend(file_records)
        state["generated"] = generated
        state["new_files"] = file_records
        files_history = list(state.get("files_history") or [])
        files_history.append(file_records)
        state["files_history"] = files_history
        state["final_file_paths"] = [str(record["path"]) for record in file_records]

    state["active_production_session_id"] = production_state.production_session.production_session_id
    state["active_production_capability"] = production_state.production_session.capability
    state["active_production_stage"] = production_state.stage
    state["active_production_status"] = production_state.status
    return file_records


def project_production_pointer_to_adk_state(
    state: MutableMapping[str, object],
    *,
    production_state: ProductionState,
) -> None:
    """Project only active production pointers into ADK session state."""
    state["active_production_session_id"] = production_state.production_session.production_session_id
    state["active_production_capability"] = production_state.production_session.capability
    state["active_production_stage"] = production_state.stage
    state["active_production_status"] = production_state.status
