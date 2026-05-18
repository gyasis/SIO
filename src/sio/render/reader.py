"""Load an optimized DSPy artifact + its DB metadata for rendering."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def load_artifact(artifact_path: Path) -> dict:
    """Load a DSPy compiled-module JSON. Returns {instruction, demos, fields}.

    Handles both shapes:
      - Full DSPy state dump: {'predict': {'signature': {'instructions': ...,
        'fields': [...]}, 'demos': [...]}}
      - Minimal fallback: {'module': repr(...)}  (no useful content extractable)
    """
    data = json.loads(artifact_path.read_text())

    # Full DSPy state dump path
    predict = data.get("predict")
    if isinstance(predict, dict):
        signature = predict.get("signature", {})
        return {
            "instruction": signature.get("instructions", ""),
            "fields": signature.get("fields", []),
            "demos": predict.get("demos", []),
            "shape": "dspy_full",
        }

    # Minimal fallback
    return {
        "instruction": "",
        "fields": [],
        "demos": [],
        "shape": "minimal",
        "raw_repr": data.get("module", ""),
    }


def load_module_metadata(module_id: int, db_path: str | None = None) -> dict:
    """Read optimized_modules row + return as dict."""
    if db_path is None:
        db_path = os.path.expanduser("~/.sio/sio.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM optimized_modules WHERE id = ?", (module_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No optimized_modules row with id={module_id}")
        return dict(row)
    finally:
        conn.close()


def find_active_module(module_type: str = "suggestion_generator",
                       db_path: str | None = None) -> int:
    """Return the id of the currently-active module of the given type."""
    if db_path is None:
        db_path = os.path.expanduser("~/.sio/sio.db")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM optimized_modules "
            "WHERE module_type = ? AND is_active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (module_type,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No active module of type '{module_type}'")
        return row[0]
    finally:
        conn.close()
