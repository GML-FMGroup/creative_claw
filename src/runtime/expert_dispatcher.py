"""Helpers for orchestrator-driven expert invocation."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from google.adk.apps import App
from google.adk.artifacts import BaseArtifactService, InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content, Part

from src.runtime.expert_registry import build_fallback_parameters
from src.runtime.tool_context_artifact_service import ToolContextArtifactService
from src.runtime.workspace import (
    build_workspace_file_record,
    normalize_file_references,
    resolve_workspace_path,
)

_NON_FORWARDABLE_STATE_KEYS = {
    "app_name",
    "uid",
    "sid",
    "user_prompt",
    "step",
    "input_files",
    "current_parameters",
    "current_output",
    "files_history",
    "new_files",
    "text_history",
    "message_history",
    "summary_history",
    "last_output_message",
    "last_expert_result",
    "expert_history",
    "workflow_status",
    "final_summary",
    "final_response",
    "last_orchestrator_response",
    "orchestration_events",
}


@dataclass(slots=True)
class ExpertInvocationResult:
    """Normalized result returned from one expert invocation."""

    agent_name: str
    normalized_parameters: dict[str, Any]
    current_output: dict[str, Any]
    state_delta: dict[str, Any]
    tool_result: dict[str, Any]


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence when present."""
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_prompt_as_parameters(agent_name: str, prompt: str) -> dict[str, Any]:
    """Parse one `invoke_agent` prompt into expert parameters."""
    stripped = _strip_code_fence(prompt)
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            nested = payload.get("parameters")
            if isinstance(nested, dict):
                return dict(nested)
            return dict(payload)

    return build_fallback_parameters(agent_name, stripped)


def _resolve_named_files(state: dict[str, Any], names: list[str]) -> list[str]:
    """Resolve legacy file names against recorded workspace file history."""
    available_files = list(state.get("input_files", []))
    for file_group in state.get("files_history", []):
        available_files.extend(file_group)

    resolved_paths: list[str] = []
    for raw_name in names:
        target_name = str(raw_name or "").strip()
        if not target_name:
            continue
        matched = next(
            (
                str(file_info.get("path", "")).strip()
                for file_info in reversed(available_files)
                if str(file_info.get("name", "")).strip() == target_name
            ),
            "",
        )
        if not matched:
            raise ValueError(f"invoke_agent got an unknown workspace file name: '{target_name}'.")
        resolved_paths.append(matched)
    return resolved_paths


def _all_workspace_paths_exist(paths: list[str]) -> bool:
    """Return whether every normalized workspace path currently exists."""
    try:
        return all(resolve_workspace_path(path).exists() for path in paths)
    except Exception:
        return False


def normalize_invoke_agent_parameters(
    *,
    agent_name: str,
    prompt: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Normalize an `invoke_agent` prompt into expert parameters."""
    normalized = _parse_prompt_as_parameters(agent_name, prompt)
    if "input_path" in normalized or "input_paths" in normalized:
        input_paths = normalize_file_references(
            normalized.get("input_paths", normalized.get("input_path"))
        )
    elif "input_name" in normalized:
        raw_value = normalized.get("input_name")
        if isinstance(raw_value, str):
            input_names = [raw_value]
        elif isinstance(raw_value, list):
            input_names = [str(item) for item in raw_value]
        else:
            raise ValueError("invoke_agent parameters['input_name'] must be a string or a list")
        direct_paths = normalize_file_references(input_names)
        if direct_paths and _all_workspace_paths_exist(direct_paths):
            input_paths = direct_paths
        else:
            input_paths = _resolve_named_files(state, input_names)
        normalized.pop("input_name", None)
    else:
        input_paths = []

    if input_paths:
        for path in input_paths:
            resolved = resolve_workspace_path(path)
            if not resolved.exists():
                raise ValueError(f"invoke_agent got a missing workspace file: '{path}'.")
        normalized["input_paths"] = input_paths
        if len(input_paths) == 1:
            normalized["input_path"] = input_paths[0]

    return normalized


def _normalize_output_files(output_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize expert output file records before saving them into parent state."""
    normalized_files: list[dict[str, str]] = []
    for file_info in output_files:
        path = str(file_info.get("path", "")).strip()
        if not path:
            continue
        normalized_files.append(
            build_workspace_file_record(
                path,
                description=str(file_info.get("description", "")).strip(),
                source=str(file_info.get("source", "expert")).strip() or "expert",
                name=str(file_info.get("name", "")).strip() or None,
            )
        )
    return normalized_files


def _filter_parent_state_for_child_session(parent_state: dict[str, Any]) -> dict[str, Any]:
    """Build the child expert state from parent state without ADK internals."""
    return {
        key: copy.deepcopy(value)
        for key, value in parent_state.items()
        if not str(key).startswith("_adk")
    }


def _extract_forwardable_state_delta(state_delta: dict[str, Any]) -> dict[str, Any]:
    """Keep only child state keys that are safe to merge back into the parent."""
    return {
        key: value
        for key, value in state_delta.items()
        if not str(key).startswith("_adk") and key not in _NON_FORWARDABLE_STATE_KEYS
    }


def _resolve_child_artifact_service(
    *,
    tool_context: ToolContext,
    fallback_service: BaseArtifactService,
) -> BaseArtifactService:
    """Pick the artifact service used by the child expert runner."""
    required_methods = ("save_artifact", "load_artifact", "list_artifacts")
    if all(hasattr(tool_context, method_name) for method_name in required_methods):
        return ToolContextArtifactService(tool_context)
    return fallback_service


def _build_child_runner(
    *,
    agent,
    app_name: str,
    session_service: InMemorySessionService,
    artifact_service: BaseArtifactService,
    invocation_context,
) -> Runner:
    """Create one child expert runner using ADK's preferred App-based path."""
    child_plugins = getattr(getattr(invocation_context, "plugin_manager", None), "plugins", None)
    runner_kwargs = {
        "app_name": app_name,
        "session_service": session_service,
        "artifact_service": artifact_service,
        "memory_service": InMemoryMemoryService(),
        "credential_service": getattr(invocation_context, "credential_service", None),
    }
    if child_plugins:
        runner_kwargs["app"] = App(
            name=app_name,
            root_agent=agent,
            plugins=list(child_plugins),
        )
    else:
        runner_kwargs["agent"] = agent
    return Runner(**runner_kwargs)


def _build_tool_result(
    *,
    agent_name: str,
    current_output: dict[str, Any],
    normalized_parameters: dict[str, Any],
    normalized_files: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the tool result returned to the orchestrator model."""
    return {
        "agent_name": agent_name,
        "status": str(current_output.get("status", "error")).strip() or "error",
        "message": str(current_output.get("message", "")).strip(),
        "output_text": current_output.get("output_text", ""),
        "output_files": normalized_files,
        "structured_data": {
            key: value
            for key, value in current_output.items()
            if key not in {"status", "message", "output_text", "output_files"}
        },
        "parameters": normalized_parameters,
    }


def _build_state_delta(
    *,
    parent_state: dict[str, Any],
    forwarded_state_delta: dict[str, Any],
    agent_name: str,
    normalized_parameters: dict[str, Any],
    current_output: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge one child expert result back into the parent session state."""
    current_step = int(parent_state.get("step", 0) or 0)
    normalized_files = _normalize_output_files(current_output.get("output_files", []))
    message = str(current_output.get("message", "")).strip()
    if not message:
        message = f"{agent_name} finished without a message."

    inherited_delta = dict(forwarded_state_delta)
    summary_history = list(parent_state.get("summary_history") or [])
    text_history = list(parent_state.get("text_history") or [])
    message_history = list(parent_state.get("message_history") or [])
    files_history = list(parent_state.get("files_history") or [])
    expert_history = list(parent_state.get("expert_history") or [])

    tool_result = _build_tool_result(
        agent_name=agent_name,
        current_output=current_output,
        normalized_parameters=normalized_parameters,
        normalized_files=normalized_files,
    )

    state_delta = dict(inherited_delta)
    state_delta.update(
        {
            "step": current_step + 1,
            "current_parameters": normalized_parameters,
            "current_output": current_output,
            "last_output_message": message,
            "last_expert_result": tool_result,
            "expert_history": expert_history + [tool_result],
            "summary_history": summary_history + [f"{agent_name}: {message}"],
            "message_history": message_history + [message],
        }
    )

    output_text = current_output.get("output_text")
    state_delta["text_history"] = text_history + [output_text if output_text else None]

    if normalized_files:
        state_delta["new_files"] = normalized_files
        state_delta["files_history"] = files_history + [normalized_files]
    else:
        state_delta["new_files"] = []
        state_delta["files_history"] = files_history + [[]]

    return state_delta, tool_result


async def dispatch_expert_call(
    *,
    agent_name: str,
    prompt: str,
    tool_context: ToolContext,
    expert_runners: dict[str, Runner],
    app_name: str,
    artifact_service: InMemoryArtifactService,
) -> ExpertInvocationResult:
    """Run one expert in a child session and merge the result into the parent state."""
    if agent_name not in expert_runners:
        raise ValueError(f"invoke_agent got an unknown expert: '{agent_name}'.")

    parent_state = tool_context.state.to_dict()
    normalized_parameters = normalize_invoke_agent_parameters(
        agent_name=agent_name,
        prompt=prompt,
        state=parent_state,
    )

    child_state = _filter_parent_state_for_child_session(parent_state)
    child_state["current_parameters"] = normalized_parameters

    invocation_context = tool_context._invocation_context
    child_session_service = InMemorySessionService()
    child_artifact_service = _resolve_child_artifact_service(
        tool_context=tool_context,
        fallback_service=artifact_service,
    )
    child_runner = _build_child_runner(
        agent=expert_runners[agent_name].agent,
        app_name=app_name,
        session_service=child_session_service,
        artifact_service=child_artifact_service,
        invocation_context=invocation_context,
    )

    child_session = await child_session_service.create_session(
        app_name=app_name,
        user_id=invocation_context.user_id,
        state=child_state,
    )

    current_output: dict[str, Any]
    forwarded_state_delta: dict[str, Any] = {}
    try:
        async for event in child_runner.run_async(
            user_id=child_session.user_id,
            session_id=child_session.id,
            new_message=Content(
                role="user",
                parts=[
                    Part(
                        text=(
                            f"Execute delegated expert task for {agent_name}. "
                            "Use the parameters stored in session current_parameters."
                        )
                    )
                ],
            ),
        ):
            if event.actions and event.actions.state_delta:
                forwarded_state_delta.update(
                    _extract_forwardable_state_delta(event.actions.state_delta)
                )
        final_child_session = await child_session_service.get_session(
            app_name=app_name,
            user_id=child_session.user_id,
            session_id=child_session.id,
        )
        child_state = final_child_session.state if final_child_session is not None else child_state
        current_output = child_state.get("current_output") or {
            "status": "error",
            "message": f"{agent_name} did not produce current_output.",
        }
    except Exception as exc:
        current_output = {
            "status": "error",
            "message": f"{agent_name} execution failed: {type(exc).__name__}: {exc}",
        }
    finally:
        await child_runner.close()

    state_delta, tool_result = _build_state_delta(
        parent_state=parent_state,
        forwarded_state_delta=forwarded_state_delta,
        agent_name=agent_name,
        normalized_parameters=normalized_parameters,
        current_output=current_output,
    )
    tool_context.state.update(state_delta)

    return ExpertInvocationResult(
        agent_name=agent_name,
        normalized_parameters=normalized_parameters,
        current_output=current_output,
        state_delta=state_delta,
        tool_result=tool_result,
    )
