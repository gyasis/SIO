"""Stdlib `logging` → RunLog warnings router (Principle XIII clause 3).

DSPy emits silent warnings via the standard logging module (e.g.
``WARNING dspy.predict.predict: Not all input fields were provided to module``).
Litellm prints "Untracked error: No module named 'bs4'" to stderr.
Without this filter, those warnings are seen by NOBODY — they vanish into the
terminal or get swallowed by a redirected log file.

This module installs a logging.Handler that captures WARN+ records from
target loggers (dspy.*, litellm, distilabel) and routes them into the
active RunLog's warnings[] array, where ``sio runs <id>`` will surface them.
"""
from __future__ import annotations

import logging
from typing import List

from .writer import current

# Loggers to capture. Add more as silent-failure modes are discovered.
_TARGET_LOGGERS = (
    "dspy",            # dspy.predict.predict "Missing fields"
    "litellm",         # "Untracked error" type messages
    "distilabel",      # pipeline-level non-fatal issues
    "sio",             # internal SIO warnings
)


class RunLogHandler(logging.Handler):
    """Routes WARNING+ log records into the active RunLog's warnings/errors."""

    def emit(self, record: logging.LogRecord) -> None:
        rl = current()
        if rl is None:
            return
        try:
            msg = record.getMessage()
            code = f"LOG_{record.name.upper().replace('.', '_')}_{record.levelname}"
            # Trim noise / Truncate for the JSON record
            if record.levelno >= logging.ERROR:
                # Treat ERROR-level as an error event (more severe than warn)
                rl.warn(code, f"[{record.name}:{record.levelname}] {msg}",
                        stage=None)
            else:
                rl.warn(code, f"[{record.name}] {msg}", stage=None)
        except Exception:
            pass  # logger handlers must never raise


_installed_handlers: List[RunLogHandler] = []


def install() -> None:
    """Attach RunLogHandler to all target loggers. Idempotent."""
    if _installed_handlers:
        return  # already installed
    handler = RunLogHandler(level=logging.WARNING)
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        # Don't lower the logger's level — just add our handler
        logger.addHandler(handler)
    _installed_handlers.append(handler)


def uninstall() -> None:
    """Detach RunLogHandler from all target loggers."""
    if not _installed_handlers:
        return
    handler = _installed_handlers.pop()
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        try:
            logger.removeHandler(handler)
        except Exception:
            pass
