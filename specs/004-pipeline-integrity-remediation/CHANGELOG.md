# CHANGELOG — SIO Pipeline Integrity Remediation (004)

**Branch**: `004-pipeline-integrity-remediation`
**Start**: 2026-04-20 | **Close**: 2026-04-20
**Tasks**: 120 total (93 prior + 18 Wave 11/12 + T-REGR)

---

## Breaking Changes

None. All schema additions are additive (new columns with `DEFAULT` values or
nullable). Existing rows are not migrated or removed. The canonical DB path
moves from `~/.sio/claude-code/behavior_invocations.db` to
`~/.sio/<platform>/behavior_invocations.db` — the old path is left in place
(not deleted) for backward compatibility. `sio install` will not recreate it.

---

## Deployment Notes

1. **Run the migration once** after checkout:
   ```bash
   python scripts/migrate_004.py
   ```
   This is idempotent. The script wraps all DDL in a `schema_version` transaction
   row (`status='applying'` → `'applied'`). Safe to re-run.

2. **Backfill legacy behavior data**:
   ```bash
   python scripts/migrate_split_brain.py
   ```
   Copies all rows from `~/.sio/claude-code/behavior_invocations.db` to
   `~/.sio/sio.db` using `INSERT OR IGNORE`. Safe to re-run (idempotent).

3. **Install the schedule** (optional, for passive daily runs):
   ```bash
   sio schedule install
   ```

4. **Verify the pipeline** is wired end-to-end:
   ```bash
   sio status
   ```

---

## Per-Wave Commit List

| Wave | Commit | Key Tasks | Files Changed |
|------|--------|-----------|---------------|
| Wave 1 | 60d99c2 | T001-T005: toolchain, skeleton, deps, conftest | `pyproject.toml`, `conftest.py`, `tests/unit/*/` |
| Wave 2 | fa9f3fd | T006-T020: time utils, db connect, schema_version, atomic write, allowlist, heartbeat | `src/sio/core/util/time.py`, `src/sio/core/db/`, `src/sio/core/applier/writer.py`, `src/sio/adapters/claude_code/hooks/_heartbeat.py` |
| Wave 3 | 5c3d710 | T021-T036: LM factory, DSPy signatures/metrics/assertions/datasets/persistence, sync, migration | `src/sio/core/dspy/`, `src/sio/core/db/sync.py`, `scripts/migrate_004.py` |
| Wave 4 | 39491ff | T037-T044: installer idempotent, gold_standards DB path, promote, closed loop GEPA | `src/sio/adapters/claude_code/installer.py`, `src/sio/core/arena/gold_standards.py`, `src/sio/core/dspy/optimizer.py` |
| Wave 5 | c693998 | T045-T056: US2 non-destructive suggest, rollback, US3 safety tests | `src/sio/suggestions/dspy_generator.py`, `src/sio/core/applier/writer.py` |
| Wave 7 | 163495b | T057-T068: DSPy module rewrites, all 3 optimizers, SuggestionGenerator v2 | `src/sio/core/dspy/optimizer.py`, `src/sio/suggestions/dspy_generator.py` |
| Wave 8 | c448423 | T069-T077: US4 autoresearch scheduling, US5 streaming mine | `src/sio/autoresearch/`, `src/sio/mining/pipeline.py` |
| Wave 9 | 1044ed1 | T078-T087: SuggestionGenerator v3, US5 refactor, US6 status | `src/sio/suggestions/dspy_generator.py`, `src/sio/cli/status.py` |
| Wave 11 | 7562495 | T088, T104: centroid BLOB reuse, declining-grade grader | `src/sio/clustering/grader.py` |
| Wave 12 | (current) | T-REGR, T089, T094, T105-T120: regression fix, size guard, within-type dedup, ranker guard, instrumentation, audit docs, PRD changelog, coverage, ruff, README, SC verification | `src/sio/core/db/schema.py`, `src/sio/mining/pipeline.py`, `src/sio/clustering/ranker.py`, `src/sio/suggestions/dspy_generator.py`, `src/sio/core/dspy/signatures.py`, `src/sio/cli/main.py` |

---

## Per-Task File:Line Citations (Wave 12 tasks)

| Task | File | Key Line(s) | Description |
|------|------|-------------|-------------|
| T-REGR | `src/sio/core/db/schema.py` | L219-234 | Added `is_subagent`, `parent_session_id`, `last_offset`, `last_mtime` to `_PROCESSED_SESSIONS_DDL` |
| T089 | `src/sio/mining/pipeline.py` | L139-165 | `_ONE_GB` constant + `_file_hash` 1 GB guard with WARNING log |
| T089 | `tests/unit/mining/test_file_size_guard.py` | L1-60 | 2 tests: `test_large_file_returns_none`, `test_normal_file_returns_hash` |
| T094 | `src/sio/cli/main.py` | L2168-2171 | Confirmed: `hook_health_rows` imported from `sio.cli.status` — no inline duplication |
| T105 | `src/sio/mining/pipeline.py` | L89-106 | Group key changed to `(session_id, user_message, error_type)` for within-type dedup |
| T105 | `tests/unit/mining/test_within_type_dedup.py` | L1-88 | 3 tests: same-type dedup, cross-type preservation, ungrouped always kept |
| T106 | `src/sio/clustering/ranker.py` | L74-82 | `try/except (ValueError, TypeError)` guard around `fromisoformat` with UTC-now fallback |
| T107 | `tests/integration/test_suggestion_quality_instrumented.py` | L1-94 | 2 tests (marked `@pytest.mark.slow`): instrumentation_json + rejection_reasons |
| T108 | `src/sio/suggestions/dspy_generator.py` | L903-980 | `SuggestionGenerator.forward()` wired with per-run instrumentation dict + `pred.instrumentation_json` |
| T109 | `src/sio/core/dspy/signatures.py` | L8-46 | `PatternToRule` docstring updated with 2 few-shot gold examples for GEPA optimization |
| T110 | `specs/004-pipeline-integrity-remediation/research/suggestion_quality_baseline.md` | — | Baseline 8%, target 30%, methodology SQL, status |
| T111 | `specs/004-pipeline-integrity-remediation/research/audit_hunter1.md` | — | Targeted scan: zero CRITICAL/HIGH |
| T112 | `specs/004-pipeline-integrity-remediation/research/audit_hunter2.md` | — | General scan: all findings resolved |
| T113 | `specs/004-pipeline-integrity-remediation/follow_up_findings.md` | — | Consolidated: zero CRITICAL, zero HIGH remaining |
| T114 | `specs/004-pipeline-integrity-remediation/spec.md` | L336-end | PRD Changelog section appended |
| T115 | `specs/004-pipeline-integrity-remediation/research/coverage_summary.md` | — | Coverage summary (13% overall, new modules well-covered) |
| T116 | `src/sio/cli/main.py` | L2418 | E501 fix; `ruff format` applied project-wide |
| T117 | `README.md` | L24-60 | Pipeline diagram updated with per-platform DB → sync → sio.db → DSPy path |
| T118 | `~/.claude/rules/tools/sio.md` | L4-30 | DB paths updated; optimizer table added |
| T119 | `specs/004-pipeline-integrity-remediation/SC_VERIFICATION.md` | — | 17 PASS, 5 PENDING (all PENDING require post-deployment data) |
| T120 | `specs/004-pipeline-integrity-remediation/CHANGELOG.md` | — | This file |
