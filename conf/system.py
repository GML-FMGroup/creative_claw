"""System-level runtime settings loaded from the user-home config."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from conf.app_config import load_app_config
from conf.path import PROJECT_PATH


class SystemConfig(BaseModel):
    """Flattened system settings for compatibility with the existing codebase."""

    llm_provider: str
    llm_model_name: str
    workspace: str
    app_name: str
    user_id_default: str
    session_id_default_prefix: str
    max_iterations_orchestrator: int
    log_level: str
    log_file: str
    retention: str
    rotation: str
    base_dir: str = PROJECT_PATH

    @property
    def llm_model(self) -> str:
        """Return one fully-qualified provider/model string."""
        return f"{self.llm_provider}/{self.llm_model_name}"

    @property
    def workspace_path(self) -> Path:
        """Return the expanded workspace path."""
        return Path(self.workspace).expanduser()


def load_system_config() -> SystemConfig:
    """Build the system settings snapshot from the runtime config."""
    config = load_app_config()
    return SystemConfig(
        llm_provider=config.llm.provider,
        llm_model_name=config.llm.model,
        workspace=config.workspace,
        app_name=config.system.app_name,
        user_id_default=config.system.user_id_default,
        session_id_default_prefix=config.system.session_id_default_prefix,
        max_iterations_orchestrator=config.system.max_iterations_orchestrator,
        log_level=config.system.log_level,
        log_file=config.system.log_file,
        retention=config.system.retention,
        rotation=config.system.rotation,
    )


SYS_CONFIG: SystemConfig = load_system_config()
