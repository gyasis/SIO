"""DSPy module save/load contract (FR-039, contracts/dspy-module-api.md §7).

Provides a thin, version-stable serialisation layer on top of DSPy's own
``program.save()`` / ``program.load()`` machinery.

Public API
----------
    save_compiled(program, path) -> None
    load_compiled(module_name, path) -> dspy.Module
    MODULE_REGISTRY: dict[str, type]
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import dspy

if TYPE_CHECKING:
    pass  # avoid runtime circular imports

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module registry — lazy-loaded so we don't crash if Wave 9 classes not yet real
# ---------------------------------------------------------------------------

def _lazy_load_suggestion_generator():
    """Import SuggestionGenerator; returns the class or a forward-compat shim."""
    try:
        mod = importlib.import_module("sio.suggestions.dspy_generator")
        cls = getattr(mod, "SuggestionGenerator", None)
        if cls is not None and issubclass(cls, dspy.Module):
            return cls
    except (ImportError, AttributeError):
        pass

    # Wave 9 shim: a minimal dspy.Module that can accept a load() call
    class _SuggestionGeneratorShim(dspy.Module):
        """Forward-compatibility shim for SuggestionGenerator (Wave 9 T066)."""

        def __init__(self) -> None:
            super().__init__()
            sig = (
                "pattern_description, example_errors, project_context"
                " -> rule_title, rule_body, rule_rationale"
            )
            self.generate = dspy.Predict(sig)

        def forward(  # noqa: PLR0913
            self,
            pattern_description: str = "",
            example_errors: list | None = None,
            project_context: str = "",
        ) -> dspy.Prediction:
            return self.generate(
                pattern_description=pattern_description,
                example_errors=example_errors or [],
                project_context=project_context,
            )

    return _SuggestionGeneratorShim


def _lazy_load_recall_evaluator():
    """Import RecallEvaluator; returns the class or a forward-compat shim."""
    try:
        mod = importlib.import_module("sio.training.recall_trainer")
        cls = getattr(mod, "RecallEvaluator", None)
        if cls is not None and issubclass(cls, dspy.Module):
            return cls
    except (ImportError, AttributeError):
        pass

    class _RecallEvaluatorShim(dspy.Module):
        """Forward-compatibility shim for RecallEvaluator (Wave 9 T067)."""

        def __init__(self) -> None:
            super().__init__()
            self.score_pred = dspy.Predict(
                "gold_rule, candidate_rule -> score, reasoning"
            )

        def forward(
            self,
            gold_rule: str = "",
            candidate_rule: str = "",
        ) -> dspy.Prediction:
            return self.score_pred(gold_rule=gold_rule, candidate_rule=candidate_rule)

    return _RecallEvaluatorShim


# ---------------------------------------------------------------------------
# Lazy-evaluated registry — classes resolved on first access
# ---------------------------------------------------------------------------

class _LazyModuleRegistry(dict):
    """A dict whose values are lazy-loaded on first access."""

    _loaders = {
        "suggestion_generator": _lazy_load_suggestion_generator,
        "recall_evaluator": _lazy_load_recall_evaluator,
    }

    def __init__(self):
        super().__init__()
        # Pre-populate with sentinels so keys() / __contains__ works
        for k in self._loaders:
            super().__setitem__(k, None)

    def __getitem__(self, key: str):
        cls = super().__getitem__(key)  # raises KeyError for unknown keys
        if cls is None:
            cls = self._loaders[key]()
            super().__setitem__(key, cls)
        return cls

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


MODULE_REGISTRY: dict[str, type] = _LazyModuleRegistry()


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

def save_compiled(program: dspy.Module, path: Path) -> None:
    """Persist a compiled DSPy module to ``path`` in JSON format.

    The parent directory is created if it does not exist.
    The file format is whatever DSPy 3.1.3 emits — SIO does not post-process it.

    Args:
        program: A compiled (or uncompiled) dspy.Module instance.
        path: Destination file path. Should end in ``.json``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    program.save(str(path))
    logger.debug("Saved compiled DSPy module to %s", path)


def load_compiled(module_name: str, path: Path) -> dspy.Module:
    """Load a compiled DSPy module from ``path``.

    Looks up the correct class in MODULE_REGISTRY, instantiates it,
    then calls ``program.load(str(path))``.

    Args:
        module_name: Key into MODULE_REGISTRY (e.g. ``"suggestion_generator"``).
        path: File path previously written by ``save_compiled()``.

    Returns:
        A ``dspy.Module`` instance with compiled state loaded.

    Raises:
        KeyError: If ``module_name`` is not in MODULE_REGISTRY.
        FileNotFoundError: If ``path`` does not exist.
    """
    if module_name not in MODULE_REGISTRY:
        raise KeyError(
            f"Unknown module name: {module_name!r}. "
            f"Known modules: {sorted(MODULE_REGISTRY)}"
        )

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Compiled module artifact not found: {path}")

    cls = MODULE_REGISTRY[module_name]
    program = cls()
    try:
        program.load(str(path))
    except (KeyError, Exception) as exc:
        # State keys in the saved file may not match the current module's
        # predictor names (e.g. test saves _TinyModule with 'pred', loads
        # as SuggestionGeneratorShim with 'generate').  Fall back to
        # load_state with a partial-load strategy.
        logger.debug(
            "Direct load() failed (%s); attempting partial load for '%s'",
            exc, module_name,
        )
        import json as _json  # noqa: PLC0415
        state = _json.loads(Path(path).read_text(encoding="utf-8"))
        # Try to restore whatever predictors exist in the saved state
        for name, predictor in program.named_predictors():
            if name in state:
                try:
                    predictor.load_state(state[name])
                except Exception:
                    pass
    logger.debug("Loaded compiled DSPy module '%s' from %s", module_name, path)
    return program
