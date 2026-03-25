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

_ERROR_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    tool_name TEXT,
    error_text TEXT NOT NULL,
    user_message TEXT,
    context_before TEXT,
    context_after TEXT,
    error_type TEXT,
    tool_input TEXT,
    tool_output TEXT,
    mined_at TEXT NOT NULL
)
"""

_PATTERNS_DDL = """
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT UNIQUE,
    description TEXT NOT NULL,
    tool_name TEXT,
    error_count INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    rank_score REAL NOT NULL,
    centroid_embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_PATTERN_ERRORS_DDL = """
CREATE TABLE IF NOT EXISTS pattern_errors (
    pattern_id INTEGER NOT NULL REFERENCES patterns(id),
    error_id INTEGER NOT NULL REFERENCES error_records(id),
    PRIMARY KEY (pattern_id, error_id)
)
"""

_DATASETS_DDL = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL REFERENCES patterns(id),
    file_path TEXT NOT NULL,
    positive_count INTEGER NOT NULL,
    negative_count INTEGER NOT NULL,
    min_threshold INTEGER NOT NULL DEFAULT 5,
    lineage_sessions TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_SUGGESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER REFERENCES patterns(id),
    dataset_id INTEGER REFERENCES datasets(id),
    description TEXT NOT NULL,
    confidence REAL NOT NULL,
    proposed_change TEXT NOT NULL,
    target_file TEXT NOT NULL,
    change_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_explanation TEXT,
    user_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
"""

_APPLIED_CHANGES_DDL = """
CREATE TABLE IF NOT EXISTS applied_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
    target_file TEXT NOT NULL,
    diff_before TEXT NOT NULL,
    diff_after TEXT NOT NULL,
    commit_sha TEXT,
    applied_at TEXT NOT NULL,
    rolled_back_at TEXT
)
"""

_GROUND_TRUTH_DDL = """
CREATE TABLE IF NOT EXISTS ground_truth (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL,
    error_examples_json TEXT NOT NULL,
    error_type TEXT NOT NULL,
    pattern_summary TEXT NOT NULL,
    target_surface TEXT NOT NULL CHECK(target_surface IN (
        'claude_md_rule', 'skill_update', 'hook_config',
        'mcp_config', 'settings_config', 'agent_profile', 'project_config'
    )),
    rule_title TEXT NOT NULL,
    prevention_instructions TEXT NOT NULL,
    rationale TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT 'pending' CHECK(label IN ('pending', 'positive', 'negative')),
    source TEXT NOT NULL DEFAULT 'agent'
        CHECK(source IN ('agent', 'seed', 'approved', 'edited', 'rejected')),
    confidence REAL,
    user_note TEXT,
    file_path TEXT,
    quality_assessment TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
"""

_RECALL_EXAMPLES_DDL = """
CREATE TABLE IF NOT EXISTS recall_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    session_id TEXT NOT NULL,
    raw_steps TEXT NOT NULL,
    polished_runbook TEXT,
    label TEXT NOT NULL DEFAULT 'pending'
        CHECK(label IN ('pending', 'positive', 'negative', 'edited')),
    polish_model TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
"""

_FLOW_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS flow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    flow_hash TEXT NOT NULL,
    sequence TEXT NOT NULL,
    ngram_size INTEGER NOT NULL,
    was_successful INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL DEFAULT 0,
    source_file TEXT,
    timestamp TEXT NOT NULL,
    mined_at TEXT NOT NULL
)
"""

_OPTIMIZED_MODULES_DDL = """
CREATE TABLE IF NOT EXISTS optimized_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_type TEXT NOT NULL,
    optimizer_used TEXT NOT NULL,
    file_path TEXT NOT NULL,
    training_count INTEGER NOT NULL,
    metric_before REAL,
    metric_after REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_session ON behavior_invocations(session_id)",
    ("CREATE INDEX IF NOT EXISTS idx_platform_behavior "
     "ON behavior_invocations(platform, behavior_type)"),
    "CREATE INDEX IF NOT EXISTS idx_satisfaction ON behavior_invocations(user_satisfied)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON behavior_invocations(timestamp)",
    # v2 indexes
    "CREATE INDEX IF NOT EXISTS idx_error_session ON error_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_error_type ON error_records(error_type)",
    "CREATE INDEX IF NOT EXISTS idx_error_tool ON error_records(tool_name)",
    "CREATE INDEX IF NOT EXISTS idx_error_timestamp ON error_records(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_pattern_rank ON patterns(rank_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_suggestion_status ON suggestions(status)",
    # ground_truth indexes
    "CREATE INDEX IF NOT EXISTS idx_gt_pattern ON ground_truth(pattern_id)",
    "CREATE INDEX IF NOT EXISTS idx_gt_label ON ground_truth(label)",
    "CREATE INDEX IF NOT EXISTS idx_gt_source ON ground_truth(source)",
    "CREATE INDEX IF NOT EXISTS idx_gt_surface ON ground_truth(target_surface)",
    # optimized_modules indexes
    "CREATE INDEX IF NOT EXISTS idx_om_active ON optimized_modules(module_type, is_active)",
    # flow_events indexes
    "CREATE INDEX IF NOT EXISTS idx_flow_hash ON flow_events(flow_hash)",
    "CREATE INDEX IF NOT EXISTS idx_flow_session ON flow_events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_flow_timestamp ON flow_events(timestamp)",
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
    conn.execute("PRAGMA foreign_keys=ON")

    # Create v1 tables
    conn.execute(_BEHAVIOR_INVOCATIONS_DDL)
    conn.execute(_OPTIMIZATION_RUNS_DDL)
    conn.execute(_GOLD_STANDARDS_DDL)
    conn.execute(_PLATFORM_CONFIG_DDL)

    # Create v2 tables
    conn.execute(_ERROR_RECORDS_DDL)
    conn.execute(_PATTERNS_DDL)
    conn.execute(_PATTERN_ERRORS_DDL)
    conn.execute(_DATASETS_DDL)
    conn.execute(_SUGGESTIONS_DDL)
    conn.execute(_APPLIED_CHANGES_DDL)

    # Create flow events table (v2.1 — positive pattern mining)
    conn.execute(_FLOW_EVENTS_DDL)

    # Create recall examples table (v2.1 — DSPy training data)
    conn.execute(_RECALL_EXAMPLES_DDL)

    # Create DSPy suggestion engine tables
    conn.execute(_GROUND_TRUTH_DDL)
    conn.execute(_OPTIMIZED_MODULES_DDL)

    # Migration: add columns to suggestions (safe with try/except since
    # ALTER TABLE doesn't support IF NOT EXISTS)
    try:
        conn.execute("ALTER TABLE suggestions ADD COLUMN target_surface TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE suggestions ADD COLUMN reasoning_trace TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # T105: Add quality_assessment column to ground_truth
    try:
        conn.execute("ALTER TABLE ground_truth ADD COLUMN quality_assessment TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add tool_input/tool_output to error_records
    try:
        conn.execute("ALTER TABLE error_records ADD COLUMN tool_input TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE error_records ADD COLUMN tool_output TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Create indexes
    for idx_sql in _INDEXES:
        conn.execute(idx_sql)

    conn.commit()
    return conn
