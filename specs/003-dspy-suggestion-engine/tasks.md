# Tasks: DSPy Suggestion Engine

**Input**: Design documents from `/specs/003-dspy-suggestion-engine/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/
**Tests**: REQUIRED per Constitution Principle IV (Test-First, NON-NEGOTIABLE)

**Organization**: Tasks grouped by user story. P1 stories first, then P2, then P3.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1-US7)
- Exact file paths included in descriptions

---

## Phase 1: Setup

**Purpose**: Project initialization — new packages, dependency updates, directory structure

- [x] T001 Update `dspy>=3.1.3` in pyproject.toml (currently `>=2.5`) per research.md R7
- [x] T002 [P] Create `src/sio/ground_truth/__init__.py` package directory
- [x] T003 [P] Create `~/.sio/ground_truth/` and `~/.sio/optimized/` directories in installer at `src/sio/adapters/claude_code/installer.py`

**Checkpoint**: Project structure ready, dependencies aligned

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that ALL user stories depend on — LLM config, DB schema, DSPy primitives

**CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational

- [x] T004 [P] Write test for LLM config parsing in `tests/unit/test_config_llm.py` — test `[llm]` and `[llm.sub]` TOML sections, env var auto-detection priority order, missing config fallback
- [x] T005 [P] Write test for LM factory in `tests/unit/test_lm_factory.py` — test `create_lm()` returns `dspy.LM` from config, env var detection, `None` when no LLM available
- [x] T006 [P] Write test for ground_truth table DDL in `tests/unit/test_schema.py` — extend existing schema tests with `ground_truth` and `optimized_modules` tables, CHECK constraints on `target_surface`, `label`, `source`
- [x] T007 [P] Write test for DSPy Signature definitions in `tests/unit/test_dspy_signatures.py` — test `SuggestionGenerator` has correct input/output fields, field descriptions present
- [x] T008 [P] Write test for DSPy Module in `tests/unit/test_dspy_modules.py` — test `SuggestionModule` wraps `ChainOfThought(SuggestionGenerator)`, `forward()` accepts correct args
- [x] T009 [P] Write test for module store in `tests/unit/test_module_store.py` — test `save_module()` writes JSON, `load_module()` restores, `get_active_module()` returns latest active

### Implementation for Foundational

- [x] T010 Extend `SIOConfig` dataclass with LLM fields (`llm_model`, `llm_api_key_env`, `llm_api_base_env`, `llm_temperature`, `llm_max_tokens`, `llm_sub_model`) and TOML `[llm]` section parsing in `src/sio/core/config.py`
- [x] T011 [P] Create LM factory `create_lm(config) -> dspy.LM | None` with auto-detection priority (Azure → Anthropic → OpenAI → None) in `src/sio/core/dspy/lm_factory.py`
- [x] T012 Add `ground_truth` and `optimized_modules` tables to `init_db()` in `src/sio/core/db/schema.py` per contracts/ground-truth-schema.md DDL
- [x] T013 Add `ALTER TABLE suggestions ADD COLUMN target_surface TEXT` and `reasoning_trace TEXT` migration in `src/sio/core/db/schema.py`
- [x] T014 [P] Define `SuggestionGenerator` DSPy Signature in `src/sio/core/dspy/signatures.py` per contracts/dspy-signatures.md — inputs: `error_examples`, `error_type`, `pattern_summary`; outputs: `target_surface`, `rule_title`, `prevention_instructions`, `rationale`
- [x] T015 [P] Define `GroundTruthCandidate` DSPy Signature in `src/sio/core/dspy/signatures.py` — same as SuggestionGenerator plus `quality_assessment` output field
- [x] T016 [P] Implement `SuggestionModule(dspy.Module)` with `ChainOfThought(SuggestionGenerator)` in `src/sio/core/dspy/modules.py`
- [x] T017 [P] Implement `GroundTruthModule(dspy.Module)` with `ChainOfThought(GroundTruthCandidate)` in `src/sio/core/dspy/modules.py`
- [x] T018 [P] Implement `save_module()`, `load_module()`, `get_active_module()`, `deactivate_previous()` in `src/sio/core/dspy/module_store.py`
- [x] T019 Add ground truth CRUD operations to `src/sio/core/db/queries.py` — `insert_ground_truth()`, `get_ground_truth_by_pattern()`, `get_pending_ground_truth()`, `update_ground_truth_label()`, `get_training_corpus()`, `get_ground_truth_stats()`

**Checkpoint**: Foundation ready — LLM config, DB schema, DSPy primitives all in place. All foundational tests pass.

---

## Phase 3: User Story 1 — LLM-Generated Improvement Rules (Priority: P1) MVP

**Goal**: `sio suggest` calls DSPy instead of string templates to generate specific, actionable improvement rules from error patterns

**Independent Test**: Mine 10+ errors, run `sio suggest`, verify generated rules reference specific error details (tool names, error messages) rather than generic template text. Two distinct patterns should produce two distinct rules.

### Tests for User Story 1

- [x] T020 [P] [US1] Write test for DSPy generator in `tests/unit/test_dspy_generator.py` — test `generate_dspy_suggestion()` calls `SuggestionModule.forward()` with sanitized inputs, returns suggestion dict with all required fields including `target_surface` and `reasoning_trace`
- [x] T021 [P] [US1] Write test for input sanitization in `tests/unit/test_dspy_generator.py` — test API keys, passwords, tokens stripped from error examples before LLM call (FR-012), fields truncated to 500 chars (FR-013)
- [x] T022 [P] [US1] Write test for template fallback in `tests/unit/test_dspy_generator.py` — test that when `create_lm()` returns None, generator falls back to existing template logic with user-facing message
- [x] T023 [P] [US1] Write integration test in `tests/integration/test_dspy_pipeline.py` — test full pipeline: mock LLM → generate suggestions for tool_failure pattern → verify suggestion references specific tool name and error text
- [x] T024 [P] [US1] Write contract test in `tests/contract/test_dspy_contracts.py` — test DSPy Signature input/output field names match contracts/dspy-signatures.md

### Implementation for User Story 1

- [x] T025 [US1] Create `generate_dspy_suggestion(pattern, dataset, config) -> dict` in `src/sio/suggestions/dspy_generator.py` — loads LM via factory, configures DSPy, runs `SuggestionModule.forward()`, returns suggestion dict with `target_surface`, `reasoning_trace`, `proposed_change`, `confidence`
- [x] T026 [US1] Implement input sanitization in `src/sio/suggestions/dspy_generator.py` — `_sanitize_examples()` strips secrets (regex for API keys, passwords, tokens), `_truncate_fields()` caps each field at 500 chars (FR-012, FR-013)
- [x] T027 [US1] Implement verbose trace logging in `src/sio/suggestions/dspy_generator.py` — when `verbose=True`, log DSPy input, output, and reasoning trace (FR-014)
- [x] T028 [US1] Modify `generate_suggestions()` in `src/sio/suggestions/generator.py` — add DSPy path: if `create_lm(config)` returns a valid LM, delegate each pattern to `generate_dspy_suggestion()`; else fall back to existing template builders with Rich warning message (FR-006, FR-007)
- [x] T029 [US1] Modify `sio suggest` command in `src/sio/cli/main.py` — pass `--verbose` flag through to generator, display `[DSPy]` or `[Template]` tag per suggestion in Rich output
- [x] T030 [US1] Implement multi-surface targeting — DSPy output `target_surface` maps to correct `target_file` path via lookup table in `src/sio/suggestions/dspy_generator.py` (FR-024, FR-025)

**Checkpoint**: `sio suggest` generates LLM-powered suggestions when LLM configured, falls back to templates otherwise. All US1 tests pass.

---

## Phase 4: User Story 2 — Configurable LLM Backend (Priority: P1)

**Goal**: Users configure LLM via `~/.sio/config.toml` with zero code changes

**Independent Test**: Create config with model details, run `sio suggest`, verify system uses configured model. Change config to different model, verify switch.

### Tests for User Story 2

- [x] T031 [P] [US2] Write test for config template creation in `tests/unit/test_installer.py` — test that `sio install` creates `~/.sio/config.toml` with `[llm]` section template if not exists
- [x] T032 [P] [US2] Write test for env var auto-detection in `tests/unit/test_lm_factory.py` — test priority order: AZURE_OPENAI_API_KEY → ANTHROPIC_API_KEY → OPENAI_API_KEY → None (FR-005)

### Implementation for User Story 2

- [x] T033 [US2] Add config.toml template creation to `_install_config()` in `src/sio/adapters/claude_code/installer.py` — write template `[llm]` section with commented-out examples for Azure, Anthropic, OpenAI, Ollama
- [x] T034 [US2] Add `sio config show` subcommand to `src/sio/cli/main.py` — display current LLM config (model name, provider detected, sub-model) in Rich table, mask API keys
- [x] T035 [US2] Add `sio config test` subcommand to `src/sio/cli/main.py` — run a simple `dspy.Predict("question -> answer")` call to verify LLM connectivity, report success/failure with latency

**Checkpoint**: LLM backend fully configurable via TOML + env vars. US2 tests pass.

---

## Phase 5: User Story 6 — Agent-Generated Synthetic Ground Truth (Priority: P1)

**Goal**: LLM generates candidate ideal outputs per error pattern; human reviews as data analyst; approved candidates become DSPy training data

**Independent Test**: Run `sio ground-truth generate` on existing patterns, verify 3-5 candidates per pattern. Review and approve best ones. Verify approved examples are loadable as `dspy.Example` training data.

### Tests for User Story 6

- [x] T036 [P] [US6] Write test for ground truth generator in `tests/unit/test_ground_truth_gen.py` — test `generate_candidates()` calls `GroundTruthModule` N times per pattern, stores results in DB with `label='pending'`, `source='agent'`
- [x] T037 [P] [US6] Write test for ground truth reviewer in `tests/unit/test_ground_truth_review.py` — test `approve()` sets label='positive', source='approved'; `reject()` sets label='negative', source='rejected'; `edit()` creates new row with source='edited', label='positive'
- [x] T038 [P] [US6] Write test for corpus loader in `tests/unit/test_ground_truth_corpus.py` — test `load_training_corpus()` returns list of `dspy.Example` objects with `.with_inputs()` set correctly; only positive-labeled rows included
- [x] T039 [P] [US6] Write test for seeder in `tests/unit/test_ground_truth_seeder.py` — test `seed_ground_truth()` generates examples covering all 7 surface types (FR-026), stores with `source='seed'`
- [x] T040 [P] [US6] Write integration test in `tests/integration/test_ground_truth_flow.py` — test full cycle: generate candidates → review (approve/reject) → load corpus → verify dspy.Example format

### Implementation for User Story 6

- [x] T041 [US6] Implement `generate_candidates(pattern, dataset, config, n_candidates=3)` in `src/sio/ground_truth/generator.py` — calls `GroundTruthModule.forward()` N times, inserts each candidate into `ground_truth` table with `label='pending'`, writes JSON file
- [x] T042 [US6] Implement `approve()`, `reject(note)`, `edit(new_content)` in `src/sio/ground_truth/reviewer.py` — updates `ground_truth.label` and `source` appropriately, handles edited ground truth as new positive row (FR-018, FR-019, FR-020)
- [x] T043 [US6] Implement `load_training_corpus(conn) -> list[dspy.Example]` in `src/sio/ground_truth/corpus.py` — queries positive-labeled ground truth, converts to `dspy.Example` with `.with_inputs()` per data-model.md conversion function (FR-021, FR-022)
- [x] T044 [US6] Implement `seed_ground_truth(config, conn)` in `src/sio/ground_truth/seeder.py` — generates 10 seed examples covering all 7 surface types using representative synthetic error patterns (FR-017, FR-026)
- [x] T045 [US6] Add `sio ground-truth seed`, `sio ground-truth generate`, `sio ground-truth review`, `sio ground-truth status` CLI commands in `src/sio/cli/main.py` per contracts/cli-commands.md
- [x] T046 [US6] Implement interactive review TUI in `sio ground-truth review` — Rich panels showing pattern summary, candidate output, target surface; prompt for [a]pprove/[r]eject/[e]dit/[s]kip/[q]uit
- [x] T047 [US6] Wire suggestion approval to ground truth promotion — when `sio approve <id>` runs, auto-call `promote_to_ground_truth()` in `src/sio/ground_truth/corpus.py` to create positive training example from approved suggestion (FR-018)

**Checkpoint**: Full ground truth lifecycle works: generate → review → approve → training corpus. All US6 tests pass.

---

## Phase 6: User Story 3 — Quality Scoring via LLM Metric (Priority: P2)

**Goal**: Each suggestion has a quality score based on specificity, actionability, and surface accuracy

**Independent Test**: Generate suggestions for patterns of varying quality, verify scores reflect evidence strength and rule specificity.

### Tests for User Story 3

- [x] T048 [P] [US3] Write test for metric function in `tests/unit/test_dspy_metrics.py` — test `suggestion_quality_metric()` returns float 0-1; test specificity component (mentions tool name → higher score); test actionability component (concrete steps → higher score); test surface accuracy component (correct surface → higher score, wrong surface → penalty per FR-027)
- [x] T049 [P] [US3] Write test for metric in optimization mode in `tests/unit/test_dspy_metrics.py` — test that when `trace is not None`, returns `bool(score > 0.5)` for DSPy optimization loop

### Implementation for User Story 3

- [x] T050 [US3] Implement `suggestion_quality_metric(example, pred, trace=None) -> float|bool` in `src/sio/core/dspy/metrics.py` — weighted scoring: specificity (0.35), actionability (0.35), surface accuracy (0.30) per contracts/dspy-signatures.md
- [x] T051 [US3] Implement specificity scorer in `src/sio/core/dspy/metrics.py` — checks if `prevention_instructions` references concrete details from `error_examples` (tool names, error message snippets, user contexts)
- [x] T052 [US3] Implement actionability scorer in `src/sio/core/dspy/metrics.py` — checks for concrete action verbs, specific file paths, command examples in `prevention_instructions`
- [x] T053 [US3] Implement surface accuracy scorer in `src/sio/core/dspy/metrics.py` — validates `target_surface` is appropriate for `error_type` and context signals (MCP errors → mcp_config/settings_config, tool routing → skill_update, etc.)
- [x] T054 [US3] Wire metric into suggestion confidence — modify `generate_dspy_suggestion()` in `src/sio/suggestions/dspy_generator.py` to compute quality metric on each generated suggestion and set `confidence` field

**Checkpoint**: All suggestions have meaningful quality scores. Metric function ready for optimizer use. US3 tests pass.

---

## Phase 7: User Story 4 — DSPy Optimizer Integration (Priority: P2)

**Goal**: BootstrapFewShot and MIPROv2 optimize the suggestion module using ground truth corpus as training data

**Independent Test**: Approve 10+ suggestions, trigger optimization, verify optimized module scores higher than default.

### Tests for User Story 4

- [x] T055 [P] [US4] Write test for optimizer replacement in `tests/unit/test_optimizer.py` — extend existing tests: test `_run_dspy_optimization()` now calls real `dspy.BootstrapFewShot.compile()` with `SuggestionModule` and `trainset` from ground truth corpus
- [x] T056 [P] [US4] Write test for auto-optimizer selection in `tests/unit/test_optimizer.py` — test 10-49 examples → BootstrapFewShot, 50+ examples → MIPROv2 (FR-010)
- [x] T057 [P] [US4] Write test for module persistence in `tests/unit/test_optimizer.py` — test optimized module saved to `~/.sio/optimized/`, loaded on next `sio suggest` run (FR-011)
- [x] T058 [P] [US4] Write integration test in `tests/integration/test_optimizer_real.py` — test full cycle with mock LLM: create ground truth → optimize → verify optimized module loaded on next suggest call

### Implementation for User Story 4

- [x] T059 [US4] Replace `_run_dspy_optimization()` stub in `src/sio/core/dspy/optimizer.py` — real implementation: load ground truth corpus via `load_training_corpus()`, configure DSPy LM, run `BootstrapFewShot.compile(SuggestionModule(), trainset=corpus, metric=suggestion_quality_metric)`
- [x] T060 [US4] Add MIPROv2 path in `src/sio/core/dspy/optimizer.py` — when `optimizer='miprov2'` and 50+ examples, use `MIPROv2(metric=..., auto="medium").compile()`
- [x] T061 [US4] Add auto-selection logic in `src/sio/core/dspy/optimizer.py` — when `optimizer='auto'`: <50 examples → bootstrap, >=50 → miprov2 (FR-010)
- [x] T062 [US4] Wire optimized module loading into suggestion generation — modify `generate_dspy_suggestion()` in `src/sio/suggestions/dspy_generator.py` to check `get_active_module()` and load optimized module if available (FR-011)
- [x] T063 [US4] Add `sio optimize suggestions [--optimizer TYPE] [--dry-run]` CLI command in `src/sio/cli/main.py` per contracts/cli-commands.md — show before/after metric scores, prompt for approval
- [x] T064 [US4] Implement before/after prompt diff display — when optimization completes, show Rich diff of default vs optimized module's few-shot examples and instruction text

**Checkpoint**: Optimization loop closed — approved suggestions improve future generation. US4 tests pass.

---

## Phase 8: User Story 7 — Automated and Human-in-the-Middle Modes (Priority: P2)

**Goal**: Two pipeline modes: `--auto` for high-confidence patterns, `--analyze` for human-in-the-loop dataset curation

**Independent Test**: Run `--auto` on a well-established pattern → produces suggestion without multi-step review. Run `--analyze` on a novel pattern → pauses for human review at each stage.

### Tests for User Story 7

- [x] T065 [P] [US7] Write test for mode selection logic in `tests/unit/test_dspy_generator.py` — test auto mode when confidence >= 0.8 AND surface in low-impact set; HITL mode for high-impact surfaces or low confidence
- [x] T066 [P] [US7] Write test for HITL flow in `tests/unit/test_dspy_generator.py` — test that `--analyze` mode pauses at each stage: dataset summary → ground truth review → suggestion review → final approval
- [x] T067 [P] [US7] Write test for `sio datasets inspect` in `tests/unit/test_dspy_generator.py` — test shows error distribution, session timeline, ground truth entries, coverage gaps

### Implementation for User Story 7

- [x] T068 [US7] Implement mode selection logic in `src/sio/suggestions/dspy_generator.py` — `_select_mode(pattern, confidence, target_surface) -> "auto" | "hitl"` based on confidence threshold (0.8) and surface impact classification
- [x] T069 [US7] Implement automated mode flow in `src/sio/suggestions/dspy_generator.py` — generate ground truth candidates, auto-select highest-scoring, generate suggestion, present single approve/reject
- [x] T070 [US7] Implement HITL mode flow in `src/sio/suggestions/dspy_generator.py` — 5-step interactive flow: dataset analysis summary → generate GT candidates + pause → human validates → generate suggestion + reasoning trace → final approval
- [x] T071 [US7] Add `--auto` and `--analyze` flags to `sio suggest` in `src/sio/cli/main.py` — `--auto` forces automated, `--analyze` forces HITL, default auto-selects per pattern
- [x] T072 [US7] Add `sio datasets inspect <pattern_id>` command in `src/sio/cli/main.py` — Rich panels: error distribution by type, session timeline, ground truth entries count, label distribution, coverage gaps per surface type
- [x] T073 [US7] Implement dataset analysis summary for HITL mode — Rich table showing error count, session count, date range, top tools, top error messages, surface routing prediction

**Checkpoint**: Both auto and HITL modes work. Users can control human involvement level. US7 tests pass.

---

## Phase 9: User Story 5 — SIO Runs on Itself (Priority: P3)

**Goal**: SIO mines its own development history, generates improvement suggestions, and verifies the pipeline end-to-end on real data

**Independent Test**: Run `sio mine --since "30 days"` from SIO project, then `sio suggest`, verify generated rules are specific to SIO development errors.

### Tests for User Story 5

- [x] T074 [P] [US5] Write integration test in `tests/integration/test_dspy_pipeline.py` — extend existing: test full pipeline from SIO's own SpecStory history → mine → cluster → dataset → suggest with DSPy → verify suggestions reference real SIO development patterns

### Implementation for User Story 5

- [x] T075 [US5] Create self-test script `scripts/self_test.sh` — runs: `sio mine --since "30 days"` → `sio patterns` → `sio suggest --verbose` → validates output has >= 3 suggestions with specific error references
- [x] T076 [US5] Document self-test procedure in `specs/003-dspy-suggestion-engine/quickstart.md` — step-by-step instructions for running SIO on itself

**Checkpoint**: SIO successfully improves itself. US5 integration test passes.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Replace remaining stubs, cross-story improvements, final validation

- [x] T077 [P] Replace `mine_failure_context()` stub in `src/sio/core/dspy/rlm_miner.py` with real `dspy.RLM()` call — use corpus_indexer for variable space, config sub_lm for cheap extraction (Constitution Principle XI)
- [x] T078 [P] Replace `search_embedding()` stub in `src/sio/core/dspy/corpus_indexer.py` with real fastembed vector search using existing `src/sio/core/embeddings/provider.py` (Constitution Principle XI)
- [x] T079 [P] Write test for real RLM miner in `tests/unit/test_rlm_miner.py` — extend existing: test real `dspy.RLM` call with mock corpus, verify trajectory has actual code/output steps
- [x] T080 [P] Write test for real corpus embedding search in `tests/unit/test_corpus_indexer.py` — extend existing: test `search_embedding()` returns results different from keyword search, uses fastembed vectors
- [x] T081 Run `ruff check src/ tests/` and fix all lint issues
- [x] T082 Run full test suite `pytest tests/ -v` — all 756+ existing tests still pass, all new tests pass
- [x] T083 Run `scripts/self_test.sh` — SIO runs on itself end-to-end (SC-006)
- [x] T084 Validate SC-007: generated suggestions target >= 3 different surface types
- [x] T085 Validate SC-008: seed ground truth covers all 7 surface types

---

## Phase 11: Adversarial Audit Fixes

**Purpose**: Address ALL findings from 3 adversarial audits (placeholder exterminator, spec compliance, logic bug hunter). CRITICAL/HIGH/MAJOR issues already fixed in prior wave; remaining MINOR issues tracked here.

### Already Fixed (CRITICAL/HIGH/MAJOR — completed in prior wave)

- [x] T086 [CRITICAL] Wire --auto/--analyze CLI flags through to `generate_suggestions(mode=...)` in `src/sio/cli/main.py` and `src/sio/suggestions/generator.py`
- [x] T087 [CRITICAL] Fix `_SURFACE_TARGET_MAP` in `src/sio/suggestions/dspy_generator.py` — mcp_config→`.claude/mcp.json`, agent_profile→`.claude/agents/`, project_config→`CLAUDE.md`
- [x] T088 [CRITICAL] Fix stale column refs in `src/sio/core/db/queries.py` — update `_DATASET_COLS`, `_SUGGESTION_COLS`, `_APPLIED_CHANGE_COLS` to match actual DDL
- [x] T089 [HIGH] Add SQL injection prevention via column whitelist (`_PATTERN_UPDATE_ALLOWED`, `_DATASET_UPDATE_ALLOWED`) in `src/sio/core/db/queries.py`
- [x] T090 [HIGH] Fix double-commit race in `save_module()` — inline deactivation+insert in single transaction in `src/sio/core/dspy/module_store.py`
- [x] T091 [HIGH] Fix INSERT OR REPLACE data loss — change to INSERT ... ON CONFLICT DO UPDATE in `src/sio/core/db/queries.py` `insert_pattern()`
- [x] T092 [HIGH] Fix DB connection leak in `_load_optimized_or_default()` — add try/finally in `src/sio/suggestions/dspy_generator.py`
- [x] T093 [MAJOR] Fix wrong `llm_calls=2` in heuristic path — set to 0 in `src/sio/core/dspy/rlm_miner.py` `_heuristic_mining()`
- [x] T094 [MAJOR] Add logging for swallowed exceptions in `_corpus_search()` and DSPy failure path in `src/sio/core/dspy/rlm_miner.py`
- [x] T095 [MAJOR] Fix `skill_md_update` → `skill_update` in template fallback `_TARGET_FILE_MAP` and `_infer_change_type()` in `src/sio/suggestions/generator.py`
- [x] T096 [MAJOR] Fix wrong separator in `promote_to_ground_truth()` — try em-dash first, then `" -- "` in `src/sio/ground_truth/corpus.py`
- [x] T097 [MAJOR] Fix non-deterministic fuzzy match — use `sorted(_VALID_SURFACES)` in `_normalize_surface()` in `src/sio/suggestions/dspy_generator.py`
- [x] T098 [MAJOR] Add warning log when DSPy returns default/empty field values in `src/sio/suggestions/dspy_generator.py`
- [x] T099 [MAJOR] Fix `update_suggestion_status()` column names — `reviewed_at` (was `updated_at`), `user_note` (was `note`) in `src/sio/core/db/queries.py`
- [x] T100 [MAJOR] Fix "v2 stub commands" comment to "v2 pipeline commands" in `src/sio/cli/main.py`

### Remaining Fixes (MINOR — from adversarial audits)

- [x] T101 [MINOR] Add FK reference validation for `ground_truth.pattern_id` → `patterns.id` in `src/sio/core/db/queries.py` (application-level check in insert_ground_truth)
- [x] T102 [MINOR] Add `--candidates` flag to `ground-truth generate` CLI command in `src/sio/cli/main.py` per spec FR-GT-002
- [x] T103 [MINOR] Add `--count` flag to `ground-truth seed` CLI command in `src/sio/cli/main.py` per spec FR-GT-003
- [x] T104 [MINOR] Add `--surface` filter flag to `ground-truth seed` CLI command in `src/sio/cli/main.py` per spec FR-GT-003
- [x] T105 [MINOR] Persist `quality_assessment` field from `GroundTruthCandidate` signature to ground_truth table in `src/sio/ground_truth/generator.py`
- [x] T106 [MINOR] Ensure seeded `pattern_id` values in `ground-truth seed` correspond to real pattern rows or create stub patterns in `src/sio/ground_truth/seeder.py`
- [x] T107 [MINOR] Fix `_compute_satisfaction_rate()` to return `None` instead of `0.0` when no data available in `src/sio/core/dspy/optimizer.py`
- [x] T108 [MINOR] Fix `_apply_recency_weighting()` to not mutate input list — create a copy before modifying in `src/sio/core/dspy/optimizer.py`
- [x] T109 [MINOR] Add row_factory validation in query helper functions to guard against schema drift in `src/sio/core/db/queries.py`
- [x] T110 [MINOR] Add warning for unrecognized TOML keys in config loader in `src/sio/core/config.py`
- [x] T111 [MINOR] Add shape safety (flatten) for `query_emb` in cosine similarity calculation in `src/sio/core/dspy/corpus_indexer.py`
- [x] T112 [MINOR] Add similarity threshold filtering to `search_embedding()` to prevent returning noise results in `src/sio/core/dspy/corpus_indexer.py`
- [x] T113 [MINOR] Reduce excessive per-operation commits — batch commits where possible in `src/sio/core/db/queries.py`
- [x] T114 [MINOR] Add startup diagnostic message when LLM is disabled by config default (no API key found) in `src/sio/core/dspy/lm_factory.py`

### Tests for Phase 11

- [x] T115 [P] Write test for FK validation on `ground_truth.pattern_id` in `tests/unit/test_phase11_minor_fixes.py`
- [x] T116 [P] Write test for `--candidates`, `--count`, `--surface` CLI flags in `tests/unit/test_phase11_minor_fixes.py`
- [x] T117 [P] Write test for `quality_assessment` persistence in `tests/unit/test_phase11_minor_fixes.py`
- [x] T118 [P] Write test for `_compute_satisfaction_rate()` returning None when no data in `tests/unit/test_phase11_minor_fixes.py`
- [x] T119 [P] Write test for `_apply_recency_weighting()` not mutating input in `tests/unit/test_phase11_minor_fixes.py`
- [x] T120 [P] Write test for embedding shape safety and threshold filtering in `tests/unit/test_phase11_minor_fixes.py`
- [x] T121 [P] Write test for config loader warning on unrecognized keys in `tests/unit/test_phase11_minor_fixes.py`
- [x] T122 Run `ruff check src/ tests/` and fix all lint issues from Phase 11 changes
- [x] T123 Run full test suite `pytest tests/ -v` — all tests pass after Phase 11

**Checkpoint**: Phase 11 MINOR fixes complete

---

## Phase 12: Adversarial Audit Round 2

**Purpose**: Fix ALL findings from second adversarial audit on Phase 11 code. Three audits ran: placeholder exterminator, logic bug hunter, spec compliance reviewer.

### CRITICAL + MAJOR Fixes

- [x] T124 [CRITICAL] Collapse `--n-candidates`/`--candidates` into single `--candidates` flag (default=3 per contract) on `ground-truth generate` in `src/sio/cli/main.py` — remove fragile precedence logic
- [x] T125 [MAJOR] Add optional `PATTERN_ID` positional argument to `ground-truth generate` per cli-commands.md contract in `src/sio/cli/main.py`
- [x] T126 [MAJOR] Enforce FK on `ground_truth.pattern_id` — change warn-and-continue to raise `ValueError` when pattern missing (with `strict=True` default) in `src/sio/core/db/queries.py`
- [x] T127 [MAJOR] Fix batch commit tests to use `_CommitSpy` wrapper to verify commit IS/IS NOT called based on `_batch` flag in `tests/unit/test_phase11_minor_fixes.py`
- [x] T128 [MAJOR] Remove dead `_row_to_dict` empty-dict guard (unreachable with sqlite3.Row) in `src/sio/core/db/queries.py`
- [x] T129 [MAJOR] Add `--surface` filter flag to `ground-truth review` CLI command in `src/sio/cli/main.py` — wired to `get_pending_ground_truth(surface_type=)`

### MEDIUM Fixes

- [x] T130 [MEDIUM] Fix `_normalize_surface` substring matching — use `difflib.get_close_matches()` for deterministic fuzzy matching in `src/sio/ground_truth/generator.py`
- [x] T131 [MEDIUM] Replace shallow `dict(e)` with `copy.deepcopy(e)` in `_apply_recency_weighting()` in `src/sio/core/dspy/optimizer.py`
- [x] T132 [MEDIUM] Log warning when `quality_assessment` getattr fallback triggers in `src/sio/ground_truth/generator.py` — set `quality_assessment="FALLBACK: field missing from DSPy output"`
- [x] T133 [MEDIUM] Log warning when ANY getattr fallback triggers for DSPy output fields in `src/sio/ground_truth/generator.py`

### MINOR Fixes

- [x] T134 [MINOR] Wire `min_sim` threshold as `min_similarity` parameter in `search_embedding()` in `src/sio/core/dspy/corpus_indexer.py`
- [x] T135 [MINOR] Fix seed message — conditional text when `--surface` is provided in `src/sio/cli/main.py`
- [x] T136 [MINOR] Add `quality_assessment TEXT` column to `_GROUND_TRUTH_DDL` in `src/sio/core/db/schema.py`
- [x] T137 [MINOR] Move inline `import logging` in `insert_ground_truth()` to module-level in `src/sio/core/db/queries.py`
- [x] T138 [MINOR] Add user-facing deprecation notice to legacy `optimize` CLI command in `src/sio/cli/main.py`
- [x] T139 [MINOR] Standardize exit pattern — `raise SystemExit(1)` used consistently across all CLI error paths in `src/sio/cli/main.py`
- [x] T140 [MINOR] Add deprecation timeline comment `# DEPRECATED: Remove in v0.3` to `_run_dspy_optimization`, `optimize()`, `run_optimization()` in `src/sio/core/dspy/optimizer.py`
- [x] T141 [MINOR] Stub patterns use `"Stub pattern for seed: {pattern_id}"` description as marker in `src/sio/ground_truth/seeder.py`

### Tests for Phase 12

- [x] T142 [P] Write test for single `--candidates` flag (default=3) and `PATTERN_ID` positional arg in `tests/unit/test_phase12_audit2.py`
- [x] T143 [P] Write test for strict FK enforcement — `ValueError` raised on missing pattern_id in `tests/unit/test_phase12_audit2.py`
- [x] T144 [P] Write test for `_normalize_surface` deterministic matching with `difflib` in `tests/unit/test_phase12_audit2.py`
- [x] T145 [P] Write test for deep copy in `_apply_recency_weighting` with nested dicts in `tests/unit/test_phase12_audit2.py`
- [x] T146 [P] Write test for DSPy fallback logging and `quality_assessment="FALLBACK"` in `tests/unit/test_phase12_audit2.py`
- [x] T147 [P] Write test for `--surface` on `ground-truth review` CLI in `tests/unit/test_phase12_audit2.py`
- [x] T148 Run `ruff check src/ tests/` — clean (only pre-existing E501 remain)
- [x] T149 Run full test suite `pytest tests/ -v` — 1039 passed, 1 deselected (pre-existing)

**Checkpoint**: All Phase 12 adversarial findings resolved, full test suite green

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1)**: Depends on Phase 2
- **Phase 4 (US2)**: Depends on Phase 2 — can run in parallel with Phase 3
- **Phase 5 (US6)**: Depends on Phase 2 — can run in parallel with Phases 3-4
- **Phase 6 (US3)**: Depends on Phase 2 — can run in parallel with Phases 3-5
- **Phase 7 (US4)**: Depends on Phases 5 (ground truth) + 6 (metric)
- **Phase 8 (US7)**: Depends on Phase 3 (suggestions working)
- **Phase 9 (US5)**: Depends on Phases 3, 5, 6, 7 (full pipeline working)
- **Phase 10 (Polish)**: Depends on all user stories
- **Phase 11 (Adversarial Audit)**: Depends on Phase 10 — fixes from 3 independent audits
- **Phase 12 (Adversarial Audit R2)**: Depends on Phase 11 — second round audit on Phase 11 code

### User Story Dependencies

```
Phase 2 (Foundation) ──┬──► Phase 3 (US1: Suggestions) ──► Phase 8 (US7: Auto/HITL)
                       │                                          │
                       ├──► Phase 4 (US2: Config)                 │
                       │                                          ▼
                       ├──► Phase 5 (US6: Ground Truth) ──┐  Phase 9 (US5: Self-Test)
                       │                                  │       ▲
                       └──► Phase 6 (US3: Metric) ────────┴──► Phase 7 (US4: Optimizer)
```

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (Constitution Principle IV)
2. DSPy primitives (signatures, modules) before generation logic
3. Generation logic before CLI integration
4. Unit tests before integration tests

### Parallel Opportunities

**Phase 2** (6 tests in parallel, then 10 implementation tasks with [P] parallelism):
```
T004, T005, T006, T007, T008, T009 — all test tasks in parallel
T011, T014, T015, T016, T017, T018 — independent file creation in parallel
```

**Phases 3-6** (can run in parallel after Phase 2):
```
US1 (Phase 3) ║ US2 (Phase 4) ║ US6 (Phase 5) ║ US3 (Phase 6)
```

**Phase 10** (stub replacements in parallel):
```
T077 (RLM) ║ T078 (corpus embeddings) ║ T079 (RLM test) ║ T080 (embedding test)
```

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Wave 1: All tests in parallel
Task: "Write test for LLM config parsing in tests/unit/test_config_llm.py"
Task: "Write test for LM factory in tests/unit/test_lm_factory.py"
Task: "Write test for ground_truth table DDL in tests/unit/test_schema.py"
Task: "Write test for DSPy Signature definitions in tests/unit/test_dspy_signatures.py"
Task: "Write test for DSPy Module in tests/unit/test_dspy_modules.py"
Task: "Write test for module store in tests/unit/test_module_store.py"

# Wave 2: Independent implementations in parallel
Task: "Create LM factory in src/sio/core/dspy/lm_factory.py"
Task: "Define SuggestionGenerator Signature in src/sio/core/dspy/signatures.py"
Task: "Define GroundTruthCandidate Signature in src/sio/core/dspy/signatures.py"
Task: "Implement SuggestionModule in src/sio/core/dspy/modules.py"
Task: "Implement GroundTruthModule in src/sio/core/dspy/modules.py"
Task: "Implement module store in src/sio/core/dspy/module_store.py"

# Wave 3: DB + config (may touch same files)
Task: "Extend SIOConfig with LLM fields in src/sio/core/config.py"
Task: "Add ground_truth table to schema.py"
Task: "Add ground truth CRUD to queries.py"
```

---

## Parallel Example: Phases 3-6 (User Stories)

```bash
# After Phase 2 checkpoint, launch all P1 + P2 stories simultaneously:

# Agent A: US1 (Suggestions)
Task: "Write test for DSPy generator in tests/unit/test_dspy_generator.py"
Task: "Implement generate_dspy_suggestion() in src/sio/suggestions/dspy_generator.py"

# Agent B: US6 (Ground Truth)
Task: "Write test for ground truth generator in tests/unit/test_ground_truth_gen.py"
Task: "Implement generate_candidates() in src/sio/ground_truth/generator.py"

# Agent C: US3 (Metric)
Task: "Write test for metric function in tests/unit/test_dspy_metrics.py"
Task: "Implement suggestion_quality_metric() in src/sio/core/dspy/metrics.py"

# Agent D: US2 (Config)
Task: "Write test for config template in tests/unit/test_installer.py"
Task: "Add config.toml template to installer"
```

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3 = US1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T019)
3. Complete Phase 3: US1 — LLM-Generated Rules (T020-T030)
4. **STOP and VALIDATE**: `sio suggest` with LLM generates specific rules
5. This alone replaces the string template generator with real DSPy

### Incremental Delivery

1. Setup + Foundational → DSPy primitives ready
2. US1 → Real LLM suggestions (MVP!)
3. US2 → Multi-provider config (broadens user base)
4. US6 → Ground truth pipeline (enables optimization)
5. US3 → Quality metric (enables scoring + optimizer)
6. US4 → Optimizer integration (closes the self-improvement loop!)
7. US7 → Auto/HITL modes (user control)
8. US5 → Self-test (validation)
9. Polish → Remaining stubs (RLM, embeddings)

### Critical Path

```
Setup → Foundation → US1 (MVP) → US6 (Ground Truth) → US4 (Optimizer) = Self-Improving Loop
```

This path delivers the core value: a system that generates suggestions AND improves its own suggestion generation over time.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Constitution Principle IV requires TDD — all test tasks MUST be completed and FAIL before implementation
- Constitution Principle XI — NO stubs in production code; all DSPy functions must call real DSPy
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Total: 149 tasks across 12 phases (85 original + 38 audit R1 + 26 audit R2)
- P1 stories: 30 tasks (US1: 11, US2: 5, US6: 12) + 2 setup = 32 tasks for MVP-complete
- Phase 11: 15 already fixed [x] + 14 MINOR fixes [x] + 9 tests [x] = 38 tasks
- Phase 12: 6 CRITICAL/MAJOR + 4 MEDIUM + 8 MINOR + 8 tests = 26 tasks
