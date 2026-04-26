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

    @staticmethod
    def _normalize_image_urls(current_parameters: dict[str, Any]) -> list[str]:
        """Normalize input image URLs from current parameters."""
        urls: list[str] = []
        for key in ("image_url", "image_urls"):
            raw_urls = current_parameters.get(key, [])
            if isinstance(raw_urls, str):
                raw_urls = [raw_urls]
            urls.extend(str(url).strip() for url in raw_urls if str(url).strip())
        return urls

    @staticmethod
    def _optional_bool(current_parameters: dict[str, Any], *keys: str) -> bool | None:
        """Return an optional boolean parameter from the first present key."""
        for key in keys:
            if key in current_parameters and current_parameters.get(key) not in (None, ""):
                return generation_tools.coerce_bool(current_parameters.get(key))
        return None

    @staticmethod
    def _provider_model(
        current_parameters: dict[str, Any],
        *,
        provider: str,
        default_model: str,
    ) -> str:
        """Return the model ID for a provider, ignoring the global hy3d default."""
        raw_model = str(current_parameters.get("model", "") or "").strip()
        if provider != "hy3d" and raw_model in {"", generation_tools.DEFAULT_MODEL, "3.1"}:
            return default_model
        return raw_model or default_model

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the 3D generation expert with normalized session parameters."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        provider = str(current_parameters.get("provider", "hy3d")).strip().lower() or "hy3d"

        try:
            prompt = self._normalize_prompt(current_parameters.get("prompt", ""))
            input_paths = self._normalize_input_paths(current_parameters)
            image_urls = self._normalize_image_urls(current_parameters)
        except ValueError as exc:
            error_text = f"{self._public_name} parameter normalization failed: {exc}"
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        if provider not in {"hy3d", "seed3d", "hyper3d", "hitem3d"}:
            error_text = (
                f"{self._public_name} supports providers `hy3d`, `seed3d`, "
                "`hyper3d`, and `hitem3d`."
            )
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
            return

        generate_type = ""
        if provider == "seed3d":
            if len(input_paths) + len(image_urls) != 1:
                error_text = f"{self._public_name} provider `seed3d` requires exactly one image source."
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return
            image_url = image_urls[0] if image_urls else None

            result = await generation_tools.seed3d_generate_tool(
                input_path=input_paths[0] if input_paths else None,
                image_url=image_url,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_SEED3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_SEED3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_SEED3D_FILE_FORMAT
                ),
                subdivision_level=str(
                    current_parameters.get(
                        "subdivision_level",
                        generation_tools.DEFAULT_SEED3D_SUBDIVISION_LEVEL,
                    )
                    or generation_tools.DEFAULT_SEED3D_SUBDIVISION_LEVEL
                ),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_SEED3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        elif provider == "hyper3d":
            if len(input_paths) + len(image_urls) > 5:
                error_text = f"{self._public_name} provider `hyper3d` supports at most 5 images."
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return

            result = await generation_tools.hyper3d_generate_tool(
                prompt=prompt or None,
                input_paths=input_paths,
                image_urls=image_urls,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_HYPER3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_HYPER3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_HYPER3D_FILE_FORMAT
                ),
                subdivision_level=(
                    str(current_parameters.get("subdivision_level", "")).strip() or None
                ),
                material=(str(current_parameters.get("material", "")).strip() or None),
                mesh_mode=(str(current_parameters.get("mesh_mode", "")).strip() or None),
                quality_override=current_parameters.get(
                    "quality_override",
                    current_parameters.get("face_count"),
                ),
                addons=(str(current_parameters.get("addons", "")).strip() or None),
                use_original_alpha=self._optional_bool(current_parameters, "use_original_alpha"),
                bbox_condition=current_parameters.get("bbox_condition"),
                ta_pose=self._optional_bool(current_parameters, "ta_pose", "TAPose"),
                hd_texture=self._optional_bool(current_parameters, "hd_texture"),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_HYPER3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        elif provider == "hitem3d":
            if prompt:
                error_text = (
                    f"{self._public_name} provider `hitem3d` supports parameter text only; "
                    "do not pass a free-form prompt."
                )
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return
            if input_paths:
                error_text = (
                    f"{self._public_name} provider `hitem3d` requires externally accessible "
                    "`image_url` or `image_urls`, not local input paths."
                )
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return
            if not image_urls or len(image_urls) > 4:
                error_text = f"{self._public_name} provider `hitem3d` requires 1 to 4 image URLs."
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return

            result = await generation_tools.hitem3d_generate_tool(
                image_urls=image_urls,
                model=self._provider_model(
                    current_parameters,
                    provider=provider,
                    default_model=generation_tools.DEFAULT_HITEM3D_MODEL,
                ),
                file_format=str(
                    current_parameters.get(
                        "file_format",
                        current_parameters.get(
                            "result_format",
                            generation_tools.DEFAULT_HITEM3D_FILE_FORMAT,
                        ),
                    )
                    or generation_tools.DEFAULT_HITEM3D_FILE_FORMAT
                ),
                resolution=str(
                    current_parameters.get(
                        "resolution",
                        generation_tools.DEFAULT_HITEM3D_RESOLUTION,
                    )
                    or generation_tools.DEFAULT_HITEM3D_RESOLUTION
                ),
                face_count=current_parameters.get("face", current_parameters.get("face_count")),
                request_type=current_parameters.get("request_type"),
                multi_images_bit=(
                    str(current_parameters.get("multi_images_bit", "")).strip() or None
                ),
                timeout_seconds=int(
                    current_parameters.get("timeout_seconds", generation_tools.DEFAULT_TIMEOUT_SECONDS)
                ),
                interval_seconds=int(
                    current_parameters.get(
                        "interval_seconds",
                        generation_tools.DEFAULT_HITEM3D_INTERVAL_SECONDS,
                    )
                ),
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
            )
        else:
            if len(input_paths) > 1:
                error_text = f"{self._public_name} provider `hy3d` supports at most one input image."
                logger.error(error_text)
                yield self.format_event(error_text, {"current_output": {"status": "error", "message": error_text}})
                return
            if image_urls:
                error_text = f"{self._public_name} provider `hy3d` does not support `image_url` inputs."
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
        provider_name = str(result.get("provider", provider) or provider)

        for index, file_info in enumerate(downloaded_files, start=1):
            output_path = Path(file_info["path"])
            artifact_name = output_path.name
            description = (
                f"The {index}th 3D file generated by {provider_name} in turn {current_turn}, step {current_step}. "
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
            f"{self._public_name} completed {provider_name} job {result.get('job_id', '')} with "
            f"{len(output_files)} file(s): {file_names}"
        )
        current_output = {
            "status": "success",
            "message": message,
            "output_files": output_files,
            "provider": provider_name,
            "model_name": result.get("model_name", ""),
            "job_id": result.get("job_id", ""),
            "generate_type": result.get("generate_type", generate_type),
            "file_format": result.get("file_format", ""),
            "subdivision_level": result.get("subdivision_level", ""),
            "resolution": result.get("resolution", ""),
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
