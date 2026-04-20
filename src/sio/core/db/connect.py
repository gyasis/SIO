"""Central SQLite connection factory — T009 (004-pipeline-integrity-remediation).

Every sqlite3.connect() call in SIO routes through open_db() so that all
connections share the same PRAGMA configuration:

  - WAL mode (concurrent reads + writes without reader/writer contention)
  - 30-second busy timeout (SC-006: concurrent hook + mine writes succeed)
  - NORMAL synchronous (balance durability vs. performance)
  - Foreign keys enforced (data integrity)

Contract: contracts/storage-sync.md §3
Decision: research.md R-8
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite database with the SIO-standard PRAGMA configuration.

    Args:
        path: Filesystem path to the database file.  The file is created if
              it does not exist (unless *read_only* is True, which requires
              the file to exist).
        read_only: When True, opens the database in read-only URI mode
                   (``mode=ro``).  Any attempt to write will raise
                   :class:`sqlite3.OperationalError`.

    Returns:
        An open :class:`sqlite3.Connection` with PRAGMAs applied:
        ``journal_mode=WAL``, ``busy_timeout=30000``,
        ``synchronous=NORMAL``, ``wal_autocheckpoint=1000``,
        ``foreign_keys=ON``.

    Raises:
        sqlite3.OperationalError: If *read_only=True* and the file does not
            exist, or if a write is attempted on an RO connection.
    """
    mode = "ro" if read_only else "rwc"
    uri = f"file:{path}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
