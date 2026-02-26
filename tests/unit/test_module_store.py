"""T009: TDD tests for DSPy module persistence (save/load/activate).

Module under test: src/sio/core/dspy/module_store.py

These tests are intentionally RED until module_store.py is implemented.
Uses tmp_path for file storage and in-memory SQLite for DB operations.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store_conn() -> sqlite3.Connection:
    """In-memory DB with full SIO schema (including optimized_modules table)."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def mock_dspy_module():
    """A mock dspy.Module that can be saved/loaded."""
    mod = MagicMock()
    mod.save = MagicMock()
    mod.load = MagicMock(return_value=mod)
    return mod


# ---------------------------------------------------------------------------
# Tests: save_module
# ---------------------------------------------------------------------------


class TestSaveModule:
    """save_module() must persist the module to disk and record in DB."""

    def test_save_module_creates_file(self, store_conn, mock_dspy_module, tmp_path):
        from sio.core.dspy.module_store import save_module

        row_id = save_module(
            conn=store_conn,
            module=mock_dspy_module,
            module_type="suggestion",
            optimizer_used="BootstrapFewShot",
            training_count=50,
            metric_before=0.45,
            metric_after=0.78,
            store_dir=str(tmp_path),
        )
        # The module's save method should have been called with a path
        mock_dspy_module.save.assert_called_once()
        assert row_id is not None and row_id > 0

    def test_save_module_inserts_db_row(self, store_conn, mock_dspy_module, tmp_path):
        from sio.core.dspy.module_store import save_module

        save_module(
            conn=store_conn,
            module=mock_dspy_module,
            module_type="suggestion",
            optimizer_used="BootstrapFewShot",
            training_count=50,
            metric_before=0.45,
            metric_after=0.78,
            store_dir=str(tmp_path),
        )
        row = store_conn.execute(
            "SELECT module_type, optimizer_used, training_count, metric_before, metric_after, is_active "
            "FROM optimized_modules WHERE module_type = 'suggestion'"
        ).fetchone()
        assert row is not None, "No row inserted into optimized_modules"
        # Access by index since row_factory may be sqlite3.Row
        assert row[0] == "suggestion"       # module_type
        assert row[1] == "BootstrapFewShot"  # optimizer_used
        assert row[2] == 50                  # training_count
        assert row[4] == pytest.approx(0.78) # metric_after


# ---------------------------------------------------------------------------
# Tests: get_active_module / deactivate_previous
# ---------------------------------------------------------------------------


class TestGetActiveModule:
    """get_active_module() returns the latest active row or None."""

    def test_get_active_module(self, store_conn, mock_dspy_module, tmp_path):
        from sio.core.dspy.module_store import get_active_module, save_module

        save_module(
            conn=store_conn,
            module=mock_dspy_module,
            module_type="suggestion",
            optimizer_used="BootstrapFewShot",
            training_count=50,
            metric_before=0.45,
            metric_after=0.78,
            store_dir=str(tmp_path),
        )
        active = get_active_module(store_conn, "suggestion")
        assert active is not None
        assert active["module_type"] == "suggestion"
        assert active["is_active"] == 1

    def test_get_active_module_returns_none(self, store_conn):
        from sio.core.dspy.module_store import get_active_module

        active = get_active_module(store_conn, "suggestion")
        assert active is None


class TestDeactivatePrevious:
    """deactivate_previous() must set is_active=0 on old modules of that type."""

    def test_deactivate_previous(self, store_conn, mock_dspy_module, tmp_path):
        from sio.core.dspy.module_store import (
            deactivate_previous,
            save_module,
        )

        # Save two modules of the same type
        save_module(
            conn=store_conn,
            module=mock_dspy_module,
            module_type="suggestion",
            optimizer_used="BootstrapFewShot",
            training_count=30,
            metric_before=0.40,
            metric_after=0.65,
            store_dir=str(tmp_path),
        )
        save_module(
            conn=store_conn,
            module=mock_dspy_module,
            module_type="suggestion",
            optimizer_used="MIPROv2",
            training_count=60,
            metric_before=0.65,
            metric_after=0.85,
            store_dir=str(tmp_path),
        )

        # After two saves: save_module #2 already deactivated #1.
        # Verify #1 is inactive, #2 is active.
        rows = store_conn.execute(
            "SELECT is_active FROM optimized_modules WHERE module_type = 'suggestion' ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 0, "First save deactivated by second save"
        assert rows[1][0] == 1, "Second save is active"

        # Now explicitly deactivate all — both should be 0
        deactivate_previous(store_conn, "suggestion")
        rows = store_conn.execute(
            "SELECT is_active FROM optimized_modules WHERE module_type = 'suggestion' ORDER BY id"
        ).fetchall()
        assert rows[0][0] == 0
        assert rows[1][0] == 0, "deactivate_previous sets all to inactive"


# ---------------------------------------------------------------------------
# Tests: load_module
# ---------------------------------------------------------------------------


class TestLoadModule:
    """load_module() loads a dspy.Module from a saved JSON file."""

    def test_load_module_from_file(self, tmp_path):
        from sio.core.dspy.module_store import load_module

        # Create a mock module class with a load classmethod/staticmethod
        MockModuleClass = MagicMock()
        mock_instance = MagicMock()
        MockModuleClass.return_value = mock_instance

        # Write a placeholder file so the path exists
        module_file = tmp_path / "suggestion_v1.json"
        module_file.write_text(json.dumps({"type": "suggestion"}))

        result = load_module(MockModuleClass, str(module_file))
        assert result is not None
