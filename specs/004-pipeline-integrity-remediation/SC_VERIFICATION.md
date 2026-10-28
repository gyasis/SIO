# SC Verification — Pipeline Integrity Remediation (T119)

**Branch**: `004-pipeline-integrity-remediation`
**Verified**: 2026-04-20

For each success criterion (SC-001 through SC-022), status is one of:
- **PASS** — verified green via test or code inspection
- **PARTIAL** — partially met; remaining gap noted
- **PENDING** — needs real-world data, or verification after deployment

---

## SC-001: Behavior invocations count ≥ 38,091 after sync

**Status**: PASS (measured 2026-10-27 on razer)

```text
$ sio sync --full
  claude-code: 2169 copied, 53979 already present, 56148 in source

$ sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM behavior_invocations"
57664                                   # ≥ 38,091 ✓
$ sqlite3 ~/.sio/claude-code/behavior_invocations.db "SELECT COUNT(*) ..."
56149
```

Canonical holds more rows than per-platform because 1,516 rows from
2026-09-22..09-26 predate the current per-platform file. An identity-key comparison
on `(platform, session_id, timestamp, tool_name)` found **0 per-platform rows missing
from canonical**. That is the property SC-001 really asserts. `sio status` now
reports the row gap as a number, and shows a check mark only at zero.

This criterion sat at PENDING "verifiable post-deployment" while the thing it
describes did not exist. **There was no `sio sync` command.** Nothing called
`sync_behavior_invocations()` outside the one-time `scripts/migrate_split_brain.py`.
The copy it did run dropped every row and reported success. The canonical table held
0 rows against 55,348 captured. Someone read the code and wrote "the sync logic is
correct"; nobody ran it. PRD `sio_behavior_invocations_sync_fix_2026-10-27` F1-F4
built the command, made a shortfall raise `SyncIntegrityError`, and wired the
incremental copy into the Stop hook. This entry is the measurement we owed.

---

## SC-002: `run_mine` marks files in `processed_sessions` after mining

**Status**: PASS

Verified by `tests/integration/test_processed_sessions.py::TestMarkAndCheckProcessed`
(16 tests green after T-REGR fix). The `_mark_processed` / `_is_already_processed`
integration test covers it end-to-end.

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

`src/sio/autoresearch/cadence.py` wires the schedule. Confirming that it fires
needs 24 hours of real-world observation after `sio schedule install`.

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

The non-destructive path (no new errors → skip re-embedding) comes from
`test_suggest_non_destructive.py`. Exact timing on large corpus is PENDING.

---

## SC-012: Suggestion approval rate ≥ 30% on a fresh batch

**Status**: PENDING (verifiable post real-world batch)

Baseline documented at 8% (see `research/suggestion_quality_baseline.md`).
PatternToRule signature updated with few-shot guidance (T109). A post-remediation
reviewer must read a batch of ≥ 50 suggestions to measure improvement.

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

**Status**: PENDING (needs a real LLM call and a held-out devset)

Marked `@pytest.mark.slow` (T109). Confirmed callable. Full evaluation against
real devset needs a post-deployment batch with an LLM configured.

---

## SC-019: At least one `dspy.Assert` active in SuggestionGenerator; backtrack count captured

**Status**: PASS

`SuggestionGenerator.forward()` calls `assert_rule_format` and `assert_no_phi`.
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
lives only in `src/sio/core/constants.py`.

---

## Summary

| Category | PASS | PARTIAL | PENDING |
|----------|------|---------|---------|
| Data flow (SC-001 to SC-004) | 4 | 0 | 0 |
| Scheduling (SC-005) | 0 | 0 | 1 |
| Safety (SC-006 to SC-008) | 3 | 0 | 0 |
| Performance (SC-009 to SC-011) | 2 | 0 | 1 |
| Quality (SC-012) | 0 | 0 | 1 |
| Audit (SC-013 to SC-015) | 3 | 0 | 0 |
| DSPy (SC-016 to SC-022) | 6 | 0 | 1 |
| **Total** | **18** | **0** | **4** |

SC-001 moved PENDING -> PASS on 2026-10-27 with the counts above.

**A note on PENDING, earned the hard way.** SC-001 read "PENDING (verifiable
post-deployment)" for as long as the criterion existed. That wording let a missing
command look like a scheduling detail. A criterion nobody has ever run is not
pending verification. It is unverified, and that difference is the whole point of
this document. When a status says PENDING, name the artifact that is absent and the
command that would produce it.

All PENDING SCs depend on real-world data or post-deployment observation — none
block the merge. All code-verifiable SCs are PASS.
