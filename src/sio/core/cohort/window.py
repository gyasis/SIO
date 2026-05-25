"""Shared experiment-window resolver.

Every CLI command that grew an ``--experiment NAME`` flag (sio scan,
suggest, trend, flows, velocity) routes through this resolver so the
window-resolution logic exists in exactly one place. T016 of the PRD.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sio.core.cohort.store import ExperimentNotFound, get_experiment


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_experiment_window(
    db_path: str | Path,
    name: str,
    *,
    end_default: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(start_ts, end_ts)`` for the named experiment.

    For an OPEN experiment, the end of the window is "now" (or the
    optional ``end_default`` override, useful for tests). For a CLOSED
    experiment, the recorded ``close_ts`` is used.

    Args:
        db_path: SIO DB path.
        name: experiment name (UNIQUE in ``experiments``).
        end_default: override for open experiments — typically the
            current timestamp; exposed for deterministic tests.

    Returns:
        ``(start_ts, end_ts)`` — both ISO-8601 UTC strings.

    Raises:
        ExperimentNotFound: if ``name`` doesn't exist.
    """
    exp = get_experiment(db_path, name)
    if exp is None:
        raise ExperimentNotFound(f"No experiment named {name!r}")
    end = exp.close_ts or end_default or _utc_now_iso()
    return exp.start_ts, end
