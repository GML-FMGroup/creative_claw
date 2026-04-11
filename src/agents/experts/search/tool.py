import asyncio
import os
import json
import httpx
from typing import Any

from google.adk.tools import ToolContext
from asyncddgs import aDDGS

from src.logger import logger


async def retrieve_image_by_text(tool_context: ToolContext) ->  dict[str, Any]:
    """
    Retrieves images based on a text query.
    """
    current_parameters = tool_context.state.get("current_parameters",{})

    query = current_parameters.get("query")
    count = current_parameters.get("count", 5)

    logger.info(f"[{retrieve_image_by_text}] called,query={query},count={count}")

    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    if not SERPER_API_KEY:
        logger.error("Serper API key is not set.")
        state_changes = {
            "status": "error",
            "error_message": "Serper API key is not set."
        }
        return state_changes

    url = "https://google.serper.dev/images"
    payload = json.dumps({"q": f"{query}"})
    headers = {
        "X-API-KEY": f"{SERPER_API_KEY}",
        "Content-Type": "application/json",
    }
    #response = requests.request("POST", url, headers=headers, data=payload)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=payload, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"HTTP error: {e.response.status_code} {e.response.reason_phrase}"
            }
        except httpx.RequestError as e:
            return {
                "status": "error",
                "message": f"Request failed: {str(e)}"
            }

    response_json = response.json()
    if "images" not in response_json:
        return {
            "status": "error",
            "message": "Search succeeded , but No images found."
        }

    url_list = [image['imageUrl'] for image in response_json["images"][:count]]
    content_list = []

    tasks = [download_image(url) for url in url_list]
    result_list = await asyncio.gather(*tasks)
    content_list = []
    for result in result_list:
        if result: content_list.append(result)
    
    if len(content_list)==0:
        return {
            "status": "error",
            "message": f"Got urls of {count} images, but all downloads failed"
        }
    else:
        return {
            "status": "success",
            "message": content_list
        }

async def download_image(image_url) -> str:
    logger.info(f"downloading image,image_url={image_url}")

    try:
        async with httpx.AsyncClient() as client:

        #response = requests.get(image_url)
            response = await client.get(url=image_url)
            response.raise_for_status()

            if not response.headers.get("Content-Type", "").startswith('image/'):
                logger.error(f"Download {image_url} failed, invalid content format: {response.headers.get('Content-Type', '')}")
                return None

        # Convert to PNG format using PIL
        from PIL import Image
        from io import BytesIO

        # Open the image from response content
        image = Image.open(BytesIO(response.content))
        # Convert to RGB if necessary (for PNG compatibility)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
        else:
            image = image.convert("RGB")

        output_buffer = BytesIO()
        image.save(output_buffer, format='PNG')
        binary_data = output_buffer.getvalue()
        logger.info(f"Successfully download {image_url}")

        return binary_data
    except Exception as e:
        logger.error(f"Download {image_url} failed: {str(e)}")
        return None


async def DDGS_search(tool_context: ToolContext) -> dict[str, Any]:
    current_parameters = tool_context.state.get("current_parameters",{})
    query = current_parameters.get("query")

    try:
        async with aDDGS() as ddgs:
            result = await ddgs.text(
                keywords=query,
                region='cn-zh',
                max_results=20,
                timelimit='y'
            )

            text = f"Found {len(result)} text results:\n\n"
            for i, item in enumerate(result):
                text += f"<result{i+1}> title: {item.get('title','no title')}, content: {item.get('body','no content')}\n"
            
            return {
                "status": "success",
                "message": result
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"duckduckgo-search error: {str(e)}"
        }
