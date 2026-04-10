import os
import json
from conf.path import CONF_ROOT
from pydantic import BaseModel, ValidationError


class SystemConfig(BaseModel):
    """
    Configuration for the system.
    """

    llm_model: str
    app_name: str
    user_id_default: str
    session_id_default_prefix: str
    max_iterations_orchestrator: int
    log_level: str
    log_file: str
    retention: str
    rotation: str
    base_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
