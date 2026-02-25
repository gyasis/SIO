"""Error logging infrastructure — structured JSON logging for SIO."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured debugging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        if hasattr(record, "platform"):
            log_entry["platform"] = record.platform
        if hasattr(record, "skill"):
            log_entry["skill"] = record.skill
        return json.dumps(log_entry)


def setup_error_logging(
    platform: str = "claude-code",
    log_dir: str | None = None,
) -> logging.Logger:
    """Set up structured error logging for a platform.

    Args:
        platform: Platform name.
        log_dir: Directory for log files. Default: ~/.sio/<platform>/

    Returns:
        Configured logger instance.
    """
    if log_dir is None:
        log_dir = os.path.expanduser(f"~/.sio/{platform}")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "error.log")

    logger = logging.getLogger(f"sio.{platform}")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.FileHandler(log_path)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

    return logger
