"""Workspace helpers for file-based Creative Claw workflows."""

from __future__ import annotations

import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any

from google.genai.types import Blob, Part

from conf.system import SYS_CONFIG

_WORKSPACE_DIR_NAME = "workspace"
_INBOX_DIR_NAME = "inbox"
_GENERATED_DIR_NAME = "generated"


def workspace_root() -> Path:
    """Return the fixed workspace root for all runtime file interactions."""
    preferred = SYS_CONFIG.workspace_path.resolve()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe_path = preferred / f".write_probe_{uuid.uuid4().hex}"
        probe_path.write_bytes(b"")
        probe_path.unlink()
        return preferred
    except OSError:
        fallback = Path("/tmp/creative-claw-workspace").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def inbox_root() -> Path:
    """Return the directory used for inbound channel files."""
    path = workspace_root() / _INBOX_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def generated_root() -> Path:
    """Return the directory used for generated expert outputs."""
    path = workspace_root() / _GENERATED_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def generated_session_dir(session_id: str, turn_index: int | None = None) -> Path:
    """Return the generated file directory for one session or turn."""
    safe_session = _safe_segment(session_id or "default")
    path = generated_root() / safe_session
    if turn_index is not None:
        path = path / _turn_segment(turn_index)
    path.mkdir(parents=True, exist_ok=True)
    return path


def channel_inbox_dir(channel: str, session_id: str, turn_index: int | None = None) -> Path:
    """Return the inbox directory for one channel session or turn."""
    safe_channel = _safe_segment(channel or "unknown")
    safe_session = _safe_segment(session_id or "default")
    path = inbox_root() / safe_channel / safe_session
    if turn_index is not None:
        path = path / _turn_segment(turn_index)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_workspace_path(path: str | Path) -> Path:
    """Resolve one relative or absolute path inside the fixed workspace."""
    raw_path = Path(path).expanduser()
    target = raw_path if raw_path.is_absolute() else workspace_root() / raw_path
    resolved = target.resolve()
    resolved.relative_to(workspace_root())
    return resolved


def workspace_relative_path(path: str | Path) -> str:
    """Convert one workspace file path into a workspace-relative string."""
    return str(resolve_workspace_path(path).relative_to(workspace_root()))


def build_workspace_file_record(
    path: str | Path,
    *,
    description: str = "",
    source: str = "",
    name: str | None = None,
    turn: int | None = None,
    step: int | None = None,
    expert_step: int | None = None,
) -> dict[str, Any]:
    """Build one normalized file record stored in session state."""
    resolved = resolve_workspace_path(path)
    relative = str(resolved.relative_to(workspace_root()))
    record: dict[str, Any] = {
        "name": name or resolved.name,
        "path": relative,
        "description": description.strip(),
        "source": source.strip(),
    }
    if turn is not None:
        record["turn"] = int(turn)
    if step is not None:
        record["step"] = int(step)
    if expert_step is not None:
        record["expert_step"] = int(expert_step)
    return record


def stage_attachment_into_workspace(
    source_path: str | Path,
    *,
    channel: str,
    session_id: str,
    turn_index: int | None = None,
    attachment_index: int | None = None,
    preferred_name: str = "",
) -> Path:
    """Copy one inbound attachment into the workspace inbox and return the saved path."""
    source = Path(source_path).expanduser().resolve()
    destination_dir = channel_inbox_dir(channel, session_id, turn_index=turn_index)
    target_name = Path(preferred_name).name if preferred_name else source.name
    prefix = f"{attachment_index}_" if attachment_index is not None else f"{uuid.uuid4().hex[:8]}_"
    destination = destination_dir / f"{prefix}{target_name}"
    shutil.copy2(source, destination)
    return destination.resolve()


def build_generated_output_path(
    *,
    session_id: str,
    turn_index: int | None,
    step: int,
    output_type: str,
    index: int,
    extension: str = ".png",
) -> Path:
    """Build one deterministic output path for generated expert files."""
    suffix = extension if extension.startswith(".") else f".{extension}"
    normalized_turn = _coerce_index(turn_index, default=0)
    normalized_step = _coerce_index(step, default=0)
    file_name = f"turn{normalized_turn}_step{normalized_step}_{output_type}_output{index}{suffix}"
    return generated_session_dir(session_id, turn_index=normalized_turn) / file_name


def relocate_generated_output(
    path: str | Path,
    *,
    session_id: str,
    turn_index: int | None,
    step: int,
    output_type: str,
    index: int,
) -> Path:
    """Move one auto-generated workspace file into the standardized generated directory."""
    source = resolve_workspace_path(path)
    destination = build_generated_output_path(
        session_id=session_id,
        turn_index=turn_index,
        step=step,
        output_type=output_type,
        index=index,
        extension=source.suffix or ".bin",
    )
    if source == destination:
        return source.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return destination.resolve()


def save_binary_output(
    data: bytes,
    *,
    session_id: str,
    turn_index: int | None,
    step: int,
    output_type: str,
    index: int,
    extension: str = ".png",
) -> Path:
    """Persist one generated binary file into the session workspace."""
    destination = build_generated_output_path(
        session_id=session_id,
        turn_index=turn_index,
        step=step,
        output_type=output_type,
        index=index,
        extension=extension,
    )
    destination.write_bytes(data)
    return destination.resolve()


def load_local_file_part(path: str | Path) -> Part:
    """Load one local workspace file into a Gemini-compatible inline-data part."""
    resolved = resolve_workspace_path(path)
    mime_type, _ = mimetypes.guess_type(str(resolved))
    return Part(
        inline_data=Blob(
            mime_type=mime_type or "application/octet-stream",
            data=resolved.read_bytes(),
        )
    )


def looks_like_image(path: str | Path) -> bool:
    """Return whether one file path appears to be an image."""
    mime_type, _ = mimetypes.guess_type(str(path))
    return bool(mime_type and mime_type.startswith("image/"))


def normalize_file_references(value: Any) -> list[str]:
    """Normalize one single path or list of paths into a list of relative workspace paths."""
    if isinstance(value, str):
        return [workspace_relative_path(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(workspace_relative_path(item))
        return result
    return []


def _safe_segment(value: str) -> str:
    """Sanitize one filesystem path segment."""
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return cleaned or "default"


def _turn_segment(turn_index: int) -> str:
    """Convert one turn index into a directory segment."""
    return f"turn_{_coerce_index(turn_index, default=0)}"


def _coerce_index(value: Any, *, default: int) -> int:
    """Convert one index-like value into a non-negative integer."""
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= 0 else default
