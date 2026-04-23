"""Centralized failure logging for SIO — no more silent errors.

Every ``except`` handler in SIO that previously swallowed an error with
``pass`` or a lonely ``logger.warning`` should instead call
:func:`log_failure`. Failures are:

1. Written to stderr via the standard :mod:`logging` stack (visible to user).
2. Appended as one JSON line per event to
   ``~/.sio/logs/<category>.log`` (rotating, 5 MB x 3 files).
3. Returned in-band from mining/pipeline functions so CLI commands can
   surface a yellow banner + inline examples (see ``sio flows``).

Usage
-----
    from sio.core.observability import log_failure

    try:
        risky_op()
    except Exception as e:
        log_failure("purge_errors", file_path, e)
        # still decide whether to continue or re-raise — observability is
        # ORTHOGONAL to control flow. It just guarantees the event is seen.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = Path.home() / ".sio" / "logs"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_handlers: dict[str, RotatingFileHandler] = {}


def _get_category_logger(category: str) -> logging.Logger:
    """Return (cached) logger for a named failure category."""
    cat_logger = logging.getLogger(f"sio.failures.{category}")
    if category in _handlers:
        return cat_logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / f"{category}.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
    )
    cat_logger.addHandler(handler)
    cat_logger.setLevel(logging.WARNING)
    cat_logger.propagate = False
    _handlers[category] = handler
    return cat_logger


def log_failure(
    category: str,
    subject: str,
    error: BaseException | str,
    *,
    stage: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "warning",
) -> dict[str, Any]:
    """Record a failure to both stderr and a per-category rotating log.

    Parameters
    ----------
    category
        Logical bucket for grouping failures (e.g. ``flow_failures``,
        ``purge_errors``, ``parse_errors``). Determines log file name.
    subject
        What the failure is about — usually a file path, DB table,
        or session id.
    error
        The exception instance or a pre-formatted string.
    stage
        Optional free-form pipeline stage (``"stat"``, ``"extract"``, etc).
    extra
        Optional additional context merged into the log record.

    Returns
    -------
    dict
        The record that was logged (useful for collecting into a return
        payload for CLI banners).
    """
    if isinstance(error, BaseException):
        err_str = f"{type(error).__name__}: {error}"
    else:
        err_str = str(error)

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "subject": subject,
        "error": err_str,
    }
    if stage:
        record["stage"] = stage
    if extra:
        record.update(extra)
    record["severity"] = severity

    level = logging.DEBUG if severity == "debug" else logging.WARNING

    # (1) stderr — warning severity is visible; debug is typically filtered
    logger.log(level, "[%s] %s: %s", category, subject, err_str)

    # (2) persistent rotating log — EVERY severity persists, including debug.
    # No more silent errors — even expected fallbacks leave an audit trail.
    try:
        cat_logger = _get_category_logger(category)
        cat_logger.setLevel(logging.DEBUG)  # ensure debug survives to disk
        cat_logger.log(level, json.dumps(record))
    except Exception as e:  # pragma: no cover — observability must not fail loud
        logger.error("observability.log_failure: could not persist %s: %s", category, e)

    return record


def log_path(category: str) -> Path:
    """Return the absolute path to the log file for a category."""
    return _LOG_DIR / f"{category}.log"
