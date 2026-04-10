"""Planning-oriented orchestrator runtime for Creative Claw."""

from __future__ import annotations

import json
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.agent import experts_list
from conf.system import SYS_CONFIG
from src.logger import logger
from src.runtime.step_events import (
    CreativeClawStepEventPlugin,
    publish_orchestration_step_event,
    step_event_streaming_active,
)
from src.runtime.tool_display import format_tool_args, stringify_value, summarize_tool_result
from src.runtime.workspace import (
    build_workspace_file_record,
    load_local_file_part,
    looks_like_image,
)
from src.skills import get_skill_registry
from src.tools.builtin_tools import (
    BuiltinToolbox,
)


async def orchestrator_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject compact runtime state and recent workspace files into the model request."""
    state = callback_context.state
    step = state.get("step", 0)
    workflow_status = state.get("workflow_status", "running")

    summary_lines = [
        f"# Workflow status: {workflow_status}",
        f"# Executed steps: {step}",
        f"# User task:\n{state.get('user_prompt', '')}",
    ]

    input_files = state.get("input_files") or state.get("input_artifacts", [])
    if input_files:
        summary_lines.append("# Original input files in workspace:")
        summary_lines.extend(
            (
                f"- {file_info['name']}: path={file_info.get('path', '')}; "
                f"description={file_info.get('description', '')}"
            )
            for file_info in input_files
        )

    summary_history = state.get("summary_history", [])
    message_history = state.get("message_history", [])
    if summary_history and message_history:
        summary_lines.append("# Execution history:")
        for index, (summary, message) in enumerate(zip(summary_history, message_history), start=1):
            summary_lines.append(f"- Step {index}: target={summary}; result={message}")

    files_history = state.get("files_history") or state.get("artifacts_history", [])
    if files_history:
        summary_lines.append("# Workspace file history:")
        for step_index, file_group in enumerate(files_history, start=1):
            if not file_group:
                summary_lines.append(f"- Step {step_index}: no output artifact")
                continue
            file_summaries = []
            for file_index, file_info in enumerate(file_group, start=1):
                file_name = str(file_info.get("name", "")).strip() or f"file_{file_index}"
                file_path = str(file_info.get("path", "")).strip()
                file_description = str(file_info.get("description", "")).strip()
                if file_description:
                    file_summaries.append(
                        f"file {file_index}: name={file_name}; path={file_path}; description={file_description}"
                    )
                else:
                    file_summaries.append(f"file {file_index}: name={file_name}; path={file_path}")
            summary_lines.append(f"- Step {step_index}: {' | '.join(file_summaries)}")

        latest_file_group = next(
            (file_group for file_group in reversed(files_history) if file_group),
            [],
        )
        if latest_file_group:
            latest_paths = ", ".join(
                str(file_info.get("path", "")).strip()
                for file_info in latest_file_group
                if str(file_info.get("path", "")).strip()
            )
            if latest_paths:
                summary_lines.append(f"# Most recent available output files: {latest_paths}")

    llm_request.contents.append(
        Content(role="user", parts=[Part(text="\n".join(summary_lines))])
    )

    new_files = state.get("new_files") or state.get("new_artifacts", [])
    if not new_files:
        return

    file_parts = [Part(text="Recent workspace files for reference:\n")]
    for index, file_info in enumerate(new_files, start=1):
        file_parts.append(
            Part(
                text=(
                    f"File {index}: {file_info['name']}. "
                    f"Path: {file_info.get('path', '')}. "
                    f"Description: {file_info.get('description', '')}\n"
                )
            )
        )
        file_path = str(file_info.get("path", "")).strip()
        if file_path and looks_like_image(file_path):
            file_parts.append(load_local_file_part(file_path))

    llm_request.contents.append(Content(role="user", parts=file_parts))


class Orchestrator:
    """Plan one workflow step at a time with skills and builtin tools."""

    def __init__(
        self,
        session_service: InMemorySessionService,
        artifact_service: InMemoryArtifactService,
        expert_runners: dict[str, Runner],
        app_name: str = SYS_CONFIG.app_name,
        save_dir: str = "",
        llm_model: str = "",
    ) -> None:
        self.app_name = app_name
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.expert_runners = expert_runners
        self.save_dir = save_dir
        self.uid = ""
        self.sid = ""
        self.skill_registry = get_skill_registry()
        self.toolbox = BuiltinToolbox()

        model_name = llm_model or SYS_CONFIG.llm_model
        logger.info("OrchestratorAgent: using llm: {}", model_name)

        self.agent = LlmAgent(
            name="CreativeClawOrchestrator",
            model=model_name,
            instruction=self._build_instruction(),
            before_model_callback=orchestrator_before_model_callback,
            tools=[
                self.list_skills,
                self.read_skill,
                self.list_dir,
                self.read_file,
                self.write_file,
                self.edit_file,
                self.image_crop,
                self.image_rotate,
                self.image_flip,
                self.exec_command,
                self.web_search,
                self.web_fetch,
            ],
        )
        self.runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
            session_service=self.session_service,
            artifact_service=self.artifact_service,
            plugins=[CreativeClawStepEventPlugin()],
        )

    def _build_instruction(self) -> str:
        """Build the planner instruction for the orchestrator."""
        available_experts = "\n".join(
            str(expert) for expert in experts_list if expert.enable
        )
        skills_summary = self.skill_registry.build_summary()

        return f"""
You are Creative Claw's single orchestrator.

Your job is to inspect the current state, use skills and built-in tools when needed, and then decide exactly one next workflow step.
Do not create a full upfront plan unless the user explicitly asks for one.

You can use four kinds of capabilities:
1. Skills from local markdown files
2. Built-in local file tools inside the fixed workspace
3. Built-in shell and web tools
4. Existing expert agents, but you cannot execute them directly in this invocation

Rules:
- When a skill seems relevant, call `list_skills` first and then `read_skill`.
- Never invent skill content. Read the actual `SKILL.md` before using it deeply.
- Prefer direct execution over abstract planning.
- Use built-in tools for local workspace work: `list_dir`, `read_file`, `write_file`, `edit_file`, `image_crop`, `image_rotate`, `image_flip`, `exec`, `web_search`, `web_fetch`.
- All file paths must be relative to the fixed `workspace` directory unless the tool explicitly returns a workspace-relative path.
- Inspect local files with `list_dir` and `read_file` before changing them when the path or contents are uncertain.
- Use local image tools for lightweight preprocessing, and keep the returned suffixed output path instead of overwriting the original by default.
- Keep changes small and reviewable, and re-check the latest state after each meaningful action.
- When planning expert parameters, pass workspace file paths with `input_path` or `input_paths` instead of artifact names.
- `input_name` is legacy and should not be used unless compatibility fallback is absolutely required.
- When using `ImageGenerationAgent`, you may pass optional `provider`, `aspect_ratio`, and `resolution`.
- When using `ImageEditingAgent`, you may pass optional `provider`.
- Default image provider is `nano_banana` unless the user or task clearly requires `seedream`.
- When the user refers to a previously generated image or file without re-uploading it, inspect the workspace file history and use the most recent relevant workspace path.
- Prefer files already listed in the current session file history. Do not inspect or reuse files from unrelated session directories unless the user explicitly asks for cross-session access.
- Keep the language of any user-facing summary or reply aligned with the user's language.
- If the user primarily writes in Chinese, reply in Chinese. If the user primarily writes in English, reply in English.
- If the user mixes languages, follow the primary language of the user's latest message.
- At the end of the turn, output exactly one JSON object and nothing else.
- The JSON schema must be:
  {{
    "next_agent": "AgentName or FINISH",
    "parameters": {{}},
    "summary": "One short sentence describing the chosen next step"
  }}
- If the task is complete, set `"next_agent"` to `"FINISH"` and keep `"parameters"` empty.
- If the task is not complete, choose exactly one expert agent for the next step.
- Do not output markdown fences or any explanatory text outside the JSON object.

Available skills:
{skills_summary}

Available expert agents:
{available_experts}
"""

    @staticmethod
    def _append_step_event(
        state: dict[str, Any],
        *,
        title: str,
        detail: str,
        stage: str = "orchestrating",
        session_id: str = "",
    ) -> None:
        """Append one structured orchestrator step event into session state."""
        normalized_title = title.strip() or "In Progress"
        normalized_detail = detail.strip() or "Processing the current step."
        normalized_stage = stage.strip() or "orchestrating"
        events = list(state.get("orchestration_events", []))
        events.append(
            {
                "title": normalized_title,
                "detail": normalized_detail,
                "stage": normalized_stage,
            }
        )
        state["orchestration_events"] = events
        resolved_session_id = session_id.strip() or str(state.get("sid", "")).strip()
        if resolved_session_id:
            publish_orchestration_step_event(
                session_id=resolved_session_id,
                title=normalized_title,
                detail=normalized_detail,
                stage=normalized_stage,
            )

    @staticmethod
    def _stringify_value(value: Any, max_chars: int = 180) -> str:
        """Render one tool argument or result into a compact display string."""
        return stringify_value(value, max_chars=max_chars)

    @classmethod
    def _format_tool_args(cls, args: dict[str, Any]) -> str:
        """Format tool arguments for progress display."""
        return format_tool_args(args)

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, result: Any) -> tuple[str, str]:
        """Summarize one tool result into status plus short preview."""
        return summarize_tool_result(tool_name, result)

    def _record_tool_started(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        stage: str,
    ) -> None:
        """Record one tool-call start event."""
        self._append_step_event(
            state,
            title=tool_name,
            detail=f"Status: started\nArgs: {self._format_tool_args(args)}",
            stage=stage,
        )

    @staticmethod
    def _resolve_tool_context_session_id(tool_context: ToolContext | None) -> str:
        """Safely extract one session id from a tool context-like object."""
        session = getattr(tool_context, "session", None)
        return str(getattr(session, "id", "") or "").strip()

    def _record_tool_finished(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        stage: str,
    ) -> None:
        """Record one tool-call completion event."""
        status, summary = self._summarize_tool_result(tool_name, result)
        self._append_step_event(
            state,
            title=tool_name,
            detail=(
                f"Status: {'success' if status == 'success' else 'error'}\n"
                f"Args: {self._format_tool_args(args)}\n"
                f"Result: {summary}"
            ),
            stage=stage,
        )

    @staticmethod
    def _record_workspace_files(
        state: dict[str, Any],
        *,
        paths: list[str],
        description: str,
        source: str,
    ) -> None:
        """Persist tool-produced workspace files into session state."""
        if not paths:
            return
        file_records = [
            build_workspace_file_record(path, description=description, source=source)
            for path in paths
        ]
        history = list(state.get("files_history", []))
        history.append(file_records)
        state["new_files"] = file_records
        state["files_history"] = history

    def _maybe_record_tool_files(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        """Persist workspace file outputs produced by builtin tools."""
        if isinstance(result, str) and result.startswith("Error"):
            return
        if tool_name in {"image_crop", "image_rotate", "image_flip"} and isinstance(result, str):
            self._record_workspace_files(
                state,
                paths=[result],
                description=f"Workspace image generated by builtin tool `{tool_name}`.",
                source="builtin_tool",
            )
        elif tool_name in {"write_file", "edit_file"}:
            path = str(args.get("path", "")).strip()
            if path:
                self._record_workspace_files(
                    state,
                    paths=[path],
                    description=f"Workspace file updated by builtin tool `{tool_name}`.",
                    source="builtin_tool",
                )

    def _run_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one tool and record its start and finish events when context exists."""
        if tool_context is None:
            return runner()
        if not step_event_streaming_active():
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = runner()
        self._maybe_record_tool_files(tool_context.state, tool_name=tool_name, args=args, result=result)
        if not step_event_streaming_active():
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        return result

    @staticmethod
    def build_runner_message(instruction: str) -> Content:
        """Create an ADK-compatible user message for one orchestrator turn."""
        return Content(role="user", parts=[Part(text=instruction)])

    @staticmethod
    def _parse_json_response(response_text: str) -> dict[str, Any]:
        """Parse one JSON object from the model response."""
        stripped = str(response_text or "").strip()
        if not stripped:
            raise ValueError("Orchestrator returned an empty response.")
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        start_index = stripped.find("{")
        if start_index < 0:
            raise ValueError(f"Orchestrator did not return JSON: {response_text}")
        decoder = json.JSONDecoder()
        plan, _ = decoder.raw_decode(stripped[start_index:])
        if not isinstance(plan, dict):
            raise ValueError(f"Orchestrator returned a non-object plan: {response_text}")
        return plan

    def _normalize_step_plan(self, raw_plan: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize one single-step plan."""
        raw_next_agent = raw_plan.get("next_agent")
        next_agent = str(raw_next_agent or "").strip()
        if next_agent.upper() in {"", "NONE", "NULL", "FINISH"}:
            next_agent = "FINISH"
        elif next_agent not in self.expert_runners:
            raise ValueError(f"Orchestrator selected an unknown expert: {next_agent}")

        parameters = raw_plan.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError("Plan field `parameters` must be a JSON object.")

        summary = str(raw_plan.get("summary") or "").strip()
        if not summary:
            if next_agent == "FINISH":
                summary = "The current task is complete."
            else:
                summary = f"Call `{next_agent}` for the next step."

        return {
            "next_agent": next_agent,
            "parameters": parameters,
            "summary": summary,
        }

    async def _persist_step_plan(
        self,
        *,
        normalized_plan: dict[str, Any],
        final_response: str,
        previous_event_count: int,
    ) -> dict[str, Any]:
        """Persist one normalized plan into session state and return planner output."""
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        if session is None:
            raise ValueError(f"Session {self.sid} not found for user {self.uid}")

        state = session.state
        next_agent = normalized_plan["next_agent"]
        summary = normalized_plan["summary"]
        parameters = normalized_plan["parameters"]

        if next_agent == "FINISH":
            self._append_step_event(
                state,
                title="Finalize Result",
                detail="Preparing the final reply.",
                stage="finalizing",
                session_id=self.sid,
            )
            state_delta = {
                "current_plan": normalized_plan,
                "workflow_status": "finished",
                "final_summary": summary,
                "last_output_message": summary,
                "last_orchestrator_response": final_response,
                "current_parameters": {},
                "orchestration_events": list(state.get("orchestration_events", [])),
            }
        else:
            self._append_step_event(
                state,
                title="Call Expert Agent",
                detail=f"Next step will call `{next_agent}`. Goal: {summary}",
                stage="expert_execution",
                session_id=self.sid,
            )
            state_delta = {
                "current_plan": normalized_plan,
                "workflow_status": "running",
                "last_output_message": "",
                "last_orchestrator_response": final_response,
                "current_parameters": parameters,
                "orchestration_events": list(state.get("orchestration_events", [])),
            }

        await self.session_service.append_event(
            session,
            Event(author="api_server", actions=EventActions(state_delta=state_delta)),
        )

        orchestration_events = list(state_delta["orchestration_events"])
        return {
            "workflow_status": state_delta["workflow_status"],
            "final_summary": state_delta.get("final_summary", ""),
            "last_response": final_response,
            "last_output_message": state_delta["last_output_message"],
            "new_orchestration_events": orchestration_events[previous_event_count:],
            "current_plan": normalized_plan,
        }

    def list_skills(self, tool_context: ToolContext | None = None) -> str:
        """List available skills in JSON format."""
        if tool_context is not None:
            self._append_step_event(
                tool_context.state,
                title="List Skills",
                detail="Checking the currently available skills.",
                stage="planning",
                session_id=self._resolve_tool_context_session_id(tool_context),
            )
        payload = [
            {
                "name": info.name,
                "description": info.description,
                "source": info.source,
            }
            for info in self.skill_registry.list_skills()
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def read_skill(self, name: str, tool_context: ToolContext | None = None) -> str:
        """Read the full markdown content of one skill."""
        if tool_context is not None:
            self._append_step_event(
                tool_context.state,
                title="Read Skill",
                detail=f"Reading the documentation for skill `{name}`.",
                stage="planning",
                session_id=self._resolve_tool_context_session_id(tool_context),
            )
        return self.skill_registry.read_skill(name)

    def list_dir(self, path: str = ".", tool_context: ToolContext | None = None) -> str:
        """List one directory and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_dir",
            stage="inspection",
            args={"path": path},
            runner=lambda: self.toolbox.list_dir(path),
        )

    def read_file(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one file and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="read_file",
            stage="inspection",
            args={"path": path},
            runner=lambda: self.toolbox.read_file(path),
        )

    def write_file(self, path: str, content: str, tool_context: ToolContext | None = None) -> str:
        """Write one file and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="write_file",
            stage="editing",
            args={"path": path, "content": content},
            runner=lambda: self.toolbox.write_file(path, content),
        )

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Edit one file and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="edit_file",
            stage="editing",
            args={"path": path, "old_text": old_text, "new_text": new_text},
            runner=lambda: self.toolbox.edit_file(path, old_text, new_text),
        )

    def image_crop(
        self,
        path: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Crop one image and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="image_crop",
            stage="image_processing",
            args={"path": path, "left": left, "top": top, "right": right, "bottom": bottom},
            runner=lambda: self.toolbox.image_crop(path, left, top, right, bottom),
        )

    def image_rotate(
        self,
        path: str,
        degrees: float,
        expand: bool = True,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Rotate one image and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="image_rotate",
            stage="image_processing",
            args={"path": path, "degrees": degrees, "expand": expand},
            runner=lambda: self.toolbox.image_rotate(path, degrees, expand),
        )

    def image_flip(self, path: str, direction: str, tool_context: ToolContext | None = None) -> str:
        """Flip one image and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="image_flip",
            stage="image_processing",
            args={"path": path, "direction": direction},
            runner=lambda: self.toolbox.image_flip(path, direction),
        )

    def exec_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int = 60,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Execute one command and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="exec_command",
            stage="execution",
            args={"command": command, "working_dir": working_dir, "timeout": timeout},
            runner=lambda: self.toolbox.exec_command(command, working_dir, timeout),
        )

    def web_search(self, query: str, count: int = 5, tool_context: ToolContext | None = None) -> str:
        """Search the web and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="web_search",
            stage="research",
            args={"query": query, "count": count},
            runner=lambda: self.toolbox.web_search(query, count),
        )

    def web_fetch(self, url: str, max_chars: int = 50000, tool_context: ToolContext | None = None) -> str:
        """Fetch one webpage and record the step."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="web_fetch",
            stage="research",
            args={"url": url, "max_chars": max_chars},
            runner=lambda: self.toolbox.web_fetch(url, max_chars),
        )

    async def run_agent_and_log_events(
        self,
        user_id: str,
        session_id: str,
        new_message: Optional[Content] = None,
    ) -> str:
        """Run one orchestrator turn and collect the final text response."""
        final_response_text = ""
        async for event in self.runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
        ):
            logger.debug(
                "uid: {}, sid: {}, Event: {}",
                user_id,
                session_id,
                event.model_dump_json(indent=2, exclude_none=True),
            )
            if event.is_final_response() and event.content and event.content.parts:
                text_part = next((part.text for part in event.content.parts if part.text), None)
                if text_part:
                    final_response_text = text_part
        return final_response_text

    async def generate_step_plan(self) -> dict:
        """Run one planner turn and persist one normalized single-step plan."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        current_session.state["last_output_message"] = ""
        current_session.state["last_orchestrator_response"] = ""
        previous_event_count = len(current_session.state.get("orchestration_events", []))

        final_response = await self.run_agent_and_log_events(
            user_id=self.uid,
            session_id=self.sid,
            new_message=self.build_runner_message(
                "Review the current state, use built-in tools if needed, and then output the next single-step plan as JSON."
            ),
        )
        raw_plan = self._parse_json_response(final_response)
        normalized_plan = self._normalize_step_plan(raw_plan)
        return await self._persist_step_plan(
            normalized_plan=normalized_plan,
            final_response=final_response,
            previous_event_count=previous_event_count,
        )
