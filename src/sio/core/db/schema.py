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
    labeled_at TEXT,
    tool_name TEXT,
    tool_input TEXT,
    conversation_pointer TEXT
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
    exempt_from_purge INTEGER DEFAULT 1,
    task_type TEXT NOT NULL DEFAULT 'suggestion',
    dspy_example_json TEXT,
    promoted_by TEXT
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
    mined_at TEXT NOT NULL,
    is_subagent INTEGER NOT NULL DEFAULT 0,
    parent_session_id TEXT,
    pattern_id TEXT
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
    rank_score REAL NOT NULL DEFAULT 0.0,
    centroid_embedding BLOB,
    centroid_model_version TEXT,
    centroid_text TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    grade TEXT DEFAULT 'emerging'
        CHECK(grade IN ('emerging', 'strong', 'established', 'declining')),
    cycle_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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

_PROCESSED_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS processed_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    tool_call_count INTEGER NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    mined_at TEXT NOT NULL,
    is_subagent INTEGER NOT NULL DEFAULT 0,
    parent_session_id TEXT,
    last_offset INTEGER NOT NULL DEFAULT 0,
    last_mtime REAL,
    UNIQUE(file_path, file_hash)
)
"""

_SESSION_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS session_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    total_cache_create_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_ratio REAL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    session_duration_seconds REAL,
    message_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    correction_count INTEGER NOT NULL DEFAULT 0,
    positive_signal_count INTEGER NOT NULL DEFAULT 0,
    sidechain_count INTEGER NOT NULL DEFAULT 0,
    stop_reason_distribution TEXT,
    model_used TEXT,
    mined_at TEXT NOT NULL
)
"""

_POSITIVE_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS positive_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    signal_type TEXT NOT NULL CHECK(
        signal_type IN (
            'confirmation', 'gratitude', 'implicit_approval', 'session_success'
        )
    ),
    signal_text TEXT NOT NULL,
    context_before TEXT,
    tool_name TEXT,
    sentiment_score REAL,
    source_file TEXT NOT NULL,
    mined_at TEXT NOT NULL
)
"""

_VELOCITY_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS velocity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    error_rate REAL NOT NULL,
    error_count_in_window INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    rule_applied INTEGER NOT NULL DEFAULT 0,
    rule_suggestion_id INTEGER REFERENCES suggestions(id),
    created_at TEXT NOT NULL
)
"""

_AUTORESEARCH_TXLOG_DDL = """
CREATE TABLE IF NOT EXISTS autoresearch_txlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_number INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(
        action IN (
            'mine', 'cluster', 'grade', 'generate', 'assert',
            'experiment_create', 'validate', 'promote', 'rollback',
            'error', 'stop'
        )
    ),
    suggestion_id INTEGER REFERENCES suggestions(id),
    experiment_branch TEXT,
    assertion_results TEXT,
    details TEXT,
    status TEXT NOT NULL CHECK(
        status IN ('success', 'failure', 'skipped', 'pending_approval')
    ),
    created_at TEXT NOT NULL
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
    file_path TEXT,
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
    (
        "CREATE INDEX IF NOT EXISTS idx_platform_behavior "
        "ON behavior_invocations(platform, behavior_type)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_satisfaction ON behavior_invocations(user_satisfied)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON behavior_invocations(timestamp)",
    # Audit Round 2 N-R2D.1: identity UNIQUE indexes required for INSERT OR IGNORE
    # dedup. Without these, per-platform DBs created by init_db() never dedupe
    # hook-written rows, and canonical-DB sync silently duplicates every row.
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity "
        "ON behavior_invocations(platform, session_id, timestamp, tool_name)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_bi_platform_timestamp "
        "ON behavior_invocations(platform, timestamp)"
    ),
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
    # Audit Round 2 N-R2D.1: identity UNIQUE required for INSERT OR IGNORE dedup
    # on flow mining re-runs (FR-008). Without this, re-mine duplicates flows.
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_fe_identity "
        "ON flow_events(file_path, session_id, flow_hash)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_fe_success_hash "
        "ON flow_events(was_successful, flow_hash)"
    ),
    # processed_sessions indexes
    "CREATE INDEX IF NOT EXISTS idx_ps_path ON processed_sessions(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_ps_hash ON processed_sessions(file_hash)",
    # session_metrics indexes
    "CREATE INDEX IF NOT EXISTS idx_sm_session ON session_metrics(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_mined ON session_metrics(mined_at)",
    # positive_records indexes
    "CREATE INDEX IF NOT EXISTS idx_pr_session ON positive_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_pr_type ON positive_records(signal_type)",
    "CREATE INDEX IF NOT EXISTS idx_pr_tool ON positive_records(tool_name)",
    # velocity_snapshots indexes
    "CREATE INDEX IF NOT EXISTS idx_vs_type ON velocity_snapshots(error_type)",
    ("CREATE INDEX IF NOT EXISTS idx_vs_window ON velocity_snapshots(window_start, window_end)"),
    # autoresearch_txlog indexes
    "CREATE INDEX IF NOT EXISTS idx_tx_cycle ON autoresearch_txlog(cycle_number)",
    "CREATE INDEX IF NOT EXISTS idx_tx_action ON autoresearch_txlog(action)",
]


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Add v3 columns to existing tables (competitive enhancement).

    Uses try/except since ALTER TABLE does not support IF NOT EXISTS.
    """
    # patterns: add grade column for pattern lifecycle tracking
    try:
        conn.execute(
            "ALTER TABLE patterns ADD COLUMN grade TEXT DEFAULT 'emerging' "
            "CHECK(grade IN ('emerging', 'strong', 'established', 'declining'))"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    # applied_changes: add delta_type column for rule write strategy
    try:
        conn.execute(
            "ALTER TABLE applied_changes ADD COLUMN delta_type TEXT DEFAULT 'append' "
            "CHECK(delta_type IN ('append', 'merge'))"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists


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

    # Create v3 tables (competitive enhancement)
    conn.execute(_PROCESSED_SESSIONS_DDL)
    conn.execute(_SESSION_METRICS_DDL)
    conn.execute(_POSITIVE_RECORDS_DDL)
    conn.execute(_VELOCITY_SNAPSHOTS_DDL)
    conn.execute(_AUTORESEARCH_TXLOG_DDL)

    # v3 migrations: add columns to existing tables
    _migrate_v3(conn)

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
    try:
        conn.execute("ALTER TABLE suggestions ADD COLUMN skill_file_path TEXT")
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


# ---------------------------------------------------------------------------
# schema_version — FR-017 (data-model.md §2.1)
# ---------------------------------------------------------------------------

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'applied',
    description TEXT
)
"""


class PartialMigrationError(Exception):
    """Raised when a migration row with status='applying' is detected at startup.

    Indicates a previous migration crashed mid-run.  Operator must run
    ``sio db repair`` to mark stuck rows as 'failed' before SIO will start.
    """


def ensure_schema_version(conn: sqlite3.Connection) -> None:
    """Create schema_version table and seed baseline row if not present.

    Idempotent: safe to call multiple times on the same connection.
    Seeds ``(version=1, status='applied', description='baseline')`` on first run.
    """
    from sio.core.util.time import utc_now_iso  # noqa: PLC0415

    conn.execute(_SCHEMA_VERSION_DDL)
    conn.commit()
    existing = conn.execute("SELECT version FROM schema_version WHERE version=1").fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, status, description) "
            "VALUES (1, ?, 'applied', 'baseline')",
            (utc_now_iso(),),
        )
        conn.commit()


def begin_migration(conn: sqlite3.Connection, version: int, description: str) -> None:
    """Insert a migration row with status='applying' at migration start.

    Must be called before executing migration SQL.  Paired with
    :func:`finish_migration` on success.
    """
    from sio.core.util.time import utc_now_iso  # noqa: PLC0415

    conn.execute(
        "INSERT INTO schema_version (version, applied_at, status, description) "
        "VALUES (?, ?, 'applying', ?)",
        (version, utc_now_iso(), description),
    )
    conn.commit()


def finish_migration(conn: sqlite3.Connection, version: int) -> None:
    """Update a migration row from 'applying' to 'applied' on success."""
    conn.execute(
        "UPDATE schema_version SET status='applied' WHERE version=?",
        (version,),
    )
    conn.commit()


def refuse_to_start(conn: sqlite3.Connection) -> None:
    """Raise PartialMigrationError if any schema_version row has status='applying' or 'failed'.

    Called at SIO startup to prevent running against a partially-migrated DB.
    Operator must run ``sio db repair`` to resolve.
    """
    row = conn.execute(
        "SELECT version, status, description FROM schema_version "
        "WHERE status IN ('applying', 'failed') LIMIT 1"
    ).fetchone()
    if row is not None:
        version, status, description = row
        raise PartialMigrationError(
            f"Migration version={version} ({description!r}) has status={status!r} — "
            "the previous migration did not complete successfully.  "
            "Run 'sio db repair' to resolve."
        )


def repair_schema_version(conn: sqlite3.Connection) -> list[int]:
    """Mark any 'failed' or 'applying' schema_version rows as 'applied'.

    Used by ``sio db repair`` after the operator has manually verified the DB
    is in a consistent state.  Returns the list of version numbers repaired.
    """
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE status IN ('applying', 'failed')"
    ).fetchall()
    versions = [row[0] for row in rows]
    if versions:
        conn.execute(
            "UPDATE schema_version SET status='applied', applied_at=datetime('now') "
            "WHERE status IN ('applying', 'failed')"
        )
        conn.commit()
    return versions
