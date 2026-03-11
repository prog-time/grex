import os
import sys
from loguru import logger

from config import settings


def setup_logger():
    logger.opt(depth=1)
    logger.remove()

    logger_format = (
        "<cyan>{time:HH:mm:ss}</cyan> | "
        f"<cyan>{settings['soft_version']}</cyan> | "
        "<level>{level: ^8}</level> | "
        "<cyan>{extra[info]}</cyan> | "
        "<level>{message}</level>"
    )

    if settings.get("debug", {}).get("active", False):
        log_level = "DEBUG"
    else:
        log_level = "INFO"

    def add_default_extra(record):
        if settings.get("is_demo", False):
            record["extra"]["info"] = "DEMO MODE"
        elif "info" not in record["extra"]:
            record["extra"]["info"] = "SYSTEM"
        return True

    logger.add(
        sys.stderr,
        format=logger_format,
        enqueue=True,
        level=log_level,
        filter=add_default_extra
    )

    if settings.get("debug", {}).get("logging_in_file"):
        path_pattern = os.path.join("logs", "{time:YYYY-MM-DD}.log")
        os.makedirs("logs", exist_ok=True)

        logger.add(
            path_pattern,
            rotation="00:00",
            retention="7 days",
            level=log_level,
            format=logger_format,
            enqueue=True,
            filter=add_default_extra
        )
