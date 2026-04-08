import os
import base64
from pathlib import Path
from urllib.parse import urlparse
from typing import ByteString
from pydantic import BaseModel

from src.logger import logger
from conf.system import SYS_CONFIG

def is_valid_url(url_string: str) -> bool:
    if not url_string or not isinstance(url_string, str):
        return False
    try:
        result = urlparse(url_string)
        # Require both a scheme (for example http) and a network location.
        return all([result.scheme, result.netloc])
    except (ValueError, AttributeError):
        return False


def create_file_protocol_url(local_path_str: str) -> str:

    if not local_path_str or not isinstance(local_path_str, str):
        logger.warning(f"invalid local path: {local_path_str}")
        return ""

    path_obj = Path(local_path_str)

    # Resolve relative paths from the project root.
    if not path_obj.is_absolute():
        path_obj = Path(SYS_CONFIG.base_dir) / path_obj

    # Ensure the target file exists.
    if not path_obj.is_file():
        logger.error(f"File not found at '{path_obj}'. Cannot create a file:// URL.")
        return ""

    # Format the URL according to the current OS path rules.
    if os.name == "nt":
        return "file://" + str(path_obj).replace("\\", "/")
    else:
        return "file://" + str(path_obj)
    

def binary_to_base64(image_binary: ByteString, image_format:str = 'image/png', with_head:bool=True) -> str:
    encoded_string = base64.b64encode(image_binary).decode('utf-8')
    if with_head:
        return f"data:{image_format};base64,{encoded_string}"
    else:
        return encoded_string
