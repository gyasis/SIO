# Adversarial Bug Hunter #2 — General Codebase Scan (T112)

**Scan date**: 2026-04-20
**Scope**: All `src/sio/` Python files (general sweep for HIGH/MEDIUM issues
not originally in PRD §4.3)

---

## Finding 1: `_file_hash` return type widened to `str | None`

**File**: `src/sio/mining/pipeline.py`
**Severity**: MEDIUM (addressed in this wave as T089)

The new `_file_hash` can return `None` for files > 1 GB. All call sites in
the pipeline now guard against `None` via the standard pre-scan flow (files
that return `None` from `_file_hash` are skipped in the dedup path). No
unguarded call sites were found after T089 implementation.

**Status**: RESOLVED in Wave 11 (T089).

---

## Finding 2: `ranker.py` `fromisoformat("")` on empty timestamp

**File**: `src/sio/clustering/ranker.py:75`
**Severity**: MEDIUM (addressed in this wave as T106)

`datetime.fromisoformat("")` raises `ValueError`. Unlike `grader.py` (which
already guards via try/except at lines 98-103 and 167-175), `ranker.py` had
no guard. An empty `last_seen` field would crash the full ranking pass.

**Status**: RESOLVED in Wave 11 (T106) — added try/except with UTC-now fallback.

---

## Finding 3: `processed_sessions` schema missing T085 columns

**File**: `src/sio/core/db/schema.py:_PROCESSED_SESSIONS_DDL`
**Severity**: HIGH (T-REGR — addressed as regression fix in this wave)

T085 (Wave 10 subagent wire) added `is_subagent` and `parent_session_id`
to the `INSERT` SQL in `_mark_processed` but did not update the DDL in
`schema.py`. `init_db(":memory:")` created the table without those columns,
causing 9 integration tests to fail with `sqlite3.OperationalError`.

**Status**: RESOLVED in T-REGR — `_PROCESSED_SESSIONS_DDL` now includes
`is_subagent INTEGER NOT NULL DEFAULT 0`, `parent_session_id TEXT`,
`last_offset INTEGER NOT NULL DEFAULT 0`, and `last_mtime REAL`.

---

## Finding 4: Cross-type dedup was collapsing distinct error categories

**File**: `src/sio/mining/pipeline.py:_dedup_by_error_type_priority`
**Severity**: MEDIUM (addressed in this wave as T105)

The dedup function grouped by `(session_id, user_message)` and kept only
the single highest-priority winner across all `error_type` values. This
suppressed `tool_failure` rows whenever a `user_correction` existed for the
same `(session_id, user_message)`, losing potentially useful training data.

**Status**: RESOLVED in T105 — group key is now
`(session_id, user_message, error_type)` so cross-type rows are preserved.

---

## Finding 5: No new CRITICAL or HIGH findings in remaining modules

Modules scanned with no HIGH/CRITICAL findings:
- `src/sio/adapters/` — hook heartbeat logic, installer
- `src/sio/autoresearch/` — cadence checks
- `src/sio/core/arena/` — gold standards, promotion
- `src/sio/core/dspy/` — optimizer, datasets, metrics
- `src/sio/suggestions/` — dspy_generator, confidence
- `src/sio/cli/` — main, status, apply

No `SELECT *`, no hardcoded DB paths outside config, no unguarded file writes,
no missing `commit()` calls on writes.

---

## Summary

| Finding | Severity | Status |
|---------|----------|--------|
| `_file_hash` None return (T089) | MEDIUM | RESOLVED |
| `ranker.py` fromisoformat crash (T106) | MEDIUM | RESOLVED |
| `processed_sessions` schema gap (T-REGR) | HIGH | RESOLVED |
| Cross-type dedup data loss (T105) | MEDIUM | RESOLVED |
| All other modules | — | PASS |

**Overall: zero CRITICAL. Zero remaining HIGH (all HIGH resolved). All MEDIUM resolved.**
Feature is clear for merge.
