import os
import json
from conf.path import CONF_ROOT
from pydantic import BaseModel, ValidationError, model_validator


class SystemConfig(BaseModel):
    """
    Configuration for the system.
    """

    plan_enabled: bool = True  # Flag to enable or disable planning features
    execute_enabled: bool = True  # Flag to enable or disable execution features
    llm_model: str
    api_port: int
    app_name: str
    user_id_default: str
    session_id_default_prefix: str
    max_iterations_orchestrator: int
    image_output_dir: str
    video_output_dir: str
    log_level: str
    log_file: str
    retention: str
    rotation: str
    password: dict
    secret_key: str
    base_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @model_validator(mode="after")
    def resolve_paths(self) -> "SystemConfig":
        """
        Resolves relative paths for directory fields to be absolute from the project root.
        """
        path_fields = [
            "image_output_dir",
            "video_output_dir",
        ]
        for field in path_fields:
            path_value = getattr(self, field)
            if path_value and not os.path.isabs(path_value):
                setattr(self, field, os.path.join(self.base_dir, path_value))
        return self


def load_system_config(config_file_path: str) -> SystemConfig:
    """
    Load the system configuration from a file.

    Args:
        config_file_path (str): Path to the configuration file.

    Returns:
        SystemConfig: An instance of SystemConfig with loaded settings.
    """
    try:
        with open(config_file_path, "r") as file:
            config_data = json.load(file)
            return SystemConfig(**config_data)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
        print(
            f"FATAL: Could not load system configuration from {config_file_path}. Reason: {e}"
        )
        raise


SYS_CONFIG: SystemConfig = load_system_config(
    os.path.join(CONF_ROOT, "jsons/system.json")
)
