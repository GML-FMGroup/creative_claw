"""Filesystem path helpers for package assets and user-home runtime state."""

from __future__ import annotations

from pathlib import Path

from conf.app_config import get_instance_root, get_logs_dir

PROJECT_PATH = str(Path(__file__).resolve().parent.parent)
CONF_ROOT = str(Path(PROJECT_PATH) / "conf")
INSTANCE_ROOT = str(get_instance_root())
DATA_ROOT = str(Path(INSTANCE_ROOT) / "data")
LOGS_ROOT = str(get_logs_dir())
SRC_ROOT = str(Path(PROJECT_PATH) / "src")
TEST_ROOT = str(Path(PROJECT_PATH) / "unit_test")
