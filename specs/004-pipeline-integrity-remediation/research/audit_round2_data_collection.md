# Adversarial Audit Round 2 — Data Collection (post-fix-pack)

**Date**: 2026-04-21
**Branch**: `004-pipeline-integrity-remediation`
**Commits audited**: 3d5c7d9 (fix-pack), da33196 (test principle fix)
**Scope**: Verify Round 1 CRITICAL/HIGH closed; find NEW bugs introduced by fix-pack.
**Method**: Live repro against real `init_db()` + `migrate_004()`, not hand-rolled test schemas.

---

## Executive Summary

**Verdict: RE-AUDIT FAILS. Fix-pack claims closure of defects that remain broken under production schema.**

- Round 1 CRITICAL: **2 of 4 actually closed**; 2 claimed-closed are **still broken** (different symptom, same root cause).
- Round 1 HIGH: **4 of 5 closed**; 1 still broken (same as a CRITICAL above).
- NEW CRITICAL findings: **3**
- NEW HIGH findings: **2**

**The central new defect**: the fix-pack added DDL columns but did not add corresponding CREATE INDEX and backfill statements to `init_db()`. Four problems follow from this:

1. `init_db()` produces a DB WITHOUT the new unique indexes (they exist only in `migrate_004.py`). Any code path that uses `init_db()` alone (per-platform hooks, most tests) sees no dedup.
2. The production schema for `processed_sessions` has `message_count NOT NULL` / `tool_call_count NOT NULL` with **no defaults**, but `_update_session_state()` INSERT omits them — every byte-offset resume update crashes `IntegrityError`.
3. `log_invocation()` in `telemetry/logger.py` and `_INVOCATION_COLS` in `core/db/queries.py` do NOT write `tool_name`, `tool_input`, or `conversation_pointer` — even though the whole point of the fix-pack was that sync.py reads these. Result: sync copies `tool_name=NULL` forever, and the UNIQUE index `(platform, session_id, timestamp, tool_name)` never dedupes (NULLs are distinct in SQLite UNIQUE indexes).
4. Most tests use hand-rolled schemas that diverge from production DDL — masking bugs the tests should have caught.

---

## Round 1 CRITICAL verification

### C-R1.1 — PARTIAL / STILL BROKEN
**Claim**: `behavior_invocations` DDL now has `tool_name`, `tool_input`, `conversation_pointer`.
**Reality**: Columns ARE present (verified via `PRAGMA table_info`), but no producer writes them, and the required UNIQUE index is missing from `init_db()`.

**Evidence (live repro)**:
```
$ python -c "from sio.core.db.schema import init_db; c=init_db(':memory:'); print([r[1] for r in c.execute('PRAGMA table_info(behavior_invocations)').fetchall()])"
[... 'tool_name', 'tool_input', 'conversation_pointer']   ← columns exist
```
```
$ python -c "from sio.core.db.schema import init_db; c=init_db(':memory:'); print(c.execute('SELECT name FROM sqlite_master WHERE type=\"index\" AND tbl_name=\"behavior_invocations\" AND sql LIKE \"%UNIQUE%\"').fetchall())"
[]   ← UNIQUE INDEX MISSING on init_db() DB
```

Consequence: on the per-platform DB (created by hooks via `init_db()` only), INSERT OR IGNORE cannot dedupe. Without `migrate_004`, the per-platform `behavior_invocations` table grows unbounded with exact duplicates. See NEW finding **N-R2D.1**.

Even on the canonical DB (where `migrate_004` runs), the dedup is broken because `tool_name` is NULL — see NEW finding **N-R2D.2**.

### C-R1.2 — CLOSED ✅
**Claim**: `flow_events` DDL has `file_path`; UNIQUE index uses `file_path`.
**Reality**: `file_path TEXT` column is present. `migrate_004` creates `CREATE UNIQUE INDEX ix_fe_identity ON flow_events(file_path, session_id, flow_hash)`. The flow_pipeline INSERT writes both `source_file` and `file_path` columns (the latter intentionally redundant for the dedup constraint).

**Evidence (live repro)**:
```
$ ... INSERT same row 3 times into flow_events on migrate_004 DB
flow_events dedup (file_path non-NULL): 1 rows (expected 1)   ← works
```

**Caveat**: On a DB that only had `init_db()` run (no `migrate_004`), the UNIQUE index is absent → duplicates accumulate. Same pattern as C-R1.1; see N-R2D.1.

### C-R1.3 — CLOSED ✅
**Claim**: `error_records.pattern_id` column added to DDL.
**Reality**: Column is present, index `ix_er_pattern_id` creates successfully in `migrate_004.py`.

**Evidence**:
```
$ python -c "from sio.core.db.schema import init_db; c=init_db(':memory:'); print([r[1] for r in c.execute('PRAGMA table_info(error_records)')])"
['id', ..., 'pattern_id']   ← present
```

### C-R1.4 — PARTIAL / STILL BROKEN
**Claim**: `ON CONFLICT(file_path)` changed to `ON CONFLICT(file_path, file_hash)`.
**Reality**: The UPSERT statement now targets the correct UNIQUE constraint — but the INSERT omits the `NOT NULL` columns `message_count` and `tool_call_count`, so the entire call still raises IntegrityError on fresh rows.

**Evidence (live repro)**:
```
$ python -c "
from sio.core.db.schema import init_db
from sio.mining.pipeline import _update_session_state
c = init_db(':memory:')
_update_session_state(c, '/fresh/path.jsonl', 1000, 1000.0)"
IntegrityError: NOT NULL constraint failed: processed_sessions.message_count
```

Caught via `try/except` in `flow_pipeline.run_flow_mine()` (line 165), which logs a warning and continues — so flow mining doesn't crash outright, but the byte-offset resume NEVER persists for fresh files → every run re-parses every file cover-to-cover. FR-010 remains non-functional. See NEW finding **N-R2D.3**.

---

## Round 1 HIGH verification

### H-R1.1 — CLOSED ✅
**Claim**: `refuse_to_start()` now catches both `'applying'` and `'failed'`.
**Reality**: Verified via live repro.
```
$ ... seed 'failed' row, call refuse_to_start()
PASS: PartialMigrationError raised as expected
```
Also added `repair_schema_version()` helper. Good.

### H-R1.2 — CLOSED ✅
**Claim**: `_file_hash()` returns a sentinel string for oversized files instead of None.
**Reality**: Line 170 returns `f"__size_exceeded__{path_hash}"`. No code path returns None anymore.

### H-R1.3 — CLOSED ✅
**Claim**: `stop.py` no longer writes to `processed_sessions`.
**Reality**: Lines 189-192 carry a NOTE explicitly saying the stop hook does NOT write to `processed_sessions`. `_do_finalize()` now writes only to `session_metrics`.

### H-R1.4 — CLOSED ✅
**Claim**: Byte-offset resume no longer drops the first appended line.
**Reality**: Lines 452-457 implement a 1-byte peek-back: `fh.seek(start_offset - 1); prev_byte = fh.read(1); if prev_byte != b"\n": fh.readline()`. The fix correctly distinguishes "seek landed at newline" (don't discard) from "seek landed mid-line" (discard partial).

**Evidence (live repro — new 3-line file, second mine after append)**:
```
First pass: 2 events (offsets 78, 189)
Second pass (resume from offset 189): 1 events (offset 268)   ← appended line captured
```

### H-R1.5 — CLOSED ✅
**Claim**: bare `except Exception: pass` removed, centroid write failures now logged.
**Reality**: `_store_centroid()` lines 347-377 have two typed handlers: `sqlite3.OperationalError` logs WARNING with "run sio db migrate", generic `Exception` logs ERROR. Neither silently swallows.

### H10 (hardcoded `~/.sio/claude-code`) — CLOSED ✅
```
$ grep -rn '\.sio/claude-code' src/sio/
src/sio/adapters/claude_code/installer.py:73: ...   (docstring only; code uses DEFAULT_PLATFORM f-string)
```
All active code paths now use `f"~/.sio/{_DEFAULT_PLATFORM}"`.

---

## NEW findings (introduced or missed by the fix-pack)

### N-R2D.1 — CRITICAL: `init_db()` missing UNIQUE indexes; per-platform DBs never dedupe

**Severity**: CRITICAL
**Confidence**: HIGH (reproduced)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:359-401`

**Symptom**: The fix-pack added `tool_name`, `tool_input`, `conversation_pointer`, `file_path` columns to the DDL strings, but did NOT add the corresponding `CREATE UNIQUE INDEX ix_bi_identity` and `CREATE UNIQUE INDEX ix_fe_identity` to the `_INDEXES` list. Those unique indexes exist ONLY in `scripts/migrate_004.py`.

**Evidence (live repro)**:
```
$ python -c "
from sio.core.db.schema import init_db
c = init_db(':memory:')
for _ in range(3):
    c.execute('INSERT OR IGNORE INTO behavior_invocations (platform, session_id, timestamp, behavior_type, user_message, actual_action) VALUES (?, ?, ?, ?, ?, ?)', ('claude-code', 'sess1', '2026-01-01', 'skill', 'msg', 'Bash'))
print(c.execute('SELECT COUNT(*) FROM behavior_invocations').fetchone()[0])"
3   ← duplicates NOT deduped
```

```
$ python -c "
from sio.core.db.schema import init_db
c = init_db(':memory:')
for _ in range(3):
    c.execute('INSERT OR IGNORE INTO flow_events (session_id, flow_hash, sequence, ngram_size, was_successful, duration_seconds, source_file, file_path, timestamp, mined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', ('sess1', 'hash1', 'a,b,c', 3, 0, 1.0, '/foo', '/foo', 'now', 'now'))
print(c.execute('SELECT COUNT(*) FROM flow_events').fetchone()[0])"
3   ← duplicates NOT deduped on init_db-only DB
```

**Blast radius**:
- **Per-platform DB**: every `PostToolUse` hook fires `log_invocation()` → `INSERT OR IGNORE` with no effective UNIQUE key. Every duplicate session_id+timestamp is stored. The per-platform DB grows unboundedly, eventually causing disk-pressure and mining slowdown.
- **Tests**: Most test fixtures hand-roll `CREATE TABLE` statements that DO include UNIQUE constraints (see N-R2D.5), hiding the production bug.
- **Split-brain reconciliation**: sync.py copies the accumulated duplicates into the canonical DB, where `migrate_004`'s unique index SHOULD kick in — but N-R2D.2 explains why it doesn't.

**Fix suggestion**:
1. Add these two statements to `_INDEXES` in `schema.py`:
   ```python
   "CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity ON behavior_invocations(platform, session_id, timestamp, tool_name)",
   "CREATE UNIQUE INDEX IF NOT EXISTS ix_fe_identity ON flow_events(file_path, session_id, flow_hash)",
   ```
2. Add integration test that calls ONLY `init_db()` (no `migrate_004`) and confirms duplicate inserts are silently ignored.

---

### N-R2D.2 — CRITICAL: sync dedup silently broken — `tool_name` is always NULL in per-platform DB

**Severity**: CRITICAL
**Confidence**: HIGH (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/queries.py:11-32` (`_INVOCATION_COLS` missing 3 cols)
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/telemetry/logger.py:51-72` (no write to new cols)
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/sync.py:82-95` (SELECT + INSERT reads NULLs)

**Symptom**: The fix-pack's stated purpose for C-R1.1 was "sync.py reads `tool_name`/`tool_input`/`conversation_pointer` from per-platform DB and mirrors them into canonical". But no writer ever populates these columns.

`_INVOCATION_COLS` (queries.py) lists 20 columns — `actual_action` is present, but `tool_name` is NOT. `log_invocation()` (logger.py:56) builds a record with `"actual_action": tool_name` and does not set `tool_name`/`tool_input`/`conversation_pointer`. When `insert_invocation()` runs its generated INSERT, those three columns default to NULL.

**Evidence (live repro — post_tool_use data flow)**:
```
$ python -c "
from sio.core.db.schema import init_db
from sio.core.telemetry.logger import log_invocation
c = init_db(':memory:')
log_invocation(conn=c, session_id='sess1', tool_name='Bash', tool_input='{\"cmd\":\"ls\"}', tool_output='x', error=None, user_message='hi', platform='claude-code')
row = c.execute('SELECT tool_name, tool_input, conversation_pointer, actual_action FROM behavior_invocations').fetchone()
print('tool_name:', row[0], 'tool_input:', row[1], 'conversation_pointer:', row[2], 'actual_action:', row[3])"
tool_name: None tool_input: None conversation_pointer: None actual_action: 'Bash'
```

**Critical secondary effect — UNIQUE index + NULL ≠ dedup**: SQLite treats NULL values as distinct in UNIQUE indexes. The canonical DB's `ix_bi_identity` on `(platform, session_id, timestamp, tool_name)` with `tool_name=NULL` never dedupes:
```
$ python -c "
from sio.core.db.schema import init_db
from scripts.migrate_004 import migrate
import tempfile, os, sqlite3
tmp = tempfile.mkdtemp(); db = os.path.join(tmp,'x.db')
c = init_db(db); c.close(); migrate(db)
c = sqlite3.connect(db)
for _ in range(3):
    c.execute('INSERT OR IGNORE INTO behavior_invocations (platform, session_id, timestamp, behavior_type, user_message, actual_action) VALUES (?, ?, ?, ?, ?, ?)', ('claude-code', 'sess1', '2026-01-01', 'skill', 'msg', 'Bash'))
print(c.execute('SELECT COUNT(*) FROM behavior_invocations').fetchone()[0])"
3   ← NULL tool_name breaks dedup even WITH the unique index
```

**Blast radius**:
- Every `sync_behavior_invocations()` run copies ALL rows from per-platform → canonical without dedup → duplicates accumulate in canonical.
- `INSERT OR IGNORE` suppresses the IntegrityError silently, so operators see 0 errors and trust the pipeline is healthy.
- FR-003 (sync idempotency) is non-functional.
- The `drift_pct` computation in `compute_sync_drift()` will show 100% drift but nobody will notice because the sync "succeeds".

**Fix suggestion**:
1. Add `"tool_name", "tool_input", "conversation_pointer"` to `_INVOCATION_COLS` in `queries.py`.
2. Populate them in `log_invocation()` (set `tool_name=tool_name`, `tool_input=tool_input`, `conversation_pointer=session_id` or similar).
3. Decide whether to switch to `COALESCE(tool_name, '')` in the unique index (makes `''` the dedup key), or enforce `NOT NULL` on `tool_name`. Latter is stricter but requires a migration.

---

### N-R2D.3 — CRITICAL: `_update_session_state()` INSERT omits NOT NULL columns — byte-offset resume silently broken on fresh files

**Severity**: CRITICAL
**Confidence**: HIGH (reproduced against production schema)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py:375-386`

**Symptom**: Production `processed_sessions` DDL has `message_count INTEGER NOT NULL` and `tool_call_count INTEGER NOT NULL` with NO DEFAULT. `_update_session_state()` executes:
```sql
INSERT INTO processed_sessions (file_path, file_hash, last_offset, last_mtime, mined_at)
VALUES (?, '', ?, ?, ?)
ON CONFLICT(file_path, file_hash) DO UPDATE SET ...
```
The INSERT omits `message_count` and `tool_call_count`. On fresh files (no prior `_mark_processed` row with file_hash=''), the INSERT arm fires, raising `IntegrityError: NOT NULL constraint failed: processed_sessions.message_count`.

**Evidence (live repro — PRODUCTION schema via `init_db()`)**:
```
$ python -c "
from sio.core.db.schema import init_db
from sio.mining.pipeline import _update_session_state
c = init_db(':memory:')
_update_session_state(c, '/fresh/path.jsonl', 1000, 1000.0)"
IntegrityError: NOT NULL constraint failed: processed_sessions.message_count
```

The exception is silenced by a `try/except Exception as e: logger.warning(...); continue` in `flow_pipeline.run_flow_mine()` line 165. Every file's byte-offset update fails → no resume state is ever persisted → every `run_flow_mine` re-parses every file cover-to-cover. FR-010 byte-offset resume is entirely non-functional for files that reach flow mining before `run_mine`.

**Test gap (N-R2D.5)**: `tests/unit/mining/test_byte_offset.py::_open_db` creates its OWN `processed_sessions` schema with `message_count INTEGER DEFAULT 0` and `tool_call_count INTEGER DEFAULT 0` (lines 62-63). Tests pass because their schema is relaxed; production crashes.

```
$ python -c "[...run test body with init_db()...]"
FAIL with PRODUCTION schema: IntegrityError: NOT NULL constraint failed: processed_sessions.message_count
```

**Fix suggestion**:
Either (A) add `DEFAULT 0` to `message_count` and `tool_call_count` in `_PROCESSED_SESSIONS_DDL`, or (B) include them in the INSERT column list with value 0:
```sql
INSERT INTO processed_sessions (file_path, file_hash, message_count, tool_call_count, last_offset, last_mtime, mined_at)
VALUES (?, '', 0, 0, ?, ?, ?)
ON CONFLICT(file_path, file_hash) DO UPDATE SET ...
```
Plus: update all test fixtures to use `init_db()`+`migrate_004()` instead of hand-rolled DDLs.

---

### N-R2D.4 — HIGH: `_get_session_state()` returns wrong row when `run_mine` and `run_flow_mine` both wrote the file

**Severity**: HIGH
**Confidence**: HIGH (reproduced)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py:335-352`

**Symptom**: `_get_session_state()` queries `SELECT ... FROM processed_sessions WHERE file_path = ?` with no ORDER BY and no hash filter. When a file has been processed by both `_mark_processed` (real hash, `last_offset=0`) AND `_update_session_state` (file_hash='', real last_offset), two rows exist for the same file_path, and the SELECT returns a non-deterministic row — in practice the first one inserted, which usually has `last_offset=0`.

**Evidence (live repro)**:
```
$ python -c "
import sqlite3
from sio.core.db.schema import init_db
from scripts.migrate_004 import migrate
import tempfile, os
tmp = tempfile.mkdtemp(); db = os.path.join(tmp,'x.db')
c = init_db(db); c.close(); migrate(db)
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
# _mark_processed row (from run_mine)
c.execute('INSERT INTO processed_sessions (file_path, file_hash, message_count, tool_call_count, mined_at) VALUES (?, ?, ?, ?, ?)', ('/sess.jsonl','abc',100,20,'now'))
# _update_session_state row (from run_flow_mine) — different file_hash
c.execute('INSERT INTO processed_sessions (file_path, file_hash, message_count, tool_call_count, last_offset, last_mtime, mined_at) VALUES (?, ?, 0, 0, ?, ?, ?)', ('/sess.jsonl','',5000,1234.0,'now2'))
c.commit()
r = c.execute('SELECT last_offset FROM processed_sessions WHERE file_path = ?', ('/sess.jsonl',)).fetchone()
print('last_offset returned:', r['last_offset'], '(expected 5000 for resume to work)')"
last_offset returned: 0 (expected 5000 for resume to work)
```

**Blast radius**: Every second+ `run_flow_mine` call on the same file reads `last_offset=0` from the wrong row, ignores the real stored offset, and re-parses the file from byte 0. FR-010 is effectively a no-op.

**Fix suggestion**: Either (A) make `_get_session_state` query `WHERE file_path=? AND file_hash=''` (the byte-offset row), or (B) consolidate the design — make `_mark_processed` and `_update_session_state` write to the same row keyed ONLY by `file_path`. Option (B) requires changing `UNIQUE(file_path, file_hash)` to `UNIQUE(file_path)`, which cascades into `_is_already_processed(path, hash)` semantics.

---

### N-R2D.5 — HIGH: test-vs-production schema divergence systematically masks data-layer bugs

**Severity**: HIGH
**Confidence**: HIGH (reproduced across 13 test files)
**Files**:
- `tests/unit/mining/test_byte_offset.py:57-86` (`_open_db` hand-rolls relaxed schema)
- `tests/unit/mining/test_flow_dedup.py:51-86` (same pattern)
- `tests/unit/mining/test_subagent_link.py` (same pattern)
- `tests/unit/db/test_sync.py:25-61` (`_PLATFORM_DDL` has `tool_name NOT NULL`, no `behavior_type`)
- `tests/integration/test_mine_pipeline.py`, `tests/integration/test_mining_idempotence.py`, `tests/unit/db/test_active_cycle.py`, `tests/unit/clustering/test_slug_remap.py`, `tests/unit/cli/test_purge.py`, `tests/unit/clustering/test_declining_grade.py`, `tests/unit/db/test_sync_drift.py`, `tests/unit/db/test_migration_004.py` — all define their own DDL.

**Symptom**: 13 test files create their own table DDL instead of using `init_db()`. Several examples introduce constraint shapes that don't exist in production:

- `test_byte_offset.py::_open_db` — `message_count INTEGER DEFAULT 0` (production: NOT NULL, no default). Masks N-R2D.3.
- `test_sync.py::_PLATFORM_DDL` — `tool_name TEXT NOT NULL`, no `behavior_type` column. Masks N-R2D.2 (the NULL-tool_name bug) because the test forces a non-null tool_name.

**Blast radius**: Every schema regression can pass the test suite. This is a systemic blind spot — the fix-pack's own tests cannot catch future schema drift because the tests are not constrained by the real schema.

**Fix suggestion**:
1. Replace every hand-rolled DDL in the test suite with `init_db()` + `migrate_004()` (or a thin `make_test_db()` helper in `tests/conftest.py` that does both).
2. Add a CI check that greps for `CREATE TABLE IF NOT EXISTS behavior_invocations` / `processed_sessions` / `flow_events` / `error_records` in tests and fails if found outside the helper.

---

## Other confirmed-still-broken items

### L-R1.1 (heartbeat race, LOW) — NOT CLOSED
File: `_heartbeat.py:111`. Still uses non-unique `.tmp` suffix. Not CRITICAL/HIGH, not a regression — keeping as known LOW.

### M-R1.3 (lexicographic timestamp ordering, MEDIUM) — NOT CLOSED
File: `post_tool_use.py:111-115`. Still uses `ORDER BY timestamp DESC`. Not CRITICAL/HIGH.

### M-R1.4 (retention.purge not preserving gold-linked, MEDIUM) — NOT VERIFIED IN THIS PASS

### HIGH: `_apply_004_migration_if_needed()` silently swallows all errors
File: `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/installer.py:341-358`

The installer's migration wrapper catches ALL exceptions and returns silently. If `migrate_004` fails for any reason (permissions, SQLite version, corrupt DB), the operator gets no feedback and the installer reports success. Round 1 flagged the same pattern; fix-pack didn't touch it.

Evidence (install on a corrupt DB):
```python
try:
    migrate(canonical_db_path)
except Exception:
    pass  # Migration failure must not break installation
```

### HIGH: `migrate_split_brain.py` silently swallows non-"no such file" errors
File: `/home/gyasisutton/dev/projects/SIO/scripts/migrate_split_brain.py:25-31`

Same pattern — `return 0` (success) on all errors except "no such file". If sync fails due to schema mismatch, the installer sees a 0 exit and continues.

---

## Summary

| Category | Count | Details |
|---|---|---|
| Round 1 CRITICAL **actually closed** | **2 of 4** | C-R1.2, C-R1.3 |
| Round 1 CRITICAL **still broken** | **2 of 4** | C-R1.1 (columns added but no writer populates them, no unique index in init_db), C-R1.4 (upsert target fixed but INSERT still crashes NOT NULL) |
| Round 1 HIGH **actually closed** | **4 of 5** | H-R1.1, H-R1.2, H-R1.3, H-R1.4, H-R1.5 (all verified) |
| Round 1 HIGH **still broken** | **0 of 5** |  |
| NEW CRITICAL | **3** | N-R2D.1 (init_db missing unique indexes), N-R2D.2 (sync dedup broken — NULL tool_name), N-R2D.3 (byte-offset update crashes) |
| NEW HIGH | **2** | N-R2D.4 (`_get_session_state` returns wrong row), N-R2D.5 (test-schema divergence masks bugs) |

**Total CRITICAL+HIGH remaining: 2 (legacy, mis-classified as closed) + 5 (new) = 7.**

**PRD §8 bar (zero CRITICAL, zero HIGH): NOT MET.**

## Recommended priority (shortest-path to clean re-audit)

1. **N-R2D.1**: Add the two `CREATE UNIQUE INDEX IF NOT EXISTS` statements to `schema.py _INDEXES` list. 5-minute fix.
2. **N-R2D.2**: Populate `tool_name`/`tool_input`/`conversation_pointer` in `logger.py::log_invocation()` and add them to `_INVOCATION_COLS` in `queries.py`. 15-minute fix.
3. **N-R2D.3**: Either add `DEFAULT 0` to `message_count`/`tool_call_count` in the DDL, OR include them in the `_update_session_state()` INSERT. 5-minute fix.
4. **N-R2D.4**: Change `_get_session_state()` to filter on `file_hash=''` OR consolidate design to `UNIQUE(file_path)`. 30-minute fix + design decision.
5. **N-R2D.5**: Refactor test fixtures to use `init_db()` + `migrate_004()`. 2-hour refactor across 13 files, but high-value because it prevents future regressions.
6. Rerun this audit. Should be clean after (1)-(4).

Estimated total time to clean: **~4 hours** (structural, no design changes needed).

---

## Audit method attestation

- All reproductions above were run live against the current branch HEAD (commit da33196).
- Every "PASS/FAIL" line is actual `python3` stdout from `cd /home/gyasisutton/dev/projects/SIO && python3 -c "..."`.
- Zero findings are speculative — each has a live repro or a precise file:line citation.
- No code was modified during this audit (READ-ONLY per instructions).
