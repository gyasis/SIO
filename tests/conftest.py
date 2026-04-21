"""Shared pytest fixtures for SIO test suite."""

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _allow_tmp_path_for_applier():
    """Allow writer/rollback path validation to accept pytest tmp dirs."""
    import sio.applier.rollback as _rb
    import sio.applier.writer as _wr

    tmp_root = Path(tempfile.gettempdir())
    _wr._ALLOWED_ROOTS.append(tmp_root)
    _rb._ALLOWED_ROOTS.append(tmp_root)
    yield
    _wr._ALLOWED_ROOTS.remove(tmp_root)
    _rb._ALLOWED_ROOTS.remove(tmp_root)


@pytest.fixture
def tmp_db():
    """In-memory SQLite database with SIO schema applied."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def sample_invocation():
    """Factory for creating sample BehaviorInvocation dicts."""

    def _make(
        session_id="test-session-001",
        platform="claude-code",
        tool_name="Read",
        user_message="Read the file foo.py",
        tool_input='{"file_path": "/tmp/foo.py"}',
        tool_output="file contents here",
        error=None,
        behavior_type="skill",
        **overrides,
    ):
        record = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform,
            "user_message": user_message,
            "behavior_type": behavior_type,
            "actual_action": tool_name,
            "expected_action": None,
            "activated": 1,
            "correct_action": 1,
            "correct_outcome": 1,
            "user_satisfied": None,
            "user_note": None,
            "passive_signal": None,
            "history_file": None,
            "line_start": None,
            "line_end": None,
            "token_count": None,
            "latency_ms": None,
            "labeled_by": None,
            "labeled_at": None,
        }
        record.update(overrides)
        return record

    return _make


@pytest.fixture
def mock_platform_config():
    """Mock platform configuration for testing."""
    return {
        "platform": "claude-code",
        "db_path": ":memory:",
        "hooks_installed": 1,
        "skills_installed": 1,
        "config_updated": 1,
        "capability_tier": 1,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "last_verified": None,
    }


# ---------------------------------------------------------------------------
# v2 fixtures
# ---------------------------------------------------------------------------

_V2_DDL_STATEMENTS = [
    # Tables
    """CREATE TABLE IF NOT EXISTS error_records (
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
    mined_at TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS patterns (
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
)""",
    """CREATE TABLE IF NOT EXISTS pattern_errors (
    pattern_id INTEGER NOT NULL REFERENCES patterns(id),
    error_id INTEGER NOT NULL REFERENCES error_records(id),
    PRIMARY KEY (pattern_id, error_id)
)""",
    """CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL REFERENCES patterns(id),
    file_path TEXT NOT NULL,
    positive_count INTEGER NOT NULL,
    negative_count INTEGER NOT NULL,
    min_threshold INTEGER NOT NULL DEFAULT 5,
    lineage_sessions TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS suggestions (
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
)""",
    """CREATE TABLE IF NOT EXISTS applied_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
    target_file TEXT NOT NULL,
    diff_before TEXT NOT NULL,
    diff_after TEXT NOT NULL,
    commit_sha TEXT,
    applied_at TEXT NOT NULL,
    rolled_back_at TEXT
)""",
    # Indexes
    "CREATE INDEX IF NOT EXISTS idx_error_session ON error_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_error_type ON error_records(error_type)",
    "CREATE INDEX IF NOT EXISTS idx_error_tool ON error_records(tool_name)",
    "CREATE INDEX IF NOT EXISTS idx_error_timestamp ON error_records(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_pattern_rank ON patterns(rank_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_suggestion_status ON suggestions(status)",
]


@pytest.fixture
def v2_db():
    """In-memory SQLite with v1 AND v2 schema applied.

    Bootstraps via init_db (v1 tables + pragmas), then executes all v2 DDL
    statements on the same connection.  Yields the open connection and closes
    it on teardown.
    """
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    for ddl in _V2_DDL_STATEMENTS:
        conn.execute(ddl)
    # Seed stub parent rows so FK references from test helpers work.
    # Use id=1 for the common case; tests that explicitly INSERT id=1
    # should use INSERT OR REPLACE or a different id.
    conn.execute(
        "INSERT INTO patterns (id, pattern_id, description, tool_name, "
        "error_count, session_count, first_seen, last_seen, rank_score, "
        "created_at, updated_at) VALUES "
        "(1, 'test-pattern', 'test', 'Read', 1, 1, "
        "datetime('now'), datetime('now'), 1.0, "
        "datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO datasets (id, pattern_id, file_path, positive_count, "
        "negative_count, created_at, updated_at) VALUES "
        "(1, 1, '/tmp/test.json', 0, 0, datetime('now'), datetime('now'))"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sample_specstory_file(tmp_path: Path):
    """Factory that writes a realistic SpecStory-style Markdown file.

    Usage::

        def test_something(sample_specstory_file):
            path = sample_specstory_file()                     # defaults
            path = sample_specstory_file(errors=["read failed"])  # with errors

    Args:
        filename: Name of the file to create inside *tmp_path*.
        tool_calls: List of dicts with keys ``tool``, ``input``, ``output``.
                    Defaults to a small set of representative calls.
        errors: List of raw error strings to embed in tool output blocks.

    Returns:
        ``pathlib.Path`` pointing at the created file.
    """

    def _make(
        filename: str = "2026-02-25_10-00-00Z-test-session.md",
        tool_calls: list[dict] | None = None,
        errors: list[str] | None = None,
    ) -> Path:
        _default_tool_calls = [
            {
                "tool": "Read",
                "input": '{"file_path": "/home/user/project/src/main.py"}',
                "output": 'def main():\n    print("hello")\n',
            },
            {
                "tool": "Bash",
                "input": '{"command": "pytest tests/ -v"}',
                "output": (
                    "============================= test session starts ==============================\n"
                    "collected 4 items\n\n"
                    "tests/test_core.py::test_init PASSED\n"
                    "tests/test_core.py::test_schema PASSED\n"
                    "============================== 2 passed in 0.45s ==============================\n"
                ),
            },
            {
                "tool": "Edit",
                "input": json.dumps(
                    {
                        "file_path": "/home/user/project/src/main.py",
                        "old_string": 'print("hello")',
                        "new_string": 'print("world")',
                    }
                ),
                "output": "File updated successfully.",
            },
            {
                "tool": "Glob",
                "input": '{"pattern": "**/*.py"}',
                "output": "src/main.py\nsrc/utils.py\ntests/test_core.py\n",
            },
        ]

        resolved_tool_calls = tool_calls if tool_calls is not None else _default_tool_calls
        resolved_errors = errors or []

        lines: list[str] = [
            "# Session: test-session",
            "",
            "**Human:** Please help me review and fix the codebase.",
            "",
            "**Assistant:** Sure, let me start by reading the main module.",
            "",
        ]

        for idx, call in enumerate(resolved_tool_calls):
            tool = call.get("tool", "Unknown")
            inp = call.get("input", "{}")
            out = call.get("output", "")

            lines += [
                f"**Tool call: {tool}**",
                "```json",
                inp,
                "```",
                "",
                f"**{tool} output:**",
                "```",
                out,
                "```",
                "",
            ]

        # Append any error blocks requested by the caller.
        for err_idx, err_text in enumerate(resolved_errors):
            tool_label = "Bash" if err_idx % 2 == 0 else "Read"
            lines += [
                f"**Tool call: {tool_label}**",
                "```json",
                '{"command": "some_failing_command"}'
                if tool_label == "Bash"
                else '{"file_path": "/tmp/missing.py"}',
                "```",
                "",
                f"**{tool_label} output (error):**",
                "```",
                err_text,
                "```",
                "",
            ]

        lines += [
            "**Human:** Looks good, thank you!",
            "",
            "**Assistant:** Happy to help. The changes have been applied.",
            "",
        ]

        file_path = tmp_path / filename
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return file_path

    return _make


@pytest.fixture
def sample_jsonl_file(tmp_path: Path):
    """Factory that writes a realistic Claude Code JSONL transcript file.

    Each line is a JSON object representing one message in the conversation.
    The schema mirrors the Claude Code JSONL format with ``role``, ``content``,
    ``tool_name``, ``tool_input``, ``tool_output``, and ``error`` fields.

    Usage::

        def test_something(sample_jsonl_file):
            path = sample_jsonl_file()                         # defaults
            path = sample_jsonl_file(errors=["connection timeout"])

    Args:
        filename: Name of the file to create inside *tmp_path*.
        messages: Explicit list of message dicts to write.  When supplied,
                  *errors* is ignored.
        errors: List of error strings to embed in ``tool_output`` entries.

    Returns:
        ``pathlib.Path`` pointing at the created file.
    """

    def _make(
        filename: str = "session.jsonl",
        messages: list[dict] | None = None,
        errors: list[str] | None = None,
    ) -> Path:
        _default_messages: list[dict] = [
            {
                "role": "human",
                "content": "Please read src/main.py and tell me what it does.",
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "error": None,
                "timestamp": "2026-02-25T10:00:01.000Z",
            },
            {
                "role": "assistant",
                "content": "Let me read the file for you.",
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/user/project/src/main.py"},
                "tool_output": 'def main():\n    print("hello")\n',
                "error": None,
                "timestamp": "2026-02-25T10:00:02.000Z",
            },
            {
                "role": "assistant",
                "content": "The file defines a single `main` function that prints 'hello'.",
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "error": None,
                "timestamp": "2026-02-25T10:00:03.000Z",
            },
            {
                "role": "human",
                "content": "Run the tests.",
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "error": None,
                "timestamp": "2026-02-25T10:00:10.000Z",
            },
            {
                "role": "assistant",
                "content": "Running pytest now.",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/ -v"},
                "tool_output": "2 passed in 0.45s",
                "error": None,
                "timestamp": "2026-02-25T10:00:11.000Z",
            },
        ]

        resolved_messages: list[dict]
        if messages is not None:
            resolved_messages = messages
        else:
            resolved_messages = list(_default_messages)
            # Append error messages if requested.
            for err_idx, err_text in enumerate(errors or []):
                resolved_messages.append(
                    {
                        "role": "assistant",
                        "content": "Encountered an error during tool execution.",
                        "tool_name": "Bash",
                        "tool_input": {"command": "failing_command"},
                        "tool_output": None,
                        "error": err_text,
                        "timestamp": f"2026-02-25T10:01:{err_idx:02d}.000Z",
                    }
                )

        file_path = tmp_path / filename
        with file_path.open("w", encoding="utf-8") as fh:
            for msg in resolved_messages:
                fh.write(json.dumps(msg) + "\n")

        return file_path

    return _make


@pytest.fixture
def sample_error_records():
    """Factory that returns a list of ErrorRecord-like dicts.

    The dicts match the ``error_records`` table schema and can be inserted
    directly into a ``v2_db`` fixture connection.

    Usage::

        def test_something(sample_error_records):
            records = sample_error_records()                        # 5 defaults
            records = sample_error_records(count=10, error_type="parse_error")

    Args:
        count: Number of records to generate.
        error_type: Value placed in the ``error_type`` column for every record.
        tool_name: Value placed in the ``tool_name`` column for every record.

    Returns:
        ``list[dict]`` ready to insert into ``error_records``.
    """
    _SOURCE_FILES = [
        "2026-02-25_10-00-00Z-test-session.md",
        "2026-02-24_09-30-00Z-debug-run.md",
        "2026-02-23_14-15-00Z-refactor.md",
    ]
    _ERROR_TEXTS = [
        "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/missing.py'",
        "PermissionError: [Errno 13] Permission denied: '/etc/secret'",
        "TimeoutError: tool execution exceeded 30s limit",
        "JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
        "AttributeError: 'NoneType' object has no attribute 'read'",
        "ConnectionRefusedError: [Errno 111] Connection refused",
        "ValueError: invalid literal for int() with base 10: 'not-a-number'",
        "KeyError: 'tool_output' missing from response payload",
    ]
    _USER_MESSAGES = [
        "Read the configuration file.",
        "Run the migration script.",
        "Execute the test suite.",
        "Fetch data from the remote endpoint.",
        "Write the summary to disk.",
    ]

    def _make(
        count: int = 5,
        error_type: str = "tool_failure",
        tool_name: str = "Read",
    ) -> list[dict]:
        now = datetime.now(timezone.utc)
        records: list[dict] = []

        for i in range(count):
            # Stagger timestamps so ordering is deterministic in tests.
            ts = now.replace(second=i % 60).isoformat()
            records.append(
                {
                    "session_id": f"test-session-{i // 3:03d}",
                    "timestamp": ts,
                    "source_type": "specstory" if i % 2 == 0 else "jsonl",
                    "source_file": _SOURCE_FILES[i % len(_SOURCE_FILES)],
                    "tool_name": tool_name,
                    "error_text": _ERROR_TEXTS[i % len(_ERROR_TEXTS)],
                    "user_message": _USER_MESSAGES[i % len(_USER_MESSAGES)],
                    "context_before": f"Assistant decided to call {tool_name} with index {i}.",
                    "context_after": "User acknowledged the failure.",
                    "error_type": error_type,
                    "mined_at": now.isoformat(),
                }
            )

        return records

    return _make


# ---------------------------------------------------------------------------
# T005: 004-pipeline-integrity-remediation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_sio_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Clone of ~/.sio/sio.db sandboxed in tmp_path with heavy rows trimmed.

    Copies the live DB if it exists, deletes rows beyond rowid 1000 in the
    two heaviest tables, and sets SIO_DB_PATH so the app under test uses the
    clone instead of the real store.

    Skips (not fails) the test when no live DB is present so CI stays green.
    """
    src = Path.home() / ".sio" / "sio.db"
    if not src.exists():
        pytest.skip("no live ~/.sio/sio.db to clone from")
    dst = tmp_path / "sio.db"
    shutil.copy2(src, dst)
    con = sqlite3.connect(str(dst))
    try:
        con.execute("DELETE FROM error_records WHERE rowid > 1000")
        con.execute("DELETE FROM flow_events WHERE rowid > 1000")
        con.commit()
    except sqlite3.OperationalError:
        # Tables may not exist in older schemas — not a blocker.
        con.rollback()
    finally:
        con.close()
    monkeypatch.setenv("SIO_DB_PATH", str(dst))
    return dst


@pytest.fixture
def tmp_platform_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty per-platform behavior_invocations.db under tmp_path.

    Creates the directory structure mirroring ~/.sio/claude-code/ and
    initialises the DB with the platform schema (if the helper exists) or
    an empty WAL-mode DB.  Sets SIO_PLATFORM_DB_PATH for the app under test.
    """
    path = tmp_path / "claude-code" / "behavior_invocations.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from sio.core.db.schema import create_platform_schema

        create_platform_schema(path)
    except (ImportError, AttributeError):
        # create_platform_schema not yet implemented; just create an empty WAL DB.
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA journal_mode=WAL")
        con.commit()
        con.close()
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(path))
    return path


@pytest.fixture
def mock_lm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch dspy.LM.__call__ to return deterministic mocked responses.

    Returns the responses dict; callers can pre-populate it keyed on the
    first 80 characters of the prompt to control what the mock returns.

    Example::

        def test_something(mock_lm):
            mock_lm["Generate a rule"] = "mocked rule text"
    """
    import dspy

    responses: dict[str, Any] = {}

    def _fake_call(self: Any, prompt: str, **kwargs: Any) -> list[Any]:
        key = prompt[:80] if isinstance(prompt, str) else str(prompt)[:80]
        return [responses.get(key, "mocked")]

    monkeypatch.setattr(dspy.LM, "__call__", _fake_call)
    return responses


@pytest.fixture
def fake_fastembed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the SIO embedder with a deterministic stub returning ones.

    Patches ``sio.core.clustering.embedder.embed_texts`` (when the module
    exists) and a fallback ``sio.core.embedder.embed_texts`` path, so any
    code that imports either will receive ``np.ones((N, 384), float32)``.

    Also patches ``sio.core.applier.merger._compute_similarity`` to return 1.0
    (identical vectors), so merge-consent tests get a deterministic cosine=1.0.
    """

    def _fake_embed(texts: list[str]) -> "np.ndarray":
        return np.ones((len(texts), 384), dtype=np.float32)

    for module_path in (
        "sio.core.clustering.embedder",
        "sio.core.embedder",
    ):
        try:
            import importlib

            mod = importlib.import_module(module_path)
            monkeypatch.setattr(mod, "embed_texts", _fake_embed)
        except (ImportError, AttributeError):
            pass  # Module not yet created; silently skip.

    # Patch merger similarity so tests get cosine=1.0 (all-ones → identical)
    try:
        import importlib

        merger_mod = importlib.import_module("sio.core.applier.merger")
        monkeypatch.setattr(merger_mod, "_compute_similarity", lambda a, b: 1.0)
    except (ImportError, AttributeError):
        pass  # merger module not yet implemented; silently skip.


@pytest.fixture
def freeze_utc_now(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch sio.core.util.time.utc_now_iso to return a fixed timestamp.

    Returns the frozen ISO-8601 string so tests can assert against it.
    """
    FROZEN = "2026-04-20T12:00:00+00:00"

    try:
        import sio.core.util.time as _time_mod

        monkeypatch.setattr(_time_mod, "utc_now_iso", lambda: FROZEN)
    except ImportError:
        pass  # Module not yet implemented; fixture is a no-op in that case.

    return FROZEN
