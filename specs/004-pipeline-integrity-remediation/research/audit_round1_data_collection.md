# Adversarial Audit Round 1 — Data Collection + Tables

**Date**: 2026-04-20
**Branch**: `004-pipeline-integrity-remediation`
**Commit**: `0786839` (HEAD)
**Scope**: schema, migrations, mining, hooks, sync
**Hunter**: adversarial-bug-hunter #1 (targeted)
**Method**: Static grep + live repro with `sqlite3` against freshly-initialized SIO DBs

---

## Executive Summary

**Result: RE-AUDIT FAILED. Pipeline is fundamentally broken on multiple axes.**

- 4 CRITICAL findings (broken build — runtime crashes on first call)
- 5 HIGH findings (silent corruption, data loss, hook contract violations)
- 4 MEDIUM findings (index/behaviour drift)
- 2 LOW findings (cosmetic / robustness)

**The central defect: the `004-pipeline-integrity-remediation` schema promises columns that were never added to `_BEHAVIOR_INVOCATIONS_DDL` / `_ERROR_RECORDS_DDL` / `_FLOW_EVENTS_DDL` in `schema.py`.** The migration script and every downstream consumer (sync, flow mining, clustering) now reference phantom columns, so the entire pipeline throws `OperationalError: no such column` on the first call on any freshly-installed DB.

Several findings actively mask each other: `installer.py` silently swallows the migration failure, so `sio install` returns success while writing `schema_version.status='failed'`. The DB then continues to run half-migrated because `refuse_to_start()` only detects `status='applying'`, not `status='failed'`.

PRD requirements that appear closed in `tasks.md` but are in fact broken:
- FR-010 byte-offset resume — crashes on first call (ON CONFLICT without unique constraint)
- FR-008 flow mining dedup — crashes on every flow insert (phantom column)
- FR-031 split-brain reconciliation — crashes on every sync call (phantom column)
- FR-015 missing-index installation — migration aborts before these indexes are created
- FR-017 schema_version — row ends in permanent `'failed'` state that never unblocks

---

## CRITICAL findings

### C-R1.1 `sync.py` and `migrate_004.py` reference columns that do not exist in the `behavior_invocations` DDL

**Severity**: Blocker / **Confidence**: High (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:11-37` (DDL — lacks `tool_name`, `tool_input`, `conversation_pointer`)
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/sync.py:82-95` (INSERT/SELECT using phantom columns)
- `/home/gyasisutton/dev/projects/SIO/scripts/migrate_004.py:100-107` (UNIQUE INDEX on phantom `tool_name`)
- `/home/gyasisutton/dev/projects/SIO/specs/004-pipeline-integrity-remediation/data-model.md:41-47, 160-168` (spec assumes the columns exist)

**Evidence** (live reproduction):
```
$ python3 -c "... init_db(...); sync_behavior_invocations()"
SYNC FAIL: OperationalError: table behavior_invocations has no column named tool_name

$ python3 -c "... init_db(...); migrate('/tmp/test.db')"
MIGRATE FAIL: OperationalError: no such column: tool_name
schema_version rows: [(1,'applied','baseline'), (2,'failed','004-pipeline-integrity-remediation')]
```

The canonical DDL in `schema.py` lines 11-37 contains **none** of `tool_name`, `tool_input`, `conversation_pointer`. The only ADD COLUMN statements in the whole codebase for `behavior_invocations` are for `target_surface`, `reasoning_trace`, `skill_file_path` on the `suggestions` table, and for `tool_input`/`tool_output` on `error_records` — never on `behavior_invocations`.

**Blast radius**:
- `sio install` appears to succeed but writes `schema_version.status='failed'` for 004 (confirmed by repro).
- `sio mine` / any CLI call that transits `sync_behavior_invocations()` raises on first invocation.
- The Constitution-V split-brain reconciliation is entirely non-functional.
- `migrate_split_brain.py` is silently swallowed (`rc=0`) even though nothing is copied.

**Fix suggestion**:
1. Add the three columns to `_BEHAVIOR_INVOCATIONS_DDL`:
   ```sql
   tool_name TEXT,
   tool_input TEXT,
   conversation_pointer TEXT,
   ```
2. Or add three matching `ALTER TABLE ADD COLUMN` migrations in `init_db()` (with try/except for re-run safety) before the `_INDEXES` loop.
3. Ensure `log_invocation()` in `telemetry/logger.py` writes `tool_name`/`tool_input` directly instead of remapping `tool_name → actual_action` (lines 56-57) — otherwise the per-platform DB will have an empty `tool_name` column and the UNIQUE key becomes `(platform, session_id, timestamp, NULL)` which degrades to per-second dedup (SQLite treats NULL as distinct in UNIQUE).

**Next actions**: after adding columns, re-run `migrate_004.py` twice on a fresh DB and verify `schema_version.status='applied'` and that `sync_behavior_invocations()` returns `{"claude-code": N}` without exception.

---

### C-R1.2 `flow_events` table is missing the `file_path` column — every flow insert crashes

**Severity**: Blocker / **Confidence**: High (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:316-329` (DDL — no `file_path` column)
- `/home/gyasisutton/dev/projects/SIO/scripts/migrate_004.py:145-148` (UNIQUE INDEX references phantom `file_path`)
- `/home/gyasisutton/dev/projects/SIO/src/sio/mining/flow_pipeline.py:141-159` (INSERT references phantom `file_path`)

**Evidence** (live reproduction):
```
$ python3 ... (apply exact DDL, run migrate index)
INDEX FAIL: OperationalError: no such column: file_path
INSERT FAIL: OperationalError: table flow_events has no column named file_path
```

The DDL has a `source_file TEXT` column, but `migrate_004` and `flow_pipeline.py` both reference `file_path`. The tasks.md T087 checkpoint description ("Refactor `flow_pipeline.py:53-144` to honor `flow_events` UNIQUE `(file_path, session_id, flow_hash)` constraint") describes a schema that does not exist.

**Blast radius**:
- 100 % of flow events fail to insert.
- The UNIQUE-dedup contract for FR-008 is unsatisfiable.
- `query_flows()` / `get_promotable_flows()` return empty results in production, masking the problem as "no flows yet".

**Fix suggestion**:
- Either rename the column to `source_file` everywhere (migrate_004 + flow_pipeline.py), OR
- Add `ALTER TABLE flow_events ADD COLUMN file_path TEXT` in migrate_004 BEFORE the UNIQUE INDEX is created, and `init_db()` for fresh installs.
- Preferred: add `file_path` as a real column so the spec stays coherent.

**Next actions**: re-run `run_flow_mine()` against a sample JSONL and verify at least one row inserted into `flow_events`.

---

### C-R1.3 `error_records.pattern_id` index is created on a non-existent column

**Severity**: Blocker / **Confidence**: High (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:87-106` (DDL — no `pattern_id`)
- `/home/gyasisutton/dev/projects/SIO/scripts/migrate_004.py:135-138`
- `/home/gyasisutton/dev/projects/SIO/specs/004-pipeline-integrity-remediation/data-model.md:82-84`

**Evidence**:
```
$ python3 ... (create error_records exactly as DDL, then run migrate index)
INDEX FAIL: OperationalError: no such column: pattern_id
```

The data-model spec (§2.5 line 83) calls for `CREATE INDEX IF NOT EXISTS ix_er_pattern_id ON error_records(pattern_id)`. But `error_records` has no `pattern_id` column — the link table is `pattern_errors(pattern_id, error_id)`. The index statement is from a draft schema where `error_records.pattern_id` was a denormalised fast-path; that column was never created.

**Blast radius**: same as C-R1.1 — migrate_004 aborts here, leaving `schema_version.status='failed'`.

**Fix suggestion**: either
(a) drop this index from `migrate_004.py` and §2.5 (use `pattern_errors.pattern_id` which is already covered by the composite PK), or
(b) add a real `pattern_id INTEGER` column on `error_records` (keeps legacy pattern_errors join table as historical audit) plus the migration ADD COLUMN.

**Next actions**: decide with product whether denormalised `error_records.pattern_id` is desired; either remove or add.

---

### C-R1.4 `processed_sessions` byte-offset resume crashes — `ON CONFLICT(file_path)` has no matching unique constraint

**Severity**: Blocker / **Confidence**: High (reproduced)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py:363-375`
**Schema**: `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:219-234` (UNIQUE is on `(file_path, file_hash)`, not `file_path` alone)

**Evidence**:
```
$ python3 ... (create schema, attempt ON CONFLICT(file_path))
INSERT FAIL: OperationalError: ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint
```

`_update_session_state()` does:
```sql
INSERT INTO processed_sessions (file_path, file_hash, last_offset, last_mtime, mined_at)
VALUES (?, '', ?, ?, ?)
ON CONFLICT(file_path) DO UPDATE SET ...
```

But the only UNIQUE constraint is `UNIQUE(file_path, file_hash)`. SQLite requires `ON CONFLICT(col_set)` to match an existing PK or UNIQUE exactly.

**Blast radius**: every flow-mine run (flow_pipeline.py:163 calls `_update_session_state`) crashes on the first file processed. Byte-offset resume (FR-010) is entirely non-functional on Claude Code. Mining re-runs the entire transcript on every invocation.

**Fix suggestion**: add `CREATE UNIQUE INDEX IF NOT EXISTS ix_ps_file_path ON processed_sessions(file_path)` in schema.py `_INDEXES`, OR change the INSERT to a SELECT-then-UPDATE-or-INSERT pattern. The existing `UNIQUE(file_path, file_hash)` conflicts with this design anyway, because the same file path will be hashed repeatedly and produce multiple rows — the data model is unclear which row is "current".

**Next actions**: clarify with design — is the row per (file_path) or per (file_path, file_hash)? Current state allows BOTH which makes resume semantics ambiguous.

---

## HIGH findings

### H-R1.1 `migrate_004.py` leaves `schema_version` permanently stuck at `status='failed'`; `refuse_to_start()` only catches `'applying'`

**Severity**: Critical / **Confidence**: High (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/scripts/migrate_004.py:197-203` (except handler writes `failed`)
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:578-592` (`refuse_to_start` only checks `applying`)
- `/home/gyasisutton/dev/projects/SIO/src/sio/cli/main.py:4502-4514` (startup gate)

**Evidence**: after forcing the migration to fail (via C-R1.1), `schema_version` holds `(2, 'failed', '004-pipeline-integrity-remediation')`. The CLI entry point calls `refuse_to_start(conn)` which does not inspect `failed` rows → SIO keeps running against a half-migrated DB for the lifetime of the install.

Once the migration once fails, a re-run enters the `if existing and existing[0] == 'applied': return` guard at line 81 — wait, it checks for `'applied'` only. A `'failed'` row: the script falls through, attempts `INSERT OR IGNORE` (does nothing because version=2 already present), runs the UPDATE at line 91-94 that sets `status='applying'`, hits the same ADD COLUMN that already exists → returns silently (idempotent), but then attempts the phantom-column index again → crashes again and re-writes `'failed'`. Infinite loop with permanent broken DB.

**Fix suggestion**: add `OR status='failed'` to the retry branch, and extend `refuse_to_start()` to also detect `'failed'` rows older than, say, 24 hours.

### H-R1.2 `_file_hash()` returns `None` for files >1 GB → `INSERT INTO processed_sessions` crashes on `NOT NULL` constraint

**Severity**: Major / **Confidence**: High (reproduced)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py:142-165`, `:249-253`, `:281-296`, `:822-832`
**Schema**: `schema.py:221-223` (`file_hash TEXT NOT NULL`)

**Evidence**:
```
$ python3 ... (try INSERT with None hash)
IntegrityError: NOT NULL constraint failed: processed_sessions.file_hash
```

For any JSONL >1 GB (Claude Code transcripts can approach this in long sessions), `_file_hash()` returns `None` per the FR-027 guard. Callers then pass `None` into `_is_already_processed(db, path, None)` (which returns False since `(path, NULL)` never matches stored rows), into `_mark_skipped()` / `_mark_processed()` which crash on NOT NULL.

The boundary case at 1 GB - 1 byte vs 1 GB + 1 byte is especially fragile.

**Fix suggestion**: when `_file_hash()` returns `None`, either
- return early and skip with a log message, or
- stub the hash to `"oversized-" + sha256(path + str(mtime))[:32]` so the row still satisfies NOT NULL and is uniquely identifiable.

### H-R1.3 `stop.py` stuffs `session_id` into the `file_hash` column — breaks mining idempotency

**Severity**: Major / **Confidence**: High (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/stop.py:188-193`

```python
conn.execute(
    "INSERT OR IGNORE INTO processed_sessions "
    "(file_path, file_hash, message_count, tool_call_count, mined_at) "
    "VALUES (?, ?, ?, ?, ?)",
    (transcript_path, session_id, 0, tool_call_count, now),
)
```

`session_id` is a UUID, not a SHA-256 file hash. The UNIQUE key `(file_path, file_hash)` will now have (path, uuid) rows that can never match `_is_already_processed(db, path, real_sha256)` during a subsequent `sio mine` run. Result: the mining pipeline re-reads the transcript cover-to-cover on every run, creating duplicate error_records and flow_events (partially mitigated by `_is_cross_format_duplicate`, but at the cost of an O(N) scan on every insert).

**Fix suggestion**: remove the `processed_sessions` write from the Stop hook (it doesn't know the real file hash). Let `sio mine` manage this table exclusively.

### H-R1.4 Byte-offset resume drops the first new line after append

**Severity**: Major / **Confidence**: High (reproduced)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/jsonl_parser.py:449-452`

**Evidence** (reproduced):
```
start_offset=6 (start of line2), content after discard=b'line3\n'
```
The `seek(start_offset); readline()` pattern *always* discards the first line whose byte-range starts at `start_offset`. Since `_update_session_state()` stores `current_size` (= total file length) as the resume offset, and the file grows by appending a new line, the new line starts at byte `current_size` → `seek(current_size); readline()` reads and throws away the new line.

**Fix suggestion**: check `fh.tell()` after the discard and if it equals `start_offset + len(prev_line)` where `prev_line` is a complete line, treat the discarded line as real and emit it. Simpler: track byte-offsets per line and store `last_complete_line_end` separately from `current_size`, OR only discard when `start_offset > 0 AND the seek landed mid-line` (detect by peeking backward one byte for `\n`).

### H-R1.5 `centroid_model_version` writes silently fail until migrate_004 succeeds; the `except Exception: pass` hides it

**Severity**: Major / **Confidence**: High (reproduced)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/clustering/pattern_clusterer.py:337-353`
- `/home/gyasisutton/dev/projects/SIO/scripts/migrate_004.py:112`

Because `migrate_004` fails mid-way (C-R1.1 or C-R1.3), the `centroid_model_version` column is never added (ADD COLUMN at line 112 executes only if the earlier INDEX statements succeed in the same transaction — but SQLite DDL is NOT transactional, so some statements may run before the failure; still, ordering matters). The clustering code wraps the UPDATE in `except Exception: pass`, so silent zeroing is the norm. Pattern centroids are never persisted → every `sio suggest` re-clusters from scratch.

**Fix suggestion**:
1. Fix C-R1.1 / C-R1.3 so migrate_004 completes.
2. Replace the swallow at `pattern_clusterer.py:352` with a logged warning.
3. Add an integration test that clusters patterns, stops, restarts, and asserts the centroid BLOB round-trips.

---

## MEDIUM findings

### M-R1.1 Raw `sqlite3.connect()` bypasses `open_db()` PRAGMA in hot hook path

**Severity**: Medium / **Confidence**: High (static)
**Files**:
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py:423`
- `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/session_start.py:47`
- `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/sync.py:150`

`init_db()` uses `sqlite3.connect(db_path)` (line 423) with default isolation_level, then sets `journal_mode=WAL`; but the `timeout` is the default 5 s — not the 30 s agreed in `connect.py`. Every hook that calls `init_db()` inherits this short timeout, making concurrent hook + mine writes (FR-SC-006) race-prone.

The `session_start.py` hook opens sio.db **without** any WAL / busy_timeout. If another writer holds the write lock > 5 s, this call raises OperationalError, but the hook catches it silently in `except Exception: _log_error(...)` (line 77-78).

**Fix suggestion**: route ALL connections through `open_db()`. `init_db()` should accept a connection object or delegate to `open_db()`.

### M-R1.2 `iter_events()` discard-line pattern is vulnerable to partial writes mid-flush

**Severity**: Medium / **Confidence**: Medium (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/jsonl_parser.py:445-471`

If the mine runs while Claude Code is mid-write and the last line of the file is incomplete (no trailing `\n`), `fh.readline()` reads up to EOF, `json.loads(stripped)` raises `JSONDecodeError`, the parser logs at DEBUG and continues. So the partial line is effectively dropped — BUT the `end_offset` stored points past the partial line (at EOF), so on the next mine, `seek(end_offset); readline()` skips the (now complete) line that was partial last time.

**Fix suggestion**: only emit a `(record, end_offset)` tuple for SUCCESSFULLY parsed lines. When a JSONDecodeError occurs, yield nothing AND let the caller fall back to the offset of the last good line. This requires changing `run_flow_mine` and the mining `pipeline` to track the max of all yielded offsets rather than using `file.stat().st_size` as the truth.

### M-R1.3 `_detect_passive_signals` uses `ORDER BY timestamp DESC` on string-valued timestamps — lexicographic bugs

**Severity**: Medium / **Confidence**: Medium (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/post_tool_use.py:111-115`

```sql
SELECT id FROM behavior_invocations
WHERE session_id = ? ORDER BY timestamp DESC LIMIT 2
```

SQLite sorts strings lexicographically. As long as timestamps are all in the same `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` format, this is correct. But `log_invocation()` at `logger.py:35` uses `datetime.now(timezone.utc).isoformat()` which may or may not include microseconds depending on clock precision → a call at exactly 12:00:00.000000 serialises as `...T12:00:00+00:00` (without microseconds) and sorts BEFORE `...T11:00:00.000001+00:00` when compared lexically (6-char suffix difference shifts alignment).

**Fix suggestion**: enforce microsecond-padded format via `.isoformat(timespec='microseconds')`, or store as UNIX epoch float.

### M-R1.4 `retention.purge()` deletes `error_records` and `flow_events` without preserving gold-linked rows

**Severity**: Medium / **Confidence**: High (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/cli/main.py:326-334`

```sql
DELETE FROM error_records WHERE mined_at < datetime('now', ?)
DELETE FROM flow_events   WHERE mined_at < datetime('now', ?)
```

`behavior_invocations` retention correctly uses `id NOT IN (SELECT invocation_id FROM gold_standards)` to preserve audit trail (core/db/retention.py:34-38), but the CLI retention path for error_records and flow_events has no such preservation. Any operator-run purge is destructive for gold examples linked to old errors/flows.

**Fix suggestion**: add `AND id NOT IN (SELECT error_id FROM pattern_errors WHERE active=1)` and similar for flow_events.

---

## LOW findings

### L-R1.1 Hook heartbeat `.tmp` file is racy under concurrent hook fires

**Severity**: Low / **Confidence**: Low (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/_heartbeat.py:110-113`

```python
tmp = HEALTH_FILE.with_suffix(".tmp")
tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
os.replace(tmp, HEALTH_FILE)
```

If `post_tool_use` and `stop` fire within the same millisecond (rare but possible), both write to the same `.tmp` path before replacing; last writer wins on the tmp, and one heartbeat increment is silently lost. On crash between `write_text` and `os.replace`, the `.tmp` file lingers and `HEALTH_FILE.read_text()` still works (reads from old file).

**Fix suggestion**: use `HEALTH_FILE.with_suffix(f".{os.getpid()}.{uuid4().hex}.tmp")` to ensure per-call uniqueness.

### L-R1.2 `_detect_subagent_info` path regex uses both slash and backslash — OK on Windows but regex is overly permissive

**Severity**: Low / **Confidence**: High (static)
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py:418-455`

Input `/a/b/subagents/parent1/subagents/parent2/session.jsonl` matches against `_SUBAGENT_NESTED_RE` and returns `parent_session_id='parent2'` — the last subagents/ directory wins (greedy). Whether that is correct depends on intended linkage semantics. Harmless today, but worth a test.

**Fix suggestion**: add unit test coverage for nested-subagent paths and document the chosen semantics in the docstring.

---

## Pre-existing finding verifications

| PRD ID | Original issue | Status after Wave 12 | Evidence |
|---|---|---|---|
| C1 (split-brain) | per-platform DB not mirrored into sio.db | **STILL BROKEN** (regression) | sync_behavior_invocations raises on first call due to C-R1.1 |
| C2 (processed_sessions byte-offset missing) | column not present | PARTIALLY FIXED (columns added via migrate_004) but migrate_004 aborts → columns missing on fresh install; and C-R1.4 prevents the writes |
| C3 (missing indexes) | FR-015 hot-read indexes not created | **STILL BROKEN** — migrate_004 aborts on C-R1.1/C-R1.3 before reaching these index statements |
| H10 (platform string literal "claude-code") | hardcoded paths outside constants.py | NOT FIXED — 6 files in `hooks/` and `installer.py` still hardcode `~/.sio/claude-code` (see grep at `claude-code` literal) |
| H11 (centroid_embedding blob lost across restarts) | BLOB ephemeral | **STILL BROKEN** — column `centroid_model_version` never added because migrate_004 aborts; writes fail silently (H-R1.5) |
| H12 (timezone correctness) | bare `datetime.fromisoformat("")` unguarded | VERIFIED FIXED — `util/time.py:36-40` rejects empty string; `clustering/grader.py:98-104` and `ranker.py:78-81` guard with try/except |
| L6 (installer recreation) | installer may rebuild legacy DB | VERIFIED FIXED — installer lines 99-115 read `SIO_DB_PATH` env override correctly, only touches the two canonical paths |

**Bottom line**: every pre-existing C/H finding that depends on `migrate_004.py` running cleanly is still broken, because migrate_004 aborts on the first phantom-column index.

---

## Summary

**Findings**: 4 CRITICAL / 5 HIGH / 4 MEDIUM / 2 LOW = 15 defects total.

**Pre-existing issues verified closed**: 2 of 7 (H12 timezone, L6 installer path).
**Pre-existing issues still broken**: 5 of 7 (C1, C2, C3, H10, H11).
**Regressions introduced by Wave 1-12**: 4 CRITICAL phantom-column bugs (C-R1.1, C-R1.2, C-R1.3, C-R1.4) + H-R1.1 `failed`-state never clears.

**Re-audit verdict: FAIL.** The stated bar ("zero CRITICAL, zero HIGH for re-audit clean") is not met. Four CRITICAL defects render core pipeline functions (sync, flow mining, byte-offset resume, migration) non-functional on any freshly-installed SIO instance.

**Recommended next steps, in priority order**:
1. Fix C-R1.1 (`behavior_invocations` missing columns) — unblocks C-R1.3 and C1.
2. Fix C-R1.2 (`flow_events.file_path`) — unblocks FR-008 dedup and flow mining.
3. Fix C-R1.3 (`error_records.pattern_id`) — unblocks migrate_004 completion.
4. Fix C-R1.4 (`ON CONFLICT(file_path)`) — unblocks FR-010 byte-offset resume.
5. Fix H-R1.1 (`failed` status recovery) — unblocks operators from broken-install recovery.
6. Re-run this audit after the fixes to verify no regressions introduced.

Estimated time to clean re-audit: 4-6 hours of focused schema work plus a full test pass. No structural redesign needed — all fixes are additive schema + bug fixes to fail-safe paths.
