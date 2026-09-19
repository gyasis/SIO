"""Regression tests for the migrate_004-lives-in-scripts/ bug.

Origin: `sio.core.db.bootstrap.ensure_canonical_db_ready()` used to do
``from scripts.migrate_004 import migrate as migrate_004`` inside a bare
``except Exception: logger.debug(...)``. ``scripts/`` is a dev-repo-only
directory — it is NOT shipped in the wheel (see
``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml``, which only
force-includes ``skills``/``rules``/``docs``) — so on any install where the
current working directory wasn't the repo root, that import raised
``ModuleNotFoundError`` and was silently swallowed. The 004 columns
(``pattern_errors.active``, ``patterns.active``, ``datasets.active``,
``suggestions.active``, plus the rest of the 004 delta) then never landed on
a fresh ``~/.sio/sio.db``, and any query assuming them — e.g. ``sio trend``'s
``JOIN pattern_errors pe ... AND pe.active = 1`` — crashed with
``sqlite3.OperationalError: no such column: pe.active``.

The fix moves the migration into the package (``sio.core.db.migrate_004``),
so it can no longer fail to import for that reason, and makes any *other*
failure loud (WARNING, not DEBUG) instead of silent. These tests exercise
the migration through the exact path the CLI uses
(``ensure_canonical_db_ready`` -> ``sio trend``), and guard against the
``scripts.migrate_004`` import creeping back into ``bootstrap.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO_ROOT = Path(__file__).parents[3]
_BOOTSTRAP_SRC = (
    _REPO_ROOT / "src" / "sio" / "core" / "db" / "bootstrap.py"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _runs_dir_in_tmp(tmp_path, monkeypatch):
    """Keep `runlogged` run-logs (and anything else keyed off ~/.sio) out of
    the real ~/.sio — see tests/unit/db/test_agent_isolation.py for why a
    HOME monkeypatch alone isn't enough for the runlog writer.
    """
    from sio.core.runlog import writer as _w

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(_w, "_RUNS_DIR", runs)


@pytest.fixture()
def env_db(tmp_path, monkeypatch):
    db = tmp_path / "sio.db"
    monkeypatch.setenv("SIO_HOME", str(tmp_path))
    monkeypatch.setenv("SIO_DB_PATH", str(db))
    return db


def _run(args):
    from sio.cli.main import cli  # noqa: PLC0415

    return CliRunner().invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# 1. bootstrap.py must not import migrate_004 from scripts/ anymore
# ---------------------------------------------------------------------------


def test_bootstrap_does_not_import_scripts_migrate_004():
    """The whole bug was this exact import statement — guard against it
    coming back. (The docstring above it is allowed to still MENTION
    ``scripts.migrate_004`` in prose, explaining the history — this checks
    for the actual import statement, not the string.)
    """
    assert "from scripts.migrate_004 import" not in _BOOTSTRAP_SRC
    assert "from scripts import migrate_004" not in _BOOTSTRAP_SRC
    assert "import scripts.migrate_004" not in _BOOTSTRAP_SRC
    assert "from sio.core.db.migrate_004 import migrate_004" in _BOOTSTRAP_SRC


def test_bootstrap_still_imports_split_brain_from_scripts():
    """Not a regression guard for THIS bug — just a sanity check that we
    only moved the 004 migration, not the (separately scoped) split-brain
    step, which still legitimately lives in scripts/.
    """
    assert "from scripts.migrate_split_brain import main" in _BOOTSTRAP_SRC


# ---------------------------------------------------------------------------
# 2. sio.core.db.migrate_004 is the real, importable-from-anywhere module
# ---------------------------------------------------------------------------


def test_migrate_004_importable_from_package():
    from sio.core.db.migrate_004 import main, migrate, migrate_004  # noqa: PLC0415

    assert callable(migrate_004)
    assert migrate is migrate_004, "back-compat `migrate` alias must be migrate_004 itself"
    assert callable(main)


def test_migrate_004_unimportable_when_scripts_is_absent(tmp_path, monkeypatch):
    """Simulate the exact production failure mode: `scripts` cannot be
    imported at all (as on a real installed wheel). `sio.core.db.migrate_004`
    must still work — it no longer depends on `scripts/` in any way.
    """
    monkeypatch.setitem(sys.modules, "scripts", None)  # forces ImportError on `import scripts*`
    with pytest.raises(ImportError):
        import scripts.migrate_004  # noqa: F401,PLC0415

    # The real migration module doesn't go through `scripts` at all.
    from sio.core.db.migrate_004 import migrate_004  # noqa: PLC0415
    from sio.core.db.schema import init_db  # noqa: PLC0415

    db = tmp_path / "sio.db"
    conn = init_db(str(db))  # the full canonical v1-v3 schema, pre-004
    conn.close()
    migrate_004(db)

    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pattern_errors)").fetchall()}
    conn.close()
    assert "active" in cols


# ---------------------------------------------------------------------------
# 3. ensure_canonical_db_ready() — the function `sio init` runs — leaves the
#    004 columns present on a genuinely fresh DB, with `scripts` unimportable
# ---------------------------------------------------------------------------


def test_ensure_canonical_db_ready_adds_004_columns_without_scripts(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "scripts", None)

    from sio.core.db.bootstrap import ensure_canonical_db_ready  # noqa: PLC0415

    db = tmp_path / "sio.db"
    result = ensure_canonical_db_ready(db)
    assert result == db
    assert db.exists()

    conn = sqlite3.connect(str(db))
    for table, column in [
        ("pattern_errors", "active"),
        ("patterns", "active"),
        ("datasets", "active"),
        ("suggestions", "active"),
    ]:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert column in cols, f"{table}.{column} missing after ensure_canonical_db_ready()"
    conn.close()


def test_ensure_canonical_db_ready_logs_migrate_004_failure_loudly(tmp_path, monkeypatch, caplog):
    """A GENUINE migrate_004 failure (not "scripts is missing") must be
    visible — WARNING, not DEBUG — and must not raise out of
    ensure_canonical_db_ready() (several CLI commands call it unwrapped).
    """
    import sio.core.db.bootstrap as bootstrap_mod

    def _boom(_db_path):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(
        "sio.core.db.migrate_004.migrate_004", _boom, raising=True
    )

    with caplog.at_level(logging.WARNING, logger=bootstrap_mod.logger.name):
        result = bootstrap_mod.ensure_canonical_db_ready(tmp_path / "sio.db")

    assert result == tmp_path / "sio.db"  # did not raise
    assert any(
        "migrate_004 failed" in rec.message and "disk exploded" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


# ---------------------------------------------------------------------------
# 4. Idempotency at the package level (mirrors the existing scripts/ shim
#    test, but against the real canonical schema produced by init_db()).
# ---------------------------------------------------------------------------


def test_migrate_004_idempotent_on_canonical_schema(tmp_path):
    from sio.core.db.migrate_004 import migrate_004  # noqa: PLC0415
    from sio.core.db.schema import init_db  # noqa: PLC0415

    db = tmp_path / "sio.db"
    conn = init_db(str(db))
    conn.close()

    migrate_004(db)
    migrate_004(db)  # second call must be a no-op, not an error

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 2"
    ).fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# 5. The end-to-end reproduction: `sio trend`, via the CLI, on a DB bootstrapped
#    the way `sio init` bootstraps it, must not crash with "no such column".
# ---------------------------------------------------------------------------


def test_sio_trend_does_not_crash_on_fresh_db(env_db, monkeypatch):
    monkeypatch.setitem(sys.modules, "scripts", None)  # the real-world install shape

    from sio.core.db.bootstrap import ensure_canonical_db_ready  # noqa: PLC0415

    ensure_canonical_db_ready(env_db)

    # A single error_records row inside the trend window is enough to reach
    # the `patterns JOIN pattern_errors pe ON ... pe.active = 1` query that
    # crashed pre-fix — see the module docstring.
    conn = sqlite3.connect(str(env_db))
    conn.execute(
        "INSERT INTO error_records "
        "(session_id, timestamp, source_type, source_file, error_text, mined_at) "
        "VALUES ('s1', datetime('now'), 'tool', 'f', 'boom', datetime('now'))"
    )
    conn.commit()
    conn.close()

    res = _run(["trend"])
    assert res.exit_code == 0, res.output
    assert "no such column" not in res.output.lower()
