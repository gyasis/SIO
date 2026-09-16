"""Agent isolation (sio.core.db.agents): the ``agent`` column, migration 006,
per-agent views, the self-healing trigger, and ``sio db drop-agent``.

Every DB here is a synthetic file under ``tmp_path`` (never ``~/.sio``); the
migration's backup lands beside it because the backup path is derived from
the DB's own location.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from sio.core.db import agents as ag
from sio.core.db.queries import get_error_records, insert_error_record
from sio.core.db.schema import init_db

PI_FULL = "pi:2026-09-15T10-20-49-318Z_01a0a495-6525-707f-b8c9-daaaf128d19b"
PI_PARTIAL = "pi:01a0a495"


@pytest.fixture(autouse=True)
def _runs_dir_in_tmp(tmp_path, monkeypatch):
    """Keep `runlogged` run-logs out of the real ~/.sio/runs.

    The writer resolves its directory from Path.home() at import time, so a
    HOME monkeypatch alone does not redirect it.
    """
    from sio.core.runlog import writer as _w

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(_w, "_RUNS_DIR", runs)

# A pre-006 schema: what a real sio.db looked like before this change (no
# `agent` column, no UNIQUE fingerprint, no views / triggers).
_LEGACY_DDL = """
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied', description TEXT
);
INSERT INTO schema_version VALUES (1, '2026-01-01', 'applied', 'baseline');
INSERT INTO schema_version VALUES (2, '2026-01-01', 'applied', '004');
INSERT INTO schema_version VALUES (5, '2026-01-01', 'applied', '005');
CREATE TABLE error_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL, source_type TEXT NOT NULL, source_file TEXT NOT NULL,
    tool_name TEXT, error_text TEXT NOT NULL, user_message TEXT,
    context_before TEXT, context_after TEXT, error_type TEXT, tool_input TEXT,
    tool_output TEXT, mined_at TEXT NOT NULL, is_subagent INTEGER NOT NULL DEFAULT 0,
    parent_session_id TEXT, pattern_id TEXT
);
CREATE TABLE patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id TEXT UNIQUE, description TEXT NOT NULL,
    tool_name TEXT, error_count INTEGER NOT NULL, session_count INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, rank_score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE pattern_errors (
    pattern_id INTEGER NOT NULL REFERENCES patterns(id),
    error_id INTEGER NOT NULL REFERENCES error_records(id),
    PRIMARY KEY (pattern_id, error_id)
);
CREATE TABLE experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
    experiment_name TEXT NOT NULL, source_table TEXT NOT NULL,
    UNIQUE(event_id, experiment_name, source_table)
);
CREATE TABLE flow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, flow_hash TEXT NOT NULL,
    sequence TEXT NOT NULL, ngram_size INTEGER NOT NULL,
    was_successful INTEGER NOT NULL DEFAULT 0, duration_seconds REAL DEFAULT 0,
    source_file TEXT, file_path TEXT, timestamp TEXT NOT NULL, mined_at TEXT NOT NULL
);
CREATE TABLE positive_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, timestamp TEXT NOT NULL,
    signal_type TEXT NOT NULL, signal_text TEXT NOT NULL, context_before TEXT,
    tool_name TEXT, sentiment_score REAL, source_file TEXT NOT NULL, mined_at TEXT NOT NULL
);
CREATE TABLE session_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0, mined_at TEXT NOT NULL
);
CREATE TABLE processed_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL, file_hash TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0, tool_call_count INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0, mined_at TEXT NOT NULL,
    is_subagent INTEGER NOT NULL DEFAULT 0, parent_session_id TEXT,
    last_offset INTEGER NOT NULL DEFAULT 0, last_mtime REAL,
    UNIQUE(file_path, file_hash)
);
"""

_ER_INSERT = (
    "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
    "tool_name, error_text, error_type, mined_at) VALUES (?, ?, 'jsonl', 'f', ?, ?, ?, 'm')"
)


def _seed_legacy(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_DDL)
    rows = [
        # bare legacy claude id (id 1) + an EXACT duplicate of it (id 2)
        ("abc-legacy", "t1", "Bash", "boom", "tool_failure"),
        ("abc-legacy", "t1", "Bash", "boom", "tool_failure"),
        # canonical claude id, NULL tool_name (NULLs must compare equal)
        ("claude:def", "t2", None, "nope", "user_correction"),
        ("claude:def", "t2", None, "nope", "user_correction"),
        # the pi bug: one session under a partial AND the full id, same events
        (PI_FULL, "t3", "read", "ENOENT", "tool_failure"),
        (PI_FULL, "t4", "bash", "exit 1", "tool_failure"),
        (PI_PARTIAL, "t3", "read", "ENOENT", "tool_failure"),
        (PI_PARTIAL, "t4", "bash", "exit 1", "tool_failure"),
        # a prefix nobody knows -> legacy-claude rule, but REPORTED
        ("wuphf:zz", "t5", "Bash", "odd", "tool_failure"),
        # a pi session that is genuinely different
        ("pi:2026-09-01T00-00-00-000Z_ffffffff-0000-0000-0000-000000000000", "t6", "bash", "x", "tool_failure"),
    ]
    conn.executemany(_ER_INSERT, rows)
    conn.execute(
        "INSERT INTO patterns (id, pattern_id, description, error_count, session_count, "
        "first_seen, last_seen) VALUES (1, 'p', 'd', 1, 1, 't', 't')"
    )
    # pattern_errors points at the DUPLICATE (id 2) — must be remapped to id 1
    conn.execute("INSERT INTO pattern_errors VALUES (1, 2)")
    # ... and at the pi partial-id duplicate (id 7) — remapped to survivor id 5
    conn.execute("INSERT INTO pattern_errors VALUES (1, 7)")
    conn.execute(
        "INSERT INTO experiment_runs (event_id, experiment_name, source_table) "
        "VALUES (8, 'exp', 'error_records')"
    )
    conn.execute(
        "INSERT INTO flow_events (session_id, flow_hash, sequence, ngram_size, timestamp, mined_at) "
        "VALUES ('bare-flow', 'h', 'a -> b', 2, 't', 'm')"
    )
    conn.execute(
        "INSERT INTO positive_records (session_id, timestamp, signal_type, signal_text, "
        "source_file, mined_at) VALUES ('/x/y.jsonl:abcd', 't', 'gratitude', 'thanks', 'f', 'm')"
    )
    conn.execute(
        "INSERT INTO session_metrics (session_id, file_path, mined_at) VALUES ('/x/y.jsonl:abcd', '/x/y.jsonl', 'm')"
    )
    conn.execute(
        "INSERT INTO processed_sessions (file_path, file_hash, mined_at) "
        "VALUES ('/home/u/.claude/projects/-p/s.jsonl', 'hash', 'm')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    db = tmp_path / "sio.db"
    _seed_legacy(db)
    return db


def _q(db: Path, sql: str, params=()) -> list:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _seed_ids(tmp_path: Path, session_ids: list[str]) -> Path:
    """A pre-006 DB holding one distinct error row per given session id."""
    db = tmp_path / "sio.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_LEGACY_DDL)
    conn.executemany(
        _ER_INSERT,
        [(sid, f"t{i}", "x", "e", "tool_failure") for i, sid in enumerate(session_ids)],
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# migration 006
# ---------------------------------------------------------------------------


class TestMigration006:
    def test_backfill_merge_dedupe_constraint(self, legacy_db: Path):
        report = ag.migrate_006_agent_isolation(legacy_db)
        assert report["status"] == "applied"

        # backfill: every table has agent set, nothing left at ''
        for table in ag.AGENT_TABLES:
            assert _q(legacy_db, f"SELECT COUNT(*) FROM {table} WHERE agent = ''")[0][0] == 0
        agents = dict(_q(legacy_db, "SELECT agent, COUNT(*) FROM error_records GROUP BY agent"))
        # claude: abc-legacy (1 after dedupe) + claude:def (1) + wuphf (1) = 3
        assert agents == {"claude": 3, "pi": 3}
        assert report["unknown_prefix"]["error_records"] == 1
        assert report["before"]["error_records"] == {"claude": 5, "pi": 5}
        assert report["after"]["error_records"] == {"claude": 3, "pi": 3}

        # merge: the partial pi id is gone, folded into the full id
        pi_ids = {r[0] for r in _q(legacy_db, "SELECT DISTINCT session_id FROM error_records WHERE agent='pi'")}
        assert PI_PARTIAL not in pi_ids and PI_FULL in pi_ids
        assert report["merged_ids"]["error_records"]["pi"] == 1
        assert report["merged_rows"]["error_records"]["pi"] == 2

        # dedupe: lowest id survives, referencing rows follow it
        assert report["duplicates_removed"] == {"claude": 2, "pi": 2}
        assert _q(legacy_db, "SELECT id FROM error_records ORDER BY id")[:3] == [(1,), (3,), (5,)]
        assert sorted(_q(legacy_db, "SELECT error_id FROM pattern_errors")) == [(1,), (5,)]
        assert _q(legacy_db, "SELECT event_id FROM experiment_runs") == [(6,)]

        # constraint + views + stamp
        assert _q(legacy_db, "SELECT name FROM sqlite_master WHERE name='ux_error_records_fingerprint'")
        assert _q(legacy_db, "SELECT COUNT(*) FROM errors_pi")[0][0] == 3
        assert _q(legacy_db, "SELECT COUNT(*) FROM flows_claude")[0][0] == 1
        assert _q(legacy_db, "SELECT status FROM schema_version WHERE version=6") == [("applied",)]

        # backup was written beside the DB, not under ~/.sio
        backup = Path(report["backup"])
        assert backup.exists() and backup.parent == legacy_db.parent / "backups"
        assert "pre-agent-migration" in backup.name
        # and it is the PRE-migration state
        assert "agent" not in [r[1] for r in _q(backup, "PRAGMA table_info(error_records)")]

    def test_rerun_is_a_noop_without_backup(self, legacy_db: Path):
        first = ag.migrate_006_agent_isolation(legacy_db)
        n_backups = len(list((legacy_db.parent / "backups").iterdir()))
        second = ag.migrate_006_agent_isolation(legacy_db)
        assert first["status"] == "applied" and second["status"] == "already_applied"
        assert len(list((legacy_db.parent / "backups").iterdir())) == n_backups

    def test_ambiguous_partial_is_left_and_reported(self, tmp_path: Path):
        db = _seed_ids(
            tmp_path,
            [
                "pi:01a0a495",
                "pi:2026-01-01T00-00-00-000Z_01a0a495-1111-7000-8000-000000000001",
                "pi:2026-01-02T00-00-00-000Z_01a0a495-2222-7000-8000-000000000002",
            ],
        )
        report = ag.migrate_006_agent_isolation(db)
        assert report["ambiguous_ids"]["error_records"]["pi"] == ["pi:01a0a495"]
        assert _q(db, "SELECT COUNT(*) FROM error_records WHERE session_id='pi:01a0a495'")[0][0] == 1
        assert "merged_ids" in report and report["merged_ids"] == {}

    def test_short_partial_is_neither_merged_nor_ambiguous(self, tmp_path: Path):
        """Below the 8-char floor a stored id is a session in its own right."""
        db = _seed_ids(
            tmp_path,
            [
                "pi:ab",
                "pi:01a0a49",  # 7 chars: one short of the floor
                "pi:2026-01-01T00-00-00-000Z_01a0a495-1111-7000-8000-000000000001",
                "pi:2026-01-02T00-00-00-000Z_ab22",
            ],
        )
        report = ag.migrate_006_agent_isolation(db)
        assert report["merged_ids"] == {} and report["ambiguous_ids"] == {}
        ids = {r[0] for r in _q(db, "SELECT DISTINCT session_id FROM error_records")}
        assert {"pi:ab", "pi:01a0a49"} <= ids

    def test_name_style_ids_are_never_merged_on_a_bare_substring(self, tmp_path: Path):
        """The data-corruption case: a goose session ``main`` is NOT ``domain-fix``,
        and ``sessionone`` (8+ chars, inside a word) is not ``my-sessionone-v2``."""
        db = _seed_ids(
            tmp_path,
            [
                "goose:main",
                "goose:domain-fix",
                "goose:sessionone",
                "goose:mysessionone-v2",
            ],
        )
        report = ag.migrate_006_agent_isolation(db)
        assert report["merged_ids"] == {} and report["ambiguous_ids"] == {}
        ids = {r[0] for r in _q(db, "SELECT DISTINCT session_id FROM error_records")}
        assert ids == {"goose:main", "goose:domain-fix", "goose:sessionone", "goose:mysessionone-v2"}

    def test_codex_style_stem_partial_merges(self, tmp_path: Path):
        """codex names files ``<ts>_<uuid>`` too: the uuid's first group merges."""
        full = "codex:2026-03-04T05-06-07-890Z_deadbeef-0000-4000-8000-000000000000"
        db = _seed_ids(tmp_path, ["codex:deadbeef", full])
        report = ag.migrate_006_agent_isolation(db)
        assert report["merged_ids"]["error_records"]["codex"] == 1
        assert {r[0] for r in _q(db, "SELECT DISTINCT session_id FROM error_records")} == {full}

    def test_rule_only_runs_inside_006_migrated_db_is_untouched(self, tmp_path: Path):
        """On an already-migrated DB the merge never runs again, even when rows
        that WOULD merge under the rule are inserted afterwards."""
        db = _seed_ids(tmp_path, ["pi:2026-01-01T00-00-00-000Z_11111111-0000-0000-0000-000000000000"])
        assert ag.migrate_006_agent_isolation(db)["status"] == "applied"
        full = "pi:2026-09-15T10-20-49-318Z_01a0a495-6525-707f-b8c9-daaaf128d19b"
        conn = sqlite3.connect(str(db))
        conn.executemany(
            "INSERT INTO error_records (session_id, agent, timestamp, source_type, source_file, "
            "tool_name, error_text, error_type, mined_at) "
            "VALUES (?, 'pi', ?, 'jsonl', 'f', 'x', 'e', 'tool_failure', 'm')",
            [("pi:01a0a495", "t8"), (full, "t9")],
        )
        conn.commit()
        conn.close()
        assert ag.migrate_006_agent_isolation(db)["status"] == "already_applied"
        ids = {r[0] for r in _q(db, "SELECT DISTINCT session_id FROM error_records")}
        assert "pi:01a0a495" in ids and full in ids


class TestIsPartialOf:
    """The predicate behind the 006 id merge, in isolation."""

    FULL = "2026-09-15T10-20-49-318Z_01a0a495-6525-707f-b8c9-daaaf128d19b"

    def test_real_shape_first_uuid_group_after_underscore(self):
        assert ag.is_partial_of("01a0a495", self.FULL)

    def test_inner_uuid_group_bounded_by_hyphens(self):
        assert ag.is_partial_of("daaaf128d19b", self.FULL)

    def test_whole_uuid_bounded_by_underscore_and_end(self):
        assert ag.is_partial_of("01a0a495-6525-707f-b8c9-daaaf128d19b", self.FULL)

    @pytest.mark.parametrize(
        ("partial", "full"),
        [
            ("main", "domain-fix"),  # inside a word, and short
            ("01a0a49", FULL),  # 7 chars: under the floor
            ("1a0a4956", FULL),  # 8 chars but straddles a boundary
            ("0a495-65", FULL),  # 8 chars, starts mid-token
            ("sessionone", "mysessionone-v2"),  # long enough, no boundary before
            ("abcdefgh", "abcdefgh"),  # equal length is not a partial
            (FULL, FULL),
            ("", FULL),
        ],
    )
    def test_rejected(self, partial: str, full: str):
        assert not ag.is_partial_of(partial, full)

    def test_boundary_characters(self):
        for sep in ag.PARTIAL_ID_TOKEN_SEPARATORS:
            assert ag.is_partial_of("abcdefgh", f"xx{sep}abcdefgh{sep}yy")
        assert not ag.is_partial_of("abcdefgh", "xx abcdefgh yy")  # space is not a separator

    def test_second_occurrence_can_satisfy_the_boundary(self):
        assert ag.is_partial_of("abcdefgh", "xabcdefghx_abcdefgh")

    def test_two_concurrent_migrations_apply_exactly_once(self, legacy_db: Path):
        results: list[dict] = []
        errors: list[BaseException] = []

        def run():
            try:
                results.append(ag.migrate_006_agent_isolation(legacy_db))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors
        assert sorted(r["status"] for r in results) == ["already_applied", "applied"]
        assert _q(legacy_db, "SELECT COUNT(*) FROM schema_version WHERE version=6") == [(1,)]
        assert _q(legacy_db, "SELECT COUNT(*) FROM error_records") == [(6,)]

    def test_migration_on_memory_db_skips_backup(self):
        report = ag.migrate_006_agent_isolation(":memory:")
        assert report["status"] == "applied" and report["backup"] is None

    def test_format_report_mentions_the_things_a_human_needs(self, legacy_db: Path):
        text = ag.format_migration_report(ag.migrate_006_agent_isolation(legacy_db))
        assert "unknown 'xyz:' prefix" in text
        assert "merged 1 partial pi session id(s)" in text
        assert "claude=2, pi=2" in text
        assert "backup:" in text


# ---------------------------------------------------------------------------
# write seam, trigger, views, queries
# ---------------------------------------------------------------------------


def _rec(session_id: str, **over) -> dict:
    base = {
        "session_id": session_id,
        "timestamp": "2026-09-15T10:00:00Z",
        "source_type": "adapter",
        "source_file": "f",
        "tool_name": "bash",
        "error_text": "exit 1",
        "error_type": "tool_failure",
        "mined_at": "m",
    }
    base.update(over)
    return base


class TestWriteSeam:
    def test_insert_stamps_agent_and_ignores_duplicates(self, tmp_db):
        assert insert_error_record(tmp_db, _rec("pi:abc")) is not None
        assert insert_error_record(tmp_db, _rec("pi:abc")) is None
        assert insert_error_record(tmp_db, _rec("bare-legacy")) is not None
        rows = tmp_db.execute("SELECT session_id, agent FROM error_records ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [("pi:abc", "pi"), ("claude:bare-legacy", "claude")]

    def test_null_tool_name_still_dedupes(self, tmp_db):
        assert insert_error_record(tmp_db, _rec("pi:abc", tool_name=None, error_type=None)) is not None
        assert insert_error_record(tmp_db, _rec("pi:abc", tool_name=None, error_type=None)) is None

    def test_explicit_agent_must_match_session_id(self, tmp_db):
        with pytest.raises(ValueError, match="does not match"):
            insert_error_record(tmp_db, _rec("pi:abc", agent="claude"))
        # a bare id is namespaced under the agent the writer names
        insert_error_record(tmp_db, _rec("xyz", agent="pi"))
        assert tmp_db.execute("SELECT session_id, agent FROM error_records").fetchone()[0] == "pi:xyz"

    def test_trigger_derives_agent_for_raw_sql_writers(self, tmp_db):
        tmp_db.execute(
            "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
            "error_text, mined_at) VALUES ('kimi:s1', 't', 'x', 'f', 'e', 'm')"
        )
        tmp_db.execute(
            "INSERT INTO processed_sessions (file_path, file_hash, mined_at) "
            "VALUES ('/home/u/.claude/projects/p/s.jsonl', 'h', 'm')"
        )
        assert tmp_db.execute("SELECT agent FROM error_records").fetchone()[0] == "kimi"
        assert tmp_db.execute("SELECT agent FROM processed_sessions").fetchone()[0] == "claude"


class TestViewsAndQueries:
    def test_views_return_only_their_agent(self, tmp_db):
        insert_error_record(tmp_db, _rec("pi:a"))
        insert_error_record(tmp_db, _rec("codex:b"))
        insert_error_record(tmp_db, _rec("claude:c"))
        assert [r[0] for r in tmp_db.execute("SELECT session_id FROM errors_pi")] == ["pi:a"]
        assert [r[0] for r in tmp_db.execute("SELECT session_id FROM errors_codex")] == ["codex:b"]
        assert tmp_db.execute("SELECT COUNT(*) FROM errors_goose").fetchone()[0] == 0

    def test_claude_filter_excludes_a_newly_added_agent(self, monkeypatch):
        from sio.core import session_handle as sh

        monkeypatch.setattr(sh, "KNOWN_AGENTS", (*sh.KNOWN_AGENTS, "zeta"))
        conn = init_db(":memory:")  # bootstrap picks the new agent up: view + trigger arm
        try:
            insert_error_record(conn, _rec("zeta:z1"))
            insert_error_record(conn, _rec("claude:c1"))
            claude_ids = [r["session_id"] for r in get_error_records(conn, agent="claude")]
            assert claude_ids == ["claude:c1"]
            assert [r[0] for r in conn.execute("SELECT session_id FROM errors_zeta")] == ["zeta:z1"]
            assert conn.execute(
                "SELECT agent FROM error_records WHERE session_id='zeta:z1'"
            ).fetchone()[0] == "zeta"
        finally:
            conn.close()

    def test_view_names_must_be_identifiers(self, monkeypatch):
        from sio.core import session_handle as sh

        monkeypatch.setattr(sh, "KNOWN_AGENTS", (*sh.KNOWN_AGENTS, "bad-name; DROP"))
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            init_db(":memory:")


# ---------------------------------------------------------------------------
# drop-agent
# ---------------------------------------------------------------------------


class TestDropAgent:
    def test_dry_run_counts_and_changes_nothing(self, legacy_db: Path):
        ag.migrate_006_agent_isolation(legacy_db)
        before = _q(legacy_db, "SELECT COUNT(*) FROM error_records")[0][0]
        report = ag.drop_agent(legacy_db, "pi")
        assert report["executed"] is False
        assert report["counts"]["error_records"] == 3
        assert report["counts"]["pattern_errors"] == 1  # the (1, 5) link
        assert _q(legacy_db, "SELECT COUNT(*) FROM error_records")[0][0] == before
        assert not list((legacy_db.parent / "backups").glob("*pre-drop*"))

    def test_execute_deletes_only_that_agent_and_backs_up(self, legacy_db: Path):
        ag.migrate_006_agent_isolation(legacy_db)
        report = ag.drop_agent(legacy_db, "pi", execute=True)
        assert report["executed"] is True
        assert "pre-drop-pi" in report["backup"]
        assert _q(legacy_db, "SELECT COUNT(*) FROM error_records WHERE agent='pi'")[0][0] == 0
        assert _q(legacy_db, "SELECT COUNT(*) FROM error_records WHERE agent='claude'")[0][0] == 3
        assert _q(legacy_db, "SELECT error_id FROM pattern_errors") == [(1,)]
        assert _q(legacy_db, "SELECT COUNT(*) FROM flow_events")[0][0] == 1  # claude's, untouched

    def test_claude_needs_the_explicit_flag(self, legacy_db: Path):
        with pytest.raises(ValueError, match="including-claude"):
            ag.drop_agent(legacy_db, "claude", execute=True)
        ag.migrate_006_agent_isolation(legacy_db)
        report = ag.drop_agent(legacy_db, "claude", execute=True, allow_claude=True)
        assert report["executed"] and _q(legacy_db, "SELECT COUNT(*) FROM errors_claude")[0][0] == 0

    def test_unknown_agent_refused(self, legacy_db: Path):
        with pytest.raises(ValueError, match="unknown agent"):
            ag.drop_agent(legacy_db, "nosuch")


class TestCli:
    def test_db_drop_agent_dry_run_then_yes(self, legacy_db: Path, monkeypatch, tmp_path):
        from sio.cli.main import cli

        monkeypatch.setenv("SIO_DB_PATH", str(legacy_db))
        monkeypatch.setenv("HOME", str(tmp_path))
        ag.migrate_006_agent_isolation(legacy_db)
        runner = CliRunner()
        dry = runner.invoke(cli, ["db", "drop-agent", "pi"])
        assert dry.exit_code == 0, dry.output
        assert "DRY RUN" in dry.output and "error_records" in dry.output
        assert _q(legacy_db, "SELECT COUNT(*) FROM errors_pi")[0][0] == 3

        refused = runner.invoke(cli, ["db", "drop-agent", "claude", "--yes"])
        assert refused.exit_code == 2 and "including-claude" in refused.output

        unknown = runner.invoke(cli, ["db", "drop-agent", "nosuch", "--yes"])
        assert unknown.exit_code == 2 and "unknown agent" in unknown.output

        done = runner.invoke(cli, ["db", "drop-agent", "pi", "--yes"])
        assert done.exit_code == 0, done.output
        assert "deleted" in done.output and "backup:" in done.output
        assert _q(legacy_db, "SELECT COUNT(*) FROM errors_pi")[0][0] == 0

    def test_db_migrate_runs_006_and_reports(self, legacy_db: Path, monkeypatch, tmp_path):
        from sio.cli.main import cli

        monkeypatch.setenv("SIO_DB_PATH", str(legacy_db))
        monkeypatch.setenv("HOME", str(tmp_path))
        result = CliRunner().invoke(cli, ["db", "migrate", "--db-path", str(legacy_db)])
        assert result.exit_code == 0, result.output
        assert "agent migration (006) applied" in result.output
        assert _q(legacy_db, "SELECT status FROM schema_version WHERE version=6") == [("applied",)]

    def test_errors_agent_option(self, tmp_path: Path, monkeypatch):
        from sio.cli.main import cli

        db = tmp_path / "sio.db"
        conn = init_db(str(db))
        insert_error_record(conn, _rec("pi:a", error_text="pi-only-error"))
        insert_error_record(conn, _rec("claude:c", error_text="claude-only-error"))
        conn.close()
        monkeypatch.setenv("SIO_DB_PATH", str(db))
        monkeypatch.setenv("HOME", str(tmp_path))
        out = CliRunner().invoke(cli, ["errors", "--agent", "pi"]).output
        assert "pi-only-error" in out and "claude-only-error" not in out
