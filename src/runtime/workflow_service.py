"""Workflow runtime for channel-driven Creative Claw execution."""

from __future__ import annotations

import json
import mimetypes
import os.path as osp
import uuid
from pathlib import Path

from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Blob, Content, Part

from conf.system import SYS_CONFIG
from src.agents.experts import (
    ImageEditingAgent,
    ImageGenerationAgent,
    ImageToPromptAgent,
    ImageUnderstandingAgent,
    KnowledgeAgent,
    SearchAgent,
)
from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.logger import logger
from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.step_events import step_event_streaming_active

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

        self.outputs_path = Path(SYS_CONFIG.base_dir) / "outputs"
        self.images_dir = self.outputs_path / "images"
        self.videos_dir = self.outputs_path / "videos"
        self.uploads_dir = self.outputs_path / "uploads"
        for path in (self.outputs_path, self.images_dir, self.videos_dir, self.uploads_dir):
            path.mkdir(parents=True, exist_ok=True)

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
        for attachment in inbound.attachments:
            yield _build_progress_event(
                f"已收到附件：{attachment.name}",
                session_id=session_id,
                stage="attachment_received",
            )

        try:
            await self._set_initial_state(user_id, session_id, inbound)
        except Exception as exc:
            error_text = f"Init state failed: {exc}"
            logger.error(error_text, exc_info=True)
            yield WorkflowEvent(event_type="error", text=error_text, metadata={"session_id": session_id})
            return

        orchestrator_agent = Orchestrator(
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            expert_runners=self.expert_runners,
            app_name=SYS_CONFIG.app_name,
            save_dir=str(self.images_dir),
        )
        orchestrator_agent.uid = user_id
        orchestrator_agent.sid = session_id

        try:
            final_summary = "task workflow has started."
            max_loops = SYS_CONFIG.max_iterations_orchestrator
            orchestration_history: list[dict[str, str]] = []
            for index in range(max_loops):
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

                step_result = await orchestrator_agent.run_step()
                workflow_status = step_result.get("workflow_status", "running")
                response_text = step_result.get("last_response", "")
                output_message = step_result.get("last_output_message", "")
                orchestration_events = list(step_result.get("new_orchestration_events", []))
                if step_event_streaming_active():
                    orchestration_events = [
                        step_event
                        for step_event in orchestration_events
                        if str(step_event.get("title", "")).strip()
                        not in {
                            "list_dir",
                            "read_file",
                            "write_file",
                            "edit_file",
                            "image_crop",
                            "image_rotate",
                            "image_flip",
                            "exec_command",
                            "web_search",
                            "web_fetch",
                        }
                    ]
                final_summary = step_result.get("final_summary") or output_message or response_text or final_summary

                for step_event in orchestration_events:
                    orchestration_history.append(step_event)
                    progress_event = _build_orchestration_progress_event(step_event, session_id=session_id)
                    progress_event.text = _render_orchestration_history(orchestration_history)
                    yield progress_event

                progress_text = ""
                if output_message and workflow_status != "finished" and not orchestration_events:
                    progress_text = _summarize_step_output(output_message)
                elif (
                    not output_message
                    and response_text
                    and workflow_status != "finished"
                    and index == 0
                    and not orchestration_events
                ):
                    progress_text = "正在继续处理，请稍等。"

                if progress_text:
                    yield _build_progress_event(
                        progress_text,
                        session_id=session_id,
                        stage="in_progress",
                    )

                if workflow_status == "finished":
                    logger.info("Workflow finished. summary={}", final_summary)
                    break
            else:
                final_summary = (
                    f"Workflow has reached the max iteration ({max_loops}) and has been terminated."
                )

            final_event = await self._build_final_event(user_id, session_id, final_summary)
            yield final_event
        except Exception as exc:
            error_text = f"Workflow failed: {exc}"
            logger.error(error_text, exc_info=True)
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
        state_delta["input_artifacts"] = []
        state_delta["workflow_status"] = "running"
        state_delta["final_summary"] = ""
        state_delta["last_output_message"] = ""
        state_delta["last_orchestrator_response"] = ""
        state_delta["current_parameters"] = {}
        state_delta["current_output"] = None

        for index, attachment in enumerate(inbound.attachments, start=1):
            artifact_name = attachment.name or osp.basename(attachment.path)
            description = attachment.description or f"user input attachment {index}"
            state_delta["input_artifacts"].append(
                {
                    "name": artifact_name,
                    "path": attachment.path,
                    "description": description,
                }
            )
            state_delta["user_prompt"] += f"\nInput attachment {index} name is {artifact_name}"
            await self.artifact_service.save_artifact(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
                filename=artifact_name,
                artifact=_load_file_as_part(attachment.path, attachment.mime_type),
            )

        state_delta["artifacts_history"] = current_session.state.get("artifacts_history", [])
        state_delta["summary_history"] = current_session.state.get("summary_history", [])
        state_delta["text_history"] = current_session.state.get("text_history", [])
        state_delta["message_history"] = current_session.state.get("message_history", [])
        state_delta["new_artifacts"] = state_delta["input_artifacts"]
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

        artifacts_history = final_session.state.get("artifacts_history") or []
        final_artifacts = _select_latest_artifacts(artifacts_history)

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
            str(artifact.get("path", "")).strip()
            for artifact in final_artifacts
            if str(artifact.get("path", "")).strip()
        ]
        return WorkflowEvent(
            event_type="final",
            text=final_summary,
            artifact_paths=artifact_paths,
            metadata={"session_id": session_id, "display_style": "final"},
        )


def _load_file_as_part(file_path: str, explicit_mime_type: str = "") -> Part:
    """Load a local file as a generic ADK artifact part."""
    mime_type = explicit_mime_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as file_obj:
        file_bytes = file_obj.read()
    return Part(inline_data=Blob(mime_type=mime_type, data=file_bytes))


def _select_latest_artifacts(artifacts_history: list[list[dict]]) -> list[dict]:
    """Return the latest non-empty artifact batch from history."""
    for artifact_group in reversed(artifacts_history):
        if artifact_group:
            return artifact_group
    return []
