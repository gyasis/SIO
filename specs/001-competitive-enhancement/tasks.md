# Tasks: SIO Competitive Enhancement

**Input**: Design documents from `/specs/001-competitive-enhancement/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-commands.md, quickstart.md

**Tests**: Included — Constitution Principle IV (Test-First) is NON-NEGOTIABLE. Tests are written before implementation.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Project initialization — no code changes, just validation

- [x] T001 Verify branch `001-competitive-enhancement` is checked out and clean via `git status`
- [x] T002 Run existing test suite (`pytest tests/ -v`) to establish baseline — all must pass
- [x] T003 Verify existing schema loads cleanly: `python -c "from sio.core.db.schema import init_db; init_db(':memory:')"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema extensions, parser enhancements, and session tracking that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundation

- [x] T004 [P] Write test for new DDL tables (processed_sessions, session_metrics, positive_records, velocity_snapshots, autoresearch_txlog) and new columns (patterns.grade, applied_changes.delta_type) in `tests/test_schema_enhancement.py` — test that `init_db(':memory:')` creates all new tables and columns, verify constraints and indexes
- [x] T005 [P] Write test for enhanced JSONL parser extracting usage, costUsd, stopReason, isSidechain, model fields in `tests/test_jsonl_parser_enhanced.py` — create fixture JSONL with real Claude Code wire format containing these fields, verify extracted records include all new fields
- [x] T006 [P] Write test for processed_sessions tracking (skip re-mining, hash-based change detection) in `tests/test_processed_sessions.py` — test mine-once-skip-twice, test re-mine on content change

### Implementation for Foundation

- [x] T007 Add 5 new table DDLs (processed_sessions, session_metrics, positive_records, velocity_snapshots, autoresearch_txlog) and 2 ALTER column additions (patterns.grade, applied_changes.delta_type) to `src/sio/core/db/schema.py` — follow existing DDL pattern, add indexes per data-model.md
- [x] T008 Enhance `src/sio/mining/jsonl_parser.py` to extract `usage` object fields (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens), `costUsd`, `stopReason`, `isSidechain`, `model` from assistant messages — add these as new keys in returned dicts alongside existing role/content/tool_name/etc.
- [x] T009 Implement processed_sessions check in `src/sio/mining/pipeline.py` — before parsing a file, compute SHA-256 hash and check processed_sessions table; skip if hash matches; insert tracking record after successful mining
- [x] T010 Add session_metrics aggregation to `src/sio/mining/pipeline.py` — after mining a session, compute and insert per-session totals (tokens, cost, cache_hit_ratio, duration, message/tool/error/positive counts, stop_reason_distribution) into session_metrics table
- [ ] T011 Add smart filtering to `src/sio/mining/pipeline.py` — skip sessions with <5 messages or <2 tool calls; record as skipped in processed_sessions
- [x] T012 Add `--exclude-sidechains` flag to mining pipeline via `src/sio/cli/main.py` mine command — when set, filter out messages where isSidechain=True before aggregation
- [x] T013 Run foundation tests: `pytest tests/test_schema_enhancement.py tests/test_jsonl_parser_enhanced.py tests/test_processed_sessions.py -v` — all must pass
- [x] T014 [P] Update `src/sio/core/config.py` — add configurable defaults for: budget caps (100/50 lines), decay floor (0.3), decay bands (14/28 days), validation window (5 sessions), autoresearch interval (30 min), max experiments (3), dedup threshold (0.85), similarity threshold (0.80)
- [ ] T015_0 Run `ruff check src/sio/core/db/schema.py src/sio/mining/jsonl_parser.py src/sio/mining/pipeline.py src/sio/core/config.py --fix`

**Checkpoint**: Enhanced parser extracts all metadata; session tracking prevents re-mining; schema has all new tables. Run `sio mine` on a real session file to verify end-to-end.

---

## Phase 3: User Story 1 — Complete Session Data Extraction (Priority: P1) MVP

**Goal**: Mine sessions with >90% field extraction, including tokens, costs, cache ratios, sub-agent flags, and deduplication tracking.

**Independent Test**: Run `sio mine` on a real JSONL session → verify session_metrics row has token counts, cost, cache_hit_ratio, sidechain_count, and stop_reason_distribution populated.

> Phase 2 (Foundation) implements the core of US1. This phase adds the derived metrics and CLI output enhancements.

### Tests for User Story 1

- [ ] T015 [P] [US1] Write test for inter-message latency computation and cache_hit_ratio calculation in `tests/test_session_metrics.py` — fixture with known timestamps and token counts → verify derived metrics

### Implementation for User Story 1

- [ ] T016 [US1] Implement inter-message latency computation in `src/sio/mining/pipeline.py` — compute timestamp diffs between consecutive messages, store session_duration_seconds in session_metrics
- [x] T017 [US1] Update `sio mine` CLI output in `src/sio/cli/main.py` to display: sessions found, skipped (already processed), filtered (too small), newly mined, total cost tracked, and per-session token summary
- [ ] T018 [US1] Run `pytest tests/test_session_metrics.py -v` — must pass
- [ ] T019 [US1] Run `ruff check src/sio/mining/ src/sio/cli/main.py --fix`

**Checkpoint**: US1 complete — `sio mine` extracts >90% of available fields. Verify with real session file.

---

## Phase 4: User Story 2 — Positive Signal and Sentiment Capture (Priority: P1)

**Goal**: Capture positive signals (confirmation, gratitude, implicit approval), tool approval/rejection rates, and sentiment scoring with frustration escalation detection.

**Independent Test**: Mine a session containing "perfect, that's exactly right" → verify positive_records row with signal_type='confirmation'. Mine a session with 3+ corrections → verify frustration flag.

### Tests for User Story 2

- [x] T020 [P] [US2] Write test for positive signal extraction (7+ regex patterns, 4 signal types) in `tests/test_positive_extractor.py` — fixture messages with "yes exactly", "thanks great work", short positive after tool, session ending positively → verify each produces correct signal_type and context_before
- [x] T021 [P] [US2] Write test for tool approval/rejection detection and per-tool approval rates in `tests/test_approval_detector.py` — fixture with 10 tool calls where user approves 8 and rejects 2 → verify 80% overall rate and per-tool breakdown
- [x] T022 [P] [US2] Write test for sentiment scoring (-1.0 to +1.0) and frustration escalation (3+ consecutive negative) in `tests/test_sentiment_scorer.py` — fixture with escalating negative messages → verify scores decline and frustration flag triggers

### Implementation for User Story 2

- [x] T023 [P] [US2] Create `src/sio/mining/positive_extractor.py` — implement `extract_positive_signals(parsed_messages) -> list[dict]` with 7+ compiled regex patterns for confirmation, gratitude, implicit approval, session success; coordinate with `_POSITIVE_KEYWORDS` in `flow_extractor.py` to avoid duplication; each result includes signal_type, signal_text, context_before, tool_name
- [x] T024 [P] [US2] Create `src/sio/mining/approval_detector.py` — implement `detect_approvals(parsed_messages) -> dict` analyzing user response after each tool_use block; classify as approved/rejected based on response patterns; compute per-tool approval rates
- [x] T025 [P] [US2] Create `src/sio/mining/sentiment_scorer.py` — implement `score_sentiment(text) -> float` returning -1.0 to +1.0 using keyword frequency ratios; implement `detect_frustration_escalation(scores: list[float]) -> bool` returning True when 3+ consecutive scores are negative; include escalation keywords: "frustrated", "annoying", "waste of time", "just do X", "stop"
- [ ] T026 [US2] Integrate positive_extractor, approval_detector, and sentiment_scorer into `src/sio/mining/pipeline.py` — call extractors during mining, insert results into positive_records table, update session_metrics.positive_signal_count and correction_count
- [ ] T027 [US2] Run `pytest tests/test_positive_extractor.py tests/test_approval_detector.py tests/test_sentiment_scorer.py -v` — all must pass
- [ ] T028 [US2] Run `ruff check src/sio/mining/positive_extractor.py src/sio/mining/approval_detector.py src/sio/mining/sentiment_scorer.py --fix`

**Checkpoint**: US2 complete — system captures both errors AND positive signals. Mine a real session and verify positive_records populated.

---

## Phase 5: User Story 3 — Learning Velocity Tracking (Priority: P2)

**Goal**: Quantify how error rates change over time after rules are applied, proving that SIO's self-improvement loop works.

**Independent Test**: Apply a rule, mine 5+ sessions, run `sio velocity` → verify trend shows decrease for targeted error type.

### Tests for User Story 3

- [ ] T029 [P] [US3] Write test for velocity computation (rolling window error frequency, correction decay rate, adaptation speed) in `tests/test_velocity.py` — fixture with pre-rule and post-rule session data → verify error_rate decreases, adaptation_speed computed correctly

### Implementation for User Story 3

- [ ] T030 [US3] Create `src/sio/core/metrics/velocity.py` with `__init__.py` — implement `compute_velocity_snapshot(db, error_type, window_days=7) -> dict` computing error_rate_in_window, correction_decay_rate, adaptation_speed; implement `get_velocity_trends(db, error_type=None) -> list[dict]` returning snapshots over time; insert results into velocity_snapshots table linking to applied rule suggestion_id
- [ ] T031 [US3] Add `sio velocity` CLI command to `src/sio/cli/main.py` — options: `--error-type`, `--window` (default 7), `--format table|json`; display Rich table per contracts/cli-commands.md; flag ineffective rules (no improvement after 5 sessions)
- [ ] T032 [US3] Run `pytest tests/test_velocity.py -v` — must pass
- [ ] T033 [US3] Run `ruff check src/sio/core/metrics/ src/sio/cli/main.py --fix`

**Checkpoint**: US3 complete — `sio velocity` shows measurable error rate trends.

---

## Phase 6: User Story 4 — Instruction Budget Management (Priority: P2)

**Goal**: Prevent instruction file bloat with line caps, semantic consolidation, deduplication, and delta-based rule writing.

**Independent Test**: Set budget cap to 50 lines, attempt to apply a rule at 49 → verify consolidation triggered. Run `sio dedupe` on files with near-identical rules → verify merge proposed.

### Tests for User Story 4

- [ ] T034 [P] [US4] Write test for budget enforcement (line counting, cap checking, consolidation triggering, blocking) in `tests/test_budget.py` — test under budget (apply), near budget (consolidate), at budget (block)
- [ ] T035 [P] [US4] Write test for semantic deduplication (>85% similarity detection, merge proposal) in `tests/test_deduplicator.py` — fixture with two near-identical rules → verify consolidation proposed
- [ ] T036 [P] [US4] Write test for delta-based writer (merge vs append based on >80% similarity) in `tests/test_delta_writer.py` — test overlap detection, in-place update vs new append, delta_type tracking

### Implementation for User Story 4

- [ ] T037 [P] [US4] Create `src/sio/applier/budget.py` — implement `count_meaningful_lines(file_path) -> int` (exclude blanks, comments); implement `check_budget(file_path, new_rule_lines, config) -> BudgetResult` returning status (ok, consolidate, blocked); implement `trigger_consolidation(file_path, config) -> bool` merging semantically similar rules via FastEmbed (reuse `_get_backend()` from pattern_clusterer.py)
- [ ] T038 [P] [US4] Create `src/sio/applier/deduplicator.py` — implement `find_duplicates(file_paths, threshold=0.85) -> list[DuplicatePair]` scanning all rule files; implement `propose_merge(pair) -> str` generating consolidated text; reuse `_get_backend()` singleton
- [ ] T039 [US4] Modify `src/sio/applier/writer.py` to use delta-based writing — before appending, embed the new rule and compare against existing rules; if >80% similarity, update existing rule in place (merge); otherwise append; track delta_type ('merge' or 'append') in applied_changes table
- [ ] T040 [US4] Integrate budget check into `src/sio/applier/writer.py` — call `check_budget()` before any write; trigger consolidation if near cap; block if over cap
- [ ] T041 [US4] Add `sio budget` CLI command to `src/sio/cli/main.py` — display per-file budget usage (lines/cap/status) as Rich table per contracts/cli-commands.md
- [ ] T042 [US4] Add `sio dedupe` CLI command to `src/sio/cli/main.py` — options: `--threshold` (default 0.85), `--dry-run`, `--auto`; display duplicate pairs with proposed merges per contracts/cli-commands.md
- [ ] T043 [US4] Update `sio apply` CLI command in `src/sio/cli/main.py` — add budget check output, consolidation messaging, and block warnings per contracts/cli-commands.md
- [ ] T044 [US4] Run `pytest tests/test_budget.py tests/test_deduplicator.py tests/test_delta_writer.py -v` — all must pass
- [ ] T045 [US4] Run `ruff check src/sio/applier/ src/sio/cli/main.py --fix`

**Checkpoint**: US4 complete — instruction files stay within budget. Test with real CLAUDE.md.

---

## Phase 7: User Story 5 — Rule Violation Detection (Priority: P2)

**Goal**: Detect when the AI assistant violates rules already in its instruction files, flagging enforcement failures at higher priority than new patterns.

**Independent Test**: Add "never use SELECT *" to CLAUDE.md, mine a session where Claude used SELECT * → verify violation flagged.

### Tests for User Story 5

- [ ] T046 [P] [US5] Write test for violation detection (parse rules, match against mined errors, priority flagging) in `tests/test_violation_detector.py` — fixture with CLAUDE.md rules and session errors matching a rule → verify violation detected with higher priority than new patterns

### Implementation for User Story 5

- [ ] T047 [US5] Create `src/sio/mining/violation_detector.py` — implement `parse_rules(file_path) -> list[Rule]` extracting constraint text from markdown instruction files; implement `detect_violations(rules, error_records) -> list[Violation]` matching mined errors against parsed rules using keyword and semantic matching; flag violations at higher priority than new patterns
- [ ] T048 [US5] Add `sio violations` CLI command to `src/sio/cli/main.py` — options: `--since`, `--format table|json`; display violations sorted by frequency and recency per contracts/cli-commands.md
- [ ] T049 [US5] Run `pytest tests/test_violation_detector.py -v` — must pass
- [ ] T050 [US5] Run `ruff check src/sio/mining/violation_detector.py src/sio/cli/main.py --fix`

**Checkpoint**: US5 complete — `sio violations` identifies enforcement failures.

---

## Phase 8: User Story 6 — Confidence Decay and Pattern Grading (Priority: P2)

**Goal**: Patterns lose confidence over time if not seen; patterns that recur consistently auto-promote through lifecycle grades and trigger suggestion generation.

**Independent Test**: Create a pattern not seen in 30 days → verify confidence decayed. Create a pattern seen 3x across 3 sessions → verify grade='strong' and suggestion auto-generated.

### Tests for User Story 6

- [ ] T051 [P] [US6] Write test for temporal decay (Fresh/Cooling/Stale bands, floor at 0.3) in `tests/test_confidence_decay.py` — fixtures with patterns at 0, 14, 21, 30+ day ages → verify multipliers match bands
- [ ] T052 [P] [US6] Write test for pattern grading lifecycle (emerging→strong→established→declining) and auto-suggestion at "strong" in `tests/test_grader.py` — fixture with pattern at various occurrence/session counts → verify correct grade transitions and suggestion generation trigger

### Implementation for User Story 6

- [ ] T053 [US6] Modify `src/sio/suggestions/confidence.py` — add temporal decay as a 4th multiplicative factor in `score_confidence()`; implement `_compute_decay_multiplier(last_seen_date) -> float` with bands: Fresh (0-14 days, 1.0), Cooling (15-28 days, linear 1.0→0.6), Stale (29+ days, linear 0.6→floor 0.3)
- [ ] T054 [US6] Create `src/sio/clustering/grader.py` — implement `grade_pattern(pattern_row) -> str` computing grade from error_count, session_count, first_seen, last_seen, confidence; implement `run_grading(db) -> list[dict]` updating all patterns' grade column; implement `auto_generate_suggestions(db, strong_patterns)` creating suggestions for newly-promoted "strong" patterns without human trigger
- [ ] T055 [US6] Run `pytest tests/test_confidence_decay.py tests/test_grader.py -v` — all must pass
- [ ] T056 [US6] Run `ruff check src/sio/suggestions/confidence.py src/sio/clustering/grader.py --fix`

**Checkpoint**: US6 complete — stale patterns decay, recurring patterns promote, strong patterns auto-generate suggestions.

---

## Phase 9: User Story 7 — Real-Time Session Hooks (Priority: P3)

**Goal**: Capture session data at compaction, prompt submission, and session end via Claude Code lifecycle hooks.

**Independent Test**: Install hooks via `sio install`, run a session, trigger compaction → verify PreCompact captured metrics. Type a correction → verify UserPromptSubmit detected it. End session → verify Stop hook finalized metrics.

### Tests for User Story 7

- [ ] T057 [P] [US7] Write test for PreCompact hook (captures session snapshot, always returns allow, retry-on-failure) in `tests/test_hooks.py::TestPreCompact` — simulate hook input JSON → verify session_metrics snapshot saved and output is `{"action": "allow"}`
- [ ] T058 [P] [US7] Write test for Stop hook (finalizes metrics, saves high-confidence patterns to skills/) in `tests/test_hooks.py::TestStop` — simulate hook input → verify session_metrics finalized and pattern with confidence >0.8 saved to skills directory
- [ ] T059 [P] [US7] Write test for UserPromptSubmit hook (detects corrections, detects frustration, never blocks) in `tests/test_hooks.py::TestUserPromptSubmit` — simulate "no that's wrong, do X instead" → verify correction detected; simulate 3 negative messages → verify frustration logged
- [ ] T060 [P] [US7] Write test for hook installer registering all 4 hooks in settings.json in `tests/test_hooks.py::TestInstaller`

### Implementation for User Story 7

- [ ] T061 [P] [US7] Create `src/sio/adapters/claude_code/hooks/pre_compact.py` — read stdin JSON with session_id/transcript_path; capture session_metrics snapshot and recent positive signals; output `{"action": "allow"}`; implement retry-once-then-fail-silent error handling with logging to `~/.sio/hook_errors.log`
- [ ] T062 [P] [US7] Create `src/sio/adapters/claude_code/hooks/stop.py` — read stdin JSON; finalize session_metrics entry; run lightweight pattern detection; auto-save patterns with confidence >0.8 to `~/.claude/skills/learned/`; update processed_sessions; implement retry-once error handling
- [ ] T063 [P] [US7] Create `src/sio/adapters/claude_code/hooks/user_prompt_submit.py` — read stdin JSON with user_message; detect corrections/undo keywords; increment session correction counter; detect frustration escalation; output `{"action": "allow"}`; implement retry-once error handling; timeout budget: <2000ms
- [ ] T064 [US7] Update `src/sio/adapters/claude_code/installer.py` — register PreCompact, Stop, and UserPromptSubmit hooks alongside existing PostToolUse; update `sio install` to show all 4 hooks
- [ ] T065 [US7] Run `pytest tests/test_hooks.py -v` — all must pass
- [ ] T066 [US7] Run `ruff check src/sio/adapters/claude_code/hooks/ src/sio/adapters/claude_code/installer.py --fix`

**Checkpoint**: US7 complete — all hooks fire at designated moments. Test with real Claude Code session.

---

## Phase 10: User Story 8 — Automated Validation and Experimentation (Priority: P3)

**Goal**: Test rules before applying them permanently via binary assertions, git-backed experiments, anomaly detection, and an autonomous optimization loop with human promotion gates.

**Independent Test**: Apply a rule as experiment → verify worktree created → run assertions after 5 sessions → verify promote (pass) or rollback (fail). Start autoresearch → verify 1 cycle completes with txlog entries.

### Tests for User Story 8

- [ ] T067 [P] [US8] Write test for binary assertions (error_rate_decreased, no_new_regressions, confidence_above_threshold, budget_within_limits, no_collisions) in `tests/test_assertions.py` — fixture with pre/post session_metrics → verify pass/fail results with actual_value
- [ ] T068 [P] [US8] Write test for experiment lifecycle (create worktree, validate after N sessions, promote on pass, rollback on fail) in `tests/test_experiment.py` — mock git operations → verify branch creation, assertion running, merge or delete based on result
- [ ] T069 [P] [US8] Write test for autoresearch loop (single cycle: mine→cluster→grade→generate→assert→experiment, safety limits, stop mechanism, txlog) in `tests/test_autoresearch.py` — mock pipeline components → verify cycle completes, txlog populated, max-experiments enforced, stop file honored
- [ ] T070 [P] [US8] Write test for MAD anomaly detection (flag sessions >3 MADs from median) in `tests/test_anomaly.py` — fixture with 10 normal sessions + 1 outlier → verify outlier flagged

### Implementation for User Story 8

- [ ] T071 [P] [US8] Create `src/sio/core/arena/assertions.py` — implement `AssertionResult(passed, name, actual_value, threshold)` dataclass; implement built-in assertions: `error_rate_decreased(pre, post)`, `no_new_regressions(pre, post)`, `confidence_above_threshold(pattern, threshold)`, `budget_within_limits(file_path, config)`, `no_collisions(suggestion, existing)`; implement `run_assertions(assertion_names, context) -> list[AssertionResult]`; support custom assertions via config dict
- [ ] T072 [P] [US8] Create `src/sio/core/arena/anomaly.py` — implement `compute_mad(values) -> (median, mad)` for Median Absolute Deviation; implement `detect_anomalies(db, metric_name, threshold_mads=3) -> list[session_id]` checking error_rate, token_usage, session_duration, cost_per_session
- [ ] T073 [US8] Create `src/sio/core/arena/txlog.py` — implement `TxLog(db)` class with `append(cycle_number, action, status, details, suggestion_id=None, experiment_branch=None, assertion_results=None)` inserting into `autoresearch_txlog` SQL table (defined in schema.py T007); implement `read_log(cycle=None) -> list[dict]`; implement `active_experiment_count() -> int` counting experiments without corresponding promote/rollback entries
- [ ] T074 [US8] Create `src/sio/core/arena/experiment.py` — implement `create_experiment(suggestion_id, db) -> str` creating git worktree at `experiment/<sug-id>-<timestamp>`, applying rule in worktree; implement `validate_experiment(experiment_branch, db, assertions) -> bool` running assertions after configured sessions (default 5); implement `promote_experiment(experiment_branch, db)` merging worktree to main (requires human approval flag); implement `rollback_experiment(experiment_branch, db)` deleting worktree and marking suggestion as 'failed_experiment'
- [ ] T075 [US8] Create `src/sio/core/arena/autoresearch.py` — implement `AutoResearchLoop(db, config)` with `run_cycle() -> CycleResult` executing mine→cluster→grade→generate→assert→experiment→validate pipeline; enforce safety limits (max 3 experiments, max 1 rule/cycle, budget check); check for stop sentinel file at cycle start; pause for human approval before promotion per clarification Q1; implement `start(interval_minutes=30, max_cycles=None)` and `stop()` (writes sentinel file)
- [ ] T076 [US8] Add `sio autoresearch start|stop|status` CLI commands to `src/sio/cli/main.py` — options per contracts/cli-commands.md; `start` accepts `--interval`, `--max-cycles`, `--max-experiments`, `--dry-run`; `status` shows cycle count, active experiments, promoted/rolled back counts
- [ ] T077 [US8] Add `--experiment` flag to `sio apply` CLI command in `src/sio/cli/main.py` — when set, route through experiment.create_experiment instead of direct apply
- [ ] T078 [US8] Run `pytest tests/test_assertions.py tests/test_experiment.py tests/test_autoresearch.py tests/test_anomaly.py -v` — all must pass
- [ ] T079 [US8] Run `ruff check src/sio/core/arena/ src/sio/cli/main.py --fix`

**Checkpoint**: US8 complete — experiments create worktrees, assertions validate, autoresearch completes cycles with txlog. Test with `sio autoresearch start --max-cycles 1 --dry-run`.

---

## Phase 11: User Story 9 — Interactive Reporting (Priority: P3)

**Goal**: Generate a standalone HTML report with session metrics dashboard, error trends, pattern tables, copy-ready suggestions, and learning velocity graphs.

**Independent Test**: Run `sio report --html` with 5+ mined sessions → verify HTML file opens in browser with charts and copy-able suggestion cards.

### Tests for User Story 9

- [ ] T080 [P] [US9] Write test for HTML report generation (standalone file, embedded CSS/JS, required sections) in `tests/test_html_report.py` — fixture with session_metrics, patterns, suggestions → verify output is valid HTML with all required sections
- [ ] T081 [P] [US9] Write test for session facet extraction (4 categories, caching by file hash) in `tests/test_facet_extractor.py` — fixture with complex session → verify facets generated; re-run → verify cached result returned

### Implementation for User Story 9

- [ ] T082 [P] [US9] Create `src/sio/mining/facet_extractor.py` — implement `extract_facets(parsed_messages, session_metrics) -> dict` using keyword-based heuristics (no LLM): tool_mastery (count distinct tools used + approval rates), error_prone_area (most frequent error_type), user_satisfaction (average sentiment score), session_complexity (message count + token count + tool diversity); implement file-hash-based caching in `~/.sio/facets/`
- [ ] T083 [US9] Create `src/sio/reports/html_report.py` with `__init__.py` — implement `generate_html_report(db, days=30) -> str` producing self-contained HTML with: session metrics dashboard (tokens, cost, cache efficiency over time), error trend chart (30-day rolling), pattern table (confidence + grade + decay visualization), copy-ready suggestion cards, learning velocity graph; use Python string.Template for HTML generation; embed Chart.js inline
- [ ] T084 [US9] Add `sio report --html` CLI command to `src/sio/cli/main.py` — options: `--html`, `--output` (default ~/.sio/reports/report-YYYYMMDD.html), `--days` (default 30), `--open` (opens in browser); display generation progress and output path
- [ ] T085 [US9] Run `pytest tests/test_html_report.py tests/test_facet_extractor.py -v` — all must pass
- [ ] T086 [US9] Run `ruff check src/sio/reports/ src/sio/mining/facet_extractor.py src/sio/cli/main.py --fix`

**Checkpoint**: US9 complete — `sio report --html` generates viewable report. Open in browser to validate.

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, integration testing, and cleanup

- [ ] T087 [P] Write end-to-end integration test in `tests/test_integration_competitive.py` — mine a real session file → verify all new tables populated (session_metrics, positive_records, processed_sessions); run velocity, budget, violations commands → verify output
- [ ] T088 Run full test suite: `pytest tests/ -v` — all tests (existing + new) must pass
- [ ] T089 Run full lint: `ruff check src/ tests/ --fix`
- [ ] T090 Verify quickstart.md steps work: follow setup instructions, run each command in sequence
- [ ] T091 Verify all configurable defaults from `src/sio/core/config.py` (T014) are used consistently across all new modules — no hardcoded magic numbers

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundation)**: Depends on Phase 1 — **BLOCKS all user stories**
- **Phase 3 (US1)**: Depends on Phase 2 — completes data extraction
- **Phase 4 (US2)**: Depends on Phase 2 — uses enhanced parser output
- **Phase 5 (US3)**: Depends on Phase 3 + Phase 4 — needs session_metrics + positive signals for velocity
- **Phase 6 (US4)**: Depends on Phase 2 only — operates on rule files, not session data
- **Phase 7 (US5)**: Depends on Phase 2 — needs mined error_records
- **Phase 8 (US6)**: Depends on Phase 2 — needs patterns table with timestamps
- **Phase 9 (US7)**: Depends on Phase 2 + Phase 4 — hooks use extractors
- **Phase 10 (US8)**: Depends on Phase 5 + Phase 8 — needs velocity + grading for assertions
- **Phase 11 (US9)**: Depends on Phase 3 + Phase 4 + Phase 5 + Phase 8 — reports on everything
- **Phase 12 (Polish)**: Depends on all desired user stories complete

### User Story Dependencies (visual)

```
Phase 2 (Foundation) ─┬─→ US1 (Session Data) ──┬─→ US3 (Velocity) ──→ US8 (Experimentation)
                      │                         │                        ↑
                      ├─→ US2 (Positive Signals)┤                        │
                      │                         ├─→ US7 (Hooks)          │
                      ├─→ US4 (Budget) ─────────┤                        │
                      ├─→ US5 (Violations) ─────┤                        │
                      └─→ US6 (Decay/Grading) ──┴────────────────────────┘
                                                          │
                                                          └─→ US9 (Reporting)
```

### Parallel Opportunities

After Phase 2 completes, these can run in parallel:
- **Parallel Group A**: US1 + US2 (different extractors, different files)
- **Parallel Group B**: US4 + US5 + US6 (all independent: budget, violations, grading)
- **Parallel Group C**: US7 (hooks — depends on US2 extractors, so start after US2)

After US1+US2 complete:
- **Parallel Group D**: US3 (velocity) can start
- After US3+US6: US8 (experimentation) can start
- After US3+US6+US2: US9 (reporting) can start

---

## Parallel Example: Foundation Phase

```bash
# Launch all foundation tests in parallel (T004, T005, T006):
Agent: "Write schema enhancement tests in tests/test_schema_enhancement.py"
Agent: "Write enhanced JSONL parser tests in tests/test_jsonl_parser_enhanced.py"
Agent: "Write processed sessions tests in tests/test_processed_sessions.py"

# Then launch schema + parser in parallel (T007, T008):
Agent: "Add new tables to src/sio/core/db/schema.py"
Agent: "Enhance JSONL parser in src/sio/mining/jsonl_parser.py"
```

## Parallel Example: US2 (Positive Signals)

```bash
# Launch all US2 tests in parallel (T020, T021, T022):
Agent: "Write positive extractor tests"
Agent: "Write approval detector tests"
Agent: "Write sentiment scorer tests"

# Then launch all US2 implementations in parallel (T023, T024, T025):
Agent: "Create positive_extractor.py"
Agent: "Create approval_detector.py"
Agent: "Create sentiment_scorer.py"

# Then sequential integration (T026):
Agent: "Integrate extractors into pipeline.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundation (T004–T014)
3. Complete Phase 3: US1 — Session Data (T015–T019)
4. Complete Phase 4: US2 — Positive Signals (T020–T028)
5. **STOP and VALIDATE**: `sio mine` extracts >90% of fields AND captures positive signals
6. This alone delivers SC-001 + SC-002 from success criteria

### Incremental Delivery

1. Foundation + US1 + US2 → MVP (data extraction + positive signals)
2. Add US3 (velocity) + US6 (decay/grading) → Intelligence layer
3. Add US4 (budget) + US5 (violations) → Rule management
4. Add US7 (hooks) → Real-time capture
5. Add US8 (experimentation) → Automated validation
6. Add US9 (reporting) → User-facing visualization

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Constitution Principle IV (TDD) enforced: test tasks precede implementation tasks in every phase
- Constitution Principle XI: every implementation must be real — no stubs or placeholders
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Total: 91 tasks across 12 phases
