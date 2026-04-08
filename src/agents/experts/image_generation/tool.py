import os
import asyncio
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.genai import types
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.logger import logger


@dataclass
class ImageGenerationResult:
    """Normalized image generation result across different providers."""

    status: str
    message: Any
    provider: str
    model_name: str
    usage: dict | None = None


async def prompt_enhancement_tool(ctx: InvocationContext, prompt: str) -> dict[str, str]:
    system_prompt = """
    You are a professional prompt optimization expert, proficient in the concretization and optimization of prompt words in the field of text, biology, and graphics.
    The user will input the initial prompt, and you need to polish or expand it.
    Your task has two situations:
    1. The user entered a vague and brief instruction (usually a short sentence without any details)
    You must generate a more detailed, creative, and high-quality prompt word based on original prompt. The specific content and details of the image are all up to you, but it needs to be consistent with the original input instructions.


    2. The user entered detailed instructions (usually long sentences exceeding 100 words)
    You don't need to add any visual content, but rather polish the prompt. Your polishing mainly focuses on the following aspects:
    **Picture details**: emphasize the details in the original prompt
    **Special elements**: If there are elements such as text, symbols, etc. in the original prompt, you need to make their description more precise.
    Be careful! In this case, you must ensure that the newly generated prompt is strictly consistent with the original prompt, without losing or changing any semantic content.
    """

    def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest):
        user_prompt = f"This is the original prompt entered by the user: {prompt}, please polish or enhance it."
        llm_request.contents.append(Content(role='user', parts=[Part(text=user_prompt)]))

    
    llm = LlmAgent(
        name="prompt_enhancement",
        model=SYS_CONFIG.llm_model,
        instruction=system_prompt,
        include_contents='none',
        before_model_callback=before_model_callback
    )
    
    try:
        enhanced_prompt = None
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    enhanced_prompt = generated_text
        if enhanced_prompt:
            return {
                'status': 'success',
                'message': enhanced_prompt
            }
        else:
            return {
                'status': 'error',
                'message': "LLmAgent calling failed"
            }
            

    except Exception as e:
        error_text = f"LlmAgent failed: {str(e)}"
        logger.error(error_text)
        return {
            'status': 'error',
            'message': error_text
        }

def _normalize_aspect_ratio(raw_value: str) -> str:
    """Normalize arbitrary aspect ratio hints into one supported Gemini value."""
    supported = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
    value = str(raw_value or "").strip()
    return value if value in supported else "16:9"


async def gemini_image_generation(
    ctx: InvocationContext,
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
    resolution: str = "1K",
) -> ImageGenerationResult:
    """Generate one image with Gemini image preview."""
    try:
        normalized_ratio = _normalize_aspect_ratio(aspect_ratio)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            llm_request.contents.append(Content(role="user", parts=[Part(text=prompt)]))

        llm = LlmAgent(
            name="media_gemini_image_generation",
            model="gemini-3.1-flash-image-preview",
            instruction="Generate an image according to the prompt.",
            include_contents="none",
            generate_content_config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=normalized_ratio,
                    image_size=resolution,
                ),
            ),
            before_model_callback=before_model_callback,
        )

        text_message = ""
        image_data: bytes | None = None
        async for event in llm.run_async(ctx):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.text is not None:
                    text_message = part.text
                elif part.inline_data is not None:
                    image_data = part.inline_data.data

        if image_data:
            return ImageGenerationResult(
                status="success",
                message=image_data,
                provider="gemini",
                model_name="gemini-3.1-flash-image-preview",
            )

        return ImageGenerationResult(
            status="error",
            message=text_message or "gemini returned no image",
            provider="gemini",
            model_name="gemini-3.1-flash-image-preview",
        )
    except Exception as exc:
        return ImageGenerationResult(
            status="error",
            message=f"gemini exception: {exc}",
            provider="gemini",
            model_name="gemini-3.1-flash-image-preview",
        )


async def seedream_image_generation(prompt: str, ark_api_key: str) -> ImageGenerationResult:
    """Generate one image with Seedream when the optional SDK is available."""
    if not ark_api_key:
        return ImageGenerationResult(
            status="error",
            message="ARK_API_KEY is not set.",
            provider="seedream",
            model_name="doubao-seedream-4-0-250828",
        )

    try:
        from volcenginesdkarkruntime import Ark
        from volcenginesdkarkruntime.types.images_generate_params import (
            SequentialImageGenerationOptions,
        )
    except Exception as exc:
        return ImageGenerationResult(
            status="error",
            message=f"seedream SDK unavailable: {exc}",
            provider="seedream",
            model_name="doubao-seedream-4-0-250828",
        )

    try:
        client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=ark_api_key,
        )
        response = client.images.generate(
            model="doubao-seedream-4-0-250828",
            prompt=prompt,
            size="2K",
            sequential_image_generation="auto",
            sequential_image_generation_options=SequentialImageGenerationOptions(max_images=10),
            response_format="b64_json",
            watermark=False,
        )
        if getattr(response, "error", None):
            return ImageGenerationResult(
                status="error",
                message=f"seedream generation failed: {response.error}",
                provider="seedream",
                model_name="doubao-seedream-4-0-250828",
            )

        for item in getattr(response, "data", []) or []:
            image_base64 = getattr(item, "b64_json", None)
            if image_base64:
                import base64

                return ImageGenerationResult(
                    status="success",
                    message=base64.b64decode(image_base64),
                    provider="seedream",
                    model_name="doubao-seedream-4-0-250828",
                )

        return ImageGenerationResult(
            status="error",
            message="seedream returned empty images",
            provider="seedream",
            model_name="doubao-seedream-4-0-250828",
        )
    except Exception as exc:
        logger.error("seedream exception: {}", exc, exc_info=True)
        return ImageGenerationResult(
            status="error",
            message=f"seedream exception: {exc}",
            provider="seedream",
            model_name="doubao-seedream-4-0-250828",
        )


async def nano_banana_image_generation_tool(
    ctx: InvocationContext,
    prompt: str,
    aspect_ratio="16:9",
    resolution="1K",
) -> dict[str, Any]:
    # aspect_ratio = "16:9"  # "1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9"
    # resolution = "2K"  # "1K", "2K", "4K"
    logger.info("calling nano banana for image generation ...")

    result = await gemini_image_generation(
        ctx,
        prompt,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    if result.status == "success" and isinstance(result.message, (bytes, bytearray)):
        logger.info(f"nano_banana 已完成任务生成，二进制文件大小为{len(result.message)}")
    return {
        "status": result.status,
        "message": result.message,
        "provider": result.provider,
        "model_name": result.model_name,
        "usage": result.usage,
    }


async def seedream_image_generation_tool(prompt: str) -> AsyncGenerator[dict[str, Any], None]:
    logger.info("calling seedream for image generation ...")
    ark_api_key = os.environ.get("ARK_API_KEY") or ""
    result = await seedream_image_generation(prompt, ark_api_key)
    return {
        "status": result.status,
        "message": result.message,
        "provider": result.provider,
        "model_name": result.model_name,
        "usage": result.usage,
    }
