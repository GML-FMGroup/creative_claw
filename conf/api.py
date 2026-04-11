"""API-key configuration loaded from `.env`.

The runtime should stay importable even when optional feature credentials are absent.
Each tool or channel is responsible for validating the specific key it needs at call time.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


class APIConfig(BaseModel):
    """Centralized API-key snapshot for runtime code that prefers typed access."""

    OPENAI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    ARK_API_KEY: str = ""
    DDS_API_KEY: str = ""
    SERPER_API_KEY: str = ""
    BRAVE_API_KEY: str = ""


def _read_env(name: str) -> str:
    """Return one stripped environment value or an empty string."""
    return os.getenv(name, "").strip()


def load_api_config() -> APIConfig:
    """Load API keys from environment variables without eager validation."""
    return APIConfig(
        OPENAI_API_KEY=_read_env("OPENAI_API_KEY"),
        GOOGLE_API_KEY=_read_env("GOOGLE_API_KEY"),
        ARK_API_KEY=_read_env("ARK_API_KEY"),
        DDS_API_KEY=_read_env("DDS_API_KEY"),
        SERPER_API_KEY=_read_env("SERPER_API_KEY"),
        BRAVE_API_KEY=_read_env("BRAVE_API_KEY"),
    )


API_CONFIG = load_api_config()
