"""Input ingestion and classification for PPT production."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.production.models import new_id
from src.production.ppt.models import IngestEntry, PPTInputRole
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path

_TEMPLATE_EXTENSIONS = {".pptx", ".ppt"}
_SOURCE_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def ingest_input_files(input_files: Any, *, turn_index: int) -> list[IngestEntry]:
    """Normalize ADK file payloads into PPT input entries."""
    entries: list[IngestEntry] = []
    template_seen = False
    for record in iter_input_file_records(input_files):
        raw_path = str(record.get("path", "") or "").strip()
        if not raw_path:
            continue
        name = str(record.get("name", "") or "").strip() or Path(raw_path).name
        role = classify_input_path(raw_path)
        warning = ""
        status = "valid"
        if role == "template_pptx":
            if template_seen:
                warning = "Only the first PPT template is used in P0; this template is recorded but not applied."
                status = "unsupported"
            template_seen = True
        elif role in {"source_doc", "reference_image"}:
            warning = "This input role is recorded in P0 and will be used by a later implementation phase."
        elif role == "unknown":
            warning = "Unsupported input type for PPT production."
            status = "unsupported"

        try:
            normalized_path = workspace_relative_path(resolve_workspace_path(raw_path))
        except Exception:
            normalized_path = raw_path
            warning = warning or "Input path could not be normalized into the workspace."
            status = "unsupported"

        entries.append(
            IngestEntry(
                input_id=new_id("ppt_input"),
                path=normalized_path,
                name=name,
                role=role,
                added_turn_index=turn_index,
                status=status,  # type: ignore[arg-type]
                warning=warning,
                metadata={"description": str(record.get("description", "") or "").strip()},
            )
        )
    return entries


def classify_input_path(path: str) -> PPTInputRole:
    """Classify a workspace path into the PPT input role taxonomy."""
    suffix = Path(str(path or "")).suffix.lower()
    if suffix in _TEMPLATE_EXTENSIONS:
        return "template_pptx"
    if suffix in _SOURCE_EXTENSIONS:
        return "source_doc"
    if suffix in _IMAGE_EXTENSIONS:
        return "reference_image"
    return "unknown"


def iter_input_file_records(input_files: Any) -> list[dict[str, Any]]:
    """Return normalized file records from ADK file payload variants."""
    if input_files is None:
        return []
    if isinstance(input_files, (str, dict)):
        candidates = [input_files]
    else:
        try:
            candidates = list(input_files)
        except TypeError:
            return []

    records: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, str):
            path = item.strip()
            if path:
                records.append({"path": path, "name": Path(path).name, "description": ""})
            continue
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "") or "").strip()
        if not path:
            continue
        record = dict(item)
        record["path"] = path
        record.setdefault("name", Path(path).name)
        record.setdefault("description", "")
        records.append(record)
    return records
