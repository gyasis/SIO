"""Search-discipline report for SIO (US6, T062, FR-011).

Computes per-discipline usage rates from invocation telemetry stored in
behavior_invocations.db and exposes them as a sio CLI subcommand.

Rate definitions (from research.md §B + BASELINE.md)
-----------------------------------------------------
Total denominator: Bash rows whose ``tool_input`` JSON command contains
  "session-search" or "sio search" within the requested window.

  recency_rate       = rows with ``--recent``  / total
  multi_hop_rate     = rows with ``--refine|--within|--use-cache|--strategy``
                       / total
  files_first_rate   = rows with ``--files``   / total
  context_walk_rate  = rows with ``--context`` / total

Targets (BASELINE.md "Target deltas"):
  recency_rate       >= 85%   (was ~50% before US1)
  multi_hop_rate     >= 5%    (was ~0.4% before US3)
  context_walk_rate  >= 15%   (was ~3% before US2)

  files_first_rate has no BASELINE target; reported for observability.

Telemetry source
----------------
  DB   : ~/.sio/<platform>/behavior_invocations.db
         (default platform = claude-code, per core.constants)
  Table: behavior_invocations
  Column used: tool_input (JSON, key "command") WHERE tool_name = 'Bash'
  Existing read path: sio.core.db.queries — this module adds a fresh
  query scoped to discipline-flag detection, keeping the query minimal.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Targets (single source of truth — avoids magic numbers in three files)
# ---------------------------------------------------------------------------

#: Discipline targets from BASELINE.md "Target deltas".
#: metrics without a target are not included (files_first_rate).
TARGETS: dict[str, float] = {
    "recency_rate": 0.85,
    "multi_hop_rate": 0.05,
    "context_walk_rate": 0.15,
}


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def _is_search_command(command: str) -> bool:
    """Return True if the command is a session-search or sio search invocation."""
    return "session-search" in command or "sio search" in command


def _flag_recency(command: str) -> bool:
    return "--recent" in command


def _flag_multi_hop(command: str) -> bool:
    return any(
        flag in command
        for flag in ("--refine", "--within", "--use-cache", "--strategy")
    )


def _flag_files_first(command: str) -> bool:
    return "--files" in command


def _flag_context_walk(command: str) -> bool:
    return "--context" in command


def compute_discipline_rates(
    conn: sqlite3.Connection,
    window_days: int = 14,
) -> dict[str, Any]:
    """Compute per-discipline search rates from invocation telemetry.

    Parameters
    ----------
    conn:
        Open SQLite connection to a behavior_invocations DB.
        The connection's row_factory is used if set; otherwise columns are
        accessed positionally.
    window_days:
        Look-back window in days.  Pass 0 for unbounded (all history).

    Returns
    -------
    dict with keys:
        total_search_invocations (int)
        recency_rate        (float 0-1)
        multi_hop_rate      (float 0-1)
        files_first_rate    (float 0-1)
        context_walk_rate   (float 0-1)
        window_days         (int)
        since               (str ISO or "all")
    """
    zero = {
        "total_search_invocations": 0,
        "recency_rate": 0.0,
        "multi_hop_rate": 0.0,
        "files_first_rate": 0.0,
        "context_walk_rate": 0.0,
        "window_days": window_days,
        "since": "all",
    }

    try:
        if window_days > 0:
            since_dt = datetime.now(timezone.utc) - timedelta(days=window_days)
            since_iso = since_dt.isoformat()
            rows = conn.execute(
                "SELECT tool_input FROM behavior_invocations "
                "WHERE tool_name = 'Bash' AND timestamp >= ?",
                (since_iso,),
            ).fetchall()
        else:
            since_iso = "all"
            rows = conn.execute(
                "SELECT tool_input FROM behavior_invocations "
                "WHERE tool_name = 'Bash'",
            ).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist yet (fresh / empty DB)
        return zero

    # Extract command strings and filter to search invocations
    search_commands: list[str] = []
    for row in rows:
        raw = row[0] if not hasattr(row, "keys") else row["tool_input"]
        if raw is None:
            continue
        try:
            parsed = json.loads(raw)
            cmd = parsed.get("command", "")
        except (json.JSONDecodeError, AttributeError):
            cmd = str(raw)
        if _is_search_command(cmd):
            search_commands.append(cmd)

    total = len(search_commands)
    if total == 0:
        zero["since"] = since_iso
        return zero

    recency = sum(1 for c in search_commands if _flag_recency(c))
    multi_hop = sum(1 for c in search_commands if _flag_multi_hop(c))
    files_first = sum(1 for c in search_commands if _flag_files_first(c))
    context_walk = sum(1 for c in search_commands if _flag_context_walk(c))

    return {
        "total_search_invocations": total,
        "recency_rate": recency / total,
        "multi_hop_rate": multi_hop / total,
        "files_first_rate": files_first / total,
        "context_walk_rate": context_walk / total,
        "window_days": window_days,
        "since": since_iso,
    }


# ---------------------------------------------------------------------------
# Invocations DB path resolution
# ---------------------------------------------------------------------------


def _default_invocations_db_path() -> str:
    """Return the default behavior_invocations.db path.

    Respects SIO_INVOCATIONS_DB_PATH env override (useful for tests).
    Falls back to ~/.sio/<DEFAULT_PLATFORM>/behavior_invocations.db.
    """
    from sio.core.constants import DEFAULT_PLATFORM

    override = os.environ.get("SIO_INVOCATIONS_DB_PATH")
    if override:
        return override
    return os.path.expanduser(f"~/.sio/{DEFAULT_PLATFORM}/behavior_invocations.db")


def open_invocations_db(db_path: str | None = None) -> sqlite3.Connection | None:
    """Open the behavior_invocations DB.  Returns None if the file is absent."""
    from sio.core.db.schema import init_db

    path = db_path or _default_invocations_db_path()
    if not os.path.exists(path):
        return None
    return init_db(path)


# ---------------------------------------------------------------------------
# Formatted report text
# ---------------------------------------------------------------------------


def format_discipline_report(
    rates: dict[str, Any],
    *,
    targets: dict[str, float] | None = None,
) -> str:
    """Format a human-readable discipline report string.

    Parameters
    ----------
    rates:
        Output of :func:`compute_discipline_rates`.
    targets:
        Override the default TARGETS dict (for testing).

    Returns
    -------
    Multi-line markdown-ish string.
    """
    _targets = targets if targets is not None else TARGETS
    total = rates["total_search_invocations"]
    window = rates["window_days"]

    if total == 0:
        window_label = f"last {window}d" if window > 0 else "all history"
        return f"## Search Discipline\nNo search invocations in {window_label}."

    window_label = f"last {window}d" if window > 0 else "all history"
    lines = [
        f"## Search Discipline ({total} searches, {window_label})",
    ]

    metrics = [
        ("recency_rate", "recency-first (--recent)"),
        ("multi_hop_rate", "multi-hop (--refine/--within/--use-cache/--strategy)"),
        ("files_first_rate", "files-first (--files)"),
        ("context_walk_rate", "context walk-back (--context)"),
    ]

    for key, label in metrics:
        rate = rates.get(key, 0.0)
        target = _targets.get(key)
        if target is not None:
            flag = " ⚠ BELOW TARGET" if rate < target else " ✓"
            target_str = f"  target ≥{target:.0%}{flag}"
        else:
            target_str = "  (no target)"
        lines.append(f"- {label}: {rate:.1%}{target_str}")

    return "\n".join(lines)
