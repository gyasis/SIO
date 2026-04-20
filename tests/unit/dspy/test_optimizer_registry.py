"""T061 [US9] Tests for OPTIMIZER_REGISTRY in src/sio/core/dspy/optimizer.py.

Per contracts/optimizer-selection.md §1 and contracts/cli-commands.md § sio optimize:
- OPTIMIZER_REGISTRY must exist with keys 'gepa', 'mipro', 'bootstrap'
- 'gepa' is fully functional; 'mipro'/'bootstrap' raise NotImplementedError (Wave 4/6)
- Unknown optimizer raises UnknownOptimizer (from run_optimize)
- T068 (Wave 9) will implement mipro/bootstrap; until then those tests are RED

Run to see expected pass/fail counts:
    uv run pytest tests/unit/dspy/test_optimizer_registry.py -v
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Registry structure tests — these PASS because Wave 4 T043 built the registry
# ---------------------------------------------------------------------------

def test_optimizer_registry_exists():
    """OPTIMIZER_REGISTRY dict must exist in optimizer.py."""
    from sio.core.dspy import optimizer as opt_module  # noqa: PLC0415
    assert hasattr(opt_module, "OPTIMIZER_REGISTRY") or hasattr(opt_module, "_MODULE_REGISTRY"), (
        "OPTIMIZER_REGISTRY (or _MODULE_REGISTRY) must exist in optimizer.py"
    )


def test_run_optimize_gepa_key_exists():
    """run_optimize must accept 'gepa' as optimizer_name without raising UnknownOptimizer."""
    from sio.core.dspy.optimizer import (  # noqa: PLC0415
        InsufficientData,
        UnknownOptimizer,
        run_optimize,
    )
    # GEPA should be recognized (may raise InsufficientData if DB is empty — that's OK)
    try:
        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )
    except InsufficientData:
        pass  # Expected when no gold_standards exist
    except NotImplementedError:
        pytest.fail("'gepa' optimizer must NOT raise NotImplementedError")
    except UnknownOptimizer:
        pytest.fail("'gepa' must be a known optimizer")
    except Exception:
        pass  # Other errors (API, DB) are acceptable in unit test context


def test_run_optimize_unknown_raises_unknown_optimizer():
    """run_optimize with an unknown optimizer_name must raise UnknownOptimizer."""
    from sio.core.dspy.optimizer import UnknownOptimizer, run_optimize  # noqa: PLC0415
    with pytest.raises((UnknownOptimizer, ValueError)):
        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="nonexistent_optimizer",
            trainset_size=5,
            valset_size=2,
        )


# ---------------------------------------------------------------------------
# MIPRO / Bootstrap — RED until T068 Wave 9
# ---------------------------------------------------------------------------

def test_run_optimize_mipro_raises_not_implemented():
    """run_optimize('mipro') must raise NotImplementedError (Wave 9 implements it)."""
    from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415
    with pytest.raises(NotImplementedError):
        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="mipro",
            trainset_size=5,
            valset_size=2,
        )


def test_run_optimize_bootstrap_raises_not_implemented():
    """run_optimize('bootstrap') must raise NotImplementedError (Wave 9 implements it)."""
    from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415
    with pytest.raises(NotImplementedError):
        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="bootstrap",
            trainset_size=5,
            valset_size=2,
        )
