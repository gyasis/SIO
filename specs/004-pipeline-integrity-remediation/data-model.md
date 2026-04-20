# Data Model — SIO Pipeline Integrity & Training-Data Remediation

**Branch**: `004-pipeline-integrity-remediation`
**Date**: 2026-04-20
**Scope**: Schema deltas, new tables, new columns, and entity relationships required by spec FR-001 → FR-041. Preserves existing 14-table schema; adds 1 new table (`schema_version`) and 5 new columns across existing tables. Also documents file-system artifact schemas (heartbeat JSON, optimized-module JSON, backup path convention).

---

## 1. Database Layout Summary

| Database | Path | Purpose | Writers | Readers |
|---|---|---|---|---|
| **Per-platform** | `~/.sio/claude-code/behavior_invocations.db` | Hook write target (Constitution V) | Claude Code hooks, `sio install` | `sync.py` (mirror) |
| **Canonical** | `~/.sio/sio.db` | Mining, clustering, optimization, suggestions, audit | `sync.py`, `sio mine`, `sio flows`, `sio suggest`, `sio apply`, `sio optimize` | All DSPy readers, `sio status` |

Both DBs use `PRAGMA journal_mode=WAL; busy_timeout=30000; synchronous=NORMAL; wal_autocheckpoint=1000` applied via `src/sio/core/db/connect.py`.

---

## 2. Schema Deltas (additive only — no column drops, no renames)

### 2.1 `schema_version` — NEW (FR-017)

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,                    -- UTC ISO-8601 with +00:00
    status      TEXT NOT NULL DEFAULT 'applied',  -- 'applying' | 'applied' | 'failed'
    description TEXT
);
```

Seeded at first connect with `(1, now(), 'applied', 'baseline')`. Every subsequent migration writes `(N, now(), 'applying', ...)` at start and updates to `'applied'` on success. `sio` refuses to start if any row has `status='applying'`.

### 2.2 `behavior_invocations` — add UNIQUE + sync metadata

Applied in `~/.sio/sio.db` only (per-platform DB schema unchanged).

```sql
-- Add composite UNIQUE for idempotent INSERT OR IGNORE from sync (R-1)
CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity
    ON behavior_invocations(platform, session_id, timestamp, tool_name);

-- Add index for DSPy optimizer hot path (FR-015)
CREATE INDEX IF NOT EXISTS ix_bi_platform_timestamp
    ON behavior_invocations(platform, timestamp);
```

### 2.3 `patterns` — centroid BLOB format (FR-032, R-9)

Existing `centroid_embedding BLOB` column repurposed. Format (documented, not enforced by schema):

```
byte[0..3]    : dim (uint32 little-endian)
byte[4..11]   : model_hash (8 bytes, sha256(onnx_model_filename + version)[:8])
byte[12..]    : vector (float32 little-endian, length = dim * 4)
```

No schema change; add a `centroid_model_version TEXT` column for future-proofing:

```sql
ALTER TABLE patterns ADD COLUMN centroid_model_version TEXT;
```

### 2.4 `processed_sessions` — byte-offset resume (FR-010, R-6)

```sql
ALTER TABLE processed_sessions ADD COLUMN last_offset    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processed_sessions ADD COLUMN last_mtime     REAL;
ALTER TABLE processed_sessions ADD COLUMN is_subagent    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE processed_sessions ADD COLUMN parent_session_id TEXT;
```

### 2.5 `error_records` — subagent linkage + hot-read indexes

```sql
ALTER TABLE error_records ADD COLUMN parent_session_id TEXT;    -- FR-011
ALTER TABLE error_records ADD COLUMN is_subagent INTEGER NOT NULL DEFAULT 0;

-- FR-015 (missing indexes identified in audit)
CREATE INDEX IF NOT EXISTS ix_er_user_msg ON error_records(user_message);
CREATE INDEX IF NOT EXISTS ix_er_error_text ON error_records(error_text);
CREATE INDEX IF NOT EXISTS ix_er_pattern_id ON error_records(pattern_id);
```

### 2.6 `flow_events` — dedup key + indexes (FR-008, FR-015)

```sql
ALTER TABLE flow_events ADD COLUMN parent_session_id TEXT;
ALTER TABLE flow_events ADD COLUMN is_subagent INTEGER NOT NULL DEFAULT 0;

-- Idempotency key for flow mining dedup (R-6)
CREATE UNIQUE INDEX IF NOT EXISTS ix_fe_identity
    ON flow_events(file_path, session_id, flow_hash);

-- Hot-read index
CREATE INDEX IF NOT EXISTS ix_fe_success_hash
    ON flow_events(was_successful, flow_hash);
```

### 2.7 `applied_changes` — soft delete, not destructive delete (FR-003)

Add `superseded_at` and `superseded_by` columns. The only valid deletion path is now operator-initiated purge; routine `sio suggest` never touches this table.

```sql
ALTER TABLE applied_changes ADD COLUMN superseded_at TEXT;   -- NULL = current
ALTER TABLE applied_changes ADD COLUMN superseded_by INTEGER;-- FK to newer applied_changes.id
```

### 2.8 `patterns`, `datasets`, `pattern_errors`, `suggestions` — `active` flag

Per FR-003: `sio suggest` marks stale rows as inactive instead of deleting.

```sql
ALTER TABLE patterns ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE patterns ADD COLUMN cycle_id TEXT;                       -- UUID per sio suggest run
ALTER TABLE datasets ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE datasets ADD COLUMN cycle_id TEXT;
ALTER TABLE pattern_errors ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE pattern_errors ADD COLUMN cycle_id TEXT;
ALTER TABLE suggestions ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE suggestions ADD COLUMN cycle_id TEXT;
```

Backward compat: existing queries without `WHERE active=1` see all rows (including stale). Migration sets all existing rows to `active=1`. New callers add the filter.

### 2.9 `optimized_modules` — DSPy idiomatic persistence (FR-039)

Existing table extended with metric, training-set size, score, optimizer name.

```sql
ALTER TABLE optimized_modules ADD COLUMN optimizer_name  TEXT;    -- 'gepa' | 'mipro' | 'bootstrap'
ALTER TABLE optimized_modules ADD COLUMN metric_name     TEXT;    -- e.g., 'embedding_similarity'
ALTER TABLE optimized_modules ADD COLUMN trainset_size   INTEGER;
ALTER TABLE optimized_modules ADD COLUMN valset_size     INTEGER; -- NULL for non-GEPA
ALTER TABLE optimized_modules ADD COLUMN score           REAL;    -- dspy.Evaluate score
ALTER TABLE optimized_modules ADD COLUMN reflection_lm   TEXT;    -- NULL unless optimizer='gepa'
ALTER TABLE optimized_modules ADD COLUMN task_lm         TEXT;
ALTER TABLE optimized_modules ADD COLUMN artifact_path   TEXT NOT NULL;  -- path under ~/.sio/optimized/
```

### 2.10 `ground_truth` — slug remap column (R-5)

```sql
ALTER TABLE ground_truth ADD COLUMN remapped_from_pattern_id TEXT;  -- audit trail on Jaccard remap
```

---

## 3. Entity Definitions

### 3.1 `BehaviorInvocation` — captured tool-use event

| Field | Type | Source | Notes |
|---|---|---|---|
| id | INTEGER | auto | PK in each DB |
| platform | TEXT | writer constant | FR-031; canonical store uses it as discriminator |
| session_id | TEXT | Claude Code session UUID | Part of identity |
| timestamp | TEXT | event time | UTC ISO-8601 `+00:00` (FR-030) |
| tool_name | TEXT | tool invoked | Part of identity |
| tool_input | TEXT JSON | serialized args | — |
| user_message | TEXT | preceding user turn | Feeds DSPy routing metric |
| activated | INTEGER | 0/1 | Binary signal (Principle III) |
| correct_action | INTEGER | 0/1 | Binary signal |
| correct_outcome | INTEGER | 0/1 | Binary signal; triggers promote_to_gold (FR-005) |
| user_satisfied | INTEGER | 0/1 | Binary signal; triggers promote_to_gold |
| conversation_pointer | TEXT | path or offset | Principle VI — no text blobs in DB |

**Identity / UNIQUE**: `(platform, session_id, timestamp, tool_name)` — used by sync for `INSERT OR IGNORE`.

**Lifecycle**:
- Hook writes to per-platform DB.
- `sync_behavior_invocations()` copies into `~/.sio/sio.db` with same identity key; duplicates ignored.
- `promote_to_gold(invocation_id)` called when `user_satisfied=1 AND correct_outcome=1` → inserts into `gold_standards`.

### 3.2 `GoldStandardExample` — curated training example

| Field | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| invocation_id | INTEGER | FK → `behavior_invocations.id` |
| promoted_at | TEXT | UTC ISO-8601 |
| promoted_by | TEXT | 'auto' or operator name |
| dspy_example_json | TEXT | serialized `dspy.Example` with `.with_inputs(...)` (FR-036) |
| task_type | TEXT | 'routing' \| 'flow' \| 'suggestion' \| 'recall' |

**Relationships**: many-to-one to `BehaviorInvocation`. Many-to-many (through `optimized_modules` FK) to historical optimization runs that used this example.

### 3.3 `Pattern` — named cluster of related errors

| Field | Type | Notes |
|---|---|---|
| pattern_id | TEXT | PK; format `<toptype>_<10hex>` (R-5) |
| name | TEXT | human-readable |
| centroid_embedding | BLOB | dim+model_hash+vector (R-9) |
| centroid_model_version | TEXT | audit header |
| grade | TEXT | 'emerging' \| 'established' \| 'declining' \| 'dead' (FR-023) |
| active | INTEGER | 1 = current cycle, 0 = superseded (FR-003) |
| cycle_id | TEXT | UUID per `sio suggest` run |
| created_at | TEXT | UTC |
| last_error_at | TEXT | UTC; drives `declining` transition |

**Relationships**: one-to-many to `error_records` via `error_records.pattern_id`. One-to-many to `ground_truth` via `ground_truth.pattern_id`. Re-map audit via `ground_truth.remapped_from_pattern_id`.

### 3.4 `OptimizedModuleArtifact` — persisted DSPy program

| Field | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| module_name | TEXT | e.g., 'suggestion_generator' |
| optimizer_name | TEXT | 'gepa' \| 'mipro' \| 'bootstrap' (FR-037) |
| metric_name | TEXT | matches `dspy/metrics.py` registry |
| trainset_size | INTEGER | |
| valset_size | INTEGER | NULL for non-GEPA |
| score | REAL | `dspy.Evaluate` score on devset |
| task_lm | TEXT | model id |
| reflection_lm | TEXT | NULL unless gepa |
| artifact_path | TEXT | absolute path under `~/.sio/optimized/<module>__<optimizer>__<ts>.json` |
| created_at | TEXT | UTC |
| active | INTEGER | 1 = currently deployed, 0 = historical |

**Lifecycle**: `sio optimize` writes a new row with `active=1`, marks the prior active row for the same `module_name` as `active=0` inside a transaction.

### 3.5 `AutoresearchTransaction` — scheduler log

| Field | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| fired_at | TEXT | UTC |
| outcome | TEXT | 'promoted' \| 'pending_approval' \| 'rejected_metric' \| 'rejected_arena' \| 'error' |
| candidate_rule_id | INTEGER | FK to `suggestions` |
| metric_score | REAL | |
| arena_passed | INTEGER | 0/1 |
| message | TEXT | free-form diagnostic |

**Lifecycle**: `sio autoresearch --run-once` writes one row per firing, regardless of outcome (FR-006 "record every firing").

### 3.6 `HookHeartbeat` — observability (FR-016)

**Storage**: `~/.sio/hook_health.json` — single JSON file, per-hook keys.

```json
{
  "post_tool_use": {
    "last_success":            "2026-04-20T14:32:11+00:00",
    "last_error":              null,
    "last_error_message":      null,
    "consecutive_failures":    0,
    "total_invocations":       38091,
    "schema_version":          1
  },
  "stop":              { ... },
  "pre_compact":       { ... }
}
```

`sio status` reads this file; stale detection thresholds default to 1 h (warning), 6 h (error). Absent file ⇒ hook never fired ⇒ reported as `never-seen`.

### 3.7 `BackupSnapshot` — file-system entity (FR-004)

Path convention: `~/.sio/backups/<file-relative-to-allowlist-root>/<basename>.<UTC-ts>.bak`

Example: applying `~/.claude/CLAUDE.md` at 2026-04-20T14:32:00Z produces:

```
~/.sio/backups/CLAUDE.md/CLAUDE.md.20260420T143200Z.bak
```

Retention: `_prune_backups(dir, keep=10)` called post-write.

### 3.8 `DSPyReasoningModule` — code entity (FR-035)

Not stored in DB; listed here as the canonical set of `dspy.Module` subclasses in scope:

| Module Name | Signature | Default Optimizer | Lives at |
|---|---|---|---|
| `SuggestionGenerator` | `PatternCluster -> Rule` | gepa | `src/sio/suggestions/dspy_generator.py` |
| `RecallEvaluator` | `(gold_rule, candidate_rule) -> score` | bootstrap | `src/sio/training/recall_trainer.py` |
| `RoutingDecider` | `UserMessage -> ToolChoice` | mipro | NEW (post-Phase 3, if dataset exists) |
| `FlowPredictor` | `ErrorContext -> SuccessfulFlow` | mipro | NEW (post-Phase 3, if dataset exists) |

Phase 3 in-scope: `SuggestionGenerator` and `RecallEvaluator`. `RoutingDecider` and `FlowPredictor` listed for future-state alignment.

---

## 4. Relationships (ER-ish)

```
behavior_invocations (per-platform) ──sync──▶ behavior_invocations (sio.db)
                                                      │
                                                      ▼ (user_satisfied=1 ∧ correct_outcome=1)
                                             gold_standards
                                                      │
                                                      ▼
                                             dspy.Example trainset
                                                      │
                                                      ▼  (optimizer=gepa|mipro|bootstrap)
                                             optimized_modules ──save()──▶ ~/.sio/optimized/*.json
                                                      │
                                                      ▼ (arena_passed=1, approval_gate)
                                             autoresearch_txlog
                                                      │
                                                      ▼
                                             applied_changes ──write()──▶ ~/.claude/*.md  (+ backup)

error_records ─┐
               ├─ clustering ─▶ patterns (stable slug) ─ pattern_errors ─▶ error_records
flow_events  ──┘                    │
                                    ▼
                                ground_truth (FK pattern_id; remapped_from_pattern_id audit)

hooks ──heartbeat──▶ ~/.sio/hook_health.json ──◀── sio status
```

---

## 5. Migration Plan

Every schema delta above runs via a single migration script `scripts/migrate_004.py` invoked by `sio db migrate` and idempotent:

1. Connect to `~/.sio/sio.db` with WAL + busy_timeout.
2. Check `schema_version`; if baseline missing, insert `(1, now(), 'applied', 'baseline')`.
3. Begin migration `(2, now(), 'applying', '004-pipeline-integrity-remediation')`.
4. Apply all `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE ADD COLUMN` in one transaction.
5. Backfill: for each existing row in `patterns` / `datasets` / `pattern_errors` / `suggestions`, set `active=1` and leave `cycle_id=NULL` (interpreted as "pre-remediation").
6. Run `scripts/migrate_split_brain.py` (separate script, invoked by `sio install` post-migration): `ATTACH '~/.sio/claude-code/behavior_invocations.db' AS legacy; INSERT OR IGNORE INTO behavior_invocations SELECT *, 'claude-code' AS platform FROM legacy.behavior_invocations;`.
7. If step 4 or 5 raises → leave `schema_version` row as `'applying'` so next startup surfaces the fault.
8. Mark `(2, …, 'applied', …)`.

Rollback: operator recovers from backup (`~/.sio/sio.db.<ts>.bak`, produced by `sio db migrate` pre-flight). Additive-only schema means rollback is rarely needed — old code simply ignores new columns.
