"""T091 [US5] — busy_timeout=30000 applied via open_db() in all mining paths.

All SQLite connections in sio/mining/ must route through open_db() which
sets busy_timeout=30000 and journal_mode=WAL (FR-012, H4, R-8).

Tests:
- No direct sqlite3.connect() calls in any mining source file (grep check)
- open_db() sets busy_timeout=30000 (behavioral check)
"""

from __future__ import annotations

import re
from pathlib import Path

MINING_SRC = Path("/home/gyasisutton/dev/projects/SIO/src/sio/mining")


# ---------------------------------------------------------------------------
# T091-1: No direct sqlite3.connect() calls in mining modules
# ---------------------------------------------------------------------------


def test_no_direct_sqlite3_connect_in_mining():
    """All mining modules must route through open_db(), not sqlite3.connect() directly."""
    violations = []
    pattern = re.compile(r"sqlite3\.connect\s*\(")

    for py_file in MINING_SRC.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                # Allow comments
                stripped = line.strip()
                if not stripped.startswith("#"):
                    violations.append(f"{py_file.name}:{lineno}: {stripped!r}")

    assert not violations, (
        "Direct sqlite3.connect() calls found in mining modules. "
        "All connections must route through sio.core.db.connect.open_db() "
        "to ensure busy_timeout=30000 is applied (FR-012, H4, R-8).\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# T091-2: open_db() sets busy_timeout=30000
# ---------------------------------------------------------------------------


def test_open_db_sets_busy_timeout():
    """open_db() must apply PRAGMA busy_timeout=30000 to every connection."""
    import tempfile
    from pathlib import Path as _Path

    from sio.core.db.connect import open_db  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _Path(tmpdir) / "test.db"
        conn = open_db(db_path)
        try:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
            assert row is not None, "PRAGMA busy_timeout returned None"
            # SQLite returns the timeout in ms
            assert row[0] == 30000, f"open_db() must set busy_timeout=30000, got {row[0]}"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# T091-3: open_db() sets journal_mode=WAL
# ---------------------------------------------------------------------------


def test_open_db_sets_wal_mode():
    """open_db() must enable WAL journal mode (H4)."""
    import tempfile
    from pathlib import Path as _Path

    from sio.core.db.connect import open_db  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _Path(tmpdir) / "test.db"
        conn = open_db(db_path)
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row is not None
            assert row[0].lower() == "wal", f"open_db() must set journal_mode=WAL, got {row[0]!r}"
        finally:
            conn.close()
