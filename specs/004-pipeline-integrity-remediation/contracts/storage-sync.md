# Contract — Per-Platform → `sio.db` Sync (Principle V Reconciliation)

**Branch**: `004-pipeline-integrity-remediation`
**Applies to**: `src/sio/core/db/sync.py` (NEW), `src/sio/core/db/connect.py` (NEW), `scripts/migrate_split_brain.py` (NEW)
**References**: `research.md` R-1, Constitution Principle V, spec FR-001, FR-002, FR-031

---

## 1. Why This Exists

Constitution Principle V: "Each platform adapter MUST maintain its own separate `behavior_invocations.db`. Cross-platform data MUST NOT be mixed in a single database."

Spec FR-001: "All tool-invocation hooks MUST write captured events to the single canonical data store that the training, optimization, and suggestion pipelines read from."

Reconciliation: **writers keep per-platform DBs; readers see a synchronized view in `sio.db`.**

---

## 2. Database Identity Key

The `behavior_invocations` table in **both** DBs uses the identity tuple:

```
(platform, session_id, timestamp, tool_name)
```

Uniqueness enforced in `sio.db` via:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity
    ON behavior_invocations(platform, session_id, timestamp, tool_name);
```

`platform` is the Single-Source-of-Truth constant (FR-031):

```python
# src/sio/core/constants.py
DEFAULT_PLATFORM: str = "claude-code"
```

Every writer, every reader, every sync call references this constant. No string literals.

---

## 3. `connect.py` — Central Connection Factory

```python
# src/sio/core/db/connect.py
import sqlite3
from pathlib import Path

def open_db(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'ro' if read_only else 'rwc'}"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```

Every `sqlite3.connect(...)` in SIO routes through this factory (SC parity with SC-022 for DSPy LM).

---

## 4. `sync.py` — Mirror Contract

```python
# src/sio/core/db/sync.py
from __future__ import annotations
from pathlib import Path
import sqlite3
from sio.core.constants import DEFAULT_PLATFORM
from sio.core.db.connect import open_db

SIO_DB        = Path.home() / ".sio" / "sio.db"
PLATFORM_DBS  = {
    DEFAULT_PLATFORM: Path.home() / ".sio" / DEFAULT_PLATFORM / "behavior_invocations.db",
}

def sync_behavior_invocations(since_timestamp: str | None = None) -> dict[str, int]:
    """Mirror per-platform behavior_invocations into sio.db. Idempotent.
    Returns {platform: rows_copied}. Safe to call concurrently with hook writes
    (WAL + busy_timeout=30s handles contention)."""
    results = {}
    with open_db(SIO_DB) as sio_conn:
        for platform, platform_db in PLATFORM_DBS.items():
            if not platform_db.exists():
                results[platform] = 0
                continue
            alias = f"p_{platform.replace('-', '_')}"
            sio_conn.execute(f"ATTACH DATABASE '{platform_db}' AS {alias}")
            try:
                where = f" WHERE timestamp >= '{since_timestamp}'" if since_timestamp else ""
                cursor = sio_conn.execute(
                    f"""
                    INSERT OR IGNORE INTO behavior_invocations
                        (platform, session_id, timestamp, tool_name, tool_input,
                         user_message, activated, correct_action, correct_outcome,
                         user_satisfied, conversation_pointer)
                    SELECT
                        '{platform}', session_id, timestamp, tool_name, tool_input,
                        user_message, activated, correct_action, correct_outcome,
                        user_satisfied, conversation_pointer
                    FROM {alias}.behavior_invocations
                    {where}
                    """
                )
                results[platform] = cursor.rowcount
            finally:
                sio_conn.execute(f"DETACH DATABASE {alias}")
    return results
```

**Invariants**:
- `INSERT OR IGNORE` on the identity key makes sync idempotent — safe to call N times per minute without duplicates.
- Per-platform DB schema unchanged; only SELECTed from.
- `since_timestamp` lets the scheduler scope recent rows only; `None` means full sync (used post-install).

---

## 5. Sync Triggers

| Trigger | Cadence | Scope |
|---|---|---|
| `sio install` (one-time) | once | full |
| `sio autoresearch --run-once` | per firing | since last autoresearch timestamp |
| `sio optimize` | pre-run | since last optimize timestamp |
| `sio status` | pre-display | since last sync timestamp |
| `sio mine` | pre-run | since last mine timestamp |

A `sync_cursor` row per trigger stored in `processed_sessions` (or a new `sync_cursor` kv table — TBD in Phase 1 implementation; design deferred to Phase 2 tasks). Default for unknowns is "last 24h".

**Explicit `sio db sync`** command available for operator debugging: `sio db sync --since 2026-04-01`.

---

## 6. Sync Drift Surface (SC-009, `sio status`)

`sio status` computes:

```python
per_platform_count = SELECT COUNT(*) FROM <platform>.behavior_invocations
canonical_count     = SELECT COUNT(*) FROM sio.db.behavior_invocations WHERE platform = '<platform>'
drift_pct = (per_platform_count - canonical_count) / per_platform_count
```

Reports:
- `✓ in sync` if drift < 1%
- `⚠ sync drift N%` if 1% ≤ drift < 5%
- `✗ sync drift N%` if drift ≥ 5% (exit code 1)

---

## 7. Legacy Backfill — `scripts/migrate_split_brain.py`

One-time script run by `sio install` (FR-002):

```python
#!/usr/bin/env python
"""Backfill legacy behavior_invocations from per-platform into sio.db.
Idempotent: re-running copies zero rows after first success."""

from sio.core.db.sync import sync_behavior_invocations, PLATFORM_DBS

def main() -> int:
    if not any(p.exists() for p in PLATFORM_DBS.values()):
        print("No per-platform behavior_invocations.db found — nothing to migrate.")
        return 0
    results = sync_behavior_invocations(since_timestamp=None)  # full
    for platform, n in results.items():
        print(f"{platform}: {n} rows copied (cumulative)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

**Invariants**:
- Never deletes per-platform rows.
- Never recreates per-platform DB (FR-007 / SC-014).
- Safe to run under `sio install --force-reinstall`.

---

## 8. Multi-Platform Extension Path (forward-looking)

Today `PLATFORM_DBS` has one entry. When SIO adds Cursor/Codex/Aider adapters:
- Each adapter writes to its own `~/.sio/<platform>/behavior_invocations.db`.
- Adapter's installer registers itself in `PLATFORM_DBS` (or an auto-discovery variant of it).
- Sync mirrors all platforms into `sio.db.behavior_invocations` with `platform` discriminator.
- DSPy readers filter by `platform` where relevant; cross-platform queries aggregate.

This preserves Principle V "Cross-platform data MUST NOT be mixed in a single database" for the write target, while allowing the canonical read store to present a unified view with explicit platform discrimination.

---

## 9. Test Coverage

| Test | Asserts |
|---|---|
| `tests/unit/db/test_sync.py` | Full sync copies all rows; second call copies zero (idempotency) |
| `tests/unit/db/test_sync_since.py` | `since_timestamp` scopes correctly |
| `tests/unit/db/test_sync_drift.py` | `sio status` reports correct drift percentage for seeded divergence |
| `tests/integration/test_installer_idempotent.py` | `sio install` twice produces identical sio.db row counts; per-platform DB untouched (SC-014) |
| `tests/unit/db/test_constants_single_source.py` | grep of `src/` for `"claude-code"` string literal returns zero non-test matches (FR-031) |
