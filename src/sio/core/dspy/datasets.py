"""Training-example factory for DSPy modules (FR-036, SC-020).

Every function returns ``list[dspy.Example]`` with ``.with_inputs()`` called.
Raw dicts or tuples MUST NOT reach a DSPy teleprompter (SC-020 invariant).

Public API
----------
    build_trainset_for(module_name, limit, offset, db_path) -> list[dspy.Example]
    load_gold_standards(task_type, limit, offset, db_path)   -> list[Row]
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import dspy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DSPy 3.1.3 compatibility: dspy.Example uses _input_keys / inputs(),
# but older contracts and tests reference get_input_keys().
# Add a shim so tests pass without modifying the test files.
# ---------------------------------------------------------------------------

if not hasattr(dspy.Example, "get_input_keys"):

    def _get_input_keys(self):  # type: ignore[no-untyped-def]
        return getattr(self, "_input_keys", set())

    dspy.Example.get_input_keys = _get_input_keys  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Module registry — maps module_name -> builder function
# ---------------------------------------------------------------------------

_MODULE_BUILDERS: dict[str, Any] = {}  # populated after function defs


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    return os.environ.get(
        "SIO_DB_PATH",
        str(Path.home() / ".sio" / "sio.db"),
    )


def load_gold_standards(
    task_type: str = "suggestion",
    limit: int = 500,
    offset: int = 0,
    db_path: str | None = None,
) -> list[Any]:
    """Load rows from the gold_standards table.

    Returns a list of sqlite3.Row-like objects with attributes:
      pattern_description, example_errors, project_context,
      gold_rule_title, gold_rule_body, gold_rule_rationale,
      gold_rule, candidate_rule
    """
    from types import SimpleNamespace  # noqa: PLC0415

    db = db_path or _default_db_path()
    if not Path(db).exists():
        logger.warning("gold_standards DB not found at %s — returning empty trainset", db)
        return []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM gold_standards "
            "WHERE task_type = ? "
            "ORDER BY id "
            "LIMIT ? OFFSET ?",
            (task_type, limit, offset),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.warning("gold_standards query failed: %s", exc)
        return []
    finally:
        conn.close()

    result = []
    for row in rows:
        # Parse dspy_example_json if available
        example_json: dict = {}
        raw = row["dspy_example_json"] if "dspy_example_json" in row.keys() else None
        if raw:
            try:
                example_json = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                example_json = {}

        data = example_json.get("data", {})

        # Normalise example_errors — may be stored as JSON array string
        raw_errors = (
            data.get("example_errors")
            or row["example_errors"] if "example_errors" in row.keys() else None
        )
        if isinstance(raw_errors, str):
            try:
                raw_errors = json.loads(raw_errors)
            except (json.JSONDecodeError, TypeError):
                raw_errors = [raw_errors]
        if raw_errors is None:
            raw_errors = []

        ns = SimpleNamespace(
            pattern_description=(
                data.get("pattern_description")
                or (row["user_message"] if "user_message" in row.keys() else "")
            ),
            example_errors=raw_errors,
            project_context=(
                data.get("project_context")
                or (row["platform"] if "platform" in row.keys() else "")
            ),
            gold_rule_title=(
                data.get("rule_title")
                or (row["expected_action"] if "expected_action" in row.keys() else "")
            ),
            gold_rule_body=(
                data.get("rule_body")
                or ""
            ),
            gold_rule_rationale=(
                data.get("rule_rationale")
                or ""
            ),
            gold_rule=(
                data.get("rule_body")
                or (row["expected_action"] if "expected_action" in row.keys() else "")
            ),
            candidate_rule=(
                data.get("rule_body")
                or (row["expected_action"] if "expected_action" in row.keys() else "")
            ),
        )
        result.append(ns)

    return result


# ---------------------------------------------------------------------------
# Per-module trainset builders
# ---------------------------------------------------------------------------

def _build_suggestion_generator(
    limit: int = 500,
    offset: int = 0,
    db_path: str | None = None,
) -> list[dspy.Example]:
    """Build trainset for SuggestionGenerator (PatternToRule).

    Input keys: pattern_description, example_errors, project_context
    Output keys: rule_title, rule_body, rule_rationale
    """
    rows = load_gold_standards(
        task_type="suggestion",
        limit=limit,
        offset=offset,
        db_path=db_path,
    )
    examples = []
    for r in rows:
        ex = dspy.Example(
            pattern_description=r.pattern_description,
            example_errors=r.example_errors,
            project_context=r.project_context,
            rule_title=r.gold_rule_title,
            rule_body=r.gold_rule_body,
            rule_rationale=r.gold_rule_rationale,
        ).with_inputs("pattern_description", "example_errors", "project_context")
        examples.append(ex)
    return examples


def _build_recall_evaluator(
    limit: int = 500,
    offset: int = 0,
    db_path: str | None = None,
) -> list[dspy.Example]:
    """Build trainset for RecallEvaluator (RuleRecallScore).

    Input keys: gold_rule, candidate_rule
    Output keys: score, reasoning
    """
    rows = load_gold_standards(
        task_type="suggestion",
        limit=limit,
        offset=offset,
        db_path=db_path,
    )
    examples = []
    for r in rows:
        ex = dspy.Example(
            gold_rule=r.gold_rule,
            candidate_rule=r.candidate_rule,
            score=1.0,       # gold pair is always a perfect match by definition
            reasoning="Gold-standard pair from human-validated training set.",
        ).with_inputs("gold_rule", "candidate_rule")
        examples.append(ex)
    return examples


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_MODULE_BUILDERS = {
    "suggestion_generator": _build_suggestion_generator,
    "recall_evaluator": _build_recall_evaluator,
}


def build_trainset_for(
    module_name: str,
    limit: int = 500,
    offset: int = 0,
    db_path: str | None = None,
) -> list[dspy.Example]:
    """Return a list of dspy.Example objects for ``module_name``.

    Every example has ``.with_inputs()`` called (SC-020 invariant).

    Args:
        module_name: One of the keys in MODULE_BUILDERS.
        limit: Maximum number of examples to return (0 = empty list).
        offset: Row offset for pagination.
        db_path: Path to sio.db (defaults to SIO_DB_PATH env / ~/.sio/sio.db).

    Raises:
        ValueError: If ``module_name`` is not a known module.
    """
    if module_name not in _MODULE_BUILDERS:
        raise ValueError(
            f"Unknown module name: {module_name!r}. "
            f"Known modules: {sorted(_MODULE_BUILDERS)}"
        )

    if limit == 0:
        return []

    builder = _MODULE_BUILDERS[module_name]
    return builder(limit=limit, offset=offset, db_path=db_path)
