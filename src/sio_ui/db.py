"""Database connections for sio_ui.

Two connections:
  - sio.db    → read-only mount; never mutated
  - sio_ui.db → curator state (notes, hides, stars); created on first use
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SIO_DB = Path("~/.sio/sio.db").expanduser()
CURATOR_DB = Path("~/.sio/sio_ui.db").expanduser()

_CURATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS curator_actions (
  id INTEGER PRIMARY KEY,
  table_name TEXT NOT NULL,
  row_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('hidden','flagged','starred','approved')),
  reason TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'gyasi',
  UNIQUE(table_name, row_id, action)
);
CREATE INDEX IF NOT EXISTS idx_actions_row ON curator_actions(table_name, row_id);

CREATE TABLE IF NOT EXISTS curator_notes (
  id INTEGER PRIMARY KEY,
  table_name TEXT NOT NULL,
  row_id INTEGER NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'gyasi'
);
CREATE INDEX IF NOT EXISTS idx_notes_row ON curator_notes(table_name, row_id);
"""


def sio_ro() -> sqlite3.Connection:
    """Read-only connection to the main SIO database."""
    uri = f"file:{SIO_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def curator() -> sqlite3.Connection:
    """Read/write connection to the curator database (auto-init on first call)."""
    fresh = not CURATOR_DB.exists()
    conn = sqlite3.connect(CURATOR_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if fresh:
        conn.executescript(_CURATOR_SCHEMA)
        conn.commit()
    return conn
