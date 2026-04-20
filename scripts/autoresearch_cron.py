"""Thin autoresearch cron wrapper (T078 — US4).

Suitable for invocation by cron or systemd:
    python -m scripts.autoresearch_cron

Imports ``sio.autoresearch.scheduler``, calls ``run_once()``, logs result to
stderr, and exits 0.  No interactive prompts.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def _open_db() -> sqlite3.Connection:
    """Open the SIO database, creating it if absent."""
    import os

    db_path = os.environ.get("SIO_DB_PATH", str(Path.home() / ".sio" / "sio.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    """Run autoresearch once and log the result."""
    from sio.autoresearch.scheduler import autoresearch_run_once

    conn = _open_db()
    try:
        result = autoresearch_run_once(conn)
    finally:
        conn.close()

    print(json.dumps(result), file=sys.stderr)


if __name__ == "__main__":
    main()
