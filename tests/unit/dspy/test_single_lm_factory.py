"""T065 [US9] Enforcement test: zero direct dspy.LM( calls outside lm_factory.py.

Greps src/ for ``dspy.LM(`` patterns and asserts they only appear in
``src/sio/core/dspy/lm_factory.py`` (FR-041, SC-022).

This test is a static analysis check that enforces the "single LM factory"
architectural invariant. It is intentionally brittle — any new direct
dspy.LM( call in src/ will fail this test.

Run:
    uv run pytest tests/unit/dspy/test_single_lm_factory.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Canonical factory file (the ONE place dspy.LM( is allowed)
# ---------------------------------------------------------------------------

_FACTORY_SUFFIX = Path("sio") / "core" / "dspy" / "lm_factory.py"

# Pattern matches dspy.LM( with optional whitespace
_DIRECT_LM_CALL = re.compile(r"dspy\.LM\s*\(")


def _resolve_src_root() -> Path:
    """Return the absolute path to src/sio/ from the project root."""
    # __file__ is tests/unit/dspy/test_single_lm_factory.py
    tests_unit_dspy = Path(__file__).parent  # tests/unit/dspy
    project_root = tests_unit_dspy.parent.parent.parent  # project root
    candidate = project_root / "src" / "sio"
    if not candidate.is_dir():
        pytest.skip(f"src/sio/ not found at {candidate}; skipping grep test")
    return candidate


def _is_factory_file(py_file: Path, src_root: Path) -> bool:
    """Return True if py_file is the canonical lm_factory.py."""
    try:
        rel = py_file.relative_to(src_root.parent)  # relative to src/
        return rel == _FACTORY_SUFFIX
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# T065-1: No direct dspy.LM( in src/ except lm_factory.py
# ---------------------------------------------------------------------------


def test_no_direct_dspy_lm_outside_factory():
    """Zero src/sio/**/*.py files contain dspy.LM( outside lm_factory.py (SC-022)."""
    src_root = _resolve_src_root()
    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        if _is_factory_file(py_file, src_root):
            continue  # allowed
        if "test" in py_file.name.lower():
            continue  # test files are exempt

        text = py_file.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _DIRECT_LM_CALL.search(line):
                # Allow lines that are comments or docstrings
                stripped = line.strip()
                if (
                    stripped.startswith("#")
                    or stripped.startswith('"""')
                    or stripped.startswith("'''")
                ):
                    continue
                violations.append(
                    f"{py_file.relative_to(src_root.parent.parent)}:{lineno}: {stripped}"
                )

    assert not violations, (
        "SC-022 / FR-041 violation — direct dspy.LM( calls found outside lm_factory.py:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nFix: replace dspy.LM(...) with get_task_lm() or get_reflection_lm() "
        "from sio.core.dspy.lm_factory"
    )


# ---------------------------------------------------------------------------
# T065-2: lm_factory.py itself exports the required symbols
# ---------------------------------------------------------------------------


def test_lm_factory_exports_required_symbols():
    """lm_factory.py must export get_task_lm, get_reflection_lm, get_adapter, configure_default."""
    from sio.core.dspy import lm_factory  # noqa: PLC0415

    required = ("get_task_lm", "get_reflection_lm", "get_adapter", "configure_default")
    for name in required:
        assert hasattr(lm_factory, name), (
            f"lm_factory.py must export '{name}' (contracts/dspy-module-api.md §1)"
        )
        assert callable(getattr(lm_factory, name)), f"'{name}' must be callable"


# ---------------------------------------------------------------------------
# T065-3: No dspy.LM( in test files that use factories (best-effort style check)
# ---------------------------------------------------------------------------


def test_no_rogue_lm_construction_in_test_conftest():
    """conftest.py mock_lm fixture should not call dspy.LM() directly.

    The conftest mock_lm fixture is expected to use a MagicMock or similar
    object — not construct a real dspy.LM (which would require API keys).
    This is a soft style check: warn if dspy.LM( appears in conftest.
    """
    tests_root = Path(__file__).parent.parent.parent  # tests/
    conftest = tests_root / "conftest.py"
    if not conftest.exists():
        pytest.skip("tests/conftest.py not found")

    text = conftest.read_text(encoding="utf-8")
    matches = _DIRECT_LM_CALL.findall(text)
    # This is a WARN-only test — not a hard failure
    # because conftest.py may legitimately construct mock LMs
    if matches:
        import warnings  # noqa: PLC0415

        warnings.warn(
            f"conftest.py contains {len(matches)} dspy.LM( call(s). "
            "Prefer MagicMock or lm_factory helpers in test fixtures.",
            stacklevel=1,
        )
    # Always pass — this is advisory only
    assert True
