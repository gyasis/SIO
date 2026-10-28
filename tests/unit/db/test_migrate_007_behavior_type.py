"""schema 007 — `behavior_type` gains 'tool', and the all-'skill' corpus is reclassified.

Every row `telemetry/logger.py` ever wrote said `behavior_type='skill'`, including
46,322 Bash calls, because the CHECK domain had no value for a plain tool call. These
tests fail on schema 006.
"""

from __future__ import annotations

import sqlite3

import pytest

from sio.core.db.schema import init_db, migrate_007_behavior_type_tool
from sio.core.telemetry.logger import _behavior_type_for

# The pre-007 table, trimmed to the columns these tests touch. Written out rather than
# taken from init_db because init_db now creates the widened CHECK.
_LEGACY_DDL = """
CREATE TABLE behavior_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    platform TEXT NOT NULL,
    user_message TEXT NOT NULL,
    behavior_type TEXT NOT NULL CHECK(
        behavior_type IN ('skill', 'mcp_tool', 'preference', 'instructions_rule')
    ),
    actual_action TEXT,
    tool_name TEXT
)
"""

_ROWS = [
    ("s1", "Bash", "Bash"),
    ("s2", "Read", "Read"),
    ("s3", "mcp__graphiti__search_nodes", "mcp__graphiti__search_nodes"),
    ("s4", "Skill", "Skill"),
    ("s5", None, "Write"),  # tool_name NULL, name only in actual_action
    ("s6", None, None),  # no tool name at all
]


def _legacy_db(path) -> str:
    conn = sqlite3.connect(str(path))
    conn.execute(_LEGACY_DDL)
    for sid, tool_name, actual in _ROWS:
        conn.execute(
            "INSERT INTO behavior_invocations "
            "(session_id, timestamp, platform, user_message, behavior_type, "
            " actual_action, tool_name) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'claude-code', 'm', 'skill', ?, ?)",
            (sid, actual, tool_name),
        )
    conn.commit()
    conn.close()
    return str(path)


def _types(db: str) -> dict[str, str]:
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT session_id, behavior_type FROM behavior_invocations").fetchall()
    conn.close()
    return dict(rows)


def test_legacy_check_rejects_tool_before_the_migration(tmp_path):
    """The reason F5 needed a migration at all."""
    db = _legacy_db(tmp_path / "pre.db")
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO behavior_invocations "
            "(session_id, timestamp, platform, user_message, behavior_type) "
            "VALUES ('x', '2026-01-01T00:00:00Z', 'claude-code', 'm', 'tool')"
        )
    conn.close()


def test_widens_the_check_and_reclassifies(tmp_path):
    db = _legacy_db(tmp_path / "m.db")
    migrate_007_behavior_type_tool(db)

    assert _types(db) == {
        "s1": "tool",
        "s2": "tool",
        "s3": "mcp_tool",
        "s4": "skill",
        "s5": "tool",  # resolved through actual_action
        "s6": "skill",  # unclassifiable: left alone, not guessed
    }

    conn = sqlite3.connect(db)
    # 'tool' is now legal ...
    conn.execute(
        "INSERT INTO behavior_invocations "
        "(session_id, timestamp, platform, user_message, behavior_type) "
        "VALUES ('new', '2026-01-02T00:00:00Z', 'claude-code', 'm', 'tool')"
    )
    # ... and the constraint is still a constraint.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO behavior_invocations "
            "(session_id, timestamp, platform, user_message, behavior_type) "
            "VALUES ('bad', '2026-01-02T00:00:00Z', 'claude-code', 'm', 'nonsense')"
        )
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        conn.execute("SELECT status FROM schema_version WHERE version=7").fetchone()[0]
        == "applied"
    )
    conn.close()


def test_idempotent(tmp_path):
    db = _legacy_db(tmp_path / "i.db")
    migrate_007_behavior_type_tool(db)
    first = _types(db)
    migrate_007_behavior_type_tool(db)
    assert _types(db) == first

    conn = sqlite3.connect(db)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='behavior_invocations'"
    ).fetchone()[0]
    conn.close()
    # Not applied twice: exactly one 'tool' in the constraint list.
    assert ddl.count("'tool'") == 1


def test_refuses_an_unrecognised_check(tmp_path):
    """A rewrite of the stored DDL must never run against a schema it cannot place."""
    db = str(tmp_path / "odd.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE behavior_invocations ("
        " id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, timestamp TEXT NOT NULL,"
        " platform TEXT NOT NULL, user_message TEXT NOT NULL,"
        " behavior_type TEXT NOT NULL CHECK(behavior_type IN ('something', 'else')),"
        " actual_action TEXT, tool_name TEXT)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="neither the pre-007 nor the 007"):
        migrate_007_behavior_type_tool(db)


def test_fresh_db_accepts_tool():
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO behavior_invocations "
        "(session_id, timestamp, platform, user_message, behavior_type) "
        "VALUES ('f', '2026-01-01T00:00:00Z', 'claude-code', 'm', 'tool')"
    )
    assert (
        conn.execute("SELECT behavior_type FROM behavior_invocations").fetchone()[0] == "tool"
    )
    conn.close()


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("Bash", "tool"),
        ("Read", "tool"),
        ("Edit", "tool"),
        ("Agent", "tool"),
        ("Skill", "skill"),
        ("mcp__graphiti__add_memory", "mcp_tool"),
        ("", "tool"),
    ],
)
def test_classifier(tool_name, expected):
    assert _behavior_type_for(tool_name) == expected
