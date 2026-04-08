"""Single-orchestrator runtime for Creative Claw."""

from __future__ import annotations

import json
import os.path as osp
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.agent import experts_list
from conf.system import SYS_CONFIG
from src.logger import logger
from src.runtime.step_events import CreativeClawStepEventPlugin, step_event_streaming_active
from src.runtime.tool_display import format_tool_args, stringify_value, summarize_tool_result
from src.skills import get_skill_registry
from src.tools.builtin_tools import (
    BuiltinToolbox,
)


async def orchestrator_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject compact runtime state and recent artifacts into the model request."""
    state = callback_context.state
    step = state.get("step", 0)
    workflow_status = state.get("workflow_status", "running")

    summary_lines = [
        f"# Workflow status: {workflow_status}",
        f"# Executed steps: {step}",
        f"# User task:\n{state.get('user_prompt', '')}",
    ]

    input_artifacts = state.get("input_artifacts", [])
    if input_artifacts:
        summary_lines.append("# Original input artifacts:")
        summary_lines.extend(
            f"- {artifact['name']}: {artifact.get('description', '')}" for artifact in input_artifacts
        )

    summary_history = state.get("summary_history", [])
    message_history = state.get("message_history", [])
    if summary_history and message_history:
        summary_lines.append("# Execution history:")
        for index, (summary, message) in enumerate(zip(summary_history, message_history), start=1):
            summary_lines.append(f"- Step {index}: target={summary}; result={message}")

    llm_request.contents.append(
        Content(role="user", parts=[Part(text="\n".join(summary_lines))])
    )

    new_artifacts = state.get("new_artifacts", [])
    if not new_artifacts:
        return

    artifact_parts = [Part(text="Recent artifacts for reference:\n")]
    for index, artifact in enumerate(new_artifacts, start=1):
        artifact_parts.append(
            Part(
                text=(
                    f"Artifact {index}: {artifact['name']}. "
                    f"Description: {artifact.get('description', '')}\n"
                )
            )
        )
        artifact_part = await callback_context.load_artifact(filename=artifact["name"])
        artifact_parts.append(artifact_part)

    llm_request.contents.append(Content(role="user", parts=artifact_parts))


class Orchestrator:
    """Drive one-step-at-a-time execution with skills and tools."""

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
        self.toolbox = BuiltinToolbox(SYS_CONFIG.base_dir)

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
                self.run_expert,
                self.finish_task,
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
        """Build one compact system instruction for the orchestrator."""
        available_experts = "\n".join(
            str(expert) for expert in experts_list if expert.enable
        )
        skills_summary = self.skill_registry.build_summary()

        return f"""
You are Creative Claw's single orchestrator.

Your job is to solve the user's task by executing the next best step directly.
Do not create a full upfront plan unless the user explicitly asks for one.

You can use three kinds of capabilities:
1. Skills from local markdown files
2. Built-in local file tools
3. Built-in shell and web tools
4. Existing expert agents through `run_expert`

Rules:
- When a skill seems relevant, call `list_skills` first and then `read_skill`.
- Never invent skill content. Read the actual `SKILL.md` before using it deeply.
- Prefer direct execution over abstract planning.
- Use built-in tools for local project work: `list_dir`, `read_file`, `write_file`, `edit_file`, `image_crop`, `image_rotate`, `image_flip`, `exec`, `web_search`, `web_fetch`.
- Inspect local files with `list_dir` and `read_file` before changing them when the path or contents are uncertain.
- Use local image tools for lightweight preprocessing, and keep the returned suffixed output path instead of overwriting the original by default.
- Keep changes small and reviewable, and re-check the latest state after each meaningful action.
- Use `run_expert` for image generation, image editing, image understanding, reverse prompt extraction, search, and prompt refinement.
- When using `ImageGenerationAgent`, you may pass optional `provider`, `aspect_ratio`, and `resolution`.
- When using `ImageEditingAgent`, you may pass optional `provider`.
- Default image provider is `nano_banana` unless the user or task clearly requires `seedream`.
- Call `finish_task` when the task is complete.
- If you are not done yet, execute only one meaningful step in this turn.

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
    ) -> None:
        """Append one structured orchestrator step event into session state."""
        events = list(state.get("orchestration_events", []))
        events.append(
            {
                "title": title.strip() or "处理中",
                "detail": detail.strip() or "正在处理当前步骤。",
                "stage": stage.strip() or "orchestrating",
            }
        )
        state["orchestration_events"] = events

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
            detail=f"状态：开始\n参数：{self._format_tool_args(args)}",
            stage=stage,
        )

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
                f"状态：{'成功' if status == 'success' else '异常'}\n"
                f"参数：{self._format_tool_args(args)}\n"
                f"结果：{summary}"
            ),
            stage=stage,
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
        if tool_context is None or step_event_streaming_active():
            return runner()
        self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = runner()
        self._record_tool_finished(tool_context.state, tool_name=tool_name, args=args, result=result, stage=stage)
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
                title="查看技能列表",
                detail="正在检查当前可用的技能。",
                stage="planning",
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
                title="读取技能说明",
                detail=f"正在读取技能 `{name}` 的说明。",
                stage="planning",
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

    async def run_step(self) -> dict:
        """Run one orchestrator turn and return current workflow state."""
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
                "Review the current state, perform the next best action, and finish if the task is already complete."
            ),
        )

        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        state = session.state
        state["last_orchestrator_response"] = final_response
        orchestration_events = list(state.get("orchestration_events", []))
        return {
            "workflow_status": state.get("workflow_status", "running"),
            "final_summary": state.get("final_summary", ""),
            "last_response": final_response,
            "last_output_message": state.get("last_output_message", ""),
            "new_orchestration_events": orchestration_events[previous_event_count:],
        }

    async def run_expert(
        self,
        agent_name: str,
        parameters: dict,
        tool_context: ToolContext,
    ) -> dict:
        """Run one existing expert agent and persist its result into session state."""
        if agent_name not in self.expert_runners:
            return {"status": "error", "message": f"Unknown expert agent: {agent_name}"}

        self._append_step_event(
            tool_context.state,
            title="调用专家代理",
            detail=f"正在调用 `{agent_name}` 处理当前步骤。",
            stage="expert_execution",
        )
        tool_context.state["current_parameters"] = parameters
        tool_context.state["current_agent_name"] = agent_name
        tool_context.state["workflow_status"] = "running"

        expert_runner = self.expert_runners[agent_name]
        final_response_text = ""
        async for event in expert_runner.run_async(
            user_id=tool_context.user_id,
            session_id=tool_context.session.id,
            new_message=self.build_runner_message(
                f"Execute the current step with agent {agent_name} using parameters stored in session state."
            ),
        ):
            logger.debug(
                "[{}] Event: {}",
                agent_name,
                event.model_dump_json(indent=2, exclude_none=True),
            )
            if event.is_final_response() and event.content and event.content.parts:
                text_part = next((part.text for part in event.content.parts if part.text), None)
                if text_part:
                    final_response_text = text_part

        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=tool_context.user_id,
            session_id=tool_context.session.id,
        )
        current_output = current_session.state.get("current_output") or {
            "status": "error",
            "message": f"{agent_name} produced no current_output.",
        }

        step = current_session.state.get("step", 0)
        summary_history = current_session.state.get("summary_history", [])
        message_history = current_session.state.get("message_history", [])
        text_history = current_session.state.get("text_history", [])
        artifacts_history = current_session.state.get("artifacts_history", [])
        step_summary = f"{agent_name} with parameters {json.dumps(parameters, ensure_ascii=False)}"

        tool_context.state["step"] = step + 1
        tool_context.state["summary_history"] = summary_history + [step_summary]
        tool_context.state["message_history"] = message_history + [current_output.get("message", "")]
        tool_context.state["last_output_message"] = current_output.get("message", "")
        tool_context.state["last_expert_response"] = final_response_text

        output_text = current_output.get("output_text")
        tool_context.state["text_history"] = text_history + [output_text]

        output_artifacts = current_output.get("output_artifacts", [])
        if output_artifacts:
            for artifact in output_artifacts:
                artifact["path"] = await self._save_artifact(
                    art_name=artifact["name"],
                    user_id=tool_context.user_id,
                    session_id=tool_context.session.id,
                )
            tool_context.state["new_artifacts"] = output_artifacts
            tool_context.state["artifacts_history"] = artifacts_history + [output_artifacts]
        else:
            tool_context.state["new_artifacts"] = []
            tool_context.state["artifacts_history"] = artifacts_history + [[]]

        return {
            "status": current_output.get("status", "error"),
            "message": current_output.get("message", ""),
            "output_text": output_text,
            "output_artifacts": output_artifacts,
        }

    async def finish_task(self, summary: str, tool_context: ToolContext) -> dict:
        """Mark the workflow as completed with one final summary."""
        self._append_step_event(
            tool_context.state,
            title="整理最终结果",
            detail="正在整理最终回复内容。",
            stage="finalizing",
        )
        tool_context.state["workflow_status"] = "finished"
        tool_context.state["final_summary"] = summary
        tool_context.state["last_output_message"] = summary
        return {"status": "success", "message": f"Task marked as finished: {summary}"}

    async def _save_artifact(self, art_name: str, user_id: str, session_id: str) -> str:
        """Persist one session artifact into the configured output directory."""
        artifact_part = await self.artifact_service.load_artifact(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
            filename=art_name,
        )
        output_name = f"{user_id}_{session_id}_{art_name}"
        output_path = osp.join(self.save_dir, output_name)
        with open(output_path, "wb") as file_obj:
            file_obj.write(artifact_part.inline_data.data)
        return output_path
