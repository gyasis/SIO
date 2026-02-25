# Tasks: Self-Improving Organism (SIO)

**Input**: Design documents from `/specs/001-self-improving-organism/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Constitution**: v1.4.0 — TDD is NON-NEGOTIABLE (Principle IV), Dataset Quality is NON-NEGOTIABLE (Principle IX), Programmatic Corpus Mining via Variable Space (Principle X)

**Organization**: Tasks grouped by user story. Each story is independently testable. Tests written FIRST per Constitution Principle IV.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in every task

---

## Phase 1: Setup (Project Scaffolding)

**Purpose**: Create the package structure, install dependencies, configure tooling

- [ ] T001 Create Python package directory structure per plan.md: `src/sio/{core,adapters,cli}/`, `tests/{unit,integration,contract}/`, with `__init__.py` files in every package
- [ ] T002 Create `pyproject.toml` with project metadata, dependencies (dspy, numpy, fastembed, click, rich), optional deps ([torch], [openai], [all]), and `[project.scripts] sio = "sio.cli.main:cli"` entry point
- [ ] T003 [P] Create `tests/conftest.py` with shared pytest fixtures: `tmp_db` (in-memory SQLite), `sample_invocation` factory, `mock_platform_config`
- [ ] T004 [P] Configure `ruff.toml` with linting rules (select = ["E", "F", "I", "W"]) and formatting (line-length = 99)
- [ ] T005 Run `uv pip install -e ".[dev]"` and verify `sio --help` outputs the Click CLI help text

**Checkpoint**: Package installs, CLI entry point responds, test runner discovers conftest.

---

## Phase 2: Foundational (Core Infrastructure)

**Purpose**: Database schema, query layer, secret scrubber, embedding provider — BLOCKS all user stories

**FR Coverage**: FR-020 (retention), FR-024 (embeddings)

**CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundation

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation (Constitution IV)**

- [ ] T006 [P] Write unit tests for schema DDL in `tests/unit/test_schema.py`: test table creation, WAL mode enabled, all indexes exist, idempotent re-creation on corrupt DB
- [ ] T007 [P] Write unit tests for query layer in `tests/unit/test_queries.py`: test insert_invocation, get_invocation_by_id, get_unlabeled, get_by_skill, get_by_session, update_satisfaction, count_by_platform
- [ ] T008 [P] Write unit tests for secret scrubber in `tests/unit/test_secret_scrubber.py`: test API key patterns, bearer tokens, passwords, connection strings, no false positives on normal text
- [ ] T009 [P] Write unit tests for retention purge in `tests/unit/test_retention.py`: test purge records older than N days, gold standard exemption, dry-run mode returns count without deleting
- [ ] T010 [P] Write unit tests for embeddings provider in `tests/unit/test_embeddings.py`: test abstract interface, fastembed backend encode/encode_single, cache hit/miss, model swap invalidates cache, test ApiEmbedBackend with mocked HTTP (FR-024 external override), test fallback to fastembed when config not set
- [ ] T011 [P] Write contract tests for hook JSON schemas in `tests/contract/test_hook_contracts.py`: validate PostToolUse stdin schema, PreToolUse stdin schema, notification stdin schema, all output schemas

### Implementation for Foundation

- [ ] T012 [P] Implement database schema with DDL and WAL mode in `src/sio/core/db/schema.py`: CREATE TABLE behavior_invocations (all 20 fields from data-model.md), CREATE TABLE optimization_runs, CREATE TABLE gold_standards, CREATE TABLE platform_config, all indexes, WAL pragma
- [ ] T013 [P] Implement query layer in `src/sio/core/db/queries.py`: insert_invocation(), get_unlabeled(), get_by_skill(), update_satisfaction(), get_skill_health() (materialized view query), get_labeled_for_optimizer(), count_by_pattern() (for FR-028 threshold detection)
- [ ] T014 [P] Implement secret scrubber in `src/sio/core/telemetry/secret_scrubber.py`: regex patterns for AWS keys, API tokens, bearer tokens, passwords, connection strings. `scrub(text: str) -> str` replaces matches with `[REDACTED]`
- [ ] T015 [P] Implement 90-day retention purge in `src/sio/core/db/retention.py`: `purge(db, older_than_days=90, dry_run=False)` — delete rows where timestamp < cutoff AND id NOT IN gold_standards
- [ ] T016 [P] Implement embedding provider abstraction in `src/sio/core/embeddings/provider.py`: `EmbeddingBackend` ABC with `encode(texts) -> ndarray` and `encode_single(text) -> ndarray`
- [ ] T017 [P] Implement fastembed backend in `src/sio/core/embeddings/local_model.py`: `FastEmbedBackend(EmbeddingBackend)` using fastembed with all-MiniLM-L6-v2, SQLite cache keyed on `(sha256(text), model_name)`
- [ ] T017b [P] Implement external API embedding backend in `src/sio/core/embeddings/api_model.py`: `ApiEmbedBackend(EmbeddingBackend)` that calls a user-configured external embedding API (FR-024). Reads endpoint URL and API key from `~/.sio/config.toml`. Falls back to fastembed if config not set.
- [ ] T018 Verify all Phase 2 tests pass (Green). Fix any failures before proceeding.

**Checkpoint**: Database creates with WAL mode, CRUD works, secrets scrubbed, embeddings encode, retention purges. All foundational tests green.

---

## Phase 3: User Story 1 — Behavior Telemetry Capture (Priority: P1) MVP

**Goal**: Every AI tool call during a session is automatically recorded in the local behavior database within 2 seconds.

**Independent Test**: Run a CLI session with multiple tool calls → verify each appears as a row in the DB with correct metadata and no duplicates.

**FR Coverage**: FR-001, FR-003, FR-013, FR-021, FR-022, FR-025

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation (Constitution IV)**

- [ ] T019 [P] [US1] Write unit tests for telemetry logger in `tests/unit/test_logger.py`: test log_invocation() creates a row with all required fields, secret scrubbing applied, duplicate detection, error resilience (disk full → log error, don't crash)
- [ ] T020 [P] [US1] Write unit tests for auto-labeler in `tests/unit/test_auto_labeler.py`: test agent-inferred fields (activated, correct_action, correct_outcome) are set based on tool_output and error fields
- [ ] T021 [P] [US1] Write contract tests for PostToolUse hook in `tests/contract/test_hook_contracts.py` (extend T011): test stdin JSON parsing, error field handling, session_id propagation
- [ ] T022 [P] [US1] Write integration test for telemetry pipeline in `tests/integration/test_telemetry_pipeline.py`: simulate 20 PostToolUse hook calls → verify 20 rows in DB with correct metadata, no duplicates, secrets scrubbed

### Implementation for User Story 1

- [ ] T023 [P] [US1] Implement telemetry logger in `src/sio/core/telemetry/logger.py`: `log_invocation(db, session_id, tool_name, tool_input, tool_output, error, user_message, platform)` — validates at write time (FR-025), scrubs secrets (FR-022), inserts into behavior_invocations
- [ ] T024 [P] [US1] Implement auto-labeler in `src/sio/core/telemetry/auto_labeler.py`: `auto_label(tool_name, tool_input, tool_output, error) -> dict` — returns `{activated, correct_action, correct_outcome}` binary fields inferred from output/error signals
- [ ] T025 [US1] Implement PostToolUse hook handler in `src/sio/adapters/claude_code/hooks/post_tool_use.py`: read JSON from stdin, extract `user_message` (from stdin if present, else from latest JSONL transcript entry, else `[UNAVAILABLE]`), call logger.log_invocation() + auto_labeler.auto_label(), write `{"action": "allow"}` to stdout, exit 0 on any error
- [ ] T026 [US1] Create shell wrapper `src/sio/adapters/claude_code/hooks/post_tool_use.sh`: calls `python3 -m sio.adapters.claude_code.hooks.post_tool_use` with stdin passthrough
- [ ] T027 [US1] Verify all US1 tests pass (Green). Run `tests/unit/test_logger.py`, `tests/unit/test_auto_labeler.py`, `tests/integration/test_telemetry_pipeline.py`.

**Checkpoint**: PostToolUse hook receives JSON, logs to DB, auto-labels, scrubs secrets. 20 tool calls → 20 clean rows. US1 independently testable.

---

## Phase 4: User Story 2 — User Satisfaction Feedback (Priority: P1)

**Goal**: Users can rate any action with `++` or `--` plus optional note, and batch-review unlabeled invocations.

**Independent Test**: Perform an AI action, type `++` → verify satisfaction field updated in DB. Run `sio review` → label unlabeled invocations sequentially.

**FR Coverage**: FR-002, FR-006, FR-026, FR-029

### Tests for User Story 2

- [ ] T028 [P] [US2] Write unit tests for feedback labeler in `tests/unit/test_labeler.py`: test label_latest(session_id, signal, note) updates user_satisfied and user_note on most recent invocation, test re-labeling overwrites previous, test invalid session returns error
- [ ] T029 [P] [US2] Write unit tests for batch review in `tests/unit/test_batch_review.py`: test get_reviewable() returns unlabeled invocations sorted by timestamp, test balanced presentation (FR-026 warns if >90% one class), test skip and quit behavior
- [ ] T030 [P] [US2] Write unit tests for pattern flagging in `tests/unit/test_pattern_flag.py`: test user-flagged pattern acceleration (FR-029) — "this keeps happening" marks a skill as priority optimization candidate while still enforcing quality gates
- [ ] T031 [P] [US2] Write integration test for feedback loop in `tests/integration/test_feedback_loop.py`: simulate log 10 invocations → label 5 with `++`, 5 with `--` → verify satisfaction fields correct, health aggregate updated

### Implementation for User Story 2

- [ ] T032 [P] [US2] Implement feedback labeler in `src/sio/core/feedback/labeler.py`: `label_latest(db, session_id, signal: str, note: str | None)` — parses `++`/`--`, updates most recent invocation, sets `labeled_by='inline'`, `labeled_at=now()`
- [ ] T033 [P] [US2] Implement batch review in `src/sio/core/feedback/batch_review.py`: `get_reviewable(db, platform, session_id, limit)` returns unlabeled sorted by timestamp. `apply_label(db, invocation_id, signal, note)` applies label. Warn if distribution >90% skewed (FR-026)
- [ ] T034 [US2] Implement pattern flag detection in `src/sio/core/feedback/pattern_flag.py`: `flag_pattern(db, skill_name, note)` — marks a skill as priority optimization candidate when user explicitly flags recurring issue (FR-029). Checks if minimum quality gates are met.
- [ ] T035 [US2] Implement feedback CLI entry point in `src/sio/core/feedback/labeler_cli.py`: `python3 -m sio.core.feedback.labeler --session <id> --signal <++|--> [--note <text>]` — parses args, calls labeler.label_latest(), exits 0. This is invoked by the sio-feedback skill trigger (not a Notification hook).
- [ ] T036 [US2] Update sio-feedback SKILL.md to invoke `python3 -m sio.core.feedback.labeler --session $SESSION_ID --signal "$(echo $USER_INPUT | head -c2)" --note "$(echo $USER_INPUT | cut -c3-)"` via Bash
- [ ] T037 [US2] Implement `sio review` CLI command in `src/sio/cli/main.py`: Click command with `--platform`, `--session`, `--limit` options. Uses Rich for interactive sequential presentation. Labels: `++`/`--`/`s(kip)`/`q(uit)`
- [ ] T038 [US2] Verify all US2 tests pass (Green).

**Checkpoint**: `++`/`--` labels invocations inline. `sio review` presents unlabeled for batch labeling. Pattern flagging works. US2 independently testable.

---

## Phase 5: User Story 3 — Passive Dissatisfaction Detection (Priority: P2)

**Goal**: System auto-detects undos, corrections, and re-invocations as passive dissatisfaction signals without requiring explicit user feedback.

**Independent Test**: Trigger an AI action, type a correction ("No, actually...") → verify prior invocation flagged with passive_signal.

**FR Coverage**: FR-004, FR-028

### Tests for User Story 3

- [ ] T039 [P] [US3] Write unit tests for passive signal detector in `tests/unit/test_passive_signals.py`: test look-back correction detection (current user_message starts with "No,"/"Actually,"/"Instead," → flags previous invocation), test look-back undo detection (current tool is git checkout/revert within 30s of previous tool → flags previous as 'undo'), test re-invocation detection (current tool differs from previous but same user intent → flags previous as 'correction'), test no false positives on normal sequential tool calls
- [ ] T040 [P] [US3] Write unit tests for pattern threshold detector in `tests/unit/test_pattern_threshold.py`: test count_pattern_occurrences() counts same failure (behavior_type + failure_mode) across sessions, test threshold check (default 3, configurable 3-10, FR-028), test single incident does NOT trigger optimization candidacy

### Implementation for User Story 3

- [ ] T041 [P] [US3] Implement passive signal detector in `src/sio/core/telemetry/passive_signals.py`: `detect_correction(message: str) -> bool`, `detect_undo(session_id, timestamp, db) -> bool` (checks for git checkout/revert within 30s), `detect_re_invocation(session_id, intent, db) -> bool`
- [ ] T042 [US3] Implement pattern threshold detector in `src/sio/core/telemetry/pattern_detector.py`: `count_pattern_occurrences(db, behavior_type, failure_mode) -> int`, `is_optimization_candidate(db, skill_name, threshold=3) -> bool` (FR-028). Only returns True when same failure pattern recurs across ≥threshold sessions.
- [ ] T043 [US3] Integrate passive signals into PostToolUse hook — extend `src/sio/adapters/claude_code/hooks/post_tool_use.py` with look-back detection: on each invocation, check if the current `user_message` contains correction language ("No,", "Actually,", "Instead,") or if the current tool is a re-invocation for the same intent as the previous invocation. If detected, update the PREVIOUS invocation's `passive_signal` field via `queries.update_passive_signal(db, previous_id, signal_type)`. For undo detection (git checkout/revert), compare timestamps of the current invocation against the previous invocation's `actual_action` — if the current tool is a revert within 30 seconds, flag the prior as 'undo'.
- [ ] T044 [US3] Verify all US3 tests pass (Green).

**Checkpoint**: Passive signals auto-detected. Pattern threshold enforced (3+ sessions required). Single incidents logged but no behavior change triggered. US3 independently testable.

---

## Phase 6: User Story 4 — Prompt Optimization from Feedback (Priority: P2)

**Goal**: When a skill consistently fails (recurring pattern across sessions), the user can trigger DSPy-powered optimization that permanently improves prompts.

**Independent Test**: Accumulate 10+ labeled failures for a skill across 3+ sessions, trigger `sio optimize <skill>` → verify diff presented, approve → config file updated.

**FR Coverage**: FR-007, FR-008, FR-009, FR-016, FR-017, FR-018, FR-023, FR-027, FR-028, FR-030

### Tests for User Story 4

- [ ] T045 [P] [US4] Write unit tests for DSPy optimizer wrapper in `tests/unit/test_optimizer.py`: test quality gate enforcement (min 10 examples, min 5 failures, FR-017), test pattern threshold gate (FR-028 — must have 3+ recurring sessions), test GEPA/MIPROv2/BootstrapFewShot optimizer selection, test atomic rollback on failure (FR-023), test recency weighting in dataset export (FR-027)
- [ ] T046 [P] [US4] Write unit tests for RLM corpus miner in `tests/unit/test_rlm_miner.py`: test corpus loaded into variable space (not token space), test signature "conversation_corpus, failure_record -> failure_analysis", test trajectory logging (each step has code + output), test Deno missing raises clear error, test sub_lm routing for llm_query() calls, mock WASM sandbox for unit isolation
- [ ] T047 [P] [US4] Write unit tests for artifact writer in `tests/unit/test_artifact_writer.py`: test diff generation (before/after), test write to CLAUDE.md, test write to SKILL.md, test git commit with descriptive message (FR-009)
- [ ] T047b [P] [US4] Write unit tests for corpus indexer in `tests/unit/test_corpus_indexer.py`: test index_corpus() builds BM25 + embedding index over markdown files, test search by keyword returns ranked results, test search by embedding returns semantically similar chunks, test empty corpus returns empty index gracefully
- [ ] T048 [P] [US4] Write integration test for optimization cycle in `tests/integration/test_optimization_cycle.py`: seed DB with 15 labeled invocations (10 failures across 3 sessions + 5 successes) → trigger optimize → verify diff produced, quality gates passed, OptimizationRun record created with status='pending'
- [ ] T050b [P] [US4] Write unit tests for pattern surfacing in `tests/unit/test_pattern_surface.py`: test surface_patterns() returns recurring failure patterns with description + count + affected sessions, test pattern summary generation for user acknowledgment (FR-030), test empty patterns returns empty list, test patterns below threshold excluded

### Implementation for User Story 4

- [ ] T049 [US4] Implement DSPy optimizer wrapper in `src/sio/core/dspy/optimizer.py`: `optimize(db, skill_name, platform, optimizer='gepa')` — enforces quality gates (FR-017), checks pattern threshold (FR-028 — recurring across 3+ sessions), exports labeled data with recency weighting (FR-027), runs GEPA/MIPROv2/BootstrapFewShot, wraps in atomic transaction (FR-023)
- [ ] T050 [US4] Implement pattern surfacing for user acknowledgment in `src/sio/core/dspy/pattern_surface.py`: `surface_patterns(db, platform) -> list[PatternSummary]` — finds recurring failure patterns, returns description + count + affected sessions + proposed fix (FR-030). Nothing deploys without user acknowledgment.
- [ ] T051 [US4] Implement RLM corpus miner in `src/sio/core/dspy/rlm_miner.py`: `mine_failure_context(corpus_path, failure_record) -> MiningResult` — creates `dspy.RLM` with signature `"conversation_corpus, failure_record -> failure_analysis"`, `sub_lm` from config (cheap model for `llm_query()` calls inside REPL), `max_iterations=20`, `max_llm_calls=50`. Root LM writes Python code to search/filter the corpus in variable space (never sent to LLM context). Log `result.trajectory` for audit trail (each step's code + output). Requires Deno installed for WASM sandbox — raise clear error if missing. For unit tests: mock Deno sandbox, test signature parsing and trajectory logging.
- [ ] T052 [US4] Implement corpus indexer in `src/sio/core/dspy/corpus_indexer.py`: `index_corpus(platform, history_dir) -> CorpusIndex` — BM25 + embedding index over SpecStory `.md` files for Claude Code adapter
- [ ] T053 [US4] Implement artifact writer in `src/sio/adapters/claude_code/artifact_writer.py`: `write_optimization(platform, skill_name, diff, commit_message)` — writes to CLAUDE.md or SKILL.md, commits to git with FR-009 metadata
- [ ] T054 [US4] Implement `sio optimize` CLI command in `src/sio/cli/main.py`: Click command with `skill_name`, `--platform`, `--optimizer`, `--dry-run`. Uses Rich for diff display. Prompts `[a(pprove)/r(eject)/d(etails)]`. On approve: write + commit. On failure: atomic rollback.
- [ ] T055 [US4] Verify all US4 tests pass (Green).

**Checkpoint**: `sio optimize Read` produces diffs from labeled data across recurring sessions. Quality gates enforced (10+ examples, 3+ recurring sessions). Atomic rollback on failure. User acknowledgment required for deployment. US4 independently testable.

---

## Phase 7: User Story 5 — Regression Prevention via Arena Testing (Priority: P3)

**Goal**: Proposed optimizations are validated against gold-standard test cases before deployment. Semantic drift >40% requires manual approval. Trigger collisions detected.

**Independent Test**: Create gold standards from verified-good interactions, run an optimization that conflicts → verify the system blocks the harmful change.

**FR Coverage**: FR-010, FR-011, FR-012

### Tests for User Story 5

- [ ] T056 [P] [US5] Write unit tests for gold standards in `tests/unit/test_gold_standards.py`: test promote_to_gold() copies invocation fields, test exempt_from_purge always True, test replay_against_prompt() returns pass/fail
- [ ] T057 [P] [US5] Write unit tests for drift detector in `tests/unit/test_drift_detector.py`: test cosine distance calculation, test 40% threshold triggers manual approval flag, test below threshold auto-passes
- [ ] T058 [P] [US5] Write unit tests for collision detector in `tests/unit/test_collision.py`: test embedding similarity between skill descriptions, test 0.85 threshold triggers collision warning
- [ ] T059 [P] [US5] Write integration test for arena validation in `tests/integration/test_arena_validation.py`: seed gold standards → run optimization → verify gold standards replayed, drift checked, collisions checked, blocking optimization rejected

### Implementation for User Story 5

- [ ] T060 [P] [US5] Implement gold standards manager in `src/sio/core/arena/gold_standards.py`: `promote_to_gold(db, invocation_id)`, `get_all_for_skill(db, skill_name)`, `replay_against_prompt(gold, new_prompt) -> bool`
- [ ] T061 [P] [US5] Implement drift detector in `src/sio/core/arena/drift_detector.py`: `measure_drift(original_prompt, new_prompt, embedder) -> float`, `requires_manual_approval(drift_score, threshold=0.40) -> bool`
- [ ] T062 [P] [US5] Implement collision detector in `src/sio/core/arena/collision.py`: `check_collisions(skill_descriptions: dict, embedder) -> list[CollisionWarning]`, `is_collision(sim_score, threshold=0.85) -> bool`
- [ ] T063 [US5] Implement arena regression runner in `src/sio/core/arena/regression.py`: `run_arena(db, skill_name, new_prompt, embedder) -> ArenaResult` — orchestrates gold standard replay + drift check + collision check. Returns pass/fail with reasons.
- [ ] T064 [US5] Integrate arena into optimizer pipeline — extend `src/sio/core/dspy/optimizer.py` to call `run_arena()` after optimization, before presenting diff to user. Block deployment if arena fails.
- [ ] T065 [US5] Verify all US5 tests pass (Green).

**Checkpoint**: Gold standards block harmful optimizations. Drift >40% requires manual approval. Trigger collisions detected. Arena gates integrated into optimizer. US5 independently testable.

---

## Phase 8: User Story 6 — Skill Health Dashboard (Priority: P3)

**Goal**: Users see per-skill performance metrics to know which skills need attention and track improvement over time.

**Independent Test**: Accumulate session data → run `sio health` → see per-skill metrics in a formatted table.

**FR Coverage**: FR-005

### Tests for User Story 6

- [ ] T066 [P] [US6] Write unit tests for health aggregator in `tests/unit/test_aggregator.py`: test compute_health() returns correct counts (total, satisfied, unsatisfied, unlabeled, false_trigger, missed_trigger), test satisfaction_rate calculation, test skills below 50% flagged
- [ ] T067 [P] [US6] Write contract tests for CLI health command in `tests/contract/test_cli_commands.py`: test `sio health` exit code 0, test `--format json` outputs valid JSON, test `--skill` filter works

### Implementation for User Story 6

- [ ] T068 [US6] Implement health aggregator in `src/sio/core/health/aggregator.py`: `compute_health(db, platform=None, skill=None) -> list[SkillHealth]` — SQL aggregation query per data-model.md SkillHealth entity
- [ ] T069 [US6] Implement `sio health` CLI command in `src/sio/cli/main.py`: Click command with `--platform`, `--skill`, `--format`. Uses Rich Table for display. Highlights skills <50% satisfaction in red.
- [ ] T070 [US6] Verify all US6 tests pass (Green).

**Checkpoint**: `sio health` shows per-skill metrics. Low-performing skills highlighted. JSON export works. US6 independently testable.

---

## Phase 9: User Story 7 — Multi-Platform Installation (Priority: P4)

**Goal**: Install SIO for any supported platform with a single command. Platform-native hooks, skills, config, and separate DBs.

**Independent Test**: Run `sio install --platform claude-code` → verify hooks registered, skills installed, CLAUDE.md updated, DB created, smoke test passes.

**FR Coverage**: FR-013, FR-014

### Tests for User Story 7

- [ ] T071 [P] [US7] Write unit tests for Claude Code installer in `tests/unit/test_installer.py` (mock filesystem): test hook registration (writes to settings.json), test skill installation (copies SKILL.md files), test CLAUDE.md update (appends SIO rules), test DB initialization (creates with WAL), test smoke test logic
- [ ] T072 [P] [US7] Write contract tests for CLI install command in `tests/contract/test_cli_commands.py` (extend): test `sio install --platform claude-code` exit code 0, test `sio install --auto` with mock platform detection

### Implementation for User Story 7

- [ ] T073 [US7] Implement Claude Code installer in `src/sio/adapters/claude_code/installer.py`: `install(db_path=None)` — creates `~/.sio/claude-code/`, initializes DB with WAL + busy_timeout=1000, registers hooks in `~/.claude/settings.json` (PostToolUse + PreToolUse arrays, MUST merge with existing hooks not overwrite), installs skills to `~/.claude/skills/sio-{feedback,optimize,health,review}/`, appends SIO rules to `~/.claude/CLAUDE.md`, runs smoke test, writes PlatformConfig record
- [ ] T074 [US7] Create SKILL.md files per hook-contracts.md: `src/sio/adapters/claude_code/skills/sio-feedback/SKILL.md`, `sio-optimize/SKILL.md`, `sio-health/SKILL.md`, `sio-review/SKILL.md`
- [ ] T075 [US7] Implement `sio install` CLI command in `src/sio/cli/main.py`: Click command with `--platform` choice and `--auto` flag. Calls platform-specific installer. Outputs installation summary with smoke test result.
- [ ] T076 [US7] Implement `sio purge` and `sio export` CLI commands in `src/sio/cli/main.py`: purge uses retention.py, export writes CSV/JSON from query layer
- [ ] T077 [US7] Verify all US7 tests pass (Green).

**Checkpoint**: `sio install --platform claude-code` fully sets up SIO. All 6 CLI commands work. Platform-specific DB and hooks. US7 independently testable.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Integration testing, configuration, documentation, hardening

- [ ] T078 [P] Implement config loader in `src/sio/core/config.py`: load `~/.sio/config.toml` with defaults from quickstart.md (embedding backend, retention days, optimization thresholds, pattern threshold)
- [ ] T078b [P] Write unit tests for config loader in `tests/unit/test_config.py`: test default values when no config file exists, test TOML parsing with all fields, test partial config (missing keys use defaults), test invalid TOML returns error with clear message
- [ ] T079 [P] Implement error logging infrastructure in `src/sio/core/logging.py`: separate error log at `~/.sio/<platform>/error.log`, structured JSON logging for debugging
- [ ] T080 [P] Write end-to-end integration test in `tests/integration/test_e2e_closed_loop.py`: install → capture telemetry → label feedback → detect passive signals → detect recurring pattern → optimize → arena validates → approve → verify config updated. Full closed-loop test.
- [ ] T081 Run `ruff check src/ tests/` and fix all linting issues
- [ ] T082 Run full test suite `pytest tests/ -v --cov=sio --cov-report=term-missing` and verify ≥80% coverage
- [ ] T083 Run quickstart.md validation: follow every step in quickstart.md on a clean environment and verify it works end-to-end
- [ ] T084 Final code review: verify no placeholder/stub code in production paths, all FRs (FR-001 through FR-030) have corresponding implementation, all quality gates enforced

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundation)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1 Telemetry)**: Depends on Phase 2 — MVP foundation
- **Phase 4 (US2 Feedback)**: Depends on Phase 2 — can run parallel with US1
- **Phase 5 (US3 Passive Signals)**: Depends on Phase 2 — can run parallel with US1/US2
- **Phase 6 (US4 Optimization)**: Depends on Phase 2 + needs US1 data and US2 labels
- **Phase 7 (US5 Arena)**: Depends on Phase 6 (extends optimizer pipeline)
- **Phase 8 (US6 Health)**: Depends on Phase 2 — can run parallel with any story
- **Phase 9 (US7 Install)**: Depends on all hooks/skills being implemented (US1, US2)
- **Phase 10 (Polish)**: Depends on all desired user stories complete

### User Story Dependencies

```
Phase 2 (Foundation) ─┬─→ US1 (Telemetry) ──┬─→ US4 (Optimization) ──→ US5 (Arena)
                      ├─→ US2 (Feedback)  ──┘
                      ├─→ US3 (Passive)
                      ├─→ US6 (Health)
                      └─→ US7 (Install) depends on US1+US2 hooks
```

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (Constitution IV)
2. Models/schemas before services
3. Services before CLI commands
4. Core implementation before hook/adapter integration
5. Verify tests pass (Green) before moving to next story
6. Commit after each task or logical group

### Parallel Opportunities

- **Phase 1**: T003, T004 can run in parallel
- **Phase 2**: T006-T011 (all test tasks) can run in parallel, then T012-T017 (all implementation tasks) can run in parallel
- **Phase 3-5**: US1, US2, US3 can all start after Phase 2 (but US1+US2 are P1 priority)
- **Phase 6**: Requires US1+US2 data, so starts after those checkpoint
- **Phase 8**: US6 (Health) is fully independent — can run any time after Phase 2
- **Phase 10**: T078, T079, T080 can run in parallel

---

## Parallel Example: Phase 2 (Foundation)

```bash
# Wave 1: Launch all foundation TESTS in parallel
Task: "Unit tests for schema DDL in tests/unit/test_schema.py"
Task: "Unit tests for query layer in tests/unit/test_queries.py"
Task: "Unit tests for secret scrubber in tests/unit/test_secret_scrubber.py"
Task: "Unit tests for retention purge in tests/unit/test_retention.py"
Task: "Unit tests for embeddings in tests/unit/test_embeddings.py"
Task: "Contract tests for hook schemas in tests/contract/test_hook_contracts.py"

# Wave 2: Confirm tests FAIL (Red), then launch all foundation IMPLEMENTATION in parallel
Task: "Implement schema DDL in src/sio/core/db/schema.py"
Task: "Implement query layer in src/sio/core/db/queries.py"
Task: "Implement secret scrubber in src/sio/core/telemetry/secret_scrubber.py"
Task: "Implement retention purge in src/sio/core/db/retention.py"
Task: "Implement embedding provider in src/sio/core/embeddings/provider.py"
Task: "Implement fastembed backend in src/sio/core/embeddings/local_model.py"

# Wave 3: Verify ALL tests pass (Green)
Task: "Verify all Phase 2 tests pass"
```

## Parallel Example: US1 + US2 + US3 (after Foundation)

```bash
# US1, US2, US3 tests can all launch in parallel (different files):
Task: "Unit tests for telemetry logger in tests/unit/test_logger.py"          # US1
Task: "Unit tests for feedback labeler in tests/unit/test_labeler.py"         # US2
Task: "Unit tests for passive signals in tests/unit/test_passive_signals.py"  # US3
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup → package installs
2. Complete Phase 2: Foundation → DB, queries, scrubber, embeddings
3. Complete Phase 3: US1 → telemetry logging via PostToolUse hook
4. Complete Phase 4: US2 → `++`/`--` feedback + `sio review`
5. **STOP and VALIDATE**: Install on Claude Code, use for a week, accumulate labeled data
6. This delivers value as a **session activity log with feedback** even before optimization exists

### Incremental Delivery

1. Setup + Foundation → core infrastructure ready
2. US1 + US2 → **MVP**: telemetry + feedback (deployable)
3. US3 → passive detection (reduces labeling burden)
4. US4 → optimization (the brain — requires accumulated data from step 2)
5. US5 → arena (regression safety net)
6. US6 → health dashboard (visibility)
7. US7 → multi-platform install (expansion)
8. Each increment is independently testable and deployable

### Data Collection Strategy (Constitution IX)

The MVP (US1+US2) should run for **at least 1-2 weeks** before attempting US4 (optimization) to accumulate enough quality labeled data. The pattern threshold (FR-028, 3+ recurring sessions) naturally enforces this — optimization won't have enough recurring patterns from a single session.

---

## Notes

- [P] tasks = different files, no dependencies — safe to parallelize
- [Story] label maps task to specific user story for traceability
- Constitution IV (TDD): Tests written FIRST, must FAIL, then implement until Green
- Constitution IX (Dataset Quality): Data collection quality gates enforced in FR-025, FR-026, FR-027, FR-028
- Constitution III (Pattern Thresholds): Single incidents logged but never trigger optimization. 3+ recurring sessions required (FR-028).
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
