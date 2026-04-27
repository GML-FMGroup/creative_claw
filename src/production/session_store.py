"""Workspace-backed persistence for production sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from src.production.errors import ProductionPersistenceError, ProductionSessionNotFoundError
from src.production.models import (
    ProductionOwnerRef,
    ProductionSession,
    ProductionSessionIndexEntry,
    ProductionState,
    new_id,
    utc_now_iso,
)
from src.production.projection import (
    project_final_artifacts_to_adk_state,
    project_production_pointer_to_adk_state,
)
from src.runtime.workspace import generated_session_dir, resolve_workspace_path, workspace_relative_path


StateT = TypeVar("StateT", bound=ProductionState)


class ProductionSessionStore:
    """Persist production sessions and project final artifacts to ADK state."""

    def production_root(self, adk_session_id: str) -> Path:
        """Return the root directory containing production sessions for one ADK session."""
        root = generated_session_dir(adk_session_id) / "production"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def index_path(self, adk_session_id: str) -> Path:
        """Return the index file path for one ADK session."""
        return self.production_root(adk_session_id) / "index.json"

    def create_session(
        self,
        *,
        capability: str,
        adk_session_id: str,
        turn_index: int,
        owner_ref: ProductionOwnerRef,
        status: str = "running",
    ) -> ProductionSession:
        """Create metadata and directories for one production session."""
        production_session_id = new_id(capability)
        session_root = self.production_root(adk_session_id) / production_session_id
        for child_name in ("assets", "audio", "renders", "final"):
            (session_root / child_name).mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        return ProductionSession(
            production_session_id=production_session_id,
            capability=capability,
            adk_session_id=adk_session_id,
            turn_index=turn_index,
            owner_ref=owner_ref,
            root_dir=workspace_relative_path(session_root),
            status=status,  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )

    def session_root(self, session: ProductionSession) -> Path:
        """Return the workspace path for one persisted production session."""
        root = resolve_workspace_path(session.root_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def save_state(self, state: ProductionState) -> None:
        """Persist a production state checkpoint and update its session index."""
        state.production_session.updated_at = utc_now_iso()
        state.production_session.status = state.status
        root = self.session_root(state.production_session)
        state_path = root / "state.json"
        events_path = root / "events.jsonl"
        try:
            state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            events_path.write_text(
                "".join(
                    f"{event.model_dump_json()}\n"
                    for event in state.production_events
                ),
                encoding="utf-8",
            )
            self._write_index_entry(state, state_ref=workspace_relative_path(state_path))
        except OSError as exc:
            raise ProductionPersistenceError(f"Failed to save production state: {exc}") from exc

    def load_state(
        self,
        *,
        production_session_id: str,
        adk_session_id: str,
        owner_ref: ProductionOwnerRef,
        state_type: type[StateT],
        capability: str | None = None,
    ) -> StateT:
        """Load a production state after checking current session ownership."""
        entry = self._load_index_entry(
            production_session_id=production_session_id,
            adk_session_id=adk_session_id,
            owner_ref=owner_ref,
        )
        if capability is not None and entry.capability != capability:
            raise ProductionSessionNotFoundError("production_session_not_found_or_not_owned")
        state_path = resolve_workspace_path(entry.state_ref)
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductionPersistenceError(f"Failed to load production state: {exc}") from exc
        return state_type.model_validate(payload)

    def project_to_adk_state(self, adk_state, production_state: ProductionState) -> list[dict[str, object]]:
        """Project final artifacts and active production pointers to ADK session state."""
        return project_final_artifacts_to_adk_state(adk_state, production_state=production_state)

    def project_pointer_to_adk_state(self, adk_state, production_state: ProductionState) -> None:
        """Project only active production pointers to ADK session state."""
        project_production_pointer_to_adk_state(adk_state, production_state=production_state)

    def _load_index(self, adk_session_id: str) -> list[ProductionSessionIndexEntry]:
        path = self.index_path(adk_session_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductionPersistenceError(f"Failed to read production index: {exc}") from exc
        entries = payload if isinstance(payload, list) else []
        return [ProductionSessionIndexEntry.model_validate(entry) for entry in entries]

    def _save_index(self, adk_session_id: str, entries: list[ProductionSessionIndexEntry]) -> None:
        path = self.index_path(adk_session_id)
        try:
            path.write_text(
                json.dumps(
                    [entry.model_dump(mode="json") for entry in entries],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ProductionPersistenceError(f"Failed to write production index: {exc}") from exc

    def _write_index_entry(self, state: ProductionState, *, state_ref: str) -> None:
        session = state.production_session
        entries = self._load_index(session.adk_session_id)
        entry = ProductionSessionIndexEntry(
            production_session_id=session.production_session_id,
            capability=session.capability,
            adk_session_id=session.adk_session_id,
            owner_ref=session.owner_ref,
            state_ref=state_ref,
            status=state.status,
            stage=state.stage,
            artifacts=state.artifacts,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )
        updated_entries = [
            existing
            for existing in entries
            if existing.production_session_id != session.production_session_id
        ]
        updated_entries.append(entry)
        self._save_index(session.adk_session_id, updated_entries)

    def _load_index_entry(
        self,
        *,
        production_session_id: str,
        adk_session_id: str,
        owner_ref: ProductionOwnerRef,
    ) -> ProductionSessionIndexEntry:
        entries = self._load_index(adk_session_id)
        for entry in entries:
            if entry.production_session_id != production_session_id:
                continue
            if entry.adk_session_id == adk_session_id or entry.owner_ref == owner_ref:
                return entry
            break
        raise ProductionSessionNotFoundError("production_session_not_found_or_not_owned")
