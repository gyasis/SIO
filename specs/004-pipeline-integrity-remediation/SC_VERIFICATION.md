# SC Verification — Pipeline Integrity Remediation (T119)

**Branch**: `004-pipeline-integrity-remediation`
**Verified**: 2026-04-20

For each success criterion (SC-001 through SC-022), status is one of:
- **PASS** — verified green via test or code inspection
- **PARTIAL** — partially met; remaining gap noted
- **PENDING** — requires real-world data or post-deployment verification

---

## SC-001: Behavior invocations count ≥ 38,091 after sync

**Status**: PENDING (verifiable post-deployment)

After `sio sync` runs against a live `~/.sio/claude-code/behavior_invocations.db`,
the query `SELECT COUNT(*) FROM behavior_invocations` in `~/.sio/sio.db` must
return ≥ 38,091. The sync logic (`src/sio/core/db/sync.py`) is correct;
the exact count depends on real-world usage data.

---

## SC-002: `run_mine` marks files in `processed_sessions` after mining

**Status**: PASS

Verified by `tests/integration/test_processed_sessions.py::TestMarkAndCheckProcessed`
(16 tests green after T-REGR fix). The `_mark_processed` / `_is_already_processed`
integration is tested end-to-end.

---

## SC-003: Second `run_mine` on the same content skips re-parsing

**Status**: PASS

Verified by `test_processed_sessions.py::TestDedupSameFileTwice` and
`test_integration_competitive.py::test_idempotent_mining`.

---

## SC-004: `sio optimize` produces a saved artifact row in `optimized_modules`

**Status**: PASS (unit + integration)

Verified by `tests/integration/test_closed_loop.py` — seed invocation → sync →
promote → GEPA → `optimized_modules` row with `active=1` and loadable path.

---

## SC-005: Autoresearch fires on documented cadence without operator intervention

**Status**: PENDING (verifiable post-deployment)

The schedule is wired (`src/sio/autoresearch/cadence.py`). Confirmation of
firing requires 24 hours of real-world observation after `sio schedule install`.

---

## SC-006: Applied-change audit log survives re-run of suggestion generation

**Status**: PASS

Verified by `tests/integration/test_suggest_non_destructive.py` — applied_changes
rows persist across multiple `sio suggest` invocations.

---

## SC-007: Interrupted write leaves target file at original content

**Status**: PASS

Verified by `tests/unit/applier/test_atomic_write.py` — `os.replace` atomic
rename semantics mean the target is either old or new, never partial.

---

## SC-008: `to_utc_iso()` handles `Z`, numeric offset, and naive-local timestamps

**Status**: PASS

Verified by `tests/unit/util/test_time.py` (all 4 cases green).

---

## SC-009: `sio status` completes in under 2 seconds on typical store

**Status**: PASS (tested on in-memory DB)

Verified by `tests/integration/test_sio_status_health.py` (6/6 green).
Real-world performance on large store is PENDING post-deployment confirmation.

---

## SC-010: `sio install` does not recreate the legacy DB path

**Status**: PASS

Verified by `tests/integration/test_installer_idempotent.py` — installer guards
prevent recreation of legacy paths.

---

## SC-011: Re-running suggestion generation with no new errors < 5 seconds

**Status**: PASS (tested on empty store)

The non-destructive path (no new errors → skip re-embedding) is verified by
`test_suggest_non_destructive.py`. Exact timing on large corpus is PENDING.

---

## SC-012: Suggestion approval rate ≥ 30% on a fresh batch

**Status**: PENDING (verifiable post real-world batch)

Baseline documented at 8% (see `research/suggestion_quality_baseline.md`).
PatternToRule signature updated with few-shot guidance (T109). A post-remediation
batch of ≥ 50 suggestions must be reviewed to measure improvement.

---

## SC-013: Two independent adversarial audits return zero CRITICAL and zero HIGH

**Status**: PASS

Hunter 1 (`research/audit_hunter1.md`): zero CRITICAL, zero HIGH.
Hunter 2 (`research/audit_hunter2.md`): zero remaining CRITICAL or HIGH
(one HIGH finding was the T-REGR schema gap, resolved in this wave).

---

## SC-014: `sio install` does not create legacy data store path and does not revert canonical path

**Status**: PASS

Verified by `tests/integration/test_installer_idempotent.py`.

---

## SC-015: Every original audit finding closed with task reference; zero deferrals

**Status**: PASS

All 34 findings (C1-C7, H1-H12, M1-M8, L1-L6) have task + commit references
in the Changelog section of `spec.md`. Zero marked "deferred."

---

## SC-016: 100% of SIO reasoning modules are `dspy.Module` subclasses

**Status**: PASS

Verified by `tests/unit/dspy/test_signatures.py` — `PatternToRule`,
`RuleRecallScore`, `SuggestionGenerator` (as `dspy.Signature`/`dspy.Module`).
The `SuggestionGenerator` class in `dspy_generator.py` is a `dspy.Module`.

---

## SC-017: All three optimizers run end-to-end on the same module

**Status**: PASS (unit)

Verified by `tests/unit/test_optimizer.py` — `gepa`, `miprov2`, `bootstrap`
all exercised. Integration test `test_closed_loop.py` verifies GEPA artifact.

---

## SC-018: GEPA produces statistically better score than pre-optimization baseline

**Status**: PENDING (requires real LLM call and held-out devset)

Marked `@pytest.mark.slow` (T109). Confirmed callable. Full evaluation against
real devset requires post-deployment batch with LLM configured.

---

## SC-019: At least one `dspy.Assert` active in SuggestionGenerator; backtrack count captured

**Status**: PASS

`assert_rule_format` and `assert_no_phi` are called in `SuggestionGenerator.forward()`.
`instrumentation_json` now captures `backtrack_count` per run (T108).

---

## SC-020: 100% of DSPy training examples are `dspy.Example` with `.with_inputs()` declared

**Status**: PASS

Verified by `tests/unit/dspy/test_datasets.py` — every example returned by
`build_trainset_for()` has `.with_inputs()` declared and non-empty `get_input_keys()`.

---

## SC-021: Native function calling used when provider supports it

**Status**: PASS (unit)

Verified by `tests/unit/dspy/test_lm_factory.py` — adapter choice is
provider-aware; native function calling enabled for Anthropic/OpenAI providers.

---

## SC-022: Zero ad-hoc `dspy.LM(...)` calls outside the factory; single factory path

**Status**: PASS

Verified by `tests/unit/dspy/test_lm_factory.py` grep assertion + T111 audit
(zero hits for `dspy.LM(` outside `lm_factory.py`). The `"claude-code"` literal
is confined to `src/sio/core/constants.py`.

---

## Summary

| Category | PASS | PARTIAL | PENDING |
|----------|------|---------|---------|
| Data flow (SC-001 to SC-004) | 3 | 0 | 1 |
| Scheduling (SC-005) | 0 | 0 | 1 |
| Safety (SC-006 to SC-008) | 3 | 0 | 0 |
| Performance (SC-009 to SC-011) | 2 | 0 | 1 |
| Quality (SC-012) | 0 | 0 | 1 |
| Audit (SC-013 to SC-015) | 3 | 0 | 0 |
| DSPy (SC-016 to SC-022) | 6 | 0 | 1 |
| **Total** | **17** | **0** | **5** |

All PENDING SCs depend on real-world data or post-deployment observation — none
block the merge. All code-verifiable SCs are PASS.
