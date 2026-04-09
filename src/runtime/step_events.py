"""Realtime step-event publishing for tool lifecycle callbacks."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from google.adk.plugins.base_plugin import BasePlugin

from src.channels.events import OutboundMessage
from src.runtime.tool_context import get_route
from src.runtime.tool_display import format_tool_args, summarize_tool_result

_STEP_EVENT_PUBLISHER: Callable[[OutboundMessage], Awaitable[None] | None] | None = None
_HISTORY_BY_SESSION: dict[str, list[dict[str, str]]] = {}
_BUILTIN_TOOL_STAGES = {
    "list_dir": "inspection",
    "read_file": "inspection",
    "write_file": "editing",
    "edit_file": "editing",
    "image_crop": "image_processing",
    "image_rotate": "image_processing",
    "image_flip": "image_processing",
    "exec_command": "execution",
    "web_search": "research",
    "web_fetch": "research",
}


def configure_step_event_publisher(
    publisher: Callable[[OutboundMessage], Awaitable[None] | None] | None,
) -> None:
    """Configure the async publisher used by realtime step events."""
    global _STEP_EVENT_PUBLISHER
    _STEP_EVENT_PUBLISHER = publisher


def step_event_publisher_configured() -> bool:
    """Return whether realtime step publishing is currently enabled."""
    return _STEP_EVENT_PUBLISHER is not None


def step_event_streaming_active() -> bool:
    """Return whether realtime step publishing is active for the current route."""
    channel, chat_id = get_route()
    return _STEP_EVENT_PUBLISHER is not None and bool(channel) and bool(chat_id)


def _render_history(history: list[dict[str, str]], limit: int = 8) -> str:
    """Render recent tool events into one readable progress timeline."""
    recent = history[-limit:]
    blocks: list[str] = []
    for index, step_event in enumerate(recent, start=1):
        title = str(step_event.get("title", "")).strip() or "处理中"
        detail = str(step_event.get("detail", "")).strip() or "正在处理当前步骤。"
        blocks.append(f"**{index}. {title}**\n{detail}")
    return "\n\n".join(blocks)


def _session_history_key(channel: str, chat_id: str, session_id: str) -> str:
    """Build the in-memory history key for one channel session."""
    return f"{channel}:{chat_id}:{session_id}"


def _build_detail(*, status: str, args: dict[str, Any], result_text: str | None = None) -> str:
    """Build the detail body shown in the progress card."""
    lines = [f"状态：{status}", f"参数：{format_tool_args(args)}"]
    if result_text:
        lines.append(f"结果：{result_text}")
    return "\n".join(lines)


async def _publish_step_event(
    *,
    session_id: str,
    tool_name: str,
    stage: str,
    detail: str,
) -> None:
    """Publish one realtime tool progress event through the configured publisher."""
    publisher = _STEP_EVENT_PUBLISHER
    channel, chat_id = get_route()
    if publisher is None or not channel or not chat_id:
        return

    key = _session_history_key(channel, chat_id, session_id)
    history = _HISTORY_BY_SESSION.setdefault(key, [])
    history.append({"title": tool_name, "detail": detail, "stage": stage})

    maybe_awaitable = publisher(
        OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            text=_render_history(history),
            metadata={
                "session_id": session_id,
                "display_style": "progress",
                "stage": stage,
                "stage_title": tool_name,
            },
        )
    )
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def reset_step_event_history(*, session_id: str) -> None:
    """Reset the in-memory realtime history for the current routed session."""
    channel, chat_id = get_route()
    if not channel or not chat_id or not session_id:
        return
    _HISTORY_BY_SESSION[_session_history_key(channel, chat_id, session_id)] = []


def publish_orchestration_step_event(
    *,
    session_id: str,
    title: str,
    detail: str,
    stage: str,
) -> None:
    """Schedule one realtime publish for an orchestrator-level step event."""
    if not step_event_streaming_active():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _publish_step_event(
            session_id=session_id,
            tool_name=title,
            stage=stage,
            detail=detail,
        )
    )


class CreativeClawStepEventPlugin(BasePlugin):
    """Publish builtin tool lifecycle events in realtime during ADK execution."""

    def __init__(self) -> None:
        super().__init__(name="creative_claw_step_events")

    async def before_run_callback(self, *, invocation_context) -> None:
        """Initialize one empty realtime history per invocation."""
        return None

    async def after_run_callback(self, *, invocation_context) -> None:
        """Release one invocation history after the runner finishes."""
        return None

    async def before_tool_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
    ) -> None:
        """Publish one realtime start event before builtin tool execution."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        await _publish_step_event(
            session_id=tool_context.session.id,
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(status="开始", args=tool_args),
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
        result: Any,
    ) -> None:
        """Publish one realtime completion event after builtin tool execution."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        status, summary = summarize_tool_result(tool.name, result)
        await _publish_step_event(
            session_id=tool_context.session.id,
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(
                status="成功" if status == "success" else "异常",
                args=tool_args,
                result_text=summary,
            ),
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool,
        tool_args: dict[str, Any],
        tool_context,
        error: Exception,
    ) -> None:
        """Publish one realtime error event when builtin tool execution fails."""
        stage = _BUILTIN_TOOL_STAGES.get(tool.name)
        if stage is None or not step_event_streaming_active():
            return None
        await _publish_step_event(
            session_id=tool_context.session.id,
            tool_name=tool.name,
            stage=stage,
            detail=_build_detail(status="异常", args=tool_args, result_text=str(error).strip()),
        )
        return None
