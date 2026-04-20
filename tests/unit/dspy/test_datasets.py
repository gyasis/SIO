"""Failing tests for datasets.py — T029 (TDD red).

Tests assert (per contracts/dspy-module-api.md §4):
  1. build_trainset_for("suggestion_generator") returns list[dspy.Example]
  2. Every example has get_input_keys() non-empty
  3. suggestion_generator input keys == {pattern_description, example_errors, project_context}
  4. recall_evaluator input keys == {gold_rule, candidate_rule}
  5. limit=0 returns empty list
  6. Unknown module name raises ValueError

Run to confirm RED before T030:
    uv run pytest tests/unit/dspy/test_datasets.py -v
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


def _import_datasets():
    from sio.core.dspy import datasets  # noqa: PLC0415
    return datasets


def _make_mock_gold_row(**kwargs):
    """Return a SimpleNamespace mimicking a gold_standards DB row."""
    from types import SimpleNamespace  # noqa: PLC0415
    defaults = dict(
        pattern_description="Edit tool fails with duplicate string",
        example_errors=["string not unique", "edit failed: duplicate"],
        project_context="SIO project — Python tooling",
        gold_rule_title="Verify uniqueness before Edit",
        gold_rule_body="Always grep for the target string before editing.",
        gold_rule_rationale="Prevents 'not unique' errors in large files.",
        gold_rule="Always grep before edit.",
        candidate_rule="Check occurrences first.",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. build_trainset_for returns list[dspy.Example]
# ---------------------------------------------------------------------------

def test_build_trainset_for_suggestion_generator_returns_list(tmp_sio_db):
    """build_trainset_for('suggestion_generator') returns a list."""
    import dspy  # noqa: PLC0415
    ds = _import_datasets()

    mock_rows = [_make_mock_gold_row() for _ in range(3)]
    with patch.object(ds, "load_gold_standards", return_value=mock_rows):
        result = ds.build_trainset_for("suggestion_generator", limit=3)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == 3


def test_build_trainset_for_returns_dspy_examples(tmp_sio_db):
    """build_trainset_for must return dspy.Example objects, not dicts."""
    import dspy  # noqa: PLC0415
    ds = _import_datasets()

    mock_rows = [_make_mock_gold_row()]
    with patch.object(ds, "load_gold_standards", return_value=mock_rows):
        result = ds.build_trainset_for("suggestion_generator", limit=1)

    for ex in result:
        assert isinstance(ex, dspy.Example), (
            f"Expected dspy.Example, got {type(ex)}"
        )


# ---------------------------------------------------------------------------
# 2. Every example has get_input_keys() non-empty
# ---------------------------------------------------------------------------

def test_every_example_has_with_inputs_declared(tmp_sio_db):
    """Every example must have .with_inputs(...) called (get_input_keys non-empty)."""
    ds = _import_datasets()

    mock_rows = [_make_mock_gold_row() for _ in range(5)]
    with patch.object(ds, "load_gold_standards", return_value=mock_rows):
        result = ds.build_trainset_for("suggestion_generator", limit=5)

    for i, ex in enumerate(result):
        keys = ex.get_input_keys()
        assert keys, (
            f"Example {i} has empty get_input_keys() — .with_inputs() was not called"
        )


# ---------------------------------------------------------------------------
# 3. suggestion_generator input keys
# ---------------------------------------------------------------------------

def test_suggestion_generator_input_keys_match_contract(tmp_sio_db):
    """suggestion_generator examples must have the 3 required input keys."""
    ds = _import_datasets()

    mock_rows = [_make_mock_gold_row()]
    with patch.object(ds, "load_gold_standards", return_value=mock_rows):
        result = ds.build_trainset_for("suggestion_generator", limit=1)

    expected = {"pattern_description", "example_errors", "project_context"}
    for ex in result:
        assert ex.get_input_keys() == expected, (
            f"Expected input keys {expected}, got {ex.get_input_keys()}"
        )


# ---------------------------------------------------------------------------
# 4. recall_evaluator input keys
# ---------------------------------------------------------------------------

def test_recall_evaluator_input_keys_match_contract(tmp_sio_db):
    """recall_evaluator examples must have gold_rule and candidate_rule as inputs."""
    ds = _import_datasets()

    mock_rows = [_make_mock_gold_row()]
    with patch.object(ds, "load_gold_standards", return_value=mock_rows):
        result = ds.build_trainset_for("recall_evaluator", limit=1)

    expected = {"gold_rule", "candidate_rule"}
    for ex in result:
        assert ex.get_input_keys() == expected, (
            f"Expected input keys {expected}, got {ex.get_input_keys()}"
        )


# ---------------------------------------------------------------------------
# 5. limit=0 returns empty list
# ---------------------------------------------------------------------------

def test_limit_zero_returns_empty_list(tmp_sio_db):
    """build_trainset_for with limit=0 must return an empty list."""
    ds = _import_datasets()

    with patch.object(ds, "load_gold_standards", return_value=[]):
        result = ds.build_trainset_for("suggestion_generator", limit=0)

    assert result == [], f"Expected [], got {result!r}"


# ---------------------------------------------------------------------------
# 6. Unknown module name raises ValueError
# ---------------------------------------------------------------------------

def test_unknown_module_name_raises_value_error(tmp_sio_db):
    """build_trainset_for with unknown module name must raise ValueError."""
    ds = _import_datasets()

    with pytest.raises((ValueError, KeyError)):
        ds.build_trainset_for("nonexistent_module_xyz", limit=10)
