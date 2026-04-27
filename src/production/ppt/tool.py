"""ADK tool wrapper for PPT production."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from google.adk.tools.tool_context import ToolContext

from src.production.models import ProductionRunResult
from src.production.ppt.manager import PPTProductionManager


async def run_ppt_production(
    action: Literal[
        "start",
        "status",
        "resume",
        "view",
        "add_inputs",
        "analyze_revision_impact",
        "apply_revision",
        "regenerate_stale_segments",
    ],
    user_prompt: str = "",
    production_session_id: str | None = None,
    view_type: Literal[
        "overview",
        "brief",
        "inputs",
        "document_summary",
        "template_summary",
        "outline",
        "deck_spec",
        "previews",
        "quality",
        "events",
        "artifacts",
    ] = "overview",
    input_files: list[Any] | str | None = None,
    placeholder_assets: bool = False,
    render_settings: dict[str, Any] | None = None,
    user_response: dict[str, Any] | str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run, inspect, resume, view, revise, or analyze a durable PPT production task."""
    if tool_context is None:
        return ProductionRunResult(
            status="failed",
            capability="ppt",
            production_session_id=production_session_id or "",
            stage="missing_tool_context",
            progress_percent=0,
            message="tool_context is required for PPT production.",
        ).model_dump(mode="json")

    state = tool_context.state
    raw_input_files = input_files if input_files is not None else state.get("uploaded") or state.get("input_files")
    resolved_input_files = _normalize_input_files(raw_input_files)
    manager = PPTProductionManager()
    if action == "start":
        result = await manager.start(
            user_prompt=user_prompt,
            input_files=resolved_input_files,
            placeholder_assets=placeholder_assets,
            render_settings=render_settings,
            adk_state=state,
        )
    elif action == "status":
        result = await manager.status(production_session_id=production_session_id, adk_state=state)
    elif action == "resume":
        result = await manager.resume(
            production_session_id=production_session_id,
            user_response=user_response,
            adk_state=state,
        )
    elif action == "view":
        result = await manager.view(
            production_session_id=production_session_id,
            view_type=view_type,
            adk_state=state,
        )
    elif action == "add_inputs":
        result = await manager.add_inputs(
            production_session_id=production_session_id,
            input_files=resolved_input_files,
            user_response=user_response,
            adk_state=state,
        )
    elif action == "analyze_revision_impact":
        result = await manager.analyze_revision_impact(
            production_session_id=production_session_id,
            user_response=user_response,
            adk_state=state,
        )
    elif action == "apply_revision":
        result = await manager.apply_revision(
            production_session_id=production_session_id,
            user_response=user_response,
            adk_state=state,
        )
    elif action == "regenerate_stale_segments":
        result = await manager.regenerate_stale_segments(
            production_session_id=production_session_id,
            adk_state=state,
        )
    else:
        result = ProductionRunResult(
            status="failed",
            capability="ppt",
            production_session_id=production_session_id or "",
            stage="invalid_action",
            progress_percent=0,
            message=f"Unsupported PPT production action: {action}",
        )
    return result.model_dump(mode="json")


def _normalize_input_files(raw_files: Any) -> list[dict[str, Any]]:
    """Normalize ADK file payload variants into workspace file records."""
    if raw_files is None:
        return []
    if isinstance(raw_files, (str, dict)):
        candidates = [raw_files]
    else:
        try:
            candidates = list(raw_files)
        except TypeError:
            return []

    normalized: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, str):
            path = item.strip()
            if path:
                normalized.append({"path": path, "name": Path(path).name, "description": ""})
            continue
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "") or "").strip()
        if not path:
            continue
        normalized_item = dict(item)
        normalized_item["path"] = path
        normalized_item.setdefault("name", Path(path).name)
        normalized_item.setdefault("description", "")
        normalized.append(normalized_item)
    return normalized
