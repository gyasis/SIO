"""Module store — save/load optimized DSPy modules to disk."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

_DEFAULT_STORE_DIR = os.path.expanduser("~/.sio/optimized")


def save_module(
    conn: sqlite3.Connection,
    module,  # dspy.Module
    module_type: str,
    optimizer_used: str,
    training_count: int,
    metric_before: float | None = None,
    metric_after: float | None = None,
    store_dir: str | None = None,
) -> int:
    """Save an optimized DSPy module to disk and record in DB.

    Args:
        conn: Database connection with optimized_modules table.
        module: The DSPy module to persist.
        module_type: Identifier for the module kind (e.g. 'suggestion', 'ground_truth').
        optimizer_used: Name of the DSPy optimizer (e.g. 'MIPROv2', 'BootstrapFewShot').
        training_count: Number of training examples used.
        metric_before: Metric score before optimization.
        metric_after: Metric score after optimization.
        store_dir: Directory to save JSON files. Defaults to ~/.sio/optimized.

    Returns:
        The database row ID of the new record.
    """
    # Deactivate previous active modules of this type
    deactivate_previous(conn, module_type)

    # Save module to JSON file
    if store_dir is None:
        store_dir = _DEFAULT_STORE_DIR
    os.makedirs(store_dir, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = f"{module_type}_{optimizer_used}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    file_path = os.path.join(store_dir, filename)
    module.save(file_path)

    # Record in DB
    cursor = conn.execute(
        "INSERT INTO optimized_modules "
        "(module_type, optimizer_used, file_path, training_count, "
        "metric_before, metric_after, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        (module_type, optimizer_used, file_path, training_count,
         metric_before, metric_after, now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def load_module(module_class, file_path: str):
    """Load an optimized DSPy module from disk.

    Args:
        module_class: The DSPy Module class to instantiate.
        file_path: Path to the saved JSON file.

    Returns:
        An instance of module_class with loaded state.
    """
    instance = module_class()
    instance.load(file_path)
    return instance


def get_active_module(conn: sqlite3.Connection, module_type: str) -> dict | None:
    """Get the currently active optimized module for a type.

    Args:
        conn: Database connection.
        module_type: The module type to look up.

    Returns:
        Dict of the active module row, or None if no active module exists.
    """
    row = conn.execute(
        "SELECT * FROM optimized_modules "
        "WHERE module_type = ? AND is_active = 1 "
        "ORDER BY created_at DESC LIMIT 1",
        (module_type,),
    ).fetchone()
    return dict(row) if row else None


def deactivate_previous(conn: sqlite3.Connection, module_type: str) -> None:
    """Deactivate all previous active modules of this type.

    Args:
        conn: Database connection.
        module_type: The module type to deactivate.
    """
    conn.execute(
        "UPDATE optimized_modules SET is_active = 0 "
        "WHERE module_type = ? AND is_active = 1",
        (module_type,),
    )
    conn.commit()
