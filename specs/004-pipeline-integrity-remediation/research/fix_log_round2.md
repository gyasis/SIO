# Fix Log — Round 2 Deep Fix Pack

**Branch:** `004-pipeline-integrity-remediation`
**Session:** 2026-04-21
**Commits:** `da33196`, `90a0158`

## Summary

Round 2 applied 13 audit defects (7 CRITICAL + 6 HIGH) as a stashed fix pack,
then resolved 29 test failures introduced by those fixes. All tests now pass.

---

## Production Fixes Applied (13 audit defects)

### CRITICAL (7)

| ID | Module | Fix |
|----|--------|-----|
| C-R2.6 | `suggestions/dspy_generator.py` | `_load_optimized_or_default()` returns `SuggestionGenerator` (PatternToRule, 3-input) instead of deprecated `SuggestionModule` (4-input) |
| C-R2.7 | `clustering/pattern_clusterer.py` | `_load_stored_centroids()` returns `tuple[dict, dict]` with collision-protected `desc_cache` (excludes descriptions mapping to multiple patterns) |
| C-R2.8 | `core/dspy/persistence.py` | `load_compiled()` raises `ArtifactStructureMismatch` on zero-predictor-key overlap instead of silently returning default module |
| C-R2.9 | `suggestions/dspy_generator.py` | `generate_dspy_suggestion()` calls `module.forward(pattern_description=..., example_errors=..., project_context=...)` matching PatternToRule contract |
| C-R2.10 | `suggestions/dspy_generator.py` | Output extraction supports both old (`prevention_instructions`, `rationale`) and new (`rule_body`, `rule_rationale`) field names via `getattr()` dual-fallback |
| C-R2.11 | `core/dspy/metrics.py` | Neutral floor for empty `details` is `0.5` (reverted accidental change to `0.0` labeled as N-R2D.10 — that defect did not exist in the 13-defect audit) |
| C-R2.12 | `core/ground_truth/corpus.py` | `load_training_corpus()` emits PatternToRule fields: `pattern_description`, `example_errors`, `project_context`, `rule_title`, `rule_body`, `rule_rationale` |

### HIGH (6)

| ID | Module | Fix |
|----|--------|-----|
| H-R2.1 | `core/dspy/persistence.py` | `save_compiled()` uses SQLite SAVEPOINT/RELEASE/ROLLBACK TO for atomicity |
| H-R2.2 | `core/dspy/persistence.py` | `MODULE_REGISTRY` dict maps `"suggestion_generator"` to `SuggestionGenerator` class |
| H-R2.3 | `core/dspy/optimizer.py` | `_load_optimized_or_default()` exported and returns `SuggestionGenerator` default |
| H-R2.4 | `suggestions/dspy_generator.py` | Verbose logging updated to use `pattern_description` label |
| H-R2.5 | `suggestions/dspy_generator.py` | `quality_example` and `quality_pred` namespaces include both old and new field names |
| H-R2.7 | `clustering/pattern_clusterer.py` | Encode-skip optimization restored using collision-safe `desc_cache` from `_load_stored_centroids()` |

---

## Test Failures Resolved (29 total)

### Group 1 — dspy_generator tests (11 failures)
**Root cause:** `generate_dspy_suggestion()` still called old 4-input args after C-R2.6 changed `_load_optimized_or_default()` to return 3-input `SuggestionGenerator`.

**Fixes:**
- Updated `generate_dspy_suggestion()` to call `module.forward(pattern_description=..., example_errors=..., project_context=...)`
- Updated all `test_dspy_generator.py` tests to patch `_load_optimized_or_default` instead of `SuggestionModule`
- Updated mock predictions to include both old (`prevention_instructions`, `rationale`) and new (`rule_body`, `rule_rationale`) fields

### Group 2 — metrics neutral floor (1 failure)
**Root cause:** Accidental `return 0.0` in `_score_specificity()` labeled as "N-R2D.10" — that defect ID was not in the 13-defect audit list.

**Fix:** Reverted to `return 0.5  # neutral when we can't extract details`

### Group 3 — ground truth corpus fields (1 failure)
**Root cause:** `test_with_inputs_set_correctly` checked old 4-input fields (`error_examples`, `error_type`, `pattern_summary`, `tool_input_context`).

**Fix:** Updated test assertions to check PatternToRule fields (`pattern_description`, `example_errors`, `project_context`, `rule_body`, `rule_rationale`)

### Group 4 — save_load tests (2 failures)
**Root cause:** Tests saved `_TinyModule` (predictor key `pred`) but loaded as `suggestion_generator` (predictor key `generate.predict`) — our N-R2D.4 zero-overlap check correctly detected the mismatch.

**Fix:** Added `_make_suggestion_generator()` helper; tests now save a real `SuggestionGenerator` so round-trip works

### Group 5 — centroid encode-skip test (1 failure)
**Root cause:** H-R2.7 initial fix completely removed `desc_cache`, also removing the encode-skip optimization that `test_recluster_same_members_skips_embed` was testing.

**Fix:** Restored collision-safe `desc_cache` — only exclude descriptions mapping to multiple pattern IDs; single-mapping descriptions still enable the encode-skip

### Group 6 — test_optimizer fallback type (1 failure)
**Root cause:** `test_falls_back_to_default_when_no_db` asserted `isinstance(result, SuggestionModule)` but C-R2.6 changed the default to `SuggestionGenerator`.

**Fix:** Updated assertion to `isinstance(result, SuggestionGenerator)`

### Group 7 — contract test stale assertions (1 failure, pre-existing)
**Root cause:** `test_status_with_db` checked `"Errors mined"` and `"Patterns found"` but the status command renders metric keys (`error_records`, `gold_standards`) in Rich tables. This failure existed at the `3d5c7d9` checkpoint before any Round 2 changes.

**Fix:** Updated assertions to check actual rendered content (`error_records` or `Mining`, `gold_standards` or `Training`)

---

## Final State

```
tests/ — all pass (0 failures)
Skipped: 8 (LLM-gated, schema-gated, or documented wave-6 compromises)
```

**Files modified:**
- `src/sio/suggestions/dspy_generator.py`
- `src/sio/clustering/pattern_clusterer.py`
- `src/sio/core/dspy/metrics.py`
- `src/sio/core/dspy/persistence.py`
- `src/sio/core/ground_truth/corpus.py`
- `tests/unit/test_dspy_generator.py`
- `tests/unit/test_ground_truth_corpus.py`
- `tests/unit/test_optimizer.py`
- `tests/unit/dspy/test_save_load.py`
- `tests/contract/test_v2_cli_commands.py`
