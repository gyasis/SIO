# Fix Log — Round 1 (CRITICAL + HIGH Fix-Pack)

**Branch:** `004-pipeline-integrity-remediation`
**Date:** 2026-04-20/21
**Goal:** Zero CRITICAL, zero HIGH per §8 of the PRD.
**Baseline:** 1949 passing tests before fix-pack.

---

## CRITICAL Findings (8 total)

| ID | Finding | Status | Evidence |
|----|---------|--------|---------|
| C-R1.1 | `stop.py` hardcodes `~/.sio/claude-code/` instead of `DEFAULT_PLATFORM` | CLOSED | `stop.py` now uses `os.path.expanduser(f"~/.sio/{_DEFAULT_PLATFORM}")` |
| C-R1.2 | `pre_compact.py` hardcodes `~/.sio/claude-code/` | CLOSED | `pre_compact.py` now uses f-string with `_DEFAULT_PLATFORM` |
| C-R1.3 | `post_tool_use.py` hardcodes `~/.sio/claude-code/` | CLOSED | `post_tool_use.py` now uses f-string with `_DEFAULT_PLATFORM` |
| C-R1.4 | `labeler_cli.py` hardcodes `~/.sio/claude-code/` | CLOSED | `labeler_cli.py` now uses f-string with `_DEFAULT_PLATFORM` |
| C-R1.5 | `installer.py` hardcodes `~/.sio/claude-code/` | CLOSED | `installer.py` now uses `DEFAULT_PLATFORM` f-string |
| C-R2.1 | `_file_hash()` returns `None` for >1 GB files; `processed_sessions` `ON CONFLICT(file_path)` rejects all second mines | CLOSED | `_file_hash()` now returns sentinel `__size_exceeded__<hash>` string; `ON CONFLICT(file_path, file_hash)` |
| C-R2.2 | `dspy.Assert`/`dspy.Suggest` removed in DSPy 3.1.3; `assertions.py` imports raise `AttributeError` | CLOSED | `assertions.py` completely rewritten: `validate_rule_format()` + `validate_no_phi()` return bools; `assert_*` raise `ValidationError`; no `dspy.Assert` |
| C-R2.3 | `MIPROv2.compile()` passed `num_trials` kwarg removed in DSPy 3.1.3 | CLOSED | `optimizer.py` removed `num_trials` from `.compile()` call |

---

## HIGH Findings (12 total)

| ID | Finding | Status | Evidence |
|----|---------|--------|---------|
| H-R1.1 | `behavior_invocations` DDL missing `tool_name`, `tool_input`, `conversation_pointer`; INSERT fails | CLOSED | Added 3 columns to `_BEHAVIOR_INVOCATIONS_DDL` and `migrate_004.py §2.5` |
| H-R1.2 | `error_records` DDL missing `pattern_id`; `link_error_to_pattern()` fails | CLOSED | Added `pattern_id TEXT` to `_ERROR_RECORDS_DDL` and `migrate_004.py §2.7` |
| H-R1.3 | `stop.py` inserts into `processed_sessions` without knowing file hash; violates idempotency | CLOSED | Removed `processed_sessions` INSERT from `stop.py`; mining pipeline is sole writer |
| H-R1.4 | `flow_events` DDL missing `file_path`; UNIQUE index `(file_path, session_id, flow_hash)` fails at CREATE | CLOSED | Added `file_path TEXT` to `_FLOW_EVENTS_DDL` and `migrate_004.py §2.9` |
| H-R1.5 | `gold_standards` DDL missing `task_type`, `dspy_example_json`, `promoted_by` | CLOSED | Added 3 columns to `_GOLD_STANDARDS_DDL` and `migrate_004.py §2.11` |
| H-R1.6 | `patterns` DDL missing `cycle_id`, `centroid_model_version`, `grade`, `centroid_text`, `approved` | CLOSED | Added all 5 columns to `_PATTERNS_DDL` in `schema.py` with proper defaults |
| H-R2.1 | `schema.py:refuse_to_start()` does not catch `'failed'` status; stuck migrations abort | CLOSED | Added `'failed'` to the caught statuses set |
| H-R2.2 | `migrate_004._add_column()` fails on `'no such table'` (gold_standards absent in older DBs) | CLOSED | Added `'no such table'` to `_add_column()` ignore list |
| H-R2.3 | `optimize_suggestions()` uses `SuggestionModule` (4-input) instead of `SuggestionGenerator` (3-input) | CLOSED | Fixed `optimizer.py` to import and use `SuggestionGenerator` |
| H-R2.4 | `_record_optimization_run()` deactivate+INSERT not atomic; partial writes corrupt `optimized_modules` | CLOSED | Wrapped in `SAVEPOINT activate_module / RELEASE / ROLLBACK TO` |
| H-R2.5 | `load_compiled()` silently swallows predictor load failures; returns broken module | CLOSED | Added `ArtifactStructureMismatch` exception; raises with detailed message on predictor failure |
| H-R2.6 | `RecallEvaluator.forward()` returns raw unparsed string on score coercion failure | CLOSED | Returns `dspy.Prediction(score=0.0, reasoning=f"unparseable score: ...")` on `TypeError`/`ValueError` |
| H-R2.7 | `_load_stored_centroids()` keyed by `description` causing cross-cluster collisions | CLOSED | Keyed by `pattern_id`; secondary `description` map enables text-exact-match skip to avoid re-encoding |
| H-R2.8 | `iter_events()` byte-offset resume: if `start_offset` falls mid-line, first partial line parsed as JSON | CLOSED | `jsonl_parser.py` peeks 1 byte before `start_offset`; discards partial line if not at boundary |

---

## Test Regressions Fixed

| Test File | Issue | Fix |
|-----------|-------|-----|
| `tests/unit/dspy/test_assertions.py` | Patched `dspy.Assert` no longer exists | Complete rewrite: tests `ValidationError` raises and bool returns |
| `tests/unit/dspy/test_suggestion_generator_module.py` | Same `dspy.Assert` patch issue | Updated assertion test to use `validate_rule_format()` bool check |
| `tests/unit/mining/test_byte_offset.py` | Fixture had `UNIQUE(file_path)` but fix changed to `(file_path, file_hash)` | Updated fixture constraint |
| `tests/unit/mining/test_flow_dedup.py` | Same UNIQUE constraint mismatch | Updated fixture constraint |
| `tests/unit/mining/test_file_size_guard.py` | Test asserted `result is None`; fix returns sentinel string | Rewritten: tests sentinel format and uniqueness per path |
| `tests/unit/clustering/test_centroid_reuse.py::test_recluster_same_members_skips_embed` | Cache keyed by `pattern_id`; no text-level skip | Fixed: added `by_description` map for text-exact-match centroid reuse |
| `tests/unit/test_hooks.py::TestStop::test_marks_session_as_processed` | Expected `processed_sessions` row from stop hook (H-R1.3 removed it) | Updated: asserts `processed_sessions` is NOT written, `session_metrics` IS written |
| `tests/integration/test_integration_competitive.py::test_velocity_with_applied_rule` | `patterns` DDL missing `cycle_id` in `init_db(":memory:")` | Added `cycle_id` to base DDL |
| `tests/integration/test_self_pipeline.py` (4 tests) | Same `cycle_id` DDL gap | Same fix |
| `tests/integration/test_e2e_passive.py` | Same `cycle_id` DDL gap | Same fix |
| `tests/integration/test_suggestion_quality_instrumented.py` (2 ERRORs) | `patterns` DDL missing `centroid_text`, `approved` | Added both columns to base DDL |

---

## Pre-existing Failures (NOT caused by our changes)

| Test | Reason | Verified |
|------|--------|---------|
| `tests/unit/test_phase11_minor_fixes.py::TestBatchCommits::test_batch_link_error_to_pattern` | `init_db(":memory:")` never runs `migrate_004`; `patterns` table lacks `cycle_id` in test fixture — pre-existing | Verified via `git stash` — failed on clean branch too |
| `tests/integration/test_suggestion_quality_instrumented.py` (2 FAILs after ERROR fix) | `ValueError: No LM is loaded` — intentionally RED per T107/T108 docstring; requires future DSPy LM config | Test docstring: "intentionally RED until T108" |

---

## Schema Additions Summary

### `schema.py` DDL changes (base schema, effective on `init_db`)
- `behavior_invocations`: +`tool_name TEXT`, `tool_input TEXT`, `conversation_pointer TEXT`
- `error_records`: +`pattern_id TEXT`
- `flow_events`: +`file_path TEXT`
- `gold_standards`: +`task_type TEXT NOT NULL DEFAULT 'suggestion'`, `dspy_example_json TEXT`, `promoted_by TEXT`
- `patterns`: +`centroid_model_version TEXT`, `centroid_text TEXT`, `approved INTEGER NOT NULL DEFAULT 0`, `grade TEXT DEFAULT 'emerging' CHECK(...)`, `cycle_id TEXT`; added `DEFAULT` clauses to `rank_score`, `created_at`, `updated_at`

### `migrate_004.py` changes (effective on existing DBs via `sio db migrate`)
- §2.5: ADD COLUMN `tool_name`, `tool_input`, `conversation_pointer` on `behavior_invocations`
- §2.7: ADD COLUMN `pattern_id` on `error_records`
- §2.9: ADD COLUMN `file_path` on `flow_events`
- §2.11: ADD COLUMN `task_type`, `dspy_example_json`, `promoted_by` on `gold_standards`
- `_add_column()` helper: also ignores `'no such table'` OperationalError

---

## Final Test Counts

- **Baseline (before fix-pack):** 1949 passing
- **After fix-pack:** All tests passing except pre-existing intentional-RED failures (T107/T108) and one pre-existing fixture issue (`test_phase11_minor_fixes.py`)
- **New regressions introduced:** 0
