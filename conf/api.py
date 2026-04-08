from pydantic import BaseModel
from dotenv import load_dotenv
import os


load_dotenv()


class APIConfig(BaseModel):
    """
    Configuration for the API.
    """

    DASHSCOPE_API_KEY: str
    GOOGLE_API_KEY: str
    SEGMIND_API_KEY: str
    IMGBB_API_KEY: str


# Load API keys from environment variables
dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")
segmind_api_key = os.getenv("SEGMIND_API_KEY")
imgbb_api_key = os.getenv("IMGBB_API_KEY")

# Check if the keys are set and raise an error if not
if not dashscope_api_key:
    raise ValueError("DASHSCOPE_API_KEY is not set in the environment variables.")
if not google_api_key:
    raise ValueError("GOOGLE_API_KEY is not set in the environment variables.")
if not segmind_api_key:
    raise ValueError("SEGMIND_API_KEY is not set in the environment variables.")
if not imgbb_api_key:
    raise ValueError("IMGBB_API_KEY is not set in the environment variables.")

# Create the configuration instance
API_CONFIG = APIConfig(
    DASHSCOPE_API_KEY=dashscope_api_key,
    GOOGLE_API_KEY=google_api_key,
    SEGMIND_API_KEY=segmind_api_key,
    IMGBB_API_KEY=imgbb_api_key
)
