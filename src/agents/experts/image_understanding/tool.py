from typing import Dict, Any

from dashscope import MultiModalConversation
from google.adk.tools import ToolContext
from openai import AsyncOpenAI

from conf.api import API_CONFIG
from src.logger import logger
from src.utils import binary_to_base64


async def image_to_text_tool(tool_context: ToolContext, input_name: str, mode: str = 'description') -> Dict[str, Any]:
    """
    Using the Qwen2.5-VL model to analyze local images and generate relevant text
    """
    tool_name_for_log = "image_to_text_tool"
    
    # Load the artifact and convert it to base64.
    artifact_part = await tool_context.load_artifact(filename=input_name)
    image_base64 = binary_to_base64(artifact_part.inline_data.data, artifact_part.inline_data.mime_type)

    # API-KEY
    DASHSCOPE_API_KEY = API_CONFIG.DASHSCOPE_API_KEY
    if not DASHSCOPE_API_KEY:
        return {
            "status": "error",
            "error_message": " DASHSCOPE_API_KEY is not set",
        }

    # message
    prompts_map = {
        "description": "Please provide a detailed description of the content of this image, including the main objects, scenes, atmosphere, and possible storyline.",
        "style": "Please analyze and describe the artistic style of this image, such as painting style, color application, composition characteristics, light and shadow effects, and overall impression.",
        "ocr": "Please extract all the text content from this image. If multiple languages are included, please list them separately.",
    }
    text_prompt = prompts_map.get(mode, prompts_map['description'])

    messages = [
        {"role": "system", "content": [{"type":"text", "text": "You are a professional image analyst"}]},
        {"role": "user", "content": 
            [{"type":"text","text": text_prompt}, {"type": "image_url", "image_url":{"url":image_base64}}]
        }
    ]

    # call Qwen-VL
    try:
        logger.info(f"[{tool_name_for_log}] called: name='{input_name}', mode='{mode}'")
        async with AsyncOpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ) as client:
            response = await client.chat.completions.create(messages=messages, model='qwen-vl-plus')
        
        logger.info(f"[{tool_name_for_log}] tongyi VL analysis success")
        content = response.choices[0].message.content
        return {'status': 'success', 'message': content}

    except Exception as e:
        logger.error(
            f"[{tool_name_for_log}] calling tongyi VL API have exception: {e}", exc_info=True
        )
        return {"status": "error", "message": f"calling tongyi VL API have exception: {e}"}
