# Contract — `sio` CLI Command Surface

**Branch**: `004-pipeline-integrity-remediation`
**Applies to**: `src/sio/cli/main.py` plus `src/sio/cli/status.py` (new)
**Scope**: Every command that changes behavior under this feature. Unchanged commands are not re-documented here.

All commands exit `0` on success, `1` on user error, `2` on integrity failure (e.g., partial migration detected). All destructive commands prompt for confirmation unless `--yes` is passed.

---

## `sio install`

Install / repair the Claude Code hook wiring and initialize both databases.

**Behavior (changed)**:
- Connects to `~/.sio/sio.db` and runs `sio db migrate` first.
- Runs `scripts/migrate_split_brain.py` to backfill legacy rows from `~/.sio/claude-code/behavior_invocations.db` into `sio.db.behavior_invocations` via ATTACH + `INSERT OR IGNORE` (FR-002).
- Preserves the per-platform DB file (FR-007, R-1). MUST NOT delete or recreate.
- Writes a baseline `~/.sio/hook_health.json` with `never-seen` status for each hook.
- Idempotent: re-running produces no schema drift and no duplicate rows (SC-014).

**Flags**:
- `--force-reinstall` — rewrites hook scripts (preserves DBs).
- `--skip-schedule` — does not call `sio autoresearch --install-schedule` (default: installs Claude Code `CronCreate` per R-3).
- `--yes` — bypass confirmation.

**Exit**: `0` on success; `2` if `schema_version` shows `status='applying'` (partial migration).

---

## `sio mine`

Run error-record mining.

**Behavior (changed)**:
- Reads per-file state from `processed_sessions` including `last_offset` and `last_mtime` (FR-010, R-6).
- Streams JSONL (`for line in open(path, 'rb')`) — never full-file-read (FR-009).
- Detects subagent JSONLs via path regex (`.../subagents/<parent>/...` or `<parent>__subagent_<id>.jsonl`) and sets `is_subagent=1`, `parent_session_id=<parent>` (FR-011, R-13).
- Logs a WARNING to stderr (not silent skip) if an expected session directory is absent (FR-027).
- Skips files > 1 GB with WARNING (FR-028).
- Normalizes every ingested timestamp via `core/util/time.to_utc_iso()` (FR-030).

**Flags**:
- `--include-subagents` — also count subagent rows in top-level aggregates (default: excluded).
- `--since <date>` — only re-parse sessions touched since `<date>`.
- `--full-rescan` — reset `last_offset=0` for all sessions (debug use).

**Idempotency**: Running twice on an unchanged corpus writes zero new rows (SC-006).

---

## `sio flows`

Flow-event mining + promotion.

**Behavior (changed)**:
- Honors `processed_sessions` like `sio mine` (FR-008).
- Uses UNIQUE index on `(file_path, session_id, flow_hash)` for idempotent inserts.
- Flow success requires explicit positive marker (FR-021) — absence-of-negative no longer promotes.
- n-gram extraction uses `range(n_min, n_max + 1)` (FR-022).
- Accepts `.rs`, `.go`, `.java`, `.cpp`, `.ipynb` in addition to existing extensions (FR-026).

**Flags**:
- `--mine-first` — run `sio mine` first; existing flag, now idempotent.

---

## `sio suggest`

Pattern clustering + suggestion generation.

**Behavior (changed)** — the biggest behavior change in this feature:
- **NON-DESTRUCTIVE** (FR-003). Creates a new `cycle_id` UUID for this run.
- Before any write: marks prior active rows in `patterns`, `datasets`, `pattern_errors`, `suggestions` as `active=0` (keyed on `active=1 AND cycle_id != <this cycle>`).
- Inserts new rows with `active=1, cycle_id=<this>`.
- **Never touches `applied_changes`** (FR-003).
- Uses stable centroid-hash slugs (FR-014, R-5). Ground-truth rows with pre-existing slugs remapped via Jaccard overlap on first run post-migration (R-5).
- Reuses `patterns.centroid_embedding` BLOB when `centroid_model_version` matches current fastembed model (FR-032, R-9). Recomputes only for new/changed clusters.
- Emits via the idiomatic `SuggestionGenerator` `dspy.Module` (FR-035) with `dspy.Assert` format guardrails (FR-038), scored by `dspy.Evaluate` against a held-out devset (FR-029).

**Flags**:
- `--optimizer <gepa|mipro|bootstrap>` — *invalid here; applies to `sio optimize`* (this flag rejected with message).
- `--dry-run` — run full pipeline but suppress final writes.

**Idempotency**: Running twice preserves 100% of `applied_changes` rows (SC-002).

---

## `sio apply` / `sio apply-change` (FR-004, FR-019, FR-024)

Apply a previously generated suggestion to a target file.

**Behavior (changed)**:
- Validates target path via `_validate_target_path()` (R-14) — rejects any path outside the allowlist (`$HOME/.claude/` + `SIO_APPLY_EXTRA_ROOTS`).
- Uses `atomic_write()` per R-4: read → backup → tmp → fsync → rename → post-write verify.
- Writes backup to `~/.sio/backups/<relpath>/<basename>.<UTC-ts>.bak` first.
- Retention: keeps last 10 backups per file (pruned post-write).
- Any merge-with-existing-similar-rule operation requires `--merge` flag OR interactive `y/N` consent (FR-024). Silent fabrication disallowed.
- Writes an `applied_changes` row with `superseded_at=NULL`.

**Flags**:
- `--merge` — explicit consent to merge with similar existing rule.
- `--no-backup` — NOT SUPPORTED (raises `BackupRequired`; safety bar).
- `--yes` — bypass confirmation prompt.

**Rollback** (`sio apply --rollback <applied_change_id>`):
- Reads the backup path from the `applied_changes` row and restores via `atomic_write`.
- Marks the `applied_changes` row with `superseded_at=now()`, `superseded_by=NULL`.

---

## `sio optimize` (FR-037, new primary surface)

Run DSPy optimization on a reasoning module.

**Behavior (new)**:
- Loads trainset from `gold_standards` as `dspy.Example` objects with `.with_inputs(...)` (FR-036).
- Selects optimizer per `--optimizer` flag: `gepa` (default), `mipro`, `bootstrap`.
- GEPA uses `task_lm` from `SIO_TASK_LM` env (or factory default) and `reflection_lm` from `SIO_REFLECTION_LM`.
- Runs `dspy.Evaluate` on a held-out valset; records score.
- Calls `optimized.save(artifact_path)`; inserts row into `optimized_modules`.
- Transitions prior active artifact for the same module to `active=0`.

**Flags**:
- `--module <name>` — required. One of `suggestion_generator`, `recall_evaluator`.
- `--optimizer <gepa|mipro|bootstrap>` — default `gepa`.
- `--trainset-size <N>` — default 200, minimum 20 (below minimum raises `InsufficientData`).
- `--valset-size <N>` — GEPA/MIPROv2 only; default 50.
- `--reflection-lm <id>` — GEPA only; overrides env default.
- `--auto <light|medium|heavy>` — MIPROv2 only; default `medium`.
- `--dry-run` — log what would run; write nothing.

**Exit**: `0` on success with row written; `1` on `InsufficientData`; `2` on optimizer crash (partial run persisted for debug).

---

## `sio status` (FR-016, expanded)

Comprehensive health surface.

**Output sections**:

```
SIO Status — 2026-04-20 14:32:11 UTC

Hooks
  post_tool_use    ✓ healthy  last_success 45s ago  (0 consec failures)
  stop             ✓ healthy  last_success 45s ago
  pre_compact      ⚠ stale    last_success 6h ago   (threshold 1h)

Mining
  sio mine         last_run 2h ago   error_records 45,536  last_session ...
  sio flows        last_run 2h ago   flow_events   66,567

Training
  behavior_invocations (sio.db)   38,091   ↔ claude-code per-platform: 38,091  ✓ in sync
  gold_standards                     152
  optimized_modules                    3   (active: suggestion_generator gepa score=0.71)
  optimization_runs                    1

Audit
  applied_changes (active)             4
  autoresearch_txlog (24h)             6   last fired 4h ago

Database
  ~/.sio/sio.db                      258 MB
  schema_version                     latest=2 status=applied
```

**Exit**: `0` if all sections green/warn; `1` if any section error (stale > error-threshold, `schema_version='applying'`, sync drift > 5%).

**Latency**: < 2 s on typical store (SC-009).

---

## `sio purge` (FR-025)

**Behavior (changed)**:
- Targets `~/.sio/sio.db` (NOT the per-platform DB — fixes audit finding M7).
- Default: purge `error_records`, `flow_events` WHERE `mined_at < now() - --days`.
- `--behavior-only` (new flag): additionally purge `behavior_invocations` (per-platform + canonical sync).

**Flags**:
- `--days <N>` — retention window (default 30).
- `--behavior-only` — include behavior_invocations.
- `--yes` — skip confirmation.

---

## `sio autoresearch` (FR-006, new)

Scheduler-invoked loop.

**Behavior (new)**:
- `sio autoresearch --run-once` — single firing. Selects candidates with `arena_passed=1` AND metric > threshold; writes each to `autoresearch_txlog` with outcome (`promoted` / `pending_approval` / `rejected_metric` / `rejected_arena`).
- `sio autoresearch --install-schedule <cron|systemd>` — installs the schedule (R-3). `cron` uses Claude Code `CronCreate`; `systemd` writes a user unit.
- Auto-promotion requires `arena_passed=1` AND (operator-approved OR `--auto-approve-above <threshold>`). Default: gate at approval.

---

## `sio db migrate` (new, FR-017)

**Behavior**: Runs the idempotent migration from data-model.md §5. Refuses to run if `schema_version` has `status='applying'` (operator must resolve manually via `sio db repair`).

---

## `sio db repair` (new)

**Behavior**: Prompts operator to either roll forward the stuck migration or mark it failed. Writes `(N, now(), 'failed', ...)`.

---

## Contract Test Coverage Matrix

| Command | Integration test file |
|---|---|
| `sio install` | `tests/integration/test_installer_idempotent.py` (SC-014) |
| `sio mine` + `sio flows` | `tests/integration/test_mining_idempotence.py` (SC-006, SC-007) |
| `sio suggest` | `tests/integration/test_suggest_non_destructive.py` (SC-002) |
| `sio apply` | `tests/integration/test_apply_safety.py` (SC-003) |
| `sio optimize` | `tests/integration/test_dspy_idiomatic.py` + `test_gepa_vs_baseline.py` (SC-017, SC-018) |
| `sio status` | `tests/integration/test_sio_status_health.py` (SC-009) |
| `sio autoresearch` | `tests/integration/test_autoresearch_cadence.py` (SC-005) |
| `sio db migrate` | `tests/unit/db/test_schema_version.py` |
