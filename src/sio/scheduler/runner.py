"""sio.scheduler.runner — orchestrator for passive background analysis.

Public API
----------
    run_analysis(mode: str = "daily", db_path: str = None) -> dict

Orchestrates the full analysis pipeline:

    mine → cluster → build datasets → generate suggestions → write home file

Modes
-----
    "daily"  — mine the last 24 hours
    "weekly" — mine the last 7 days, full re-analysis
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.expanduser("~/.sio/sio.db")
_HOME_FILE_PATH = os.path.expanduser("~/.sio/suggestions.md")


def run_analysis(
    mode: str = "daily",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Run the passive background analysis pipeline.

    Parameters
    ----------
    mode:
        "daily"  — mines errors from the last 24 hours.
        "weekly" — mines errors from the last 7 days.
    db_path:
        Path to the SIO SQLite database.  Defaults to ``~/.sio/sio.db``.

    Returns
    -------
    dict
        Summary with keys: ``mode``, ``errors_found``, ``patterns_found``,
        ``datasets_built``, ``suggestions_generated``, ``home_file``.
    """
    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import get_error_records
    from sio.core.db.schema import init_db
    from sio.datasets.builder import build_dataset
    from sio.mining.pipeline import run_mine
    from sio.suggestions.generator import generate_suggestions
    from sio.suggestions.home_file import write_suggestions

    # --- Resolve DB path ----------------------------------------------------
    resolved_db_path = db_path or _DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(resolved_db_path), exist_ok=True)
    conn = init_db(resolved_db_path)

    try:
        # --- Determine time window ------------------------------------------
        since = "24 hours" if mode == "daily" else "7 days"

        # --- Mine -----------------------------------------------------------
        source_dirs: list[Path] = []
        specstory_dir = Path(os.path.expanduser("~/.specstory/history"))
        jsonl_dir = Path(os.path.expanduser("~/.claude/projects"))
        if specstory_dir.exists():
            source_dirs.append(specstory_dir)
        if jsonl_dir.exists():
            source_dirs.append(jsonl_dir)

        mine_result: dict[str, Any] = {"errors_found": 0, "error_records": []}
        if source_dirs:
            try:
                mine_result = run_mine(conn, source_dirs, since, "both", None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mining step failed: %s", exc)

        # --- Cluster --------------------------------------------------------
        all_errors = get_error_records(conn)
        patterns: list[dict[str, Any]] = []
        if all_errors:
            try:
                clustered = cluster_errors(all_errors)
                patterns = rank_patterns(clustered)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Clustering step failed: %s", exc)

        # --- Build datasets -------------------------------------------------
        datasets: dict[str, dict[str, Any]] = {}
        if patterns:
            for pattern in patterns:
                try:
                    metadata = build_dataset(pattern, all_errors, conn)
                    if metadata is not None:
                        pid: str = metadata["pattern_id"]
                        # Augment with the DB row id for the suggestion generator.
                        # build_dataset does not store a DB row by itself; we use
                        # the pattern's numeric id as a stable stand-in so that
                        # the generator can reference it.
                        row = conn.execute(
                            "SELECT id FROM datasets"
                            " WHERE pattern_id = ?"
                            " ORDER BY id DESC LIMIT 1",
                            (pattern["id"],),
                        ).fetchone()
                        metadata["id"] = row[0] if row else pattern["id"]
                        metadata["pattern_row_id"] = pattern["id"]
                        datasets[pid] = metadata
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Dataset build failed for pattern %s: %s",
                        pattern.get("pattern_id"),
                        exc,
                    )

        # --- Generate suggestions -------------------------------------------
        suggestions: list[dict[str, Any]] = []
        if patterns and datasets:
            try:
                suggestions = generate_suggestions(patterns, datasets, conn)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Suggestion generation failed: %s", exc)

        # --- Write home file ------------------------------------------------
        home_file = _HOME_FILE_PATH
        try:
            write_suggestions(suggestions, home_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home file write failed: %s", exc)
            home_file = ""

    finally:
        conn.close()

    return {
        "mode": mode,
        "errors_found": mine_result.get("errors_found", 0),
        "patterns_found": len(patterns),
        "datasets_built": len(datasets),
        "suggestions_generated": len(suggestions),
        "home_file": home_file,
    }
