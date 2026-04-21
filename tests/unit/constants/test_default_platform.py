"""Failing tests for sio.core.constants.DEFAULT_PLATFORM — T010 (TDD red).

Tests assert:
  1. DEFAULT_PLATFORM == "claude-code" (contract: contracts/storage-sync.md §2)
  2. Zero raw "claude-code" string literals exist in src/sio/ outside of
     src/sio/core/constants.py (FR-031, SC-022 parity — no string escape hatches)

Run to confirm RED before implementing constants.py:
    uv run pytest tests/unit/constants/test_default_platform.py -v
"""

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constant value
# ---------------------------------------------------------------------------


def test_default_platform_equals_claude_code():
    """DEFAULT_PLATFORM must equal the string 'claude-code'."""
    from sio.core.constants import DEFAULT_PLATFORM  # noqa: PLC0415

    assert DEFAULT_PLATFORM == "claude-code", (
        f"Expected DEFAULT_PLATFORM == 'claude-code', got {DEFAULT_PLATFORM!r}"
    )


def test_default_platform_is_str():
    """DEFAULT_PLATFORM must be a plain str, not bytes or enum."""
    from sio.core.constants import DEFAULT_PLATFORM  # noqa: PLC0415

    assert isinstance(DEFAULT_PLATFORM, str)


# ---------------------------------------------------------------------------
# Grep: no string-literal "claude-code" outside constants.py
# ---------------------------------------------------------------------------


def _src_sio_root() -> Path:
    """Resolve the src/sio/ directory relative to this test file."""
    # tests/unit/constants/ → tests/unit/ → tests/ → project root
    tests_unit_constants = Path(__file__).parent
    project_root = tests_unit_constants.parent.parent.parent
    candidate = project_root / "src" / "sio"
    if not candidate.is_dir():
        pytest.skip(f"src/sio/ not found at {candidate}; skipping grep test")
    return candidate


_LITERAL_PATTERN = re.compile(r"""["']claude-code["']""")
_CONSTANTS_FILE = Path("sio") / "core" / "constants.py"


def test_no_string_literal_claude_code_outside_constants():
    """Zero raw 'claude-code' string literals in src/sio/ except constants.py.

    This test enforces FR-031: every reference to the platform name MUST
    import DEFAULT_PLATFORM from sio.core.constants — no escape hatches.

    Allowed files (exempted from the check):
      - src/sio/core/constants.py   (the definition)
      - Any file under tests/        (test code may reference the literal)
    """
    src_root = _src_sio_root()
    violations: list[str] = []

    for py_file in src_root.rglob("*.py"):
        # Skip the canonical definition file.
        if py_file.parts[-3:] == ("sio", "core", "constants.py"):
            continue
        # Skip any test files that may have ended up under src/ (should not happen).
        if "test" in py_file.name.lower():
            continue

        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _LITERAL_PATTERN.search(line):
                violations.append(
                    f"{py_file.relative_to(src_root.parent.parent)}:{lineno}: {line.strip()}"
                )

    assert not violations, (
        "FR-031 violation — raw 'claude-code' string literals found in src/sio/ "
        "(import DEFAULT_PLATFORM from sio.core.constants instead):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
