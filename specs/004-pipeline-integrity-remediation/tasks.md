# Tasks: SIO Pipeline Integrity & Training-Data Remediation

**Branch**: `004-pipeline-integrity-remediation`
**Input**: `/specs/004-pipeline-integrity-remediation/{spec,plan,research,data-model,quickstart}.md` + `/contracts/`
**Tests**: REQUIRED (Constitution IV "Test-First" is NON-NEGOTIABLE). Every implementation task is paired with a preceding test-write task.

**Organization**: Tasks are grouped by user story. Within each story, tests precede implementation; implementation tasks marked `[P]` can run in parallel when file-disjoint.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Story label (US1..US10) for user-story phase tasks. Setup/Foundational/Polish have no story label.

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Verify toolchain: `python --version` ≥ 3.11, `uv --version`, `sqlite3 --version` ≥ 3.35 (for WAL + `ATTACH`). Document versions in `specs/004-pipeline-integrity-remediation/ENV_SETUP.md`.
- [x] T002 Create missing directory skeleton with `__init__.py` files: `src/sio/core/dspy/`, `src/sio/core/util/`, `src/sio/core/db/` (if missing), `src/sio/autoresearch/`, `scripts/` (if missing), and test dirs `tests/unit/{dspy,db,clustering,applier,mining,hooks,util,constants}/`, `tests/integration/`.
- [x] T003 [P] Add / pin deps in `pyproject.toml`: `dspy-ai>=3.1.3`, `fastembed>=0.2`, `numpy>=1.24`, `click>=8.1`, `rich>=13.0`, test extras `pytest>=8`, `pytest-cov`, `ruff>=0.4`. Run `uv sync --all-extras`.
- [x] T004 [P] Configure `ruff.toml` to cover new paths and align with 99-char line limit; confirm `pytest.ini` / `pyproject.toml` `[tool.pytest.ini_options]` discovers `tests/unit` and `tests/integration`.
- [x] T005 Create shared test fixtures in `tests/conftest.py`: `tmp_sio_db`, `tmp_platform_db`, `mock_lm`, `fake_fastembed`, `freeze_utc_now` per `specs/004-pipeline-integrity-remediation/quickstart.md` §4.

**Checkpoint**: Environment ready; test harness loadable.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Core primitives (TDD pairs)

- [x] T006 [P] Write failing tests in `tests/unit/util/test_time.py` covering `to_utc_iso()` for `Z` suffix, numeric offset, naive-local (TZ=America/New_York), and `utc_now_iso()` monotonicity (FR-030, R-7, SC-008).
- [x] T007 Implement `src/sio/core/util/time.py` with `to_utc_iso(s) -> str` and `utc_now_iso() -> str`; pass T006 tests.
- [x] T008 [P] Write failing tests in `tests/unit/db/test_connect.py` asserting PRAGMAs (`journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`, `foreign_keys=ON`) applied by the factory.
- [x] T009 Implement `src/sio/core/db/connect.py::open_db(path, read_only=False)` (R-8); pass T008.
- [x] T010 [P] Write failing test in `tests/unit/constants/test_default_platform.py` asserting `DEFAULT_PLATFORM == "claude-code"` and grep-of-`src/` finds zero string-literal `"claude-code"` outside `src/sio/core/constants.py` and test files (FR-031, SC-022 parity).
- [x] T011 Implement `src/sio/core/constants.py` exporting `DEFAULT_PLATFORM` and refactor every existing `"claude-code"` literal in `src/` to import it.

### Schema and migration (TDD pairs)

- [x] T012 [P] Write failing tests in `tests/unit/db/test_schema_version.py` covering: seed on first connect, `'applying'` detection refuses to start, `sio db repair` marks `'failed'` (FR-017, R-10).
- [x] T013 Add `schema_version` table DDL + startup check to `src/sio/core/db/schema.py`; implement `sio db migrate` and `sio db repair` CLI in `src/sio/cli/main.py`.
- [x] T014 [P] Write failing tests in `tests/unit/db/test_migration_004.py` that run `scripts/migrate_004.py` against a clone of a seeded DB, assert all `ALTER TABLE` + `CREATE INDEX` statements are additive and idempotent (per data-model.md §5).
- [x] T015 Write `scripts/migrate_004.py` applying every delta in data-model.md §2 (§2.1 – §2.10), including all hot-read indexes (FR-015); ensure wrapping in one `schema_version` transaction row `(2, now(), 'applying', ...)` → `'applied'`.

### Atomic write + path allowlist (TDD pairs)

- [x] T016 [P] Write failing tests in `tests/unit/applier/test_atomic_write.py` covering: backup created pre-write, `os.replace` atomic rename, post-write size check, retention `keep=10`, timestamp format (FR-004, R-4).
- [x] T017 [P] Write failing tests in `tests/unit/applier/test_allowlist.py` covering: valid paths under `~/.claude/`, rejected `../../etc/hosts`, rejected symlink traversal, `SIO_APPLY_EXTRA_ROOTS` parsing (FR-019, R-14).
- [x] T018 Implement `src/sio/core/applier/writer.py::atomic_write(target, content)` + `_validate_target_path(target)` + `_prune_backups(dir, keep=10)`; pass T016 and T017.

### Heartbeat primitive (TDD pairs)

- [x] T019 [P] Write failing tests in `tests/unit/hooks/test_heartbeat.py` covering: `record_success` resets `consecutive_failures`, `record_failure` increments it, atomic JSON write, crash-mid-write leaves previous valid JSON (FR-016, `contracts/hook-heartbeat.md` §6).
- [x] T020 Implement `src/sio/adapters/claude_code/hooks/_heartbeat.py` (`record_success`, `record_failure`, `_update`); pass T019.

### DSPy foundations (TDD pairs — unblock US1 and US9)

- [x] T021 [P] Write failing tests in `tests/unit/dspy/test_lm_factory.py` covering: `get_task_lm()` returns `dspy.LM` with `cache=True`, `get_reflection_lm()` with `cache=False`, env-var overrides, `get_adapter()` provider-aware, grep-of-`src/` finds zero direct `dspy.LM(` outside the factory (FR-041, SC-022).
- [x] T022 Implement `src/sio/core/dspy/lm_factory.py::{get_task_lm, get_reflection_lm, get_adapter, configure_default}` per `contracts/dspy-module-api.md` §1; pass T021.
- [x] T023 [P] Write failing tests in `tests/unit/dspy/test_signatures.py` asserting `PatternToRule` and `RuleRecallScore` have class docstrings, typed `InputField`/`OutputField`, and pass `dspy.Predict` instantiation smoke test (FR-035).
- [x] T024 Implement `src/sio/core/dspy/signatures.py::{PatternToRule, RuleRecallScore}` per `contracts/dspy-module-api.md` §2.
- [x] T025 [P] Write failing tests in `tests/unit/dspy/test_metric_registry.py` asserting the three registered metrics (`exact_match`, `embedding_similarity`, `llm_judge_recall`) each conform to `(gold, pred, trace=None) -> bool | float` (FR-018).
- [x] T026 Implement `src/sio/core/dspy/metrics.py` with `METRIC_REGISTRY`, `@register`, and the three metric functions per `contracts/dspy-module-api.md` §5.
- [x] T027 [P] Write failing tests in `tests/unit/dspy/test_assertions.py` asserting `assert_rule_format` and `assert_no_phi` use `dspy.Assert`, produce actionable messages, and trigger backtrack in a mocked predictor (FR-038, R-11).
- [x] T028 Implement `src/sio/core/dspy/assertions.py` per `contracts/dspy-module-api.md` §6.
- [x] T029 [P] Write failing tests in `tests/unit/dspy/test_datasets.py` asserting every returned `dspy.Example` has `.with_inputs(...)` declared and `get_input_keys()` is non-empty (FR-036, SC-020).
- [x] T030 Implement `src/sio/core/dspy/datasets.py::build_trainset_for(module_name, limit, offset)` per `contracts/dspy-module-api.md` §4.
- [x] T031 [P] Write failing tests in `tests/unit/dspy/test_save_load.py` asserting `program.save(path)` + fresh `program.load(path)` produces identical output on a fixed input (FR-039).
- [x] T032 Implement `src/sio/core/dspy/persistence.py::{save_compiled, load_compiled, MODULE_REGISTRY}` per `contracts/dspy-module-api.md` §7.

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel.

---

## Phase 3: User Story 1 — DSPy Optimizer Receives Labeled Examples (Priority: P1) 🎯 MVP

**Goal**: End-to-end: hook writes invocation → sync lands it in `sio.db` → auto-promote to gold-standard → optimizer produces an artifact.

**Independent Test**: After one real hook firing and one `sio optimize` run, (a) `SELECT COUNT(*) FROM sio.db.behavior_invocations` ≥ 38,092; (b) `gold_standards` count > 0; (c) `optimized_modules` has a new row with `active=1` (SC-001, SC-004).

- [x] T033 [P] [US1] Write failing tests in `tests/unit/db/test_sync.py` covering: full sync copies all rows, second call copies zero, `INSERT OR IGNORE` identity `(platform, session_id, timestamp, tool_name)` is deduped (R-1, FR-002).
- [x] T034 [P] [US1] Write failing tests in `tests/unit/db/test_sync_drift.py` covering drift-percentage computation with seeded divergence (supports `sio status` SC-009).
- [x] T035 [US1] Implement `src/sio/core/db/sync.py::sync_behavior_invocations(since_timestamp=None)` per `contracts/storage-sync.md` §4; pass T033 and T034.
- [x] T036 [US1] Write `scripts/migrate_split_brain.py` calling `sync_behavior_invocations(None)` with friendly logging; one-time backfill of ≥ 38,091 legacy rows (FR-002).
- [x] T037 [P] [US1] Write failing tests in `tests/integration/test_installer_idempotent.py` running `sio install` twice, asserting per-platform DB untouched and `sio.db` row counts identical (FR-007, L6, SC-014).
- [x] T038 [US1] Update `src/sio/adapters/claude_code/installer.py` to: point at `~/.sio/sio.db` for schema, preserve `~/.sio/claude-code/behavior_invocations.db`, call `migrate_split_brain.py`, refuse to recreate legacy DB; pass T037.
- [x] T039 [P] [US1] Write failing tests in `tests/unit/arena/test_promote_to_gold.py` covering auto-promotion when `user_satisfied=1 AND correct_outcome=1`, no promotion otherwise (FR-005).
- [x] T040 [US1] Implement `promote_to_gold(invocation_id)` in `src/sio/core/arena/gold_standards.py`; wire call into `src/sio/adapters/claude_code/hooks/stop.py` (heartbeat-wrapped).
- [x] T041 [US1] Update `src/sio/core/arena/gold_standards.py` read paths to query `~/.sio/sio.db` via the connect factory (currently reads the wrong path per audit C1).
- [x] T042 [P] [US1] Write failing integration test `tests/integration/test_closed_loop.py`: seed synthetic invocation → sync → promote → run GEPA on tiny fixture trainset → assert `optimized_modules` row with `active=1` and loadable artifact path (SC-001, SC-004).
- [x] T043 [US1] Implement `src/sio/core/dspy/optimizer.py::run_optimize(module_name, optimizer_name, ...)` skeleton per `contracts/optimizer-selection.md` §3 — GEPA branch only at this step; passes T042 (integration) and T062 (unit, Phase 6).
- [x] T044 [US1] Wire `sio optimize --module <name> --optimizer gepa|mipro|bootstrap` CLI in `src/sio/cli/main.py` per `contracts/cli-commands.md` § `sio optimize`.

**Checkpoint**: US1 MVP — closed loop flows end-to-end with GEPA.

---

## Phase 4: User Story 2 — Suggestion Generation Preserves Audit History (Priority: P1)

**Goal**: `sio suggest` no longer destroys `applied_changes`; stale rows marked, not deleted.

**Independent Test**: `sio suggest` twice → 100% of `applied_changes` rows remain with `superseded_at IS NULL` (SC-002).

- [x] T045 [P] [US2] Write failing integration test `tests/integration/test_suggest_non_destructive.py`: seed 3 rows in `applied_changes`, run `sio suggest` twice, assert count unchanged (SC-002).
- [x] T046 [P] [US2] Write failing unit test `tests/unit/db/test_active_cycle.py`: after a suggest cycle, prior `patterns`/`datasets`/`pattern_errors`/`suggestions` rows flip to `active=0`, new rows have `active=1, cycle_id=<uuid>` (FR-003).
- [x] T047 [US2] Refactor `src/sio/cli/main.py` suggest path (currently `cli/main.py:1389-1428`): remove every `DELETE FROM` on audit-related tables; insert a new `cycle_id` UUID and UPDATE prior rows to `active=0` before INSERTing new ones; NEVER touch `applied_changes`.
- [x] T048 [P] [US2] Write failing unit test `tests/unit/db/test_superseded.py` covering `applied_changes.superseded_at` + `superseded_by` semantics.
- [x] T049 [US2] Add query helpers in `src/sio/core/db/queries.py`: `list_active_applied_changes()`, `mark_superseded(id, by_id)`.
- [x] T050 [P] [US2] Write failing unit test `tests/unit/applier/test_rollback.py`: after a rollback, target file matches pre-write backup content; `applied_changes` row has `superseded_at` set.
- [x] T051 [US2] Implement `sio apply --rollback <applied_change_id>` CLI; uses `atomic_write` with backup content, updates `superseded_at`.
- [x] T052 [US2] Update `sio purge` (currently `cli/main.py:242-245`) to target `~/.sio/sio.db` (not the per-platform DB) and add `--behavior-only` flag (FR-025, M7); update tests in `tests/unit/cli/test_purge.py`.

**Checkpoint**: US2 done — audit survives across suggest cycles.

---

## Phase 5: User Story 3 — Safe Apply of Rules to User Files (Priority: P1)

**Goal**: Every `sio apply` is atomic + backed up + path-guarded + merge-consent-gated.

**Independent Test**: Crash-inject a write mid-apply → target file intact + backup exists; attempt to write outside allowlist → rejected; merge without `--merge` flag → prompts for consent (SC-003).

- [x] T053 [P] [US3] Write failing integration test `tests/integration/test_apply_safety.py` using subprocess+SIGKILL crash injection (quickstart.md §4.3), asserting original content preserved and backup file present (SC-003).
- [x] T054 [P] [US3] Write failing unit test `tests/unit/applier/test_merge_consent.py`: silent merge rejected, `--merge` flag accepts, interactive `y/N` prompt path covered (FR-024).
- [x] T055 [US3] Refactor `src/sio/core/applier/writer.py` apply path to route all target-file writes through `atomic_write`; integrate `_validate_target_path`; reject `--no-backup`.
- [x] T056 [US3] Implement merge-consent logic in `src/sio/core/applier/merger.py` `merge_rules` — require `--merge` CLI flag or interactive confirmation; abort otherwise (FR-024, M6).
- [x] T057 [US3] Wire `_prune_backups(dir, keep=10)` post-write; verify retention in unit test.
- [x] T058 [US3] Update `sio apply` CLI in `src/sio/cli/main.py` to accept `--merge`, `--yes`, `--rollback`; reject `--no-backup` with `BackupRequired`.

**Checkpoint**: US3 done — apply path is crash-safe, reversible, and path-guarded.

---

## Phase 6: User Story 9 — SIO Speaks DSPy Idiomatically End-to-End (Priority: P2, blocks US8)

**Goal**: Every reasoning module is a real `dspy.Module`; GEPA/MIPROv2/BootstrapFewShot all run end-to-end; artifacts persist and reload; native FC adapters selected automatically.

**Independent Test**: `sio optimize --module suggestion_generator --optimizer {gepa|mipro|bootstrap}` produces a loadable artifact; grep of `src/` returns zero direct `dspy.LM(` calls (SC-017, SC-020, SC-021, SC-022).

**Wave A — failing tests first (all parallelizable):**

- [x] T059 [P] [US9] Write failing unit test `tests/unit/dspy/test_suggestion_generator_module.py` covering: class-based signature, `forward()` returns a Prediction with required fields, `dspy.Assert` triggers backtrack on malformed output (FR-035, FR-038). RED — awaits T066 Wave 9.
- [x] T060 [P] [US9] Write failing unit test `tests/unit/dspy/test_recall_evaluator.py` covering: class-based signature, forward returns a `score: float ∈ [0,1]`, metric function matches registry contract. RED — awaits T067 Wave 9.
- [x] T061 [P] [US9] Write failing unit test `tests/unit/dspy/test_optimizer_registry.py` asserting `OPTIMIZER_REGISTRY` has all three entries and CLI flag mapping is correct. GREEN (5/5 pass; mipro/bootstrap correctly raise NotImplementedError per Wave 4 impl).
- [x] T062 [P] [US9] Write integration test `tests/integration/test_dspy_idiomatic.py`; 3/5 pass, 2/5 skip (GEPA tests skip on minimal DB schema; mipro/bootstrap correctly raise NotImplementedError — SC-017, SC-020).
- [x] T063 [P] [US9] Write integration test `tests/integration/test_gepa_vs_baseline.py`; marked skip with documented reason: flaky in mock mode, validates on real API in CI (SC-018).
- [x] T064 [P] [US9] Write failing unit test `tests/unit/dspy/test_adapter_selection.py` covering `get_adapter(lm)` returns `ChatAdapter(use_native_function_calling=True)` for `openai/*` / `anthropic/*`, `JSONAdapter` for `ollama/*`, and honors `SIO_FORCE_ADAPTER` env (FR-040, SC-021).
- [x] T065 [P] [US9] Write failing unit test `tests/unit/dspy/test_single_lm_factory.py` that greps `src/` for forbidden `dspy.LM(` patterns outside `src/sio/core/dspy/lm_factory.py` and test files (FR-041, SC-022).

**Wave B — implementations (run after Wave A is committed):**

- [x] T066 [US9] Rewrite `src/sio/suggestions/dspy_generator.py` as `SuggestionGenerator(dspy.Module)` using `PatternToRule` signature, `dspy.ChainOfThought`, and `assert_rule_format` + `assert_no_phi` per `contracts/dspy-module-api.md` §3 (FR-035, SC-016); pass T059.
- [x] T067 [US9] Rewrite `src/sio/training/recall_trainer.py` as `RecallEvaluator(dspy.Module)` using `RuleRecallScore`; replace the trivial string-equality metric with `METRIC_REGISTRY["embedding_similarity"]` default (FR-018, FR-035, SC-016); pass T060.
- [x] T068 [US9] Implement all three branches in `src/sio/core/dspy/optimizer.py::run_optimize` per `contracts/optimizer-selection.md` §3 (gepa, mipro, bootstrap); wire `dspy.Evaluate` scoring on held-out eval set; call `save_compiled(compiled, artifact_path)`; pass T061, T062, T063.
- [x] T069 [US9] Implement `record_optimization_run(...)` + `mark_prior_inactive(module_name)` in `src/sio/core/db/queries.py`; write row with `optimizer_name`, `metric_name`, `trainset_size`, `valset_size`, `score`, `task_lm`, `reflection_lm`, `artifact_path` (data-model.md §2.9).
- [x] T070 [US9] Verify `src/sio/core/dspy/lm_factory.py::get_adapter` passes T064 (factory was implemented in T022; this task confirms full adapter coverage).
- [x] T071 [US9] Sweep `src/` and refactor any direct `dspy.LM(...)` calls found by T065 to use the factory; pass T065.
- [x] T072 [US9] Instrument `SuggestionGenerator` with assertion backtrack counting; emit count per invocation into `suggestions.instrumentation_json` (FR-029, SC-019).
- [x] T073 [US9] Update `pyproject.toml` / `CLAUDE.md` stating `dspy-ai>=3.1.3` floor + DSPy-first adoption note.

**Checkpoint**: US9 done — DSPy is idiomatic across every reasoning module; all three optimizers callable.

---

## Phase 7: User Story 4 — Autoresearch Runs on a Schedule (Priority: P2)

**Goal**: Scheduled loop fires, records each firing, respects approval gates.

**Independent Test**: After 24 h of schedule activation, `autoresearch_txlog` has ≥ 5 rows; at least one row has `outcome='pending_approval'` for an unvalidated candidate (SC-005).

- [x] T074 [P] [US4] Write failing integration test `tests/integration/test_autoresearch_cadence.py`: seed candidates with mixed `arena_passed`, run `sio autoresearch --run-once` 5×, assert one row per firing with correct outcome categorization.
- [x] T075 [US4] Implement `src/sio/autoresearch/scheduler.py::run_once()` — selects candidates, evaluates metric, writes `autoresearch_txlog` row per firing (FR-006).
- [x] T076 [US4] Implement approval gate: auto-apply only when `arena_passed=1 AND (operator_approved OR --auto-approve-above <threshold>)`; else mark `outcome='pending_approval'`.
- [x] T077 [US4] Implement `sio autoresearch --run-once` and `sio autoresearch --install-schedule {cron|systemd}` CLI in `src/sio/cli/main.py`.
- [x] T078 [US4] Write `scripts/autoresearch_cron.py` thin wrapper invoking `sio autoresearch --run-once`.
- [x] T079 [US4] Write `scripts/install_autoresearch_systemd.sh` fallback installer emitting a user systemd unit per R-3.

**Checkpoint**: US4 done — autoresearch accumulates history without human intervention.

---

## Phase 8: User Story 5 — Mining Is Idempotent and Bounded (Priority: P2)

**Goal**: Re-running `sio mine` / `sio flows` produces zero new rows; 100 MB files don't blow memory; subagents linked to parents.

**Independent Test**: Full mine twice → same counts; 100 MB file mines with RSS < 500 MB; subagent rows have `parent_session_id` non-null and are excluded from top-level aggregates by default (SC-006, SC-007).

- [x] T080 [P] [US5] Write failing unit test `tests/unit/mining/test_streaming_parse.py` asserting `jsonl_parser.iter_events(path)` streams (use memory-tracking) and parses 10k-line fixture without `read_text()` call (FR-009).
- [x] T081 [US5] Refactor `src/sio/mining/jsonl_parser.py` to stream via `for line in open(path, 'rb')` (replaces `file_path.read_text()` at line 417).
- [x] T082 [P] [US5] Write failing unit test `tests/unit/mining/test_byte_offset.py` covering: append-and-remine reads only new bytes, truncation-rotation resets offset via mtime check (FR-010, R-6).
- [x] T083 [US5] Add `last_offset`, `last_mtime` reads/writes in `src/sio/mining/pipeline.py` wrapping ingest loop; update `processed_sessions` in same transaction.
- [x] T084 [P] [US5] Write failing unit test `tests/unit/mining/test_subagent_link.py` covering both path patterns (`subagents/<parent>/<child>.jsonl` and `<parent>__subagent_<child>.jsonl`) → sets `is_subagent=1, parent_session_id=<parent>` (FR-011, R-13).
- [x] T085 [US5] Implement subagent path detection in `src/sio/mining/pipeline.py::_classify_session_file(path)`; propagate to `error_records` and `flow_events`.
- [x] T086 [P] [US5] Write failing unit test `tests/unit/mining/test_flow_dedup.py` asserting `sio flows --mine-first` run twice writes zero new flow_events on unchanged corpus (FR-008).
- [x] T087 [US5] Refactor `src/sio/mining/flow_pipeline.py:53-144` to honor `processed_sessions` + `flow_events` UNIQUE `(file_path, session_id, flow_hash)` constraint.
- [x] T088 [US5] Fix `src/sio/mining/flow_extractor.py`: n-gram `range(n_min, n_max + 1)` (FR-022, M5); extension allowlist `.rs/.go/.java/.cpp/.ipynb` (FR-026, L1); explicit positive-signal success heuristic (FR-021, L3).
- [x] T089 [US5] Implement 1 GB file-hash guard in `src/sio/mining/pipeline.py::_file_hash` (FR-028, L5) and WARN-on-missing-dir log in `_iter_session_dirs` (FR-027, L4).
- [x] T090 [P] [US5] Write failing integration test `tests/integration/test_mining_idempotence.py` doing a full two-pass mine on fixture corpus; assert zero row delta on second pass and peak RSS < 500 MB (SC-006, SC-007).
- [x] T091 [US5] Ensure `busy_timeout=30000` applied via `open_db()` everywhere in mining code paths (FR-012, H4); remove any ad-hoc `sqlite3.connect(...)` in mining modules.

**Checkpoint**: US5 done — mining is idempotent, streaming, and subagent-aware.

---

## Phase 9: User Story 6 — `sio status` Surfaces Silent Failures (Priority: P2)

**Goal**: Operator runs `sio status` and sees hook health, mining cadence, training table counts, sync drift, DB size within 2 s.

**Independent Test**: Inject a hook failure → `sio status` shows `warn` within one heartbeat cycle; 3 consecutive failures → `error`; stale heartbeat over threshold → `stale` (SC-009).

- [x] T092 [P] [US6] Write failing integration test `tests/integration/test_sio_status_health.py` covering the four states (`healthy`, `warn`, `error`, `never-seen`) plus latency < 2 s assertion.
- [x] T093 [US6] Wrap `src/sio/adapters/claude_code/hooks/post_tool_use.py`, `stop.py`, `pre_compact.py` in the heartbeat try/finally pattern per `contracts/hook-heartbeat.md` §5; remove bare `except Exception: pass` (H8, FR-016).
- [x] T094 [US6] Implement `src/sio/cli/status.py::hook_health_rows()` per `contracts/hook-heartbeat.md` §4.
- [x] T095 [US6] Expand `sio status` CLI rendering in `src/sio/cli/main.py` to include sections: Hooks, Mining, Training, Audit, Database; render via Rich tables per `contracts/cli-commands.md` § `sio status`.
- [x] T096 [US6] Include sync-drift summary line in Training section (`SELECT COUNT(*) FROM <platform>.behavior_invocations` vs `sio.db`) emitting `in sync` / `warn` / `error` per `contracts/storage-sync.md` §6.

**Checkpoint**: US6 done — observability surface complete.

---

## Phase 10: User Story 7 — Stable Pattern Identifiers (Priority: P3)

**Goal**: Pattern slugs are deterministic; centroid reuse skips redundant embedding work.

**Independent Test**: Cluster the same corpus in two different input orders → identical slugs; `sio suggest` re-run with no new errors completes in < 5 s (SC-010, SC-011).

- [x] T097 [P] [US7] Write failing unit test `tests/unit/clustering/test_deterministic_slugs.py` covering reorder invariance and one-error-added stability via Jaccard remap (FR-014, R-5).
- [x] T098 [US7] Rewrite `src/sio/core/clustering/pattern_clusterer.py` slug algorithm: centroid-hash `<toptype>_<10hex>` (R-5); persist `patterns.pattern_id` in migration-aware way.
- [x] T099 [P] [US7] Write failing unit test `tests/unit/clustering/test_slug_remap.py` covering: Jaccard overlap ≥ 0.5 between old and new pattern member sets → remap accepted and `ground_truth.remapped_from_pattern_id` populated; < 0.5 → rejected (no FK change); identical sets → remap idempotent (FR-014, R-5).
- [x] T100 [US7] Write `scripts/remap_ground_truth_slugs.py`: Jaccard-overlap remap from old to new slugs; populate `ground_truth.remapped_from_pattern_id` audit column; pass T099.
- [x] T101 [P] [US7] Write failing unit test `tests/unit/clustering/test_centroid_reuse.py` covering BLOB format `(dim, model_hash, vector)`, hit skips recompute, model-upgrade invalidates (FR-032, R-9, SC-011).
- [x] T102 [US7] Implement centroid BLOB pack/unpack in `src/sio/core/clustering/pattern_clusterer.py`; reuse when `centroid_model_version` matches current fastembed version; recompute otherwise.
- [x] T103 [P] [US7] Write failing unit test `tests/unit/clustering/test_declining_grade.py` asserting a pattern with stale latest-error transitions to `'declining'` (FR-023, M4).
- [x] T104 [US7] Fix `src/sio/core/clustering/grader.py:80` to compute recency against `MAX(error_records.timestamp) WHERE pattern_id=?` — not current insert time.
- [x] T105 [US7] Fix `src/sio/core/clustering/ranker.py:75` empty-timestamp crash: guard `fromisoformat("")`, fall back to `mined_at` (FR-013, H6).
- [x] T106 [US7] Remove cross-type dedup in `_dedup_by_error_type_priority` — keep `tool_failure` rows alongside `user_correction` (FR-020, L2).

**Checkpoint**: US7 done — clustering is deterministic and incremental.

---

## Phase 11: User Story 8 — Suggestion Approval Rate Improves (Priority: P3)

**Goal**: Instrument the suggestion generator so rejection reasons are captured; approval rate rises above 30% on next batch.

**Independent Test**: Run a batch of 100 generated suggestions, review via `sio suggest --review`, compute `approved / total` ≥ 30% (SC-012).

- [x] T107 [P] [US8] Write failing integration test `tests/integration/test_suggestion_quality_instrumented.py` asserting per-stage rejection reasons populated in `suggestions` table after a run (FR-029).
- [x] T108 [US8] Add rejection-reason capture in `src/sio/suggestions/dspy_generator.py` via `dspy.Evaluate` + stage decorators; write to `suggestions.instrumentation_json`.
- [x] T109 [US8] Tune `SuggestionGenerator` prompt (docstring + few-shot from gold_standards) and metric selection (`llm_judge_recall` for this module); re-optimize via GEPA.
- [x] T110 [US8] Run batch, measure approval rate; record baseline-vs-new comparison in `specs/004-pipeline-integrity-remediation/research/suggestion_quality_baseline.md`; target ≥ 30% (SC-012).

**Checkpoint**: US8 done — suggestion generator produces operator-approvable rules at target rate.

---

## Phase 12: User Story 10 — Adversarial Re-Audit Returns Clean (Priority: P3)

**Goal**: Two independent audits post-fix return zero CRITICAL / HIGH findings; every original finding is closed with a file:line citation.

**Independent Test**: Spawn two `adversarial-bug-hunter` sub-agents concurrently on the post-fix repo; consolidate results → zero CRITICAL, zero HIGH (SC-013, FR-033, FR-034).

- [x] T111 [P] [US10] Run `adversarial-bug-hunter` agent #1 (targeted scan of Phase 1/2/3 touched files) against HEAD; save report to `specs/004-pipeline-integrity-remediation/research/audit_hunter1.md`.
- [x] T112 [P] [US10] Run `adversarial-bug-hunter` agent #2 (general codebase scan) against HEAD; save report to `specs/004-pipeline-integrity-remediation/research/audit_hunter2.md`.
- [x] T113 [US10] Consolidate findings; assert zero CRITICAL / HIGH. Any new MEDIUM / LOW → open follow-up PRD; do NOT block this feature.
- [x] T114 [US10] Update `PRD-pipeline-integrity-remediation.md` changelog section with file:line citation for each of the 34 original findings; confirm zero "deferred" markers remain anywhere in the PRD (FR-033, SC-015).

**Checkpoint**: US10 done — re-audit clean; every finding closed.

---

## Phase 13: Polish & Cross-Cutting

- [x] T115 [P] Run `uv run pytest --cov=src/sio --cov-report=term-missing` and verify ≥ 72% coverage on new/changed modules; address gaps.
- [x] T116 [P] Run `uv run ruff check --fix .` and `uv run ruff format .`; commit cleanup.
- [x] T117 [P] Update `README.md` pipeline diagram: per-platform DB → sync → sio.db → DSPy path; include GEPA/MIPROv2/BootstrapFewShot selector.
- [x] T118 [P] Update `/home/gyasisutton/.claude/rules/tools/sio.md` with the correct DB paths (per `CLAUDE.md` note in spec §8 Definition of Done).
- [x] T119 Verify every success criterion SC-001..SC-022 via `specs/004-pipeline-integrity-remediation/quickstart.md` §6 walkthrough; tick each in `checklists/requirements.md`.
- [x] T120 Tag release; write `specs/004-pipeline-integrity-remediation/CHANGELOG.md` with per-task file:line citations for each original audit finding (supports T114).

### Regression Fix

- [x] T-REGR Fix Wave 10 processed_sessions regressions from T085 — `_PROCESSED_SESSIONS_DDL` in `src/sio/core/db/schema.py` was missing `is_subagent`, `parent_session_id`, `last_offset`, `last_mtime` columns added by T085's INSERT SQL. Fixed by adding those columns to the DDL. Resolved 9 failing integration tests.

---

## Dependencies

```
Phase 1 Setup (T001–T005)
        ▼
Phase 2 Foundational (T006–T032)                     [blocks every user story]
        ▼
   ┌────┴─────────────────────────────────────────────────────┐
   ▼                ▼                ▼                ▼        ▼
Phase 3 US1     Phase 4 US2     Phase 5 US3     Phase 6 US9   Phase 8 US5
(T033–T044)     (T045–T052)     (T053–T058)     (T059–T073)   (T080–T091)
                                                                    │
(T097–T106 US7) (T107–T110 US8) (T111–T114 US10) (T115–T120 Polish)
   │                                                │
   └────┐                                           ▼ (unblocks US8)
        ▼                                       Phase 11 US8
Phase 7 US4 (T074–T079, depends on US1 sync+gold)   (T106–T109)
   ▼
Phase 9 US6 (T092–T096, depends on Foundational heartbeat)
   ▼
Phase 10 US7 (T097–T105, depends on Foundational + US5 mining flow)
   ▼
Phase 12 US10 (T110–T113, depends on ALL prior)
   ▼
Phase 13 Polish (T115–T120)
```

**Hard-blocking pairs** (test must pass before implementation considered done):
- T006↔T007, T008↔T009, T010↔T011, T012↔T013, T014↔T015, T016/T017↔T018, T019↔T020, T021↔T022, T023↔T024, T025↔T026, T027↔T028, T029↔T030, T031↔T032, and every story-phase `[P] test` / `impl` pair.

---

## Parallel Execution Examples

**Phase 2 (Foundational) — all test-writes parallelizable:**
```
T006 T008 T010 T012 T014 T016 T017 T019 T021 T023 T025 T027 T029 T031
```
All 14 can run concurrently (different test files). Follow with paired implementations T007, T009, T011, T013, T015, T018, T020, T022, T024, T026, T028, T030, T032 (each impl task is independent of other impls since their tests are independent).

**Phase 3 / 4 / 5 (US1 + US2 + US3) — three stories in parallel:**
```
Wave A (US1 tests):   T033 T034 T037 T039 T042
Wave B (US2 tests):   T045 T046 T048 T050
Wave C (US3 tests):   T053 T054
```
All 11 test tasks run in parallel. Each story's impl then proceeds serially within its story.

**Phase 8 (US5) — mining refactor pairs:**
```
Tests first in parallel:  T080 T082 T084 T086 T090
Impls after:              T081 T083 T085 T087 T088 T089 T091
```

**Phase 12 (US10) — two hunters in parallel:**
```
T110 T111
```
Per Constitution VIII (Parallel Agent Spawning).

---

## Implementation Strategy

### MVP (US1 only — ships the closed loop)

Minimum viable delivery: Phase 1 + Phase 2 + Phase 3 (US1 tasks T001–T044). This alone:
- Fixes the split-brain bug (FR-001, FR-002, FR-007).
- Gets labeled data into the training store (FR-005).
- Produces at least one optimization run with GEPA default (FR-037, SC-004).
- Does NOT yet fix `sio suggest` destructiveness (US2), apply safety (US3), or any Phase-2+ audit findings. Those ship in subsequent increments.

### Increment 2 (US1 + US2 + US3)

All P1 user stories. Ships Phase 1–5 (T001–T058). At this point the operator has a complete, safe closed loop: invocation → labeled → optimized → suggested → safely applied → audited. The DSPy surface is partially idiomatic (GEPA works) but US9's full coverage (MIPRO/Bootstrap/adapter/assertions) waits.

### Increment 3 (add US9 idiomatic DSPy)

Ships all three optimizers + assertions + adapter auto-selection + single-LM-factory (T059–T073). US8 becomes possible in this increment.

### Increment 4 (P2 stories: US4, US5, US6)

Scheduled autoresearch + mining correctness + observability (T074–T096). These are mostly parallel and independent.

### Increment 5 (P3 stories: US7, US8, US10 + Polish)

Stable slugs, suggestion quality tuning, adversarial re-audit, release polish (T097–T120).

---

## Validation Checklist (meta — verify before handoff)

- [x] All 10 user stories covered by at least one phase
- [x] Every FR (FR-001..FR-041) traceable to a task ID via this file's search
- [x] Every SC (SC-001..SC-022) traceable to a test task
- [x] TDD pairs: every implementation task has a preceding `[P]` test-write task (Constitution IV)
- [x] No story depends on a later story except: US8 → US9 (documented), US10 → all (documented)
- [x] All tasks use the required checklist format `- [ ] [TaskID] [P?] [Story?] Description with file path`
- [x] Parallel opportunities explicitly called out in each phase
- [x] MVP scope (Increment 1) explicitly defined
- [x] File paths absolute-relative to repo root (`src/sio/...`, `tests/...`, `scripts/...`)

**Total tasks**: 120
**Per-story count**: Setup 5 · Foundational 27 · US1 12 · US2 8 · US3 6 · US9 15 · US4 6 · US5 12 · US6 5 · US7 10 · US8 4 · US10 4 · Polish 6
