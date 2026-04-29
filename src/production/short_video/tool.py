"""ADK tool wrapper for short-video production."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from google.adk.tools.tool_context import ToolContext

from src.production.models import ProductionRunResult
from src.production.short_video.expert_runtime import ShortVideoStoryboardExpertRuntime
from src.production.short_video.manager import ShortVideoProductionManager


async def run_short_video_production(
    action: Literal[
        "start",
        "status",
        "resume",
        "view",
        "add_reference_assets",
        "analyze_revision_impact",
        "apply_revision",
    ],
    user_prompt: str = "",
    production_session_id: str | None = None,
    view_type: Literal["overview", "brief", "storyboard", "asset_plan", "timeline", "quality", "events", "artifacts"] = "overview",
    input_files: list[Any] | str | None = None,
    placeholder_assets: bool = False,
    render_settings: dict[str, Any] | None = None,
    user_response: dict[str, Any] | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run, inspect, resume, view, revise, or analyze a short-video production task.

    Args:
        action: Use start, status, resume, view, add_reference_assets, analyze_revision_impact, or apply_revision.
        user_prompt: User's short-video brief when starting production.
        production_session_id: Existing production session id for status, resume, view, or impact analysis.
        view_type: Read-only view to load when action is view. Allowed values are overview, brief, storyboard, asset_plan, timeline, quality, events, and artifacts.
        input_files: Optional workspace file records or workspace-relative path strings to use as reference assets.
        placeholder_assets: Use true only for P0a placeholder rendering.
        render_settings: Optional aspect ratio, duration, fps, width, height, provider/runtime, model_name,
            resolution settings, and storyboard_expert enable/disable flag.
        user_response: User decision payload for resume, replacement details for add_reference_assets, or targets/notes for revision actions. Plain text is accepted and treated as notes.
    """
    if tool_context is None:
        return ProductionRunResult(
            status="failed",
            capability="short_video",
            production_session_id=production_session_id or "",
            stage="missing_tool_context",
            progress_percent=0,
            message="tool_context is required for short-video production.",
        ).model_dump(mode="json")

    state = tool_context.state
    raw_input_files = input_files if input_files is not None else state.get("uploaded") or state.get("input_files")
    resolved_input_files = _normalize_input_files(raw_input_files)
    manager = ShortVideoProductionManager(
        storyboard_expert_runtime=_storyboard_expert_runtime_for_tool(render_settings, tool_context),
    )
    if action == "start":
        result = await manager.start(
            user_prompt=user_prompt,
            input_files=resolved_input_files,
            placeholder_assets=placeholder_assets,
            render_settings=render_settings,
            adk_state=state,
        )
    elif action == "status":
        result = await manager.status(
            production_session_id=production_session_id,
            adk_state=state,
        )
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
    elif action == "add_reference_assets":
        result = await manager.add_reference_assets(
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
    else:
        result = ProductionRunResult(
            status="failed",
            capability="short_video",
            production_session_id=production_session_id or "",
            stage="invalid_action",
            progress_percent=0,
            message=f"Unsupported short-video production action: {action}",
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


def _storyboard_expert_runtime_for_tool(
    render_settings: dict[str, Any] | None,
    tool_context: ToolContext,
) -> ShortVideoStoryboardExpertRuntime | None:
    """Return the storyboard expert runtime for real ADK tool calls."""
    settings = render_settings or {}
    requested = settings.get("storyboard_expert", settings.get("use_storyboard_expert"))
    if requested is False:
        return None
    if requested is True:
        return ShortVideoStoryboardExpertRuntime()
    if not isinstance(tool_context, ToolContext):
        return None
    return ShortVideoStoryboardExpertRuntime()
