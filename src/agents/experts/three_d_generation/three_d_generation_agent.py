"""3D generation expert for Creative Claw."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator

from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import PrivateAttr

from src.agents.experts.base import CreativeExpert
from src.agents.experts.three_d_generation import tool as generation_tools
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record


class ThreeDGenerationAgent(CreativeExpert):
    """Generate 3D assets through provider-specific tools."""

    _public_name: str = PrivateAttr(default="3DGeneration")

    def __init__(self, name: str, description: str = "", public_name: str = "3DGeneration") -> None:
        """Initialize the 3D generation expert."""
        super().__init__(name=name, sub_agents=[], description=description)
        self._public_name = public_name

    @staticmethod
    def _normalize_prompt(raw_prompt: Any) -> str:
        """Normalize one prompt value into a single string."""
        if isinstance(raw_prompt, list):
            prompt_list = [str(item).strip() for item in raw_prompt if str(item).strip()]
            if len(prompt_list) > 1:
                raise ValueError("3DGeneration currently supports only one prompt at a time.")
            return prompt_list[0] if prompt_list else ""
        return str(raw_prompt or "").strip()

    @staticmethod
    def _normalize_input_paths(current_parameters: dict[str, Any]) -> list[str]:
        """Normalize input image paths from current parameters."""
        input_paths = current_parameters.get("input_paths", current_parameters.get("input_path", []))
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        return [str(path).strip() for path in input_paths if str(path).strip()]

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the 3D generation expert with normalized session parameters."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        provider = str(current_parameters.get("provider", "hy3d")).strip().lower() or "hy3d"

        try:
            prompt = self._normalize_prompt(current_parameters.get("prompt", ""))
            input_paths = self._normalize_input_paths(current_parameters)
        except ValueError as exc:
            error_text = f"{self._public_name} parameter normalization failed: {exc}"
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        if provider != "hy3d":
            error_text = f"{self._public_name} only supports provider `hy3d` in the current implementation."
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        if len(input_paths) > 1:
            error_text = f"{self._public_name} currently supports at most one input image."
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        generate_type = generation_tools.normalize_generate_type(
            current_parameters.get("generate_type", generation_tools.DEFAULT_GENERATE_TYPE)
        )
        if prompt and input_paths and generate_type != "Sketch":
            error_text = (
                f"{self._public_name} requires `generate_type=sketch` when both prompt and input image are provided."
            )
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        result = await generation_tools.hy3d_generate_tool(
            prompt=prompt or None,
            input_path=input_paths[0] if input_paths else None,
            model=str(current_parameters.get("model", generation_tools.DEFAULT_MODEL) or generation_tools.DEFAULT_MODEL),
            enable_pbr=generation_tools.coerce_bool(current_parameters.get("enable_pbr"), default=False),
            generate_type=generate_type,
            face_count=current_parameters.get("face_count"),
            polygon_type=(
                str(current_parameters.get("polygon_type", "")).strip() or None
            ),
            result_format=(
                str(current_parameters.get("result_format", "")).strip() or None
            ),
            timeout_seconds=int(
                current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
            ),
            interval_seconds=int(
                current_parameters.get("interval_seconds", generation_tools.DEFAULT_INTERVAL_SECONDS)
            ),
            session_id=ctx.session.id,
            turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
            step=int(ctx.session.state.get("step", 0) or 0),
        )

        if result["status"] == "error":
            current_output = {
                "status": "error",
                "message": result["message"],
                "provider": result.get("provider", provider),
                "model_name": result.get("model_name", ""),
                "job_id": result.get("job_id", ""),
            }
            logger.error("{} execution failed: {}", self._public_name, result["message"])
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        downloaded_files = result.get("downloaded_files", [])
        output_files = []
        structured_results = []
        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        prompt_description = prompt or "[image-only generation]"

        for index, file_info in enumerate(downloaded_files, start=1):
            output_path = Path(file_info["path"])
            artifact_name = output_path.name
            description = (
                f"The {index}th 3D file generated by hy3d in turn {current_turn}, step {current_step}. "
                f"generate_type={result.get('generate_type', generate_type)}, prompt={prompt_description}"
            )
            output_files.append(
                build_workspace_file_record(
                    output_path,
                    description=description,
                    source="expert",
                    name=artifact_name,
                    turn=current_turn,
                    step=current_step,
                    expert_step=current_expert_step,
                )
            )
            structured_results.append(
                {
                    "path": output_files[-1]["path"],
                    "name": artifact_name,
                    "type": file_info.get("type", ""),
                    "preview_image_url": file_info.get("preview_image_url", ""),
                    "url": file_info.get("url", ""),
                }
            )

        file_names = ", ".join(file_info["name"] for file_info in output_files)
        message = (
            f"{self._public_name} completed hy3d job {result.get('job_id', '')} with "
            f"{len(output_files)} file(s): {file_names}"
        )
        current_output = {
            "status": "success",
            "message": message,
            "output_files": output_files,
            "provider": result.get("provider", provider),
            "model_name": result.get("model_name", ""),
            "job_id": result.get("job_id", ""),
            "generate_type": result.get("generate_type", generate_type),
            "result_files": structured_results,
        }
        logger.info(message)
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "three_d_generation_results": structured_results,
            },
        )
