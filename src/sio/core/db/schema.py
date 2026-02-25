"""Database schema DDL for SIO behavior tracking.

Creates all tables with WAL mode, indexes, and pragmas per data-model.md.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

_BEHAVIOR_INVOCATIONS_DDL = """
CREATE TABLE IF NOT EXISTS behavior_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    platform TEXT NOT NULL,
    user_message TEXT NOT NULL,
    behavior_type TEXT NOT NULL CHECK(
        behavior_type IN ('skill', 'mcp_tool', 'preference', 'instructions_rule')
    ),
    actual_action TEXT,
    expected_action TEXT,
    activated INTEGER,
    correct_action INTEGER,
    correct_outcome INTEGER,
    user_satisfied INTEGER,
    user_note TEXT,
    passive_signal TEXT,
    history_file TEXT,
    line_start INTEGER,
    line_end INTEGER,
    token_count INTEGER,
    latency_ms INTEGER,
    labeled_by TEXT,
    labeled_at TEXT
)
"""

_OPTIMIZATION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    optimizer TEXT NOT NULL,
    example_count INTEGER NOT NULL,
    before_satisfaction REAL NOT NULL,
    after_satisfaction REAL,
    proposed_diff TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'approved', 'rejected', 'rolled_back', 'deployed')
    ),
    arena_passed INTEGER,
    drift_score REAL,
    created_at TEXT NOT NULL,
    deployed_at TEXT,
    commit_sha TEXT
)
"""

_GOLD_STANDARDS_DDL = """
CREATE TABLE IF NOT EXISTS gold_standards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id INTEGER NOT NULL REFERENCES behavior_invocations(id),
    platform TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    user_message TEXT NOT NULL,
    expected_action TEXT NOT NULL,
    expected_outcome TEXT,
    created_at TEXT NOT NULL,
    exempt_from_purge INTEGER DEFAULT 1
)
"""

_PLATFORM_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS platform_config (
    platform TEXT PRIMARY KEY,
    db_path TEXT NOT NULL,
    hooks_installed INTEGER,
    skills_installed INTEGER,
    config_updated INTEGER,
    capability_tier INTEGER,
    installed_at TEXT NOT NULL,
    last_verified TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_session ON behavior_invocations(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_platform_behavior ON behavior_invocations(platform, behavior_type)",
    "CREATE INDEX IF NOT EXISTS idx_satisfaction ON behavior_invocations(user_satisfied)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON behavior_invocations(timestamp)",
]


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the SIO database with schema and pragmas.

    Args:
        db_path: Path to SQLite database file, or ":memory:" for in-memory.

    Returns:
        Configured sqlite3.Connection with WAL mode and all tables created.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # auto_vacuum MUST be set before any tables exist on a new database
    current_auto_vacuum = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    if current_auto_vacuum != 2:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        # For file-based DBs, need VACUUM to apply the change
        if db_path != ":memory:":
            conn.execute("VACUUM")

    # Set pragmas — WAL for concurrent reads/writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1000")

    # Create tables
    conn.execute(_BEHAVIOR_INVOCATIONS_DDL)
    conn.execute(_OPTIMIZATION_RUNS_DDL)
    conn.execute(_GOLD_STANDARDS_DDL)
    conn.execute(_PLATFORM_CONFIG_DDL)

    # Create indexes
    for idx_sql in _INDEXES:
        conn.execute(idx_sql)

    conn.commit()
    return conn
