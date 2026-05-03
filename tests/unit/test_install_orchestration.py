"""Regression tests for the install-orchestration restoration.

Covers the six gaps documented in
``prds/prd-install-orchestration-regression.md`` that landed across
commits ``8defd49`` (lifecycle hooks), ``b25e851`` (hook registration),
``462d549`` (canonical + per-platform DB bootstrap), and ``08b22da``
(platform_config metadata write).

Plus the PR #1 regression test (``cycle_id`` column on ``datasets``
and ``suggestions`` after ``init_db``) since it shares the same
schema-bootstrap path.

Each test isolates SIO state via ``SIO_HOME``/``SIO_DB_PATH`` env
vars pointing at ``tmp_path``, so nothing the user has installed
under ``~/.sio/`` is touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from sio.harnesses.claude_code import ClaudeCodeAdapter


# ---------------------------------------------------------------------------
# Schema-bootstrap fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_sio_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SIO_HOME + SIO_DB_PATH at tmp_path so tests don't touch ~/.sio/."""
    sio_home = tmp_path / "sio_home"
    sio_home.mkdir()
    monkeypatch.setenv("SIO_HOME", str(sio_home))
    monkeypatch.setenv("SIO_DB_PATH", str(sio_home / "sio.db"))
    return sio_home


@pytest.fixture
def isolated_claude(tmp_path: Path) -> Path:
    claude = tmp_path / "claude"
    claude.mkdir()
    return claude


# ---------------------------------------------------------------------------
# Gap 1: hooks registered in settings.json
# ---------------------------------------------------------------------------


class TestHookRegistration:
    def test_post_install_creates_settings_json_with_5_hooks(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()  # need per-platform DB for post_install platform_config write
        report = adapter.post_install()

        settings_path = isolated_claude / "settings.json"
        assert settings_path.exists(), "post_install should create settings.json"
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})
        assert set(hooks) == {
            "PostToolUse",
            "PreCompact",
            "Stop",
            "UserPromptSubmit",
            "SessionStart",
        }, f"all 5 SIO hook events must be registered, got: {sorted(hooks)}"
        # report should record at least one create per hook + the platform_config
        creates = [c for c in report.changes if c.action == "create"]
        assert len(creates) == 5, f"expected 5 hook creates, got {len(creates)}"

    def test_post_install_is_idempotent(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()
        adapter.post_install()  # first run

        report = adapter.post_install()  # second run
        # No new "create" actions on the settings.json — should be all skips
        creates = [
            c for c in report.changes
            if c.action == "create" and "settings.json" in str(c.path)
        ]
        assert creates == [], f"second run created hooks again: {creates}"

    def test_post_install_preserves_user_hooks(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        # User has their own hook for some event
        settings_path = isolated_claude / "settings.json"
        existing = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "/usr/local/bin/my-bash-watcher"},
                        ],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(existing))

        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()
        adapter.post_install()

        merged = json.loads(settings_path.read_text())
        post_tool_use = merged["hooks"]["PostToolUse"]
        commands = [
            inner["command"]
            for entry in post_tool_use
            for inner in entry.get("hooks", [])
        ]
        assert any("my-bash-watcher" in c for c in commands), \
            "user's existing hook was wiped out"
        assert any("sio.adapters.claude_code.hooks.post_tool_use" in c for c in commands), \
            "SIO hook was not added alongside user's"

    def test_post_install_migrates_legacy_bare_format(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        # Legacy bare-format hook — pre-wrapped Claude Code era
        settings_path = isolated_claude / "settings.json"
        legacy = {
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "/some/legacy-script"},
                ]
            }
        }
        settings_path.write_text(json.dumps(legacy))

        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()
        adapter.post_install()

        merged = json.loads(settings_path.read_text())
        for entry in merged["hooks"]["PostToolUse"]:
            assert "matcher" in entry, \
                "bare-format hook was not migrated to wrapped format"
            assert "hooks" in entry and isinstance(entry["hooks"], list)


# ---------------------------------------------------------------------------
# Gap 2: per-platform DB initialized in pre_install
# ---------------------------------------------------------------------------


class TestPerPlatformDB:
    def test_pre_install_creates_per_platform_db(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        per_platform_db = isolated_sio_home / adapter.name / "behavior_invocations.db"
        assert not per_platform_db.exists()

        adapter.pre_install()

        assert per_platform_db.exists(), "pre_install should create per-platform DB"
        # Should have all base tables (init_db ran)
        with sqlite3.connect(per_platform_db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "behavior_invocations" in tables
        assert "platform_config" in tables

    def test_pre_install_is_idempotent(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()
        # Second call must not raise (init_db is idempotent)
        report = adapter.pre_install()
        assert report.success


# ---------------------------------------------------------------------------
# Gap 3: schema_version baseline (canonical DB bootstrap)
# ---------------------------------------------------------------------------


class TestCanonicalDBBootstrap:
    def test_ensure_canonical_db_ready_seeds_schema_version(
        self, isolated_sio_home: Path
    ) -> None:
        from sio.core.db.bootstrap import ensure_canonical_db_ready

        canonical_db = ensure_canonical_db_ready()
        assert canonical_db.exists()

        with sqlite3.connect(canonical_db) as conn:
            row = conn.execute(
                "SELECT version, status FROM schema_version WHERE version=1"
            ).fetchone()
        assert row is not None, "schema_version baseline row was not seeded"
        assert row[1] == "applied"

    def test_ensure_canonical_db_ready_is_idempotent(
        self, isolated_sio_home: Path
    ) -> None:
        from sio.core.db.bootstrap import ensure_canonical_db_ready

        ensure_canonical_db_ready()
        # Second call must not duplicate or crash
        ensure_canonical_db_ready()


# ---------------------------------------------------------------------------
# Gap 4: platform_config row recorded by post_install
# ---------------------------------------------------------------------------


class TestPlatformConfig:
    def test_post_install_records_platform_config_row(
        self, isolated_claude: Path, isolated_sio_home: Path
    ) -> None:
        adapter = ClaudeCodeAdapter(config_dir=isolated_claude)
        adapter.pre_install()
        adapter.post_install()

        per_platform_db = isolated_sio_home / adapter.name / "behavior_invocations.db"
        with sqlite3.connect(per_platform_db) as conn:
            row = conn.execute(
                "SELECT platform, hooks_installed, skills_installed, "
                "config_updated, capability_tier "
                "FROM platform_config WHERE platform=?",
                (adapter.name,),
            ).fetchone()
        assert row is not None, "platform_config row was not written"
        assert row == (adapter.name, 1, 1, 1, 1), \
            f"unexpected platform_config row contents: {row}"


# ---------------------------------------------------------------------------
# PR #1 regression: cycle_id columns on datasets + suggestions
# ---------------------------------------------------------------------------


class TestCycleIdSchemaRegression:
    """The cycle_id columns were missing from _DATASETS_DDL and
    _SUGGESTIONS_DDL in v0.1.3, causing `sio suggest` to crash at
    Step 3 on every fresh install. PR #1 added them.

    These tests assert the columns exist after a single init_db()
    call so the bug cannot regress silently.
    """

    def _cols(self, db_path: Path, table: str) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def test_datasets_has_cycle_id(self, tmp_path: Path) -> None:
        from sio.core.db.schema import init_db

        db = tmp_path / "test.db"
        init_db(str(db))
        assert "cycle_id" in self._cols(db, "datasets")

    def test_suggestions_has_cycle_id(self, tmp_path: Path) -> None:
        from sio.core.db.schema import init_db

        db = tmp_path / "test.db"
        init_db(str(db))
        assert "cycle_id" in self._cols(db, "suggestions")

    def test_init_db_idempotent_for_pre_fix_dbs(self, tmp_path: Path) -> None:
        """A DB created before the cycle_id fix won't have the column;
        init_db should ALTER TABLE to add it without crashing on
        subsequent calls. Simulates the upgrade path."""
        from sio.core.db.schema import init_db

        db = tmp_path / "test.db"

        # Simulate a pre-fix DB: create datasets table without cycle_id
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE datasets ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "pattern_id INTEGER, "
                "file_path TEXT NOT NULL, "
                "positive_count INTEGER NOT NULL, "
                "negative_count INTEGER NOT NULL, "
                "min_threshold INTEGER NOT NULL DEFAULT 5, "
                "lineage_sessions TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL"
                ")"
            )

        # init_db should ALTER the existing table without raising
        init_db(str(db))
        assert "cycle_id" in self._cols(db, "datasets")

        # And running again on the now-migrated DB shouldn't crash on
        # "duplicate column name"
        init_db(str(db))
        assert "cycle_id" in self._cols(db, "datasets")
