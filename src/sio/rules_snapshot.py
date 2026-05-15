"""Snapshot the active rule set for attribution.

T1.L.2 (PRD sio_backend_dead_loop_2026-05-15). When error_records are
inserted by the mining pipeline, each row gets stamped with the
``active_rules`` column — a JSON array of rule identifiers (file path
+ content hash) for every file in ``~/.claude/rules/`` at mine time.

This enables the velocity report (T1.L.3) to answer:
    "Did the error rate for category X drop after rule Y landed?"
by JOINing error_records to a per-rule baseline.

DESIGN
------
* Snapshot is taken ONCE per ``sio mine`` invocation (cached in-process).
  Re-mining within the same process call reuses the same snapshot.
* We use content hashes (sha256[:12]) instead of just file paths because
  rules get edited in-place and we want to detect that.
* Output is sorted by rule_id so the JSON serialisation is stable —
  enabling future GROUP BY on the column value.
* We exclude the ``.backup-*`` and ``.archive`` subdirectories so backup
  copies don't pollute the snapshot.

NOTE: this is a best-effort PROXY for "rules active at session-time."
For mining a session that happened weeks ago, the snapshot reflects
current rules, not historical ones. T1.L (future) could pull
session-start timestamps from JSONL and join against a rules git
history.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

_RULES_ROOT = Path.home() / ".claude" / "rules"
_EXCLUDE_DIR_PREFIXES = (".backup", ".archive", "__pycache__")


def _walk_rule_files() -> list[Path]:
    """Yield active rule files (exclude backup/archive subdirectories)."""
    if not _RULES_ROOT.is_dir():
        return []
    out: list[Path] = []
    for p in _RULES_ROOT.rglob("*.md"):
        # Skip any path with an excluded directory segment
        if any(part.startswith(_EXCLUDE_DIR_PREFIXES) for part in p.parts):
            continue
        out.append(p)
    return out


def _rule_id(path: Path) -> str:
    """Build a stable rule identifier.

    Format: ``<relative-path>#<sha256-12>`` so that:
    - Different rules at the same path (after edit) hash differently.
    - The relative path makes the id human-readable in the JSON column.
    """
    try:
        content = path.read_bytes()
    except OSError:
        return ""
    h = hashlib.sha256(content).hexdigest()[:12]
    try:
        rel = path.relative_to(_RULES_ROOT)
    except ValueError:
        rel = path
    return f"{rel}#{h}"


@lru_cache(maxsize=1)
def current_snapshot_json() -> str:
    """Return a JSON array (string) of current rule ids.

    Cached for the lifetime of the process so a single ``sio mine`` run
    doesn't re-hash 50+ rule files for every error record.
    """
    ids = sorted(filter(None, (_rule_id(p) for p in _walk_rule_files())))
    return json.dumps(ids)


def stamp_records(records: list[dict]) -> None:
    """Mutate records in place — set ``active_rules`` on each.

    Called by the mining pipeline after extraction, before INSERT. Cheap:
    the snapshot is cached process-wide.
    """
    snap = current_snapshot_json()
    for r in records:
        r.setdefault("active_rules", snap)
