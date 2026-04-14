from typing import Any

from PIL import Image
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.llm import build_llm, resolve_llm_model_name
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


_IMAGE_TO_PROMPT_PROMPT = """
## System Role

You are a professional reverse-prompt and image-analysis expert. You specialize in extracting key visual information from an image and turning it into a clear, structured, high-quality prompt for image generation systems.
Infer the most likely prompt that could have produced the image and present it in a normalized structure.

You must:

1. Observe the image precisely and analyze:
   - Main subject, such as person, object, character, or animal
   - Actions, pose, and expression
   - Scene, background, and environment
   - Style, such as photorealistic, anime, oil painting, or cyberpunk
   - Photography details when relevant, including focal length, depth of field, lighting, and lens type
   - Color palette, atmosphere, and composition elements
   - Fine-grained modifiers such as texture, material, and surface quality

2. Infer the most likely prompt structure, for example:
   - "subject + modifiers + style + camera details + quality terms"
   - "artist style + composition + environment + texture + color palette"

3. Return the result in the required format below:

### 1. Long Prompt
Include a complete prompt that covers subject, style, composition, lighting, atmosphere, and quality terms.

### 2. Negative Prompt
Infer an appropriate negative prompt to reduce common generation issues such as noise, malformed anatomy, distorted hands, and artifacts.

### 3. Key Attributes Breakdown
Break down the image by category so the prompt composition is easy to understand.

Rules:
- Do not exaggerate details that are not visible in the image.
- Do not invent story elements; describe only what can actually be observed.
- Keep the output concise, professional, and structured.
- Make the result directly usable for image-generation prompting.
- If something is uncertain, use wording such as "possibly" or "likely".
- Pay extra attention to style, layout, and any visible text so reproduction mistakes are less likely.
- Layout descriptions should cover image dimensions, text placement, the main visual subject, and the position of important decorative elements in as much detail as possible.
- OCR details must be accurate and preserve the original language.

Output only the prompt content. Do not add explanations or extra commentary.
""".strip()


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
        "prompt": _IMAGE_TO_PROMPT_PROMPT,
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
            model=build_llm(),
            instruction=(
                "You are a professional image analyst. "
                "Follow the requested mode exactly and return a clear, faithful result."
            ),
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
                "model_name": resolve_llm_model_name(),
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
            "model_name": resolve_llm_model_name(),
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
            "model_name": resolve_llm_model_name(),
        }
