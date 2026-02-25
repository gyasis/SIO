# Tasks: SIO v2 — Mine, Cluster, Improve

**Input**: Design documents from `/specs/002-sio-redesign/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md
**Constitution**: v1.4.0 — TDD is NON-NEGOTIABLE (Principle IV), Dataset Quality is NON-NEGOTIABLE (Principle IX)

**Organization**: Tasks grouped by user story. Each story is independently testable. Tests written FIRST per Constitution Principle IV.

**Reuse boundary**: v1 infrastructure (embeddings, config, CLI, arena) is REUSED. v1 hook-based capture (telemetry, auto-labeler, passive signals, PostToolUse) is NOT used — v2 mines existing files instead.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in every task

---

## Phase 1: Setup (New v2 Packages)

**Purpose**: Create v2-specific package directories and extend v1 infrastructure

- [x] T001 Create v2 package directories: `src/sio/{mining,clustering,datasets,suggestions,review,applier,scheduler}/` with `__init__.py` files
- [x] T002 Extend `pyproject.toml` to add any new v2 dependencies (if needed beyond existing click, rich, fastembed, numpy)
- [x] T003 [P] Extend `tests/conftest.py` with v2 fixtures: `sample_specstory_file` factory (creates temp .md with tool calls), `sample_jsonl_file` factory (creates temp .jsonl with messages), `sample_error_records` factory, `v2_db` fixture (in-memory SQLite with v2 tables)

**Checkpoint**: v2 packages exist alongside v1. Test fixtures available.

---

## Phase 2: Foundational (v2 Schema + Config Extensions)

**Purpose**: Extend v1 DB schema and config for v2 entities — BLOCKS all user stories

### Tests for Foundation

- [x] T004 [P] Write unit tests for v2 schema DDL in `tests/unit/test_v2_schema.py`: test v2 table creation (error_records, patterns, pattern_errors, datasets, suggestions, applied_changes), verify v1 tables still exist, WAL mode, all indexes
- [x] T005 [P] Write unit tests for v2 config keys in `tests/unit/test_v2_config.py`: test new defaults (similarity_threshold=0.80, min_pattern_occurrences=3, min_examples=5, daily_enabled, weekly_enabled), test v1 config keys still work

### Implementation for Foundation

- [x] T006 [P] Extend database schema in `src/sio/core/db/schema.py`: ADD v2 CREATE TABLE statements for all 6 entities from data-model.md alongside existing v1 tables
- [x] T007 [P] Extend query layer in `src/sio/core/db/queries.py`: ADD v2 insert/get/update functions for error_records, patterns, datasets, suggestions, applied_changes
- [x] T008 [P] Extend config loader in `src/sio/core/config.py`: ADD v2 config keys (similarity_threshold, min_pattern_occurrences, min_examples, daily_enabled, weekly_enabled, stale_days=30)
- [x] T009 Extend Click CLI in `src/sio/cli/main.py`: ADD stub subcommands `mine`, `patterns`, `datasets`, `review`, `approve`, `reject`, `rollback`, `schedule install`, `schedule status` (stubs that print "not implemented") alongside existing v1 commands
- [x] T010 Verify all Phase 2 tests pass AND existing v1 tests still pass

**Checkpoint**: v2 tables created alongside v1. Config extended. CLI stubs respond. v1 tests green.

---

## Phase 3: User Story 1 — Error Mining (Priority: P1) MVP

**Goal**: Mine SpecStory + JSONL files for errors, failures, corrections, and undos.

**Independent Test**: Run `sio mine --since "3 days"` against sample files → verify structured error records extracted.

### Tests for User Story 1

- [x] T011 [P] [US1] Write unit tests for SpecStory parser in `tests/unit/test_specstory_parser.py`: test parse Human/Assistant blocks, test extract tool calls from code blocks, test extract errors/failures, test handle malformed markdown gracefully
- [x] T012 [P] [US1] Write unit tests for JSONL parser in `tests/unit/test_jsonl_parser.py`: test parse line-by-line, test extract tool_name/input/output/error, test handle missing fields, test handle corrupt lines
- [x] T013 [P] [US1] Write unit tests for error extractor in `tests/unit/test_error_extractor.py`: test identify tool failures, test identify user corrections ("No, actually..."), test identify repeated attempts, test identify undos (git checkout/revert), test no false positives on normal conversation
- [x] T014 [P] [US1] Write unit tests for time filter in `tests/unit/test_time_filter.py`: test filter by days, test filter by weeks, test custom date range, test empty result when no files in window
- [x] T015 [P] [US1] Write integration test for mining pipeline in `tests/integration/test_mine_pipeline.py`: create 5 sample SpecStory files + 5 sample JSONL files with known errors → mine → verify all errors extracted with correct metadata

### Implementation for User Story 1

- [x] T016 [P] [US1] Implement SpecStory parser in `src/sio/mining/specstory_parser.py`: parse markdown into conversation blocks, extract tool calls (tool_name, input, output, error), extract timestamps from filename + content
- [x] T017 [P] [US1] Implement JSONL parser in `src/sio/mining/jsonl_parser.py`: parse line-by-line, extract message objects with role/content/tool metadata, handle corrupt lines gracefully
- [x] T018 [US1] Implement error extractor in `src/sio/mining/error_extractor.py`: `extract_errors(parsed_messages) -> list[ErrorRecord]` — identifies tool failures, user corrections, repeated attempts, undos. Reuses classification logic from v1's `core/telemetry/auto_labeler.py` for binary labeling (activated, correct_action, correct_outcome). Errors are automatically accumulated into datasets on each mining run for later weekly assessment.
- [x] T019 [US1] Implement time filter in `src/sio/mining/time_filter.py`: `filter_files(paths, since) -> list[Path]` — filters by modification time or filename-encoded timestamp. Supports "N days", "N weeks", custom date range
- [x] T020 [US1] Wire up `sio mine` CLI command in `src/sio/cli/main.py`: `--since` (required), `--project` (optional), `--source` (specstory/jsonl/both). Calls parsers → extractor → stores ErrorRecords in DB → auto-accumulates into pattern datasets (if patterns exist). Reports count.
- [x] T021 [US1] Verify all US1 tests pass (Green)

**Checkpoint**: `sio mine --since "3 days"` extracts errors from session files. US1 independently testable.

---

## Phase 4: User Story 2 — Pattern Clustering (Priority: P1)

**Goal**: Group mined errors into patterns using embedding similarity (reuses v1 fastembed provider).

**Independent Test**: Mine errors → run clustering → verify patterns with counts and affected sessions.

### Tests for User Story 2

- [x] T022 [P] [US2] Write unit tests for pattern clusterer in `tests/unit/test_pattern_clusterer.py`: test identical errors cluster together, test similar errors (fuzzy) cluster together, test different errors stay separate, test configurable similarity threshold, test empty input returns empty
- [x] T023 [P] [US2] Write unit tests for ranker in `tests/unit/test_ranker.py`: test frequency × recency scoring, test recent frequent errors rank higher than old infrequent ones, test ties broken by recency

### Implementation for User Story 2

- [x] T024 [US2] Implement pattern clusterer in `src/sio/clustering/pattern_clusterer.py`: `cluster_errors(errors, threshold=0.80) -> list[Pattern]` — uses v1's `EmbeddingBackend` to embed error_text, computes pairwise cosine similarity, groups by threshold, generates pattern_id slug and description
- [x] T025 [US2] Implement ranker in `src/sio/clustering/ranker.py`: `rank_patterns(patterns) -> list[Pattern]` — scores each pattern by `count * recency_weight` where recency_weight decays for older patterns
- [x] T026 [US2] Wire up `sio patterns` CLI command in `src/sio/cli/main.py`: reads patterns from DB, displays ranked list with Rich table (pattern description, count, sessions, time range)
- [x] T027 [US2] Verify all US2 tests pass (Green)

**Checkpoint**: `sio patterns` shows clustered error patterns ranked by importance. US2 independently testable.

---

## Phase 5: User Story 3 — Dataset Builder (Priority: P2)

**Goal**: Build positive/negative datasets from each pattern for training.

**Independent Test**: Given a pattern with errors, build dataset → verify pos/neg examples with correct structure.

### Tests for User Story 3

- [x] T028 [P] [US3] Write unit tests for dataset builder in `tests/unit/test_dataset_builder.py`: test builds positive examples from successes, test builds negative examples from failures, test minimum threshold enforced (skip if <5 examples), test incremental update appends not rebuilds, test dataset JSON schema matches data-model.md, test on-demand collection by time range, test on-demand collection by error type
- [x] T029 [P] [US3] Write unit tests for lineage tracker in `tests/unit/test_lineage.py`: test tracks contributing sessions, test tracks time window, test lineage persists across updates
- [x] T029b [P] [US3] Write unit tests for auto-accumulation in `tests/unit/test_dataset_accumulator.py`: test that mining run automatically feeds errors into existing pattern datasets, test new patterns get datasets created on first accumulation

### Implementation for User Story 3

- [x] T030 [US3] Implement dataset builder in `src/sio/datasets/builder.py`: `build_dataset(pattern, all_errors) -> Dataset` — for the pattern's tool, finds successful calls (positive) and failed calls (negative). Writes JSON to `~/.sio/datasets/<pattern_id>.json`. Supports incremental updates. Also supports on-demand collection: `collect_dataset(since=None, error_type=None, sessions=None) -> Dataset` for user-specified scope.
- [x] T030b [US3] Implement dataset accumulator in `src/sio/datasets/accumulator.py`: `accumulate(errors, patterns)` — called after every mining run, automatically feeds newly mined errors into their respective pattern datasets. Creates new dataset files for new patterns.
- [x] T031 [US3] Implement lineage tracking in `src/sio/datasets/lineage.py`: `track_lineage(dataset, sessions, time_window)` — records which sessions contributed and when
- [x] T032 [US3] Wire up `sio datasets` CLI command in `src/sio/cli/main.py`: `sio datasets` lists built datasets; `sio datasets collect --since "2 weeks" --error-type tool_failure` for on-demand collection
- [x] T033 [US3] Verify all US3 tests pass (Green)

**Checkpoint**: Datasets built with pos/neg examples per pattern. Lineage tracked. US3 independently testable.

---

## Phase 6: User Story 4 — Passive Background Analysis (Priority: P2)

**Goal**: Daily/weekly automated analysis writes suggestions to home file.

**Independent Test**: Run passive pipeline manually → verify `~/.sio/suggestions.md` populated.

### Tests for User Story 4

- [x] T034 [P] [US4] Write unit tests for suggestion generator in `tests/unit/test_suggestion_generator.py`: test generates suggestion from pattern + dataset, test confidence scoring, test suggestion includes proposed change text and target file
- [x] T035 [P] [US4] Write unit tests for home file writer in `tests/unit/test_home_file.py`: test writes valid markdown, test ranked sections (high/medium/low priority), test includes approve/reject commands, test handles empty suggestions
- [x] T036 [P] [US4] Write unit tests for cron installer in `tests/unit/test_cron.py`: test generates valid crontab entries, test daily and weekly schedules, test idempotent install (no duplicate entries)

### Implementation for User Story 4

- [x] T037 [US4] Implement suggestion generator in `src/sio/suggestions/generator.py`: `generate_suggestions(patterns, datasets) -> list[Suggestion]` — for each pattern with sufficient data, proposes a fix (CLAUDE.md rule, SKILL.md update, or hook config). Includes confidence score.
- [x] T038 [US4] Implement confidence scorer in `src/sio/suggestions/confidence.py`: `score_confidence(pattern, dataset) -> float` — based on pattern frequency, dataset size, consistency of failure mode
- [x] T039 [US4] Implement home file writer in `src/sio/suggestions/home_file.py`: `write_suggestions(suggestions, path="~/.sio/suggestions.md")` — writes ranked markdown with sections, approve/reject commands, pattern stats
- [x] T040 [US4] Implement passive runner in `src/sio/scheduler/runner.py`: `run_analysis(mode="daily"|"weekly")` — orchestrates mine → cluster → build datasets → generate suggestions → write home file
- [x] T041 [US4] Implement cron installer in `src/sio/scheduler/cron.py`: `install_schedule()` — writes crontab entries for daily (midnight) and weekly (Sunday midnight). `uninstall_schedule()`. `get_status()`.
- [x] T042 [US4] Wire up `sio schedule install`, `sio schedule status` CLI commands in `src/sio/cli/main.py`
- [x] T043 [US4] Verify all US4 tests pass (Green)

**Checkpoint**: Passive analysis pipeline produces suggestions.md. Scheduler installable. US4 independently testable.

---

## Phase 7: User Story 5 — Human Review & Tagging (Priority: P3)

**Goal**: Interactive review of suggestions with approve/reject/AI-assisted tagging.

**Independent Test**: Given suggestions, run `sio review` → approve/reject → verify state persists.

### Tests for User Story 5

- [x] T044 [P] [US5] Write unit tests for reviewer in `tests/unit/test_reviewer.py`: test loads pending suggestions, test approve changes status, test reject changes status, test defer leaves status pending, test state persists across sessions
- [x] T045 [P] [US5] Write unit tests for tagger in `tests/unit/test_tagger.py`: test AI-assisted tag generates explanation from examples, test human tag records user categorization, test tag persists on suggestion record

### Implementation for User Story 5

- [x] T046 [US5] Implement reviewer in `src/sio/review/reviewer.py`: `review_pending(db) -> list[Suggestion]` loads pending, `approve(db, id, note)`, `reject(db, id, note)`, `defer(db, id)`
- [x] T047 [US5] Implement tagger in `src/sio/review/tagger.py`: `ai_tag(pattern, dataset) -> str` generates AI explanation from pos/neg examples, `human_tag(suggestion_id, category, note)` records user categorization
- [x] T048 [US5] Wire up `sio review`, `sio approve <id>`, `sio reject <id>` CLI commands in `src/sio/cli/main.py` with Rich interactive UI
- [x] T049 [US5] Verify all US5 tests pass (Green)

**Checkpoint**: Interactive review works. Approve/reject persists. AI tagging generates explanations. US5 independently testable.

---

## Phase 8: User Story 6 — Change Application & Rollback (Priority: P3)

**Goal**: Approved suggestions write to Claude Code config files with rollback support. Uses v1 arena for drift/collision detection.

**Independent Test**: Approve suggestion → verify target file updated, git committed, rollback works.

### Tests for User Story 6

- [x] T050 [P] [US6] Write unit tests for writer in `tests/unit/test_writer.py`: test append to CLAUDE.md (never overwrites), test update SKILL.md, test merge into settings.json, test git commit created
- [x] T051 [P] [US6] Write unit tests for rollback in `tests/unit/test_rollback.py`: test reverts file to diff_before state, test logs rollback, test rollback of non-existent change returns error
- [x] T052 [P] [US6] Write integration test for suggest-to-apply pipeline in `tests/integration/test_suggest_to_apply.py`: generate suggestion → approve → apply → verify file changed → rollback → verify file restored

### Implementation for User Story 6

- [x] T053 [US6] Implement writer in `src/sio/applier/writer.py`: `apply_change(db, suggestion_id) -> AppliedChange` — reads suggestion, writes to target file (append for CLAUDE.md, merge for settings.json), records diff_before/diff_after, git commits. Uses v1's `drift_detector` and `collision` detector before applying.
- [x] T054 [US6] Implement rollback in `src/sio/applier/rollback.py`: `rollback_change(db, change_id)` — restores diff_before content, git commits the revert, marks rolled_back_at
- [x] T055 [US6] Implement changelog in `src/sio/applier/changelog.py`: `log_change(change)` appends to `~/.sio/changelog.md` with timestamp, pattern, target file, commit SHA
- [x] T056 [US6] Wire up `sio rollback <change_id>` and `sio status` CLI commands in `src/sio/cli/main.py`
- [x] T057 [US6] Verify all US6 tests pass (Green)

**Checkpoint**: Applied changes write to config files, git committed, rollback works. Full closed loop complete. US6 independently testable.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Integration testing, hardening, validation

- [ ] T058 [P] Write end-to-end integration test in `tests/integration/test_e2e_passive.py`: create sample session files → run full passive pipeline (mine → cluster → dataset → suggest → write home file) → verify suggestions.md populated
- [ ] T059 [P] Write CLI contract tests in `tests/contract/test_v2_cli_commands.py`: test all v2 CLI commands exit code 0, test `sio mine --since "1 day"` with sample data, test `sio patterns` output format, test `sio status` output
- [ ] T060 Run `ruff check src/ tests/` and fix all linting issues
- [ ] T061 Run full test suite (v1 + v2) and verify ALL pass
- [ ] T062 Run quickstart.md validation: follow every step on sample data

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundation)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1 Mining)**: Depends on Phase 2 — MVP foundation
- **Phase 4 (US2 Clustering)**: Depends on Phase 2 + US1 for error data
- **Phase 5 (US3 Datasets)**: Depends on US1 + US2 for patterns
- **Phase 6 (US4 Passive)**: Depends on US1 + US2 + US3 pipeline
- **Phase 7 (US5 Review)**: Depends on US4 for suggestions
- **Phase 8 (US6 Apply)**: Depends on US5 for approved suggestions
- **Phase 9 (Polish)**: Depends on all stories complete

### User Story Dependencies

```
Phase 2 (Foundation) → US1 (Mine) → US2 (Cluster) → US3 (Dataset) → US4 (Passive)
                                                                          ↓
                                                                    US5 (Review)
                                                                          ↓
                                                                    US6 (Apply)
```

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (Constitution IV)
2. Parsers/extractors before orchestration
3. Core logic before CLI wiring
4. Verify tests pass (Green) before moving to next story

### Parallel Opportunities

- **Phase 1**: T001-T003 can overlap (different dirs/files)
- **Phase 2**: T004-T005 (tests) in parallel, then T006-T009 (implementation) in parallel
- **Phase 3**: T011-T015 (all US1 tests) in parallel, then T016-T017 (parsers) in parallel
- **Phase 4**: T022-T023 (tests) in parallel
- **Phase 5**: T028-T029 (tests) in parallel
- **Phase 6**: T034-T036 (tests) in parallel
- **Phase 7**: T044-T045 (tests) in parallel
- **Phase 8**: T050-T052 (tests) in parallel
- **Phase 9**: T058-T059 in parallel

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (3 tasks)
2. Complete Phase 2: Foundation (7 tasks)
3. Complete Phase 3: US1 Error Mining (11 tasks)
4. **STOP and VALIDATE**: `sio mine --since "7 days"` works against real session data

### Incremental Delivery

1. Setup + Foundation → Infrastructure ready (v1 tests still green)
2. Add US1 → Mining works → Deploy/Demo (MVP!)
3. Add US2 → Patterns visible → Deploy/Demo
4. Add US3 → Datasets built → Deploy/Demo
5. Add US4 → Passive pipeline + home file → Deploy/Demo
6. Add US5 → Human review → Deploy/Demo
7. Add US6 → Full closed loop → Deploy/Demo
8. Polish → Production ready
