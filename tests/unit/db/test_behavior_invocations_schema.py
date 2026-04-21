"""Unit tests for H-R1.1: behavior_invocations DDL columns added in fix-pack.

Verifies that a freshly created DB (via init_db) has all 3 columns added
for T-REGR1 (tool_name, tool_input, conversation_pointer).
"""

from __future__ import annotations

import sqlite3


def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_behavior_invocations_has_tool_name():
    """Fresh DB must have behavior_invocations.tool_name (H-R1.1)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "behavior_invocations")
    conn.close()
    assert "tool_name" in cols, (
        "behavior_invocations.tool_name missing from freshly created DB. "
        "Add it to _BEHAVIOR_INVOCATIONS_DDL in schema.py."
    )


def test_behavior_invocations_has_tool_input():
    """Fresh DB must have behavior_invocations.tool_input (H-R1.1)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "behavior_invocations")
    conn.close()
    assert "tool_input" in cols, (
        "behavior_invocations.tool_input missing from freshly created DB. "
        "Add it to _BEHAVIOR_INVOCATIONS_DDL in schema.py."
    )


def test_behavior_invocations_has_conversation_pointer():
    """Fresh DB must have behavior_invocations.conversation_pointer (H-R1.1)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "behavior_invocations")
    conn.close()
    assert "conversation_pointer" in cols, (
        "behavior_invocations.conversation_pointer missing from freshly created DB. "
        "Add it to _BEHAVIOR_INVOCATIONS_DDL in schema.py."
    )


def test_error_records_has_pattern_id():
    """Fresh DB must have error_records.pattern_id (H-R1.2)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "error_records")
    conn.close()
    assert "pattern_id" in cols, (
        "error_records.pattern_id missing from freshly created DB. "
        "Add it to _ERROR_RECORDS_DDL in schema.py."
    )


def test_flow_events_has_file_path():
    """Fresh DB must have flow_events.file_path (H-R1.4)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "flow_events")
    conn.close()
    assert "file_path" in cols, (
        "flow_events.file_path missing from freshly created DB. "
        "Add it to _FLOW_EVENTS_DDL in schema.py."
    )


def test_patterns_has_cycle_id():
    """Fresh DB must have patterns.cycle_id (H-R1.6)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "patterns")
    conn.close()
    assert "cycle_id" in cols, (
        "patterns.cycle_id missing from freshly created DB. "
        "Add it to _PATTERNS_DDL in schema.py."
    )


def test_patterns_has_centroid_model_version():
    """Fresh DB must have patterns.centroid_model_version (H-R1.6)."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    cols = _get_columns(conn, "patterns")
    conn.close()
    assert "centroid_model_version" in cols, (
        "patterns.centroid_model_version missing from freshly created DB. "
        "Add it to _PATTERNS_DDL in schema.py."
    )
