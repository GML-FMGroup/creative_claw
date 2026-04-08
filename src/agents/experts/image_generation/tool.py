import time
import requests
import os
import asyncio
import httpx
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, AsyncGenerator
from urllib.parse import urlparse, unquote
from pathlib import PurePosixPath
from http import HTTPStatus

from dashscope import ImageSynthesis
from google.adk.tools import ToolContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.genai.types import Part, Content

from conf.system import SYS_CONFIG
from conf.api import API_CONFIG
from src.logger import logger


async def prompt_enhancement_tool(ctx: InvocationContext, prompt: str) -> AsyncGenerator[str, None]:
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

async def tongyi_text2image_tool(prompt: str) -> AsyncGenerator[dict[str, Any], None]:
    """
    Generate images through Tongyi.
    """

    logger.info(f"[text2image_tool] called: prompt='{prompt}'")
    DASHSCOPE_API_KEY = API_CONFIG.DASHSCOPE_API_KEY
    if not DASHSCOPE_API_KEY:
        return {
            "status": "error",
            "message": "DASHSCOPE_API_KEY not found",
        }

    try:
        rsp = ImageSynthesis.async_call(
            api_key=DASHSCOPE_API_KEY,
            model="wanx2.1-t2i-turbo",
            prompt=prompt,
            n=1
        )

        if not rsp.status_code == HTTPStatus.OK:
            error_msg = f"dashscope task creation failed, status_code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}"
            logger.info(error_msg)
            return {"status": "error", "message": error_msg}
        
        logger.info('dashscope task creation success, waiting for execution...')
        rsp = ImageSynthesis.wait(rsp)

        if rsp.status_code == HTTPStatus.OK:
            logger.info('dashscope t2i task finish')
            if rsp.output.task_status == 'FAILED':
                error_msg = f"dashscope task failed: {rsp['output']['message']}"
                logger.error(error_msg)
                return {'status':'error', 'message':error_msg}

            for result in rsp.output.results:
                content = requests.get(result.url).content
                return {"status": "success", "message": content}
        else:
            error_msg = f"dashscope task failed, status_code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}

    except Exception as e:
        error_msg = f"[text2image_tool] failed: {e}"
        logger.error(f"[text2image_tool] failed: {e}", exc_info=True)
        return {"status": "error", "message": error_msg}



async def GPTimage1_text2image_tool(prompt: str) -> AsyncGenerator[dict[str, Any], None]:
    """
    generation with GPT-image-1
    """
    SEGMIND_API_KEY = API_CONFIG.SEGMIND_API_KEY
    url = "https://api.segmind.com/v1/gpt-image-1"

    timeout = httpx.Timeout(None, connect=5.0)
    try:
        logger.info("calling segmind GPT-image-1 API ...")
        async with httpx.AsyncClient(timeout=timeout) as client:
            data = {
                "prompt": prompt,
                "size": "auto",
                "quality": "auto",
                "moderation": "auto",
                "background": "opaque",
                "output_compression": 100,
                "output_format": "png"
            }
            headers = {'x-api-key': SEGMIND_API_KEY}

            response = await client.post(url, json=data, headers=headers)
            if response.status_code == HTTPStatus.OK:
                content = response.content
                return {"status": "success", "message": content}
            else:
                error_msg = f"Error generating image: status code:{response.status_code}: {response.content[:500]}"
                logger.info(error_msg)
                return {"status": "error", "message": f"{response.status_code}: {response.content[:500]}"}

    except Exception as e:
        error_msg = f"[text2image_tool] failed: {e}"
        logger.error(f"[text2image_tool] failed: {e}", exc_info=True)
        return {"status": "error", "message": error_msg}
