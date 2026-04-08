from typing import Dict, Any, AsyncGenerator, List, ByteString
from http import HTTPStatus
import httpx
import asyncio

from google.adk.tools import ToolContext

from src.utils import create_file_protocol_url
from conf.api import API_CONFIG
from conf.system import SYS_CONFIG
from src.logger import logger

segmind_timeout = httpx.Timeout(None, connect=5.0)
imgbb_timeout = httpx.Timeout(None, connect=5.0)



async def segmind_GPT_image_1_tool(tool_context: ToolContext) -> AsyncGenerator[Dict, None]:
    current_parameters = tool_context.state.get("current_parameters",{})

    input_name = current_parameters.get("input_name")
    tool_name_log = "segmind_GPT_image_1_tool"
    prompt = current_parameters.get("prompt")
    count = len(prompt)
    
    if isinstance(input_name, str): input_name = [input_name]
    if isinstance(prompt, str): prompt = [prompt]

    img_binary_list = []
    for name in input_name:
        art_part = await tool_context.load_artifact(name)
        img_binary_list.append(art_part.inline_data.data)

    tasks = [upload_local_image(img_binary) for img_binary in img_binary_list]
    img_list = await asyncio.gather(*tasks)

    tasks = [call_segmind_API(img_list, p) for p in prompt]
    result_list = await asyncio.gather(*tasks)

    result = {'status': "success", "message": []}
    success_num = 0
    for item in result_list:
        if item['status'] == 'success': 
            result['message'].append(item['message'])
            success_num += 1
        else:
            result["message"].append(None)
    
    if success_num==0:
        result['message'] = f"{count} images editing task all failed, reason: {','.join([item['message'] for item in result_list])}"
        result['status']='error'

    return result
    

async def upload_local_image(image_binary:ByteString, expiration:int=600, name:str=None):
    api_key = API_CONFIG.IMGBB_API_KEY
    url = "https://api.imgbb.com/1/upload"

    files = {"image": image_binary}

    params = {
        "key": api_key,
        "expiration": expiration,
        "name": name,
    }
    
    try:
        async with httpx.AsyncClient(timeout=imgbb_timeout) as client:
            response = await client.post(url, params=params, files=files)

        response.raise_for_status()
        result = response.json()
        
        if result.get("success"):
            data = result["data"]
            logger.info(f"upload success! img ID: {data['id']}, view url: {data['url_viewer']}, direct url: {data['url']}")
            return data["url"]
        else:
            logger.error(f"Upload failed, status code: {result['status']}, error info: {result.get('error', 'no detailed error provided')}")
            return None
    except httpx.TimeoutException as e:
        logger.info(f"image upload timeout: {str(e)}")
        return None
    except httpx.RequestError as e:
        logger.info(f"image upload request failed: {str(e)}")
        return None
    
async def call_segmind_API(img_list: List, prompt: str):
    SEGMIND_API_KEY = API_CONFIG.SEGMIND_API_KEY
    url = "https://api.segmind.com/v1/gpt-image-1-edit"

    headers = {'x-api-key': SEGMIND_API_KEY}
    data = {
        "prompt": prompt,
        "image_urls": img_list,
        "size": "auto",
        "quality": "auto",
        "background": "opaque",
        "output_compression": 100,
        "output_format": "png",
        "moderation": "auto"
    }

    try:
        attempt = 0
        while(attempt<3):
            logger.info("calling segmind GPT-image-1 API ...")
            async with httpx.AsyncClient(timeout=segmind_timeout) as client:
                response = await client.post(url, headers=headers, json=data)
                logger.info(f"image editing success")
                
                if response.status_code == HTTPStatus.OK:
                    content = response.content
                    return {"status": "success", "message": content}
                else:
                    attempt+=1
                    logger.info(f"Error generating image: status code:{response.status_code}: {response.content[:500]}")
                
        logger.info("maximum retry, failed")
        return {"status": "error", "message": f"{response.status_code}: {response.content[:500]}"}
    except httpx.TimeoutException as e:
        logger.info(f"Segmind API Request failed: TimeoutException")
        return {"status": "error", "message": f"Segmind API Request failed: TimeoutException"}
    except Exception as e:
        logger.info(f"Segmind API Request failed: {str(e)}")
        return {"status": "error", "message": f"{str(e)}"}
