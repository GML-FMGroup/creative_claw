"""Planning-oriented orchestrator runtime for Creative Claw."""

from __future__ import annotations

import json
from typing import Any, Optional

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
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
from src.runtime.expert_dispatcher import dispatch_expert_call
from src.runtime.expert_registry import build_expert_contract_summary
from src.runtime.tool_display import format_tool_args, stringify_value, summarize_tool_result
from src.runtime.workspace import (
    build_workspace_file_record,
    load_local_file_part,
    looks_like_image,
    resolve_workspace_path,
    workspace_relative_path,
)
from src.skills import get_skill_registry
from src.tools.builtin_tools import (
    BuiltinToolbox,
)

_PLUGIN_MANAGED_TOOL_NAMES = {
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "write_file",
    "edit_file",
    "image_crop",
    "image_rotate",
    "image_flip",
    "exec_command",
    "process_session",
    "web_search",
    "web_fetch",
}


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

    final_file_paths = state.get("final_file_paths")
    if isinstance(final_file_paths, list):
        normalized_final_paths = [
            str(path).strip()
            for path in final_file_paths
            if isinstance(path, str) and str(path).strip()
        ]
        if normalized_final_paths:
            summary_lines.append("# Explicitly selected final reply files:")
            summary_lines.extend(f"- {path}" for path in normalized_final_paths)
        else:
            summary_lines.append("# Final reply attachments have been explicitly cleared for this response.")

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


def _select_latest_non_channel_files(files_history: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return the latest non-channel file batch recorded in session state."""
    for file_group in reversed(files_history):
        if file_group and any(str(file_info.get("source", "")).strip() != "channel" for file_info in file_group):
            return file_group
    return []


class Orchestrator:
    """Plan one workflow step at a time with skills and builtin tools."""

    def __init__(
        self,
        session_service: InMemorySessionService,
        artifact_service: InMemoryArtifactService,
        expert_agents: dict[str, BaseAgent],
        app_name: str = SYS_CONFIG.app_name,
        save_dir: str = "",
        llm_model: str = "",
    ) -> None:
        self.app_name = app_name
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.expert_agents = expert_agents
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
                self.glob,
                self.grep,
                self.read_file,
                self.write_file,
                self.edit_file,
                self.image_crop,
                self.image_rotate,
                self.image_flip,
                self.exec_command,
                self.process_session,
                self.web_search,
                self.web_fetch,
                self.list_session_files,
                self.set_final_files,
                self.invoke_agent,
            ],
        )
        self.app = App(
            name=self.app_name,
            root_agent=self.agent,
            plugins=[CreativeClawStepEventPlugin()],
        )
        self.runner = Runner(
            app=self.app,
            app_name=self.app_name,
            session_service=self.session_service,
            artifact_service=self.artifact_service,
        )

    def _build_instruction(self) -> str:
        """Build the planner instruction for the orchestrator."""
        available_experts = "\n".join(
            str(expert) for expert in experts_list if expert.enable
        )
        skills_summary = self.skill_registry.build_summary()
        expert_contracts = build_expert_contract_summary()

        return f"""
You are Creative Claw's primary user-facing orchestrator.

Your job is to inspect the current state, use skills and tools when helpful, and directly complete the user's request in this invocation whenever possible.
Do not create a full upfront plan unless the user explicitly asks for one.
You can use built-in tools, skills, `invoke_agent`, and your own reasoning to complete the task.
You are the main agent, and expert agents are supporting capabilities invoked through `invoke_agent`.
Prefer completing the task directly instead of describing an internal workflow.

You can use four kinds of capabilities:
1. Skills from local markdown files
2. Built-in local file tools inside the fixed workspace
3. Built-in shell and web tools
4. Existing expert agents through `invoke_agent(agent_name, prompt)`

Rules:
- Treat yourself as the main conversational agent. Reply to the user's actual request, not to an internal workflow.
- When a skill seems relevant, call `list_skills` first and then `read_skill`.
- Never invent skill content. Read the actual `SKILL.md` before using it deeply.
- Prefer direct execution over abstract planning.
- Use built-in tools for local workspace work: `list_dir`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`, `image_crop`, `image_rotate`, `image_flip`, `exec_command`, `process_session`, `web_search`, `web_fetch`.
- Use `list_session_files(section=...)` when you need the exact normalized workspace paths already tracked in the current session state, especially before calling `set_final_files`.
- Use `set_final_files(paths=[...])` when you need to explicitly choose which workspace files should be attached to the final reply.
- All file paths must be relative to the fixed `workspace` directory unless the tool explicitly returns a workspace-relative path.
- Inspect local files with `list_dir`, `glob`, `grep`, and `read_file` before changing them when the path or contents are uncertain.
- Use local image tools for lightweight preprocessing, and keep the returned suffixed output path instead of overwriting the original by default.
- Keep changes small and reviewable, and re-check the latest state after each meaningful action.
- For coding, debugging, and file-editing tasks, prefer solving the task directly with built-in tools before delegating to an expert.
- For coding tasks, you may inspect files, write or edit code, run targeted commands with `exec_command`, inspect stdout and stderr, and iterate based on the results.
- Use `glob` to locate candidate files quickly and `grep` to find symbols, messages, and code snippets before reading full files.
- For long-running commands, start them with `exec_command(background=true, yield_ms=...)` and then use `process_session` to list, poll, inspect logs, write stdin, kill, or remove sessions.
- After writing or editing code, prefer running a small verification command with `exec_command` before finishing when verification is feasible.
- For ordinary conversation, explanations, brainstorming, lightweight analysis, and tasks that built-in tools can complete, finish directly instead of delegating.
- When planning expert parameters, pass workspace file paths with `input_path` or `input_paths` instead of artifact names.
- `input_name` is legacy and should not be used unless compatibility fallback is absolutely required.
- When using `ImageGenerationAgent`, you may pass optional `provider`, `aspect_ratio`, and `resolution`.
- When using `ImageEditingAgent`, you may pass optional `provider`.
- Default image provider is `nano_banana` unless the user or task clearly requires `seedream`.
- When the user refers to a previously generated image or file without re-uploading it, inspect the workspace file history and use the most recent relevant workspace path.
- Prefer files already listed in the current session file history. Do not inspect or reuse files from unrelated session directories unless the user explicitly asks for cross-session access.
- If the user asks you to send one or more workspace files back in the current conversation, call `set_final_files(paths=[...])` with the exact workspace-relative paths and then answer normally.
- Selecting final files does not require GUI control. The runtime will attach the selected workspace files to the final channel reply when the channel supports attachments.
- Only choose an expert agent when the task needs specialized image, search, or other expert capability that built-in tools and direct reasoning cannot handle well.
- When calling `invoke_agent`, pass a complete expert brief.
- For experts that need several parameters, encode the `prompt` argument as a JSON object string that contains the exact expert parameters.
- Prefer workspace paths in that JSON object, such as `input_path` or `input_paths`.
- `invoke_agent` returns structured data including status, message, optional output_text, and output_files.
- Keep the language of any user-facing summary or reply aligned with the user's language.
- If the user primarily writes in Chinese, reply in Chinese. If the user primarily writes in English, reply in English.
- If the user mixes languages, follow the primary language of the user's latest message.

Response Requirements:
- Reply to the user in natural language after you finish the needed tool and expert calls.
- Do not output internal workflow JSON.
- Do not expose internal bookkeeping such as `current_output`, `workflow_status`, or private planning notes.
- If the task is unfinished because a tool or expert failed, explain the blocker directly and say what remains.

Available skills:
{skills_summary}

Available expert agents:
{available_experts}

Expert parameter contracts:
{expert_contracts}
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
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = runner()
        self._maybe_record_tool_files(tool_context.state, tool_name=tool_name, args=args, result=result)
        if should_record_manually:
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        return result

    async def _run_async_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one async tool and record its lifecycle events when context exists."""
        if tool_context is None:
            return await runner()
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = await runner()
        if should_record_manually:
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

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        entry_type: str = "files",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Find workspace paths matching one glob pattern."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="glob",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "max_results": max_results,
                "entry_type": entry_type,
            },
            runner=lambda: self.toolbox.glob(
                pattern,
                path=path,
                max_results=max_results,
                entry_type=entry_type,
            ),
        )

    def grep(
        self,
        pattern: str,
        path: str = ".",
        glob_pattern: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 100,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Search workspace file contents with regex or fixed-string matching."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="grep",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "glob_pattern": glob_pattern,
                "case_insensitive": case_insensitive,
                "fixed_strings": fixed_strings,
                "output_mode": output_mode,
                "context_before": context_before,
                "context_after": context_after,
                "max_results": max_results,
            },
            runner=lambda: self.toolbox.grep(
                pattern,
                path=path,
                glob_pattern=glob_pattern,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                output_mode=output_mode,
                context_before=context_before,
                context_after=context_after,
                max_results=max_results,
            ),
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
        background: bool = False,
        yield_ms: int = 1000,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Execute one command and record the step."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="exec_command",
            stage="execution",
            args={
                "command": command,
                "working_dir": working_dir,
                "timeout": timeout,
                "background": background,
                "yield_ms": yield_ms,
            },
            runner=lambda: self.toolbox.exec_command(
                command,
                working_dir=working_dir,
                timeout=timeout,
                background=background,
                yield_ms=yield_ms,
                scope_key=scope_key,
            ),
        )

    def process_session(
        self,
        action: str = "list",
        session_id: str | None = None,
        input_text: str = "",
        timeout_ms: int = 0,
        offset: int = 0,
        limit: int = 200,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Manage background command sessions and inspect their outputs."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="process_session",
            stage="execution",
            args={
                "action": action,
                "session_id": session_id,
                "input_text": input_text,
                "timeout_ms": timeout_ms,
                "offset": offset,
                "limit": limit,
            },
            runner=lambda: self.toolbox.process_session(
                action=action,
                session_id=session_id,
                input_text=input_text,
                timeout_ms=timeout_ms,
                offset=offset,
                limit=limit,
                scope_key=scope_key,
            ),
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

    def list_session_files(
        self,
        section: str = "all",
        tool_context: ToolContext | None = None,
    ) -> str:
        """List normalized workspace file records already known in the current session."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_session_files",
            stage="inspection",
            args={"section": section},
            runner=lambda: self._list_session_files(section, tool_context=tool_context),
        )

    @staticmethod
    def _list_session_files(section: str, *, tool_context: ToolContext | None) -> str:
        """Return session-tracked file records in JSON form for one requested section."""
        if tool_context is None:
            return "Error: tool context is required to inspect session files."

        normalized_section = str(section or "all").strip().lower() or "all"
        state = tool_context.state
        files_history = list(state.get("files_history") or [])
        latest_output_files = _select_latest_non_channel_files(files_history)
        payload_by_section = {
            "input": {"input_files": list(state.get("input_files") or [])},
            "new": {"new_files": list(state.get("new_files") or [])},
            "latest_output": {"latest_output_files": latest_output_files},
            "history": {"files_history": files_history},
            "final": {"final_file_paths": state.get("final_file_paths")},
            "all": {
                "input_files": list(state.get("input_files") or []),
                "new_files": list(state.get("new_files") or []),
                "latest_output_files": latest_output_files,
                "files_history": files_history,
                "final_file_paths": state.get("final_file_paths"),
            },
        }
        if normalized_section not in payload_by_section:
            allowed = ", ".join(payload_by_section.keys())
            return f"Error: Unsupported section `{section}`. Allowed: {allowed}"
        return json.dumps(payload_by_section[normalized_section], ensure_ascii=False, indent=2)

    def set_final_files(
        self,
        paths: list[str],
        tool_context: ToolContext | None = None,
    ) -> str:
        """Select which workspace files should be attached to the final reply."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="set_final_files",
            stage="finalizing",
            args={"paths": paths},
            runner=lambda: self._set_final_files(paths, tool_context=tool_context),
        )

    @staticmethod
    def _set_final_files(paths: Any, *, tool_context: ToolContext | None) -> str:
        """Validate and persist the explicit final file selection in session state."""
        if tool_context is None:
            return "Error: tool context is required to select final files."

        raw_paths: list[str] = []
        if isinstance(paths, str):
            normalized = paths.strip()
            if normalized:
                raw_paths.append(normalized)
        elif isinstance(paths, list):
            for item in paths:
                if isinstance(item, str):
                    normalized = item.strip()
                    if normalized:
                        raw_paths.append(normalized)
                else:
                    return "Error: Each final file path must be a string."
        else:
            return "Error: `paths` must be a string or a list of strings."

        selected_paths: list[str] = []
        seen_paths: set[str] = set()
        for raw_path in raw_paths:
            try:
                resolved = resolve_workspace_path(raw_path)
            except Exception as exc:
                return f"Error: Invalid workspace path `{raw_path}`: {exc}"
            if not resolved.exists():
                return f"Error: File not found: {raw_path}"
            if not resolved.is_file():
                return f"Error: Not a file: {raw_path}"
            relative_path = workspace_relative_path(resolved)
            if relative_path in seen_paths:
                continue
            seen_paths.add(relative_path)
            selected_paths.append(relative_path)

        tool_context.state["final_file_paths"] = selected_paths
        if not selected_paths:
            return "Cleared the final file selection. No files will be attached unless a later step selects them."
        joined_paths = ", ".join(selected_paths)
        return f"Selected {len(selected_paths)} final file(s): {joined_paths}"

    async def invoke_agent(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Invoke one expert agent through the shared dispatcher."""
        invocation = await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="invoke_agent",
            stage="expert_execution",
            args={"agent_name": agent_name, "prompt": prompt},
            runner=lambda: dispatch_expert_call(
                agent_name=agent_name,
                prompt=prompt,
                tool_context=tool_context,
                expert_agents=self.expert_agents,
                app_name=self.app_name,
                artifact_service=self.artifact_service,
            ),
        )
        return invocation.tool_result

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

    async def run_until_done(self) -> dict[str, Any]:
        """Run one orchestrator invocation and persist the final direct reply."""
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
                "Review the current state, use built-in tools or invoke_agent when helpful, and answer the user directly once the task is complete."
            ),
        )

        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        if current_session is None:
            raise ValueError(f"Session {self.sid} not found for user {self.uid}")

        state = current_session.state
        normalized_response = str(final_response or "").strip()
        if not normalized_response:
            normalized_response = str(state.get("last_output_message", "")).strip()
        if not normalized_response:
            current_output = state.get("current_output") or {}
            normalized_response = str(current_output.get("message", "")).strip()
        if not normalized_response:
            normalized_response = "The current task is complete."

        self._append_step_event(
            state,
            title="Finalize Result",
            detail="Preparing the final reply.",
            stage="finalizing",
            session_id=self.sid,
        )
        state_delta = {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "last_output_message": normalized_response,
            "last_orchestrator_response": normalized_response,
            "orchestration_events": list(state.get("orchestration_events", [])),
        }
        await self.session_service.append_event(
            current_session,
            Event(author="api_server", actions=EventActions(state_delta=state_delta)),
        )

        orchestration_events = list(state_delta["orchestration_events"])
        return {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "last_output_message": normalized_response,
            "new_orchestration_events": orchestration_events[previous_event_count:],
        }
