"""sio.ground_truth.generator -- Generate ground truth candidates via DSPy.

Public API
----------
    generate_candidates(pattern, dataset, conn, config, n_candidates=3) -> list[int]
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def generate_candidates(
    pattern: dict[str, Any],
    dataset: dict[str, Any],
    conn: sqlite3.Connection,
    config: Any,
    n_candidates: int = 3,
) -> list[int]:
    """Generate N ground truth candidates for a pattern using DSPy.

    Calls ``GroundTruthModule.forward()`` *n_candidates* times, inserting each
    result into the ``ground_truth`` table with ``label='pending'`` and
    ``source='agent'``.

    Args:
        pattern: Pattern dict with ``id``, ``pattern_id``, ``description``,
            ``tool_name``, ``error_count``, ``session_count``.
        dataset: Dataset metadata dict with ``id``, ``file_path``.
        conn: SQLite connection with SIO schema.
        config: ``SIOConfig`` instance with LLM settings.
        n_candidates: Number of candidates to generate per pattern.

    Returns:
        List of inserted ground_truth row IDs.
    """
    import dspy

    from sio.core.db.queries import insert_ground_truth
    from sio.core.dspy.lm_factory import create_lm
    from sio.core.dspy.modules import GroundTruthModule
    from sio.suggestions.dspy_generator import _sanitize_examples, _truncate_fields

    lm = create_lm(config)
    if lm is None:
        logger.warning("No LLM backend available; skipping ground truth generation.")
        return []

    dspy.configure(lm=lm)

    # Prepare inputs
    examples = _load_dataset_examples(dataset)
    examples_json = json.dumps(examples[:20], default=str)
    examples_json = _sanitize_examples(examples_json)
    examples_json = _truncate_fields(examples_json, max_chars=2000)

    error_type = pattern.get("error_type") or "unknown"
    pattern_summary = (
        f"Tool: {pattern.get('tool_name', 'unknown')}. "
        f"{pattern.get('description', 'Recurring error pattern')}. "
        f"{pattern.get('error_count', 0)} errors across "
        f"{pattern.get('session_count', 0)} sessions."
    )
    pattern_summary = _truncate_fields(pattern_summary, max_chars=500)

    pattern_id_str = str(pattern.get("pattern_id", pattern.get("id", "unknown")))

    module = GroundTruthModule()
    row_ids: list[int] = []

    for i in range(n_candidates):
        try:
            result = module.forward(
                error_examples=examples_json,
                error_type=error_type,
                pattern_summary=pattern_summary,
            )
        except Exception:
            logger.exception("GroundTruthModule call %d failed", i)
            continue

        # Extract fields from DSPy result
        target_surface = _normalize_surface(
            getattr(result, "target_surface", "claude_md_rule")
        )
        rule_title = getattr(result, "rule_title", "Improvement suggestion")
        prevention_instructions = getattr(
            result, "prevention_instructions", "Review the error pattern."
        )
        rationale = getattr(result, "rationale", "Based on observed error patterns.")

        row_id = insert_ground_truth(
            conn,
            pattern_id=pattern_id_str,
            error_examples_json=examples_json,
            error_type=error_type,
            pattern_summary=pattern_summary,
            target_surface=target_surface,
            rule_title=rule_title,
            prevention_instructions=prevention_instructions,
            rationale=rationale,
            source="agent",
            confidence=None,
            file_path=dataset.get("file_path"),
        )
        row_ids.append(row_id)
        logger.info("Inserted ground truth candidate %d (row %d)", i, row_id)

    return row_ids


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_SURFACES = frozenset({
    "claude_md_rule", "skill_update", "hook_config",
    "mcp_config", "settings_config", "agent_profile", "project_config",
})


def _normalize_surface(raw_surface: str) -> str:
    """Normalize a DSPy-returned target_surface to a valid value."""
    cleaned = raw_surface.strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in _VALID_SURFACES:
        return cleaned
    for valid in _VALID_SURFACES:
        if valid in cleaned or cleaned in valid:
            return valid
    return "claude_md_rule"


def _load_dataset_examples(dataset: dict) -> list[dict]:
    """Load examples from a dataset's JSON file."""
    from pathlib import Path

    file_path = dataset.get("file_path")
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("examples", [])
    except (json.JSONDecodeError, OSError):
        return []
