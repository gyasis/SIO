"""Single source of truth for SIO's on-disk home and database location.

Every other module that needs ``~/.sio`` or ``~/.sio/sio.db`` MUST resolve
it through :func:`sio_home` / :func:`db_path` instead of re-deriving the
default inline. This is what lets a second, independent SIO installation
(e.g. a per-product or per-office home) be selected purely via env vars —
``$SIO_HOME`` and/or ``$SIO_DB_PATH`` — with no code path silently falling
back to the default ``~/.sio``.

Both functions read the environment at CALL TIME, not at import time, so
tests (and any caller that mutates ``os.environ`` mid-process) see the
override take effect immediately without a reload.
"""

from __future__ import annotations

import os
from pathlib import Path


def sio_home() -> Path:
    """Return the SIO home directory.

    Honors ``$SIO_HOME`` (expanded via ``expanduser``) when set, else
    defaults to ``~/.sio``. Read at call time — never cache the result
    across calls.
    """
    env = os.environ.get("SIO_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sio"


def db_path() -> Path:
    """Return the canonical SIO database path.

    Honors ``$SIO_DB_PATH`` (expanded via ``expanduser``) when set, else
    defaults to ``sio_home() / "sio.db"`` — which itself honors
    ``$SIO_HOME``. Read at call time — never cache the result across
    calls.
    """
    env = os.environ.get("SIO_DB_PATH")
    if env:
        return Path(env).expanduser()
    return sio_home() / "sio.db"
