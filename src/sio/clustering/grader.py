"""sio.clustering.grader — pattern lifecycle grading and auto-suggestion generation.

Implements FR-019 (pattern grading) and FR-020 (auto-generate suggestions
for strong patterns).

Public API
----------
    grade_pattern(pattern_row) -> str | None
    run_grading(db) -> list[dict]
    auto_generate_suggestions(db, strong_patterns) -> int

Grade lifecycle
---------------
    None       -> pattern below all thresholds
    emerging   -> 2+ occurrences across 2+ sessions
    strong     -> 3+ occurrences across 3+ sessions
    established-> 5+ occurrences, consistent over 7+ days
    declining  -> confidence (with decay) below 0.5
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from sio.core.config import SIOConfig, load_config
from sio.suggestions.confidence import _compute_decay_multiplier, score_confidence

logger = logging.getLogger(__name__)


def grade_pattern(
    pattern_row: dict | sqlite3.Row,
    config: SIOConfig | None = None,
) -> str | None:
    """Assign a lifecycle grade to a pattern based on its statistics.

    Parameters
    ----------
    pattern_row:
        A dict or sqlite3.Row with keys: ``error_count``, ``session_count``,
        ``first_seen``, ``last_seen``, ``rank_score``.
    config:
        Optional SIOConfig for decay parameters.

    Returns
    -------
    str | None
        One of ``'declining'``, ``'established'``, ``'strong'``, ``'emerging'``,
        or ``None`` if the pattern does not meet any threshold.

    Grade precedence (checked in order):
    1. declining  — decay multiplier < 0.5 (pattern is aging out)
    2. established — error_count >= 5 AND span >= 7 days
    3. strong     — error_count >= 3 AND session_count >= 3
    4. emerging   — error_count >= 2 AND session_count >= 2
    5. None       — below all thresholds

    The declining check uses the temporal decay multiplier directly rather
    than the full ``score_confidence`` (which includes dataset coverage).
    This ensures that "declining" reflects temporal staleness, not missing
    training data.
    """
    if config is None:
        config = load_config()

    # Coerce sqlite3.Row to dict for uniform access
    row: dict = dict(pattern_row) if not isinstance(pattern_row, dict) else pattern_row

    error_count: int = int(row.get("error_count") or 0)
    session_count: int = int(row.get("session_count") or 0)
    first_seen: str = row.get("first_seen") or ""
    last_seen: str = row.get("last_seen") or ""

    # 1. Declining: decay multiplier below 0.5 means the pattern is aging out.
    # We use the decay multiplier directly (not full score_confidence) because
    # the full score includes dataset coverage which is irrelevant for staleness.
    if last_seen:
        decay = _compute_decay_multiplier(last_seen, config=config)
        if decay < 0.5:
            return "declining"

    # 2. Established: 5+ errors, consistent over 7+ days
    if error_count >= 5 and first_seen:
        try:
            dt_first = datetime.fromisoformat(first_seen)
            if dt_first.tzinfo is None:
                dt_first = dt_first.replace(tzinfo=timezone.utc)
            dt_now = datetime.now(timezone.utc)
            days_span = (dt_now - dt_first).total_seconds() / 86400.0
            if days_span >= 7:
                return "established"
        except ValueError:
            pass  # Unparseable date — skip established check

    # 3. Strong: 3+ errors across 3+ sessions
    if error_count >= 3 and session_count >= 3:
        return "strong"

    # 4. Emerging: 2+ errors across 2+ sessions
    if error_count >= 2 and session_count >= 2:
        return "emerging"

    return None


def run_grading(
    db: sqlite3.Connection,
    config: SIOConfig | None = None,
) -> list[dict]:
    """Grade all patterns in the database, updating the ``grade`` column.

    Parameters
    ----------
    db:
        An open sqlite3.Connection with the SIO schema.
    config:
        Optional SIOConfig for decay parameters.

    Returns
    -------
    list[dict]
        List of ``{pattern_id, old_grade, new_grade}`` for every pattern
        whose grade changed.
    """
    if config is None:
        config = load_config()

    rows = db.execute("SELECT * FROM patterns").fetchall()
    changes: list[dict] = []

    for row in rows:
        row_dict = dict(row)
        pattern_db_id: int = row_dict["id"]
        old_grade: str | None = row_dict.get("grade")
        new_grade = grade_pattern(row_dict, config=config)

        if new_grade != old_grade:
            db.execute(
                "UPDATE patterns SET grade = ? WHERE id = ?",
                (new_grade, pattern_db_id),
            )
            changes.append({
                "pattern_id": pattern_db_id,
                "old_grade": old_grade,
                "new_grade": new_grade,
            })

    db.commit()
    return changes


def auto_generate_suggestions(
    db: sqlite3.Connection,
    strong_patterns: list[dict],
) -> int:
    """Create pending suggestions for patterns newly promoted to 'strong'.

    For each pattern in *strong_patterns*, checks whether a suggestion already
    exists for that ``pattern_id`` (the integer DB row id stored in the
    ``suggestions.pattern_id`` column).  If not, inserts a new suggestion with
    ``status='pending'``.

    Parameters
    ----------
    db:
        An open sqlite3.Connection with the SIO schema.
    strong_patterns:
        List of dicts with at minimum ``pattern_id`` (int, the patterns.id
        value), ``description`` (str), and ``rank_score`` (float).

    Returns
    -------
    int
        Number of new suggestions created.
    """
    now = datetime.now(timezone.utc).isoformat()
    created = 0

    for pat in strong_patterns:
        pattern_db_id: int = pat["pattern_id"]

        # Check for existing suggestion
        existing = db.execute(
            "SELECT id FROM suggestions WHERE pattern_id = ?",
            (pattern_db_id,),
        ).fetchone()
        if existing is not None:
            continue

        # Compute confidence from pattern stats
        error_count = int(pat.get("error_count") or 0)
        rank_score = float(pat.get("rank_score") or 0.0)
        last_seen = pat.get("last_seen") or ""
        confidence = score_confidence(
            {"error_count": error_count, "rank_score": rank_score},
            {"positive_count": 0, "negative_count": 0},
            last_seen=last_seen if last_seen else None,
        )

        description = pat.get("description") or "Auto-generated from strong pattern"

        db.execute(
            "INSERT INTO suggestions "
            "(pattern_id, dataset_id, description, confidence, "
            "proposed_change, target_file, change_type, status, "
            "created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pattern_db_id,
                None,  # No dataset yet
                description,
                confidence,
                f"[Auto] Investigate and address: {description}",
                "CLAUDE.md",
                "claude_md_rule",
                "pending",
                now,
            ),
        )
        created += 1

    db.commit()
    return created
