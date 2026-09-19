"""scripts/migrate_004.py — thin CLI shim over sio.core.db.migrate_004.

The migration logic used to live entirely in this file. It has moved into
the installed package (``sio.core.db.migrate_004``) so it is importable
regardless of how ``sio`` was installed — ``scripts/`` is a dev-repo-only
directory, not bundled into the wheel (see ``[tool.hatch.build.targets.wheel]``
in ``pyproject.toml``), so ``from scripts.migrate_004 import migrate`` only
ever resolved when the current working directory happened to be the repo
root. On every other install layout ``sio.core.db.bootstrap`` caught the
resulting ``ModuleNotFoundError`` and logged it at DEBUG — invisible by
default — so the 004 columns (``pattern_errors.active``, ``patterns.active``,
``datasets.active``, ``suggestions.active``, etc.) silently never landed on
a fresh ``~/.sio/sio.db``, and anything that queried them (e.g. ``sio
trend``) crashed with ``sqlite3.OperationalError: no such column``.

This file is kept only so:
  - ``python -m scripts.migrate_004 <db_path>`` / ``python scripts/migrate_004.py
    <db_path>`` keep working as documented, repo-local CLI entry points.
  - Anything that loads this file directly by path (e.g.
    ``tests/unit/db/test_migration_004.py``, which uses
    ``importlib.util.spec_from_file_location``) keeps seeing a ``migrate``
    attribute.

Usage:
    python -m scripts.migrate_004 ~/.sio/sio.db        # apply to a DB
    python scripts/migrate_004.py ~/.sio/sio.db        # direct invocation
"""
from __future__ import annotations

from sio.core.db.migrate_004 import main, migrate, migrate_004  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
