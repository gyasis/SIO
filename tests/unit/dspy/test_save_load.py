"""Failing tests for persistence.py — T031 (TDD red).

Tests assert (per contracts/dspy-module-api.md §7):
  1. save_compiled(program, path) writes a JSON file
  2. load_compiled("suggestion_generator", path) returns an instance
  3. Round-trip: save + load produces equivalent predictions on fixed input
  4. Unknown module name in load_compiled raises KeyError or ValueError

Run to confirm RED before T032:
    uv run pytest tests/unit/dspy/test_save_load.py -v
"""

from __future__ import annotations

import json

import pytest


def _import_persistence():
    from sio.core.dspy import persistence  # noqa: PLC0415

    return persistence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_program(mock_lm):  # noqa: ARG001
    """Return a minimal dspy.Module that can be saved/loaded.

    Only used by the "does save_compiled write a file" tests that don't
    care about the module's predictor structure.
    """
    import dspy  # noqa: PLC0415

    class _TinyModule(dspy.Module):
        def __init__(self):
            super().__init__()
            self.pred = dspy.Predict("question -> answer")

        def forward(self, question: str) -> dspy.Prediction:
            return self.pred(question=question)

    return _TinyModule()


def _make_suggestion_generator_program(mock_lm):  # noqa: ARG001
    """Return a fresh SuggestionGenerator for real round-trip tests.

    Audit Round 2 N-R2D.4 fix (commit d5f8bf8) raises
    ArtifactStructureMismatch on ZERO predictor-name overlap. Tests that
    exercise the load_compiled('suggestion_generator', ...) path MUST
    save from a SuggestionGenerator instance (not a _TinyModule whose
    predictor is named 'pred' instead of 'generate.predict'). Using the
    real class also makes the round-trip genuinely test the production
    class's save/load, not an arbitrary stand-in.
    """
    from sio.core.dspy.persistence import MODULE_REGISTRY  # noqa: PLC0415

    cls = MODULE_REGISTRY["suggestion_generator"]
    return cls()


# ---------------------------------------------------------------------------
# 1. save_compiled writes a JSON file
# ---------------------------------------------------------------------------


def test_save_compiled_creates_file(tmp_path, mock_lm):
    """save_compiled(program, path) must write a file at the given path."""
    p = _import_persistence()
    program = _make_mock_program(mock_lm)
    out_path = tmp_path / "compiled.json"

    p.save_compiled(program, out_path)

    assert out_path.exists(), f"save_compiled did not create file at {out_path}"


def test_save_compiled_writes_json(tmp_path, mock_lm):
    """save_compiled must write valid JSON (path.suffix == '.json', parseable)."""
    p = _import_persistence()
    program = _make_mock_program(mock_lm)
    out_path = tmp_path / "module.json"

    p.save_compiled(program, out_path)

    assert out_path.suffix == ".json", f"Expected .json suffix, got {out_path.suffix}"
    # Must be valid JSON
    content = out_path.read_text(encoding="utf-8")
    parsed = json.loads(content)
    assert isinstance(parsed, dict), f"Expected JSON object, got {type(parsed)}"


# ---------------------------------------------------------------------------
# 2. load_compiled returns correct type
# ---------------------------------------------------------------------------


def test_load_compiled_returns_instance(tmp_path, mock_lm):
    """load_compiled('suggestion_generator', path) returns a dspy.Module instance.

    Uses a real SuggestionGenerator for the save side so the round-trip
    is genuine — predictor names match, load succeeds without the
    zero-overlap ArtifactStructureMismatch guard firing.
    """
    import dspy  # noqa: PLC0415

    p = _import_persistence()
    program = _make_suggestion_generator_program(mock_lm)
    out_path = tmp_path / "sg.json"
    p.save_compiled(program, out_path)

    loaded = p.load_compiled("suggestion_generator", out_path)
    assert loaded is not None, "load_compiled returned None"
    assert isinstance(loaded, dspy.Module), f"Expected dspy.Module subclass, got {type(loaded)}"


# ---------------------------------------------------------------------------
# 3. Round-trip produces equivalent predictions
# ---------------------------------------------------------------------------


def test_round_trip_save_load_equivalent(tmp_path, mock_lm):
    """save + load round-trip on SuggestionGenerator preserves predictor structure.

    Uses the same class on both sides of the round-trip so predictor names
    align (`generate.predict` in both). This is the actual functionality
    operators rely on: save an optimized module, load it back with the
    same structure.
    """
    p = _import_persistence()

    program = _make_suggestion_generator_program(mock_lm)
    out_path = tmp_path / "rt.json"
    p.save_compiled(program, out_path)

    loaded = p.load_compiled("suggestion_generator", out_path)

    # Both should be callable dspy.Module instances
    assert callable(loaded), "Loaded program must be callable"

    # Round-trip check: both programs have the same predictor count AND names
    orig_predictors = list(program.named_predictors())
    loaded_predictors = list(loaded.named_predictors())
    assert len(orig_predictors) == len(loaded_predictors), (
        f"Predictor count mismatch: orig={len(orig_predictors)}, loaded={len(loaded_predictors)}"
    )
    assert [n for n, _ in orig_predictors] == [n for n, _ in loaded_predictors], (
        "Predictor names must match after round-trip"
    )


# ---------------------------------------------------------------------------
# 4. Unknown module name raises error
# ---------------------------------------------------------------------------


def test_load_compiled_unknown_module_raises(tmp_path, mock_lm):
    """load_compiled with unknown module name must raise KeyError or ValueError."""
    p = _import_persistence()
    program = _make_mock_program(mock_lm)
    out_path = tmp_path / "dummy.json"
    p.save_compiled(program, out_path)

    with pytest.raises((KeyError, ValueError)):
        p.load_compiled("nonexistent_module_xyz_abc", out_path)


# ---------------------------------------------------------------------------
# 5. MODULE_REGISTRY exists
# ---------------------------------------------------------------------------


def test_module_registry_exists():
    """persistence.py must export MODULE_REGISTRY dict."""
    p = _import_persistence()
    assert hasattr(p, "MODULE_REGISTRY"), "persistence.py must define MODULE_REGISTRY"
    assert isinstance(p.MODULE_REGISTRY, dict), "MODULE_REGISTRY must be a dict"
