import sys
from loguru import logger
from conf.system import SYS_CONFIG
from conf.path import LOGS_ROOT
from pathlib import Path


def setup_logger() -> None:
    """
    Sets up the loguru logger with configured settings.
    Logs will be output to both console and a file.
    """
    # Ensure the log directory exists.
    log_dir = Path(LOGS_ROOT)
    log_dir.mkdir(exist_ok=True)

    # Load the log filename template from configuration.
    log_file_template = SYS_CONFIG.log_file
    log_file_path = log_dir / log_file_template

    # Remove default handlers so logging is fully controlled here.
    logger.remove()

    # Add a console handler.
    logger.add(
        sys.stderr,
        level=SYS_CONFIG.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # Add a file handler.
    logger.add(
        log_file_path,
        level=SYS_CONFIG.log_level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation=SYS_CONFIG.rotation,
        retention=SYS_CONFIG.retention,
        compression="zip",
        encoding="utf-8",
        serialize=False,
        backtrace=True,
        diagnose=True,
    )

    logger.info("Logger initialized.")
    logger.debug(f"Log level set to: {SYS_CONFIG.log_level}")
    logger.debug(f"Log files will be written to: {log_dir}, template: {log_file_template}")


# Configure the logger during module import.
setup_logger()

# Export the shared logger instance.
__all__ = ["logger"]
