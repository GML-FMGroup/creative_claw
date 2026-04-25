"""ADK tool wrapper for short-video production."""

from __future__ import annotations

from typing import Any, Literal

from google.adk.tools.tool_context import ToolContext

from src.production.models import ProductionRunResult
from src.production.short_video.manager import ShortVideoProductionManager


async def run_short_video_production(
    action: Literal["start", "status", "resume"],
    user_prompt: str = "",
    production_session_id: str | None = None,
    input_files: list[dict[str, Any]] | None = None,
    placeholder_assets: bool = False,
    render_settings: dict[str, Any] | None = None,
    user_response: dict[str, Any] | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run, inspect, or resume a short-video production task."""
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
    resolved_input_files = (
        list(input_files)
        if input_files is not None
        else list(state.get("uploaded") or state.get("input_files") or [])
    )
    manager = ShortVideoProductionManager()
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
