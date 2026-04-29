"""Workflow runtime for channel-driven Creative Claw execution."""

from __future__ import annotations

import json
import uuid

from google.adk.artifacts import InMemoryArtifactService
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.logger import logger
from src.runtime.expert_registry import build_expert_agents
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
    "started": "Starting",
    "attachment_received": "Attachment Received",
    "in_progress": "In Progress",
    "planning": "Planning Next Step",
    "inspection": "Inspecting Context",
    "editing": "Editing Content",
    "image_processing": "Processing Image",
    "video_processing": "Processing Video",
    "audio_processing": "Processing Audio",
    "execution": "Running Command",
    "research": "Researching",
    "expert_execution": "Calling Expert Agent",
    "finalizing": "Finalizing Result",
}


def _append_turn_file_history(
    history: list[dict[str, object]],
    *,
    turn: int,
    files: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append one turn-scoped file batch to history when files exist."""
    if not files:
        return history
    updated_history = list(history)
    updated_history.append({"turn": turn, "files": list(files)})
    return updated_history


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
    turn_index: int | None = None,
) -> WorkflowEvent:
    """Build one user-facing progress event."""
    metadata: dict[str, object] = {
        "session_id": session_id,
        "display_style": "progress",
        "stage": stage,
        "stage_title": stage_title or _PROGRESS_STAGE_TITLES.get(stage, "Current Progress"),
    }
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    return WorkflowEvent(event_type="status", text=text, metadata=metadata)


def _summarize_step_output(output_message: str) -> str:
    """Convert one raw step output into a concise user-facing progress line."""
    text = str(output_message or "").strip()
    if not text:
        return ""
    if len(text) > 160:
        text = f"{text[:157].rstrip()}..."
    return f"Current progress: {text}"


def _build_orchestration_progress_event(
    step_event: dict[str, str],
    *,
    session_id: str,
    turn_index: int | None = None,
) -> WorkflowEvent:
    """Convert one structured orchestrator step event into a progress event."""
    stage = str(step_event.get("stage", "")).strip() or "in_progress"
    title = str(step_event.get("title", "")).strip() or _PROGRESS_STAGE_TITLES.get(stage, "Current Progress")
    detail = str(step_event.get("detail", "")).strip() or "Processing the current step."
    return _build_progress_event(
        detail,
        session_id=session_id,
        stage=stage,
        stage_title=title,
        turn_index=turn_index,
    )


def _render_orchestration_history(history: list[dict[str, str]], limit: int = 8) -> str:
    """Render recent orchestration events into one readable progress timeline."""
    recent = history[-limit:]
    blocks: list[str] = []
    for index, step_event in enumerate(recent, start=1):
        title = str(step_event.get("title", "")).strip() or "In Progress"
        detail = str(step_event.get("detail", "")).strip() or "Processing the current step."
        blocks.append(f"**{index}. {title}**\n{detail}")
    return "\n\n".join(blocks)


class CreativeClawRuntime:
    """Run Creative Claw workflow for normalized channel messages."""

    def __init__(
        self,
        *,
        session_service: InMemorySessionService | None = None,
        artifact_service: InMemoryArtifactService | None = None,
        expert_agents: dict[str, BaseAgent] | None = None,
    ) -> None:
        """Initialize runtime services.

        Optional dependency injection is intended for automated tests that need
        deterministic expert stubs or direct access to the in-memory session
        service. Default channel behavior remains unchanged.
        """
        self.session_service = session_service or InMemorySessionService()
        self.artifact_service = artifact_service or InMemoryArtifactService()
        self._session_keys: dict[str, str] = {}
        self.expert_agents = (
            expert_agents
            if expert_agents is not None
            else build_expert_agents(app_name=SYS_CONFIG.app_name)
        )

        self.workspace_root = workspace_root()
        self.generated_dir = generated_root()

    def resolve_session_id(self, *, channel: str, chat_id: str) -> str | None:
        """Return the ADK session id for a channel conversation if it exists."""
        return self._session_keys.get(f"{channel}:{chat_id}")

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
        current_turn = await self._next_turn_index(user_id, session_id)

        yield _build_progress_event(
            "I'll start processing your request.",
            session_id=session_id,
            stage="started",
            turn_index=current_turn,
        )
        reset_step_event_history(session_id=session_id, turn_index=current_turn)
        for attachment in inbound.attachments:
            yield _build_progress_event(
                f"Received attachment: {attachment.name}",
                session_id=session_id,
                stage="attachment_received",
                turn_index=current_turn,
            )

        try:
            await self._set_initial_state(user_id, session_id, inbound, turn_index=current_turn)
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
            yield WorkflowEvent(
                event_type="error",
                text=error_text,
                metadata={"session_id": session_id, "turn_index": current_turn},
            )
            return

        orchestrator_agent = Orchestrator(
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            expert_agents=self.expert_agents,
            app_name=SYS_CONFIG.app_name,
            save_dir=str(self.generated_dir),
        )
        orchestrator_agent.uid = user_id
        orchestrator_agent.sid = session_id

        try:
            final_summary = "task workflow has started."
            orchestration_history: list[dict[str, str]] = []
            current_session = await self.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            logger.debug(
                "session.state (Orchestrator): {}",
                json.dumps(current_session.state, indent=2, ensure_ascii=False),
            )

            step_result = await orchestrator_agent.run_until_done()
            final_response = str(step_result.get("final_response", "") or "").strip()
            orchestration_events = list(step_result.get("new_orchestration_events", []))
            if step_event_streaming_active():
                orchestration_events = []
            final_summary = (
                step_result.get("final_summary")
                or step_result.get("last_output_message")
                or final_summary
            )

            for step_event in orchestration_events:
                orchestration_history.append(step_event)
                progress_event = _build_orchestration_progress_event(
                    step_event,
                    session_id=session_id,
                    turn_index=current_turn,
                )
                progress_event.text = _render_orchestration_history(orchestration_history)
                yield progress_event

            final_event = await self._build_final_event(
                user_id,
                session_id,
                final_response or final_summary,
                turn_index=current_turn,
            )
            yield final_event
        except Exception as exc:
            error_summary = _format_exception_summary(exc)
            error_text = f"Workflow failed (session_id={session_id}): {error_summary}"
            logger.opt(exception=exc).error(
                "Workflow failed: session_id={} error_summary={}",
                session_id,
                error_summary,
            )
            yield WorkflowEvent(
                event_type="error",
                text=error_text,
                metadata={"session_id": session_id, "turn_index": current_turn},
            )

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

    async def _next_turn_index(self, user_id: str, session_id: str) -> int:
        """Return the next turn index for one ADK session without mutating state."""
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            return 1
        previous_turn = int(current_session.state.get("turn_index", 0) or 0)
        return previous_turn + 1

    async def _set_initial_state(
        self,
        user_id: str,
        session_id: str,
        inbound: InboundMessage,
        *,
        turn_index: int | None = None,
    ) -> None:
        """Append the normalized user message and attachments to session state."""
        current_session = await self.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if current_session is None:
            raise ValueError(f"Session {session_id} not found for user {user_id}")

        state_delta = {key: None for key in current_session.state.keys()}
        previous_turn = int(current_session.state.get("turn_index", 0) or 0)
        current_turn = turn_index if turn_index is not None else previous_turn + 1
        previous_uploaded = list(current_session.state.get("uploaded") or current_session.state.get("input_files") or [])
        previous_generated = list(current_session.state.get("generated") or [])
        uploaded_history = _append_turn_file_history(
            list(current_session.state.get("uploaded_history") or []),
            turn=previous_turn,
            files=previous_uploaded,
        )
        generated_history = _append_turn_file_history(
            list(current_session.state.get("generated_history") or []),
            turn=previous_turn,
            files=previous_generated,
        )

        state_delta["app_name"] = SYS_CONFIG.app_name
        state_delta["uid"] = user_id
        state_delta["sid"] = session_id
        state_delta["channel"] = inbound.channel
        state_delta["chat_id"] = inbound.chat_id
        state_delta["sender_id"] = inbound.sender_id or user_id
        state_delta["user_prompt"] = inbound.text
        state_delta["step"] = current_session.state.get("step", 0)
        state_delta["expert_step"] = current_session.state.get("expert_step", 0)
        state_delta["turn_index"] = current_turn
        state_delta["input_files"] = []
        state_delta["uploaded"] = []
        state_delta["uploaded_history"] = uploaded_history
        state_delta["generated"] = []
        state_delta["generated_history"] = generated_history
        state_delta["workflow_status"] = "running"
        state_delta["final_summary"] = ""
        state_delta["final_response"] = ""
        state_delta["final_file_paths"] = []
        state_delta["last_output_message"] = ""
        state_delta["last_orchestrator_response"] = ""
        state_delta["current_parameters"] = {}
        state_delta["current_output"] = None
        state_delta["last_expert_result"] = None
        state_delta["expert_history"] = []

        for index, attachment in enumerate(inbound.attachments, start=1):
            saved_path = stage_attachment_into_workspace(
                attachment.path,
                channel=inbound.channel,
                session_id=session_id,
                turn_index=current_turn,
                attachment_index=index,
                preferred_name=attachment.name,
            )
            file_name = attachment.name or saved_path.name
            description = attachment.description or f"user input attachment {index}"
            record = build_workspace_file_record(
                saved_path,
                description=description,
                source="channel",
                name=file_name,
                turn=current_turn,
            )
            state_delta["input_files"].append(record)
            state_delta["uploaded"].append(record)
            state_delta["user_prompt"] += (
                f"\nInput file {index}: name={file_name}, "
                f"path={workspace_relative_path(saved_path)}"
            )

        existing_files_history = current_session.state.get("files_history", [])
        state_delta["files_history"] = (
            existing_files_history + [state_delta["uploaded"]]
            if state_delta["uploaded"]
            else existing_files_history
        )
        state_delta["summary_history"] = current_session.state.get("summary_history", [])
        state_delta["text_history"] = current_session.state.get("text_history", [])
        state_delta["message_history"] = current_session.state.get("message_history", [])
        state_delta["new_files"] = state_delta["uploaded"]
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
        *,
        turn_index: int | None = None,
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

        explicit_final_file_paths = final_session.state.get("final_file_paths")
        artifact_paths = _resolve_final_artifact_paths(explicit_final_file_paths)

        state_response = final_session.state.get("final_response")
        if state_response:
            final_summary = state_response
        else:
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

        final_turn_index = turn_index if turn_index is not None else int(final_session.state.get("turn_index", 0) or 0)
        return WorkflowEvent(
            event_type="final",
            text=final_summary,
            artifact_paths=artifact_paths,
            metadata={
                "session_id": session_id,
                "turn_index": final_turn_index,
                "display_style": "final",
            },
        )


def _resolve_final_artifact_paths(selected_paths: object) -> list[str]:
    """Resolve one explicit final-file selection into absolute artifact paths."""
    if selected_paths is None:
        return []

    artifact_paths: list[str] = []
    seen_paths: set[str] = set()
    if not isinstance(selected_paths, list):
        return artifact_paths

    for path_value in selected_paths:
        if not isinstance(path_value, str):
            continue
        raw_path = path_value.strip()
        if not raw_path:
            continue
        try:
            resolved = resolve_workspace_path(raw_path).resolve()
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        resolved_text = str(resolved)
        if resolved_text in seen_paths:
            continue
        seen_paths.add(resolved_text)
        artifact_paths.append(resolved_text)
    return artifact_paths
