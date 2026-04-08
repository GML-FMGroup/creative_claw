from typing import Any, AsyncGenerator, ByteString, Dict, List
import asyncio
import base64
import os
from io import BytesIO

import httpx
from PIL import Image

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.tools import ToolContext
from google.genai.types import Blob, Content, Part

from src.logger import logger
 

def parse_usage_obj(_obj: Any) -> None:
    """Return parsed usage when available.

    Current Creative Claw runtime does not persist usage from these providers yet,
    so this helper intentionally returns `None` and keeps the tool contract stable.
    """
    return None



# async def segmind_GPT_image_1_tool(tool_context: ToolContext) -> AsyncGenerator[Dict, None]:
#     current_parameters = tool_context.state.get("current_parameters",{})
#
#     input_name = current_parameters.get("input_name")
#     tool_name_log = "segmind_GPT_image_1_tool"
#     prompt = current_parameters.get("prompt")
#     count = len(prompt)
#
#     if isinstance(input_name, str): input_name = [input_name]
#     if isinstance(prompt, str): prompt = [prompt]
#
#     img_binary_list = []
#     for name in input_name:
#         art_part = await tool_context.load_artifact(name)
#         img_binary_list.append(art_part.inline_data.data)
#
#     tasks = [upload_local_image(img_binary) for img_binary in img_binary_list]
#     img_list = await asyncio.gather(*tasks)
#
#     tasks = [call_segmind_API(img_list, p) for p in prompt]
#     result_list = await asyncio.gather(*tasks)
#
#     result = {'status': "success", "message": []}
#     success_num = 0
#     for item in result_list:
#         if item['status'] == 'success':
#             result['message'].append(item['message'])
#             success_num += 1
#         else:
#             result["message"].append(None)
#
#     if success_num==0:
#         result['message'] = f"{count} images editing task all failed, reason: {','.join([item['message'] for item in result_list])}"
#         result['status']='error'
#
#     return result
#
#
# async def upload_local_image(image_binary:ByteString, expiration:int=600, name:str=None):
#     api_key = API_CONFIG.IMGBB_API_KEY
#     url = "https://api.imgbb.com/1/upload"
#
#     files = {"image": image_binary}
#
#     params = {
#         "key": api_key,
#         "expiration": expiration,
#         "name": name,
#     }
#
#     try:
#         async with httpx.AsyncClient(timeout=imgbb_timeout) as client:
#             response = await client.post(url, params=params, files=files)
#
#         response.raise_for_status()
#         result = response.json()
#
#         if result.get("success"):
#             data = result["data"]
#             logger.info(f"upload success! img ID: {data['id']}, view url: {data['url_viewer']}, direct url: {data['url']}")
#             return data["url"]
#         else:
#             logger.error(f"Upload failed, status code: {result['status']}, error info: {result.get('error', 'no detailed error provided')}")
#             return None
#     except httpx.TimeoutException as e:
#         logger.info(f"image upload timeout: {str(e)}")
#         return None
#     except httpx.RequestError as e:
#         logger.info(f"image upload request failed: {str(e)}")
#         return None
#
# async def call_segmind_API(img_list: List, prompt: str):
#     SEGMIND_API_KEY = API_CONFIG.SEGMIND_API_KEY
#     url = "https://api.segmind.com/v1/gpt-image-1-edit"
#
#     headers = {'x-api-key': SEGMIND_API_KEY}
#     data = {
#         "prompt": prompt,
#         "image_urls": img_list,
#         "size": "auto",
#         "quality": "auto",
#         "background": "opaque",
#         "output_compression": 100,
#         "output_format": "png",
#         "moderation": "auto"
#     }
#
#     try:
#         attempt = 0
#         while(attempt<3):
#             logger.info("calling segmind GPT-image-1 API ...")
#             async with httpx.AsyncClient(timeout=segmind_timeout) as client:
#                 response = await client.post(url, headers=headers, json=data)
#                 logger.info(f"image editing success")
#
#                 if response.status_code == HTTPStatus.OK:
#                     content = response.content
#                     return {"status": "success", "message": content}
#                 else:
#                     attempt+=1
#                     logger.info(f"Error generating image: status code:{response.status_code}: {response.content[:500]}")
#
#         logger.info("maximum retry, failed")
#         return {"status": "error", "message": f"{response.status_code}: {response.content[:500]}"}
#     except httpx.TimeoutException as e:
#         logger.info(f"Segmind API Request failed: TimeoutException")
#         return {"status": "error", "message": f"Segmind API Request failed: TimeoutException"}
#     except Exception as e:
#         logger.info(f"Segmind API Request failed: {str(e)}")
#         return {"status": "error", "message": f"{str(e)}"}
async def nano_banana_image_edit_tool(tool_context: ToolContext, enhance_prompt_list) -> AsyncGenerator[Dict, None]:
    current_parameters = tool_context.state.get("current_parameters", {})
    # input_name为一个list
    input_name = current_parameters.get("input_name")
    # tool_name_log = "segmind_GPT_image_1_tool"
    # original_prompt = current_parameters.get("prompt")
    # prompt = current_parameters.get("enhanced_prompt")
    prompt = enhance_prompt_list

    # logger.debug('prompt for image editing: ' + '\n'.join(prompt))

    count = len(prompt)

    if isinstance(input_name, str): input_name = [input_name]
    if isinstance(prompt, str): prompt = [prompt]

    invocation_ctx = getattr(tool_context, "_invocation_context", None)
    if invocation_ctx is None:
        return {"status": "error", "message": "ToolContext missing invocation context"}

    img_binary_list = []
    for name in input_name:
        art_part = await tool_context.load_artifact(name)
        img_binary_list.append(art_part.inline_data.data)

    try:
        # result_list = []
        success_num = 0
        result = {
            'status': "success",
            'message': [],
            'provider': 'gemini',
            'model_name': 'gemini-3.1-flash-image-preview',
            'usage_list': [],
        }
        fail_message = []
        for p in prompt:
            input_images: list[Image.Image] = []
            for img in img_binary_list:
                input_images.append(Image.open(BytesIO(img)))

            def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest):
                user_parts: list[Part] = [Part(text=p)]
                for img_obj in input_images:
                    buffer = BytesIO()
                    img_obj.save(buffer, format="PNG")
                    user_parts.append(
                        Part(inline_data=Blob(mime_type="image/png", data=buffer.getvalue()))
                    )
                llm_request.contents.append(Content(role="user", parts=user_parts))

            llm = LlmAgent(
                name="image_editing_nano_banana",
                model="gemini-3.1-flash-image-preview", # "gemini-3-pro-image-preview",
                instruction="Edit image(s) according to prompt.",
                include_contents="none",
                before_model_callback=before_model_callback,
            )

            text_message = ''
            img_message = ''
            usage = None
            async for event in llm.run_async(invocation_ctx):
                parsed_usage = parse_usage_obj(event)
                if parsed_usage:
                    usage = parsed_usage
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if part.text is not None:
                        text_message = part.text
                    elif part.inline_data is not None:
                        img_message = part.inline_data.data
            if img_message is not None:
                # result = {'status': "success", "message": img_message}
                success_num = success_num + 1
                result['message'].append(img_message)
                result['usage_list'].append(usage)
            else:
                result['message'].append(None)
                result['usage_list'].append(usage)
                fail_message.append(text_message)
                # result = {'status': "error", "message": text_message}

            # result_list.append(result)


        if success_num == 0:
            result['message'] = f"{count}张图片全部生成失败，原因包括：{','.join(fail_message)}"
            result['status'] = 'error'

        return result

    except Exception as e:
        error_msg = f"[nano_banana_image_edit_tool] 发生异常: {e}"
        logger.error("[nano_banana_image_edit_tool] 发生异常: {}", e, exc_info=True)
        return {"status": "error", "message": error_msg}

def read_image(filename):
    ext = filename.split(".")[-1]
    with open(filename, "rb") as f:
        img = f.read()
    data = base64.b64encode(img).decode()
    src = "data:image/{ext};base64,{data}".format(ext=ext, data=data)
    return src

async def seedream_image_edit_tool(tool_context: ToolContext, enhance_prompt_list) -> AsyncGenerator[dict[str, Any], None]:
    logger.info("calling seedream for image editing ...")

    current_parameters = tool_context.state.get("current_parameters", {})
    # input_name为一个list
    input_name = current_parameters.get("input_name")
    # tool_name_log = "segmind_GPT_image_1_tool"
    # original_prompt = current_parameters.get("prompt")
    # prompt = current_parameters.get("enhanced_prompt")
    prompt = enhance_prompt_list

    # logger.debug('prompt for image editing: ' + '\n'.join(prompt))

    count = len(prompt)

    if isinstance(input_name, str): input_name = [input_name]
    if isinstance(prompt, str): prompt = [prompt]


    img_bs64_list = []
    for name in input_name:
        art_part = await tool_context.load_artifact(name)
        ext = name.split(".")[-1]

        img_bin = art_part.inline_data.data
        data = base64.b64encode(img_bin).decode()
        data_bs64 = "data:image/{ext};base64,{data}".format(ext=ext, data=data)
        img_bs64_list.append(data_bs64)


    try:
        ARK_API_KEY = os.environ.get('ARK_API_KEY')
        if not ARK_API_KEY:
            return {"status": "error", "message": "ARK_API_KEY is not set."}
        try:
            from volcenginesdkarkruntime import Ark
            from volcenginesdkarkruntime.types.images_generate_params import (
                SequentialImageGenerationOptions,
            )
        except Exception as exc:
            return {"status": "error", "message": f"seedream SDK unavailable: {exc}"}
        client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=ARK_API_KEY,
        )
        success_num = 0
        result = {
            'status': "success",
            'message': [],
            'provider': 'seedream',
            'model_name': 'doubao-seedream-4-0-250828',
            'usage_list': [],
        }
        fail_message = []

        for p in prompt:
            imagesResponse = client.images.generate(
                model="doubao-seedream-4-0-250828",
                prompt=p,
                image=img_bs64_list,
                size="2K",
                sequential_image_generation="auto",
                sequential_image_generation_options=SequentialImageGenerationOptions(max_images=10),
                response_format="b64_json",
                watermark=False
            )

            if imagesResponse.error:
                fail_message.append(imagesResponse.error)
                result['message'].append(None)
                result['usage_list'].append(parse_usage_obj(imagesResponse))
            else:
                img_bs64_data = imagesResponse.data[0].b64_json # 暂时支持一个prompt 只生成一个图像
                img_bin_data = base64.b64decode(img_bs64_data)
                result['message'].append(img_bin_data)
                result['usage_list'].append(parse_usage_obj(imagesResponse))
                success_num = success_num + 1

        if success_num == 0:
            result['message'] = f"{count}张图片全部生成失败，原因包括：{','.join(fail_message)}"
            result['status'] = 'error'

        return result

    except Exception as e:
        error_msg = f"[seedream_image_edit_tool] 发生异常: {e}"
        logger.error("[seedream_image_edit_tool] 发生异常: {}", e, exc_info=True)
        return {"status": "error", "message": error_msg}
