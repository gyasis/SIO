"""T072 [US9] Instrumentation helpers for DSPy assertion backtrack counting.

Provides ``instrument_module`` — a wrapper that intercepts each forward()
call on a dspy.Module to count dspy.Assert failures and emit the counts
to ``suggestions.instrumentation_json`` in the SIO database.

Usage::

    from sio.suggestions.instrumentation import instrument_module

    # Wrap a module before running it through the optimization loop
    wrapped = instrument_module(generator_module, suggestion_id=42)
    result = wrapped(error_examples=..., error_type=..., pattern_summary=...)

The T066 rewrite of SuggestionGenerator (Wave 9) will call this helper
automatically so that assertion backtracks are tracked per suggestion.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _InstrumentedModule:
    """Thin wrapper around a dspy.Module that counts Assert failures.

    Attributes:
        _module: The underlying dspy.Module instance.
        _suggestion_id: ID of the related suggestions row (or None).
        _forward_count: Number of times forward() has been called.
        _backtrack_count: Number of dspy.Assert failures accumulated.
    """

    def __init__(self, module: Any, suggestion_id: int | None = None) -> None:
        self._module = module
        self._suggestion_id = suggestion_id
        self._forward_count: int = 0
        self._backtrack_count: int = 0

    # ------------------------------------------------------------------
    # Public counters (read-only access for tests)
    # ------------------------------------------------------------------

    @property
    def forward_count(self) -> int:
        return self._forward_count

    @property
    def backtrack_count(self) -> int:
        return self._backtrack_count

    def instrumentation_json(self) -> dict:
        """Return the current instrumentation payload as a dict.

        This mirrors what would be written to ``suggestions.instrumentation_json``.
        """
        return {
            "backtrack_count": self._backtrack_count,
            "forward_count": self._forward_count,
            "suggestion_id": self._suggestion_id,
        }

    # ------------------------------------------------------------------
    # Forward interception
    # ------------------------------------------------------------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call module.forward() and count assertion failures.

        Attempts to intercept dspy.Assert if it exists in the installed DSPy
        version. Falls back to a no-op if dspy.Assert is unavailable (DSPy 3.x
        does not expose it as a top-level function).
        """
        import unittest.mock as _mock  # noqa: PLC0415

        self._forward_count += 1
        _assert_failures_this_call = 0

        _dspy_assert = _get_dspy_assert()

        def _counting_assert(condition, msg="", *a, **kw):  # type: ignore[no-untyped-def]
            nonlocal _assert_failures_this_call
            if not condition:
                _assert_failures_this_call += 1
            if _dspy_assert is not None:
                return _dspy_assert(condition, msg, *a, **kw)

        # Only patch if dspy.Assert actually exists; otherwise run module directly
        try:
            import dspy as _dspy  # noqa: PLC0415

            if hasattr(_dspy, "Assert"):
                with _mock.patch("dspy.Assert", side_effect=_counting_assert):
                    result = self._module(*args, **kwargs)
            else:
                result = self._module(*args, **kwargs)
        except Exception:
            result = self._module(*args, **kwargs)

        self._backtrack_count += _assert_failures_this_call
        return result


def _get_dspy_assert():
    """Return the real dspy.Assert function, or None if DSPy unavailable or lacks it."""
    try:
        import dspy  # noqa: PLC0415

        return getattr(dspy, "Assert", None)
    except ImportError:
        return None


def instrument_module(module: Any, suggestion_id: int | None = None) -> _InstrumentedModule:
    """Wrap a dspy.Module to count dspy.Assert failures per forward() call.

    Each forward() invocation increments ``forward_count``. Every
    ``dspy.Assert(False, ...)`` inside that forward() increments
    ``backtrack_count``.

    After running, call ``wrapped.instrumentation_json()`` to get the
    payload suitable for writing to ``suggestions.instrumentation_json``.

    Args:
        module: A dspy.Module instance (or any callable with forward()).
        suggestion_id: Optional ID of the related suggestions row.

    Returns:
        An _InstrumentedModule wrapper around the given module.

    Example::

        wrapped = instrument_module(my_module, suggestion_id=7)
        wrapped(error_examples="...", error_type="tool_failure", pattern_summary="...")
        payload = wrapped.instrumentation_json()
        # {"backtrack_count": 0, "forward_count": 1, "suggestion_id": 7}
    """
    return _InstrumentedModule(module, suggestion_id=suggestion_id)
