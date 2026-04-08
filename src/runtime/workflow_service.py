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
from src.agents.executor.executor_agent import Executor
from src.agents.experts import (
    ImageEditingAgent,
    ImageGenerationAgent,
    ImageUnderstandingAgent,
    KnowledgeAgent,
    SearchAgent,
)
from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.logger import logger
from src.runtime.models import InboundMessage, WorkflowEvent


class CreativeClawRuntime:
    """Run Creative Claw workflow for normalized channel messages."""

    def __init__(self) -> None:
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()
        self._session_keys: dict[str, str] = {}

        self.expert_agents = {
            "ImageGenerationAgent": ImageGenerationAgent(name="Text2ImageAgent"),
            "ImageEditingAgent": ImageEditingAgent(name="ImageEditingAgent"),
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
        user_id, session_id = await self._ensure_session(inbound)

        yield WorkflowEvent(
            event_type="status",
            text=f"user instruction: {inbound.text}",
            metadata={"session_id": session_id},
        )
        for attachment in inbound.attachments:
            yield WorkflowEvent(
                event_type="status",
                text=f"received attachment: {attachment.name}",
                metadata={"session_id": session_id, "path": attachment.path},
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
            app_name=SYS_CONFIG.app_name,
            max_iter=-1,
        )
        executor_agent = Executor(
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            app_name=SYS_CONFIG.app_name,
            expert_runners=self.expert_runners,
            execute_enabled=SYS_CONFIG.execute_enabled,
        )
        orchestrator_agent.uid = user_id
        orchestrator_agent.sid = session_id
        executor_agent.uid = user_id
        executor_agent.sid = session_id
        executor_agent.save_dir = self.images_dir

        try:
            _, global_summary = await orchestrator_agent.generate_plan(global_plan=True)
            yield WorkflowEvent(
                event_type="status",
                text=f"Orchestrator global plan: {global_summary}",
                metadata={"session_id": session_id},
            )

            final_summary = "task workflow has started."
            max_loops = SYS_CONFIG.max_iterations_orchestrator
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

                plan, current_summary = await orchestrator_agent.generate_plan(global_plan=False)
                next_agent_name = plan.get("next_agent")
                final_summary = current_summary

                yield WorkflowEvent(
                    event_type="status",
                    text=f"Orchestrator decision: {current_summary}",
                    metadata={"session_id": session_id, "next_agent": next_agent_name},
                )

                if not next_agent_name or next_agent_name == "FINISH":
                    logger.info("Workflow finished. summary={}", final_summary)
                    break

                if next_agent_name not in self.expert_agents:
                    error_text = f"Orchestrator calls an unknown agent: '{next_agent_name}'"
                    logger.error(error_text)
                    yield WorkflowEvent(
                        event_type="error",
                        text=error_text,
                        metadata={"session_id": session_id},
                    )
                    return

                yield WorkflowEvent(
                    event_type="status",
                    text=f"Assign task to expert: {next_agent_name}",
                    metadata={"session_id": session_id, "next_agent": next_agent_name},
                )

                current_output = await executor_agent.execute_plan()
                result_text = current_output.get("message", "")
                if current_output.get("output_text"):
                    result_text = f"{result_text}\n{current_output['output_text']}"

                yield WorkflowEvent(
                    event_type="status",
                    text=f"Execution result: {result_text}",
                    metadata={"session_id": session_id, "next_agent": next_agent_name},
                )
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
        state_delta["global_plan"] = None
        state_delta["current_plan"] = None
        state_delta["step"] = current_session.state.get("step", 0)
        state_delta["input_artifacts"] = []

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
            summary_history = final_session.state.get("summary_history") or []
            if summary_history:
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
            metadata={"session_id": session_id},
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
