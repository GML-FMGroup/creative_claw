"""Workflow runtime for channel-driven Creative Claw execution."""

from __future__ import annotations

import json
import uuid

from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.agents.experts import (
    ImageEditingAgent,
    ImageGenerationAgent,
    ImageToPromptAgent,
    ImageUnderstandingAgent,
    KnowledgeAgent,
    SearchAgent,
)
from src.agents.executor.executor_agent import Executor
from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.logger import logger
from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.step_events import reset_step_event_history, step_event_streaming_active
from src.runtime.workspace import (
    build_workspace_file_record,
    generated_root,
    stage_attachment_into_workspace,
    workspace_relative_path,
    resolve_workspace_path,
    workspace_root,
)

_HELP_TEXT = (
    "CreativeClaw commands:\n"
    "/new - Start a new conversation session\n"
    "/help - Show available commands"
)

_PROGRESS_STAGE_TITLES = {
    "started": "开始处理",
    "attachment_received": "已接收输入",
    "in_progress": "处理中",
    "planning": "规划下一步",
    "inspection": "查看上下文",
    "editing": "修改内容",
    "image_processing": "处理图片",
    "execution": "执行命令",
    "research": "查询资料",
    "expert_execution": "调用专家代理",
    "finalizing": "整理结果",
}


def _format_exception_summary(exc: Exception) -> str:
    """Return a concise exception summary that always includes the exception type."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _build_progress_event(
    text: str,
    *,
    session_id: str,
    stage: str,
    stage_title: str | None = None,
) -> WorkflowEvent:
    """Build one user-facing progress event."""
    return WorkflowEvent(
        event_type="status",
        text=text,
        metadata={
            "session_id": session_id,
            "display_style": "progress",
            "stage": stage,
            "stage_title": stage_title or _PROGRESS_STAGE_TITLES.get(stage, "当前进度"),
        },
    )


def _summarize_step_output(output_message: str) -> str:
    """Convert one raw step output into a concise user-facing progress line."""
    text = str(output_message or "").strip()
    if not text:
        return ""
    if len(text) > 160:
        text = f"{text[:157].rstrip()}..."
    return f"当前进展：{text}"


def _build_orchestration_progress_event(step_event: dict[str, str], *, session_id: str) -> WorkflowEvent:
    """Convert one structured orchestrator step event into a progress event."""
    stage = str(step_event.get("stage", "")).strip() or "in_progress"
    title = str(step_event.get("title", "")).strip() or _PROGRESS_STAGE_TITLES.get(stage, "当前进度")
    detail = str(step_event.get("detail", "")).strip() or "正在处理当前步骤。"
    return _build_progress_event(
        detail,
        session_id=session_id,
        stage=stage,
        stage_title=title,
    )


def _render_orchestration_history(history: list[dict[str, str]], limit: int = 8) -> str:
    """Render recent orchestration events into one readable progress timeline."""
    recent = history[-limit:]
    blocks: list[str] = []
    for index, step_event in enumerate(recent, start=1):
        title = str(step_event.get("title", "")).strip() or "处理中"
        detail = str(step_event.get("detail", "")).strip() or "正在处理当前步骤。"
        blocks.append(f"**{index}. {title}**\n{detail}")
    return "\n\n".join(blocks)


class CreativeClawRuntime:
    """Run Creative Claw workflow for normalized channel messages."""

    def __init__(self) -> None:
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()
        self._session_keys: dict[str, str] = {}

        self.expert_agents = {
            "ImageGenerationAgent": ImageGenerationAgent(name="Text2ImageAgent"),
            "ImageEditingAgent": ImageEditingAgent(name="ImageEditingAgent"),
            "ImageToPromptAgent": ImageToPromptAgent(name="ImageToPromptAgent"),
            "ImageUnderstandingAgent": ImageUnderstandingAgent(name="ImageUnderstandingAgent"),
            "KnowledgeAgent": KnowledgeAgent(name="KnowledgeAgent"),
            "SearchAgent": SearchAgent(name="SearchAgent"),
        }
        self.expert_runners = {
            name: Runner(
                agent=agent,
                app_name=SYS_CONFIG.app_name,
                session_service=self.session_service,
                artifact_service=self.artifact_service,
            )
            for name, agent in self.expert_agents.items()
        }

        self.workspace_root = workspace_root()
        self.generated_dir = generated_root()

    async def run_message(self, inbound: InboundMessage):
        """Execute one inbound message and yield workflow events."""
        command = inbound.text.strip().lower()
        if command == "/help":
            yield WorkflowEvent(
                event_type="final",
                text=_HELP_TEXT,
                metadata={"user_id": inbound.sender_id or SYS_CONFIG.user_id_default},
            )
            return
        if command == "/new":
            user_id, session_id = await self.reset_session(inbound)
            yield WorkflowEvent(
                event_type="final",
                text="Started a new conversation session.",
                metadata={
                    "session_id": session_id,
                    "user_id": user_id,
                    "display_style": "final",
                },
            )
            return

        user_id, session_id = await self._ensure_session(inbound)

        yield _build_progress_event(
            "我先处理一下你的请求。",
            session_id=session_id,
            stage="started",
        )
        reset_step_event_history(session_id=session_id)
        for attachment in inbound.attachments:
            yield _build_progress_event(
                f"已收到附件：{attachment.name}",
                session_id=session_id,
                stage="attachment_received",
            )

        try:
            await self._set_initial_state(user_id, session_id, inbound)
        except Exception as exc:
            error_summary = _format_exception_summary(exc)
            error_text = f"Init state failed (session_id={session_id}): {error_summary}"
            logger.opt(exception=exc).error(
                "Init state failed: session_id={} channel={} sender_id={} error_summary={}",
                session_id,
                inbound.channel,
                inbound.sender_id or SYS_CONFIG.user_id_default,
                error_summary,
            )
            yield WorkflowEvent(event_type="error", text=error_text, metadata={"session_id": session_id})
            return

        orchestrator_agent = Orchestrator(
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            expert_runners=self.expert_runners,
            app_name=SYS_CONFIG.app_name,
            save_dir=str(self.generated_dir),
        )
        orchestrator_agent.uid = user_id
        orchestrator_agent.sid = session_id
        executor_agent = Executor(
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            expert_runners=self.expert_runners,
            app_name=SYS_CONFIG.app_name,
            save_dir=str(self.generated_dir),
            execute_enabled=False,
        )
        executor_agent.uid = user_id
        executor_agent.sid = session_id

        try:
            final_summary = "task workflow has started."
            max_loops = SYS_CONFIG.max_iterations_orchestrator
            orchestration_history: list[dict[str, str]] = []
            current_round = 0
            current_agent = ""
            for index in range(max_loops):
                current_round = index + 1
                logger.info("--- workflow: round {}/{} ---", index + 1, max_loops)
                current_session = await self.session_service.get_session(
                    app_name=SYS_CONFIG.app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                logger.debug(
                    "session.state (Orchestrator): {}",
                    json.dumps(current_session.state, indent=2, ensure_ascii=False),
                )

                step_result = await orchestrator_agent.generate_step_plan()
                workflow_status = step_result.get("workflow_status", "running")
                response_text = step_result.get("last_response", "")
                output_message = step_result.get("last_output_message", "")
                orchestration_events = list(step_result.get("new_orchestration_events", []))
                current_plan = step_result.get("current_plan", {})
                next_agent = str(current_plan.get("next_agent", "")).strip()
                if step_event_streaming_active():
                    orchestration_events = []
                final_summary = (
                    step_result.get("final_summary")
                    or str(current_plan.get("summary", "")).strip()
                    or output_message
                    or response_text
                    or final_summary
                )

                for step_event in orchestration_events:
                    orchestration_history.append(step_event)
                    progress_event = _build_orchestration_progress_event(step_event, session_id=session_id)
                    progress_event.text = _render_orchestration_history(orchestration_history)
                    yield progress_event

                if workflow_status == "finished":
                    logger.info("Workflow finished. summary={}", final_summary)
                    break

                if not next_agent:
                    raise ValueError("Orchestrator did not provide `next_agent` in the current plan.")

                current_agent = next_agent
                current_output = await executor_agent.execute_plan()
                output_message = str((current_output or {}).get("message", "")).strip()
                if output_message:
                    yield _build_progress_event(
                        _summarize_step_output(output_message),
                        session_id=session_id,
                        stage="expert_execution",
                        stage_title=f"{next_agent} 已返回",
                    )
                    final_summary = output_message or final_summary
            else:
                final_summary = (
                    f"Workflow has reached the max iteration ({max_loops}) and has been terminated."
                )

            final_event = await self._build_final_event(user_id, session_id, final_summary)
            yield final_event
        except Exception as exc:
            error_summary = _format_exception_summary(exc)
            context_parts = [f"session_id={session_id}"]
            if current_round:
                context_parts.append(f"round={current_round}")
            if current_agent:
                context_parts.append(f"next_agent={current_agent}")
            error_text = f"Workflow failed ({', '.join(context_parts)}): {error_summary}"
            logger.opt(exception=exc).error(
                "Workflow failed: session_id={} round={} next_agent={} error_summary={}",
                session_id,
                current_round or "-",
                current_agent or "-",
                error_summary,
            )
            yield WorkflowEvent(event_type="error", text=error_text, metadata={"session_id": session_id})

    async def reset_session(self, inbound: InboundMessage) -> tuple[str, str]:
        """Force-create a fresh ADK session for the current channel conversation."""
        user_id = inbound.sender_id or SYS_CONFIG.user_id_default
        session_key = inbound.session_key
        session_id = f"{SYS_CONFIG.session_id_default_prefix}{uuid.uuid4()}"
        await self.session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        self._session_keys[session_key] = session_id
        logger.info("Reset session for {} -> {}", session_key, session_id)
        return user_id, session_id

    async def _ensure_session(self, inbound: InboundMessage) -> tuple[str, str]:
        """Create or reuse one ADK session for a logical channel conversation."""
        user_id = inbound.sender_id or SYS_CONFIG.user_id_default
        session_key = inbound.session_key
        session_id = self._session_keys.get(session_key)

        if session_id:
            existing_session = await self.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if existing_session is not None:
                return user_id, session_id

        session_id = f"{SYS_CONFIG.session_id_default_prefix}{uuid.uuid4()}"
        await self.session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        self._session_keys[session_key] = session_id
        logger.info("Created session for {} -> {}", session_key, session_id)
        return user_id, session_id

    async def _set_initial_state(self, user_id: str, session_id: str, inbound: InboundMessage) -> None:
        """Append the normalized user message and attachments to session state."""
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            raise ValueError(f"Session {session_id} not found for user {user_id}")

        state_delta = {key: None for key in current_session.state.keys()}
        state_delta["app_name"] = SYS_CONFIG.app_name
        state_delta["uid"] = user_id
        state_delta["sid"] = session_id
        state_delta["user_prompt"] = inbound.text
        state_delta["step"] = current_session.state.get("step", 0)
        state_delta["input_files"] = []
        state_delta["workflow_status"] = "running"
        state_delta["final_summary"] = ""
        state_delta["last_output_message"] = ""
        state_delta["last_orchestrator_response"] = ""
        state_delta["current_parameters"] = {}
        state_delta["current_plan"] = None
        state_delta["current_output"] = None

        for index, attachment in enumerate(inbound.attachments, start=1):
            saved_path = stage_attachment_into_workspace(
                attachment.path,
                channel=inbound.channel,
                session_id=session_id,
                preferred_name=attachment.name,
            )
            file_name = attachment.name or saved_path.name
            description = attachment.description or f"user input attachment {index}"
            state_delta["input_files"].append(
                build_workspace_file_record(
                    saved_path,
                    description=description,
                    source="channel",
                    name=file_name,
                )
            )
            state_delta["user_prompt"] += (
                f"\nInput file {index}: name={file_name}, "
                f"path={workspace_relative_path(saved_path)}"
            )

        existing_files_history = current_session.state.get("files_history", [])
        state_delta["files_history"] = (
            existing_files_history + [state_delta["input_files"]]
            if state_delta["input_files"]
            else existing_files_history
        )
        state_delta["summary_history"] = current_session.state.get("summary_history", [])
        state_delta["text_history"] = current_session.state.get("text_history", [])
        state_delta["message_history"] = current_session.state.get("message_history", [])
        state_delta["new_files"] = state_delta["input_files"]
        state_delta["orchestration_events"] = current_session.state.get("orchestration_events", [])

        event = Event(
            author="channel_gateway",
            content=Content(
                role="user",
                parts=[
                    Part(
                        text=(
                            f"New user input task: {state_delta['user_prompt']}, "
                            "you can start to analyze."
                        )
                    )
                ],
            ),
            actions=EventActions(state_delta=state_delta),
        )
        await self.session_service.append_event(current_session, event)

    async def _build_final_event(
        self,
        user_id: str,
        session_id: str,
        final_summary: str,
    ) -> WorkflowEvent:
        """Build the final workflow event from the current session state."""
        final_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if final_session is None:
            return WorkflowEvent(
                event_type="error",
                text="Workflow ended without a valid session.",
                metadata={"session_id": session_id},
            )

        files_history = final_session.state.get("files_history") or []
        final_files = _select_latest_output_files(files_history)

        text_history = final_session.state.get("text_history") or []
        if text_history and text_history[-1]:
            final_summary = text_history[-1]
        else:
            state_summary = final_session.state.get("final_summary")
            if state_summary:
                final_summary = state_summary
            summary_history = final_session.state.get("summary_history") or []
            if summary_history and not state_summary:
                history_text = "\n".join(f"- {summary}" for summary in summary_history)
                final_summary = f"{final_summary}\nExecution history:\n{history_text}"

        artifact_paths = [
            str(resolve_workspace_path(file_info.get("path", "")).resolve())
            for file_info in final_files
            if str(file_info.get("path", "")).strip()
        ]
        return WorkflowEvent(
            event_type="final",
            text=final_summary,
            artifact_paths=artifact_paths,
            metadata={"session_id": session_id, "display_style": "final"},
        )


def _select_latest_output_files(files_history: list[list[dict]]) -> list[dict]:
    """Return the latest non-channel file batch from history."""
    for file_group in reversed(files_history):
        if file_group and any(str(file_info.get("source", "")).strip() != "channel" for file_info in file_group):
            return file_group
    return []
