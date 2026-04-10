from typing import Any

from PIL import Image
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.logger import logger
from src.runtime.workspace import load_local_file_part, resolve_workspace_path, workspace_relative_path


def _format_exception_summary(exc: Exception) -> str:
    """Return a concise exception summary that always includes the exception type."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _describe_image_metadata(image_path) -> str:
    """Return a short, human-readable summary of one local image file."""
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            transparency = "unknown"
            if image.mode == "P" and "transparency" in image.info:
                transparency = "palette transparency"
            elif image.mode in {"RGBA", "LA", "PA"}:
                alpha_channel = image.getchannel("A")
                min_alpha, _max_alpha = alpha_channel.getextrema()
                transparency = "has transparency" if min_alpha < 255 else "fully opaque alpha channel"
            else:
                transparency = "no transparency"
            return (
                f"Basic image info: format={image.format or 'unknown'}, "
                f"size={width}x{height}, mode={image.mode}, transparency={transparency}."
            )
    except Exception as exc:
        return f"Basic image info unavailable: {_format_exception_summary(exc)}"


def _build_analysis_prompt(mode: str) -> str:
    """Return the analysis prompt for one requested understanding mode."""
    prompts_map = {
        "description": "Please provide a detailed description of the content of this image, including the main objects, scenes, atmosphere, and possible storyline.",
        "style": "Please analyze and describe the artistic style of this image, such as painting style, color application, composition characteristics, light and shadow effects, and overall impression.",
        "ocr": "Please extract all the text content from this image. If multiple languages are included, please list them separately.",
        "all": (
            "Please provide a detailed description of the content of this image, including the main objects, scenes, atmosphere, "
            "and possible storyline. Then analyze the artistic style, such as painting style, color application, composition, "
            "lighting, and overall mood. Finally, extract all readable text from the image and separate different languages if present."
        ),
    }
    return prompts_map.get(mode, prompts_map["description"])


async def image_to_text_tool(ctx: InvocationContext, input_path: str, mode: str = "description") -> dict[str, Any]:
    """Analyze one workspace image with an ADK-backed multimodal LLM call."""
    tool_name_for_log = "image_to_text_tool"
    resolved_path = None
    try:
        normalized_mode = str(mode or "description").strip().lower()
        resolved_path = resolve_workspace_path(input_path)
        image_part = load_local_file_part(resolved_path)
        prompt_text = _build_analysis_prompt(normalized_mode)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            """Inject the image and the current analysis prompt into the request."""
            llm_request.contents.append(
                Content(
                    role="user",
                    parts=[
                        Part(text=prompt_text),
                        image_part,
                    ],
                )
            )

        llm = LlmAgent(
            name="ImageUnderstandingToolAgent",
            model=SYS_CONFIG.llm_model,
            instruction="You are a professional image analyst. Follow the requested mode exactly and return a clear, faithful result.",
            include_contents="none",
            before_model_callback=before_model_callback,
        )

        logger.info(
            "[{}] called: path='{}', resolved_path='{}', mode='{}'",
            tool_name_for_log,
            input_path,
            resolved_path,
            normalized_mode,
        )
        output_text = ""
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    output_text = generated_text

        if not output_text:
            return {
                "status": "error",
                "message": "Image understanding returned empty text.",
                "input_path": workspace_relative_path(resolved_path),
                "mode": normalized_mode,
                "provider": "google_adk",
                "model_name": SYS_CONFIG.llm_model,
            }

        logger.info("[{}] image analysis success", tool_name_for_log)
        basic_info = _describe_image_metadata(resolved_path)
        return {
            "status": "success",
            "message": f"{output_text}\n\n{basic_info}",
            "analysis_text": output_text,
            "basic_info": basic_info,
            "input_path": workspace_relative_path(resolved_path),
            "mode": normalized_mode,
            "provider": "google_adk",
            "model_name": SYS_CONFIG.llm_model,
        }

    except Exception as e:
        error_summary = _format_exception_summary(e)
        logger.opt(exception=e).error(
            "[{}] image analysis failed: input_path='{}' resolved_path='{}' mode='{}' error_summary={}",
            tool_name_for_log,
            input_path,
            resolved_path or "<unresolved>",
            str(mode or "description").strip().lower(),
            error_summary,
        )
        return {
            "status": "error",
            "message": (
                f"[{tool_name_for_log}] image analysis failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}', mode='{mode}'): {error_summary}"
            ),
            "input_path": (
                workspace_relative_path(resolved_path) if resolved_path is not None else str(input_path)
            ),
            "mode": str(mode or "description").strip().lower(),
            "provider": "google_adk",
            "model_name": SYS_CONFIG.llm_model,
        }
