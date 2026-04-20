# Implementation Plan: SIO Pipeline Integrity & Training-Data Remediation

**Branch**: `004-pipeline-integrity-remediation` | **Date**: 2026-04-20 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-pipeline-integrity-remediation/spec.md`

## Summary

Restore the end-to-end data flow from agent-tool-use capture → labeled examples → DSPy-powered optimization → suggestion generation → safe application → audit trail, close all 34 adversarial-audit findings (zero deferrals per PRD §6.1), and make DSPy a first-class, idiomatic dependency across every SIO reasoning module (FR-035 → FR-041). Technical approach: (1) honor Constitution Principle V by keeping per-platform `behavior_invocations.db` as the write target and introducing a sync/attach mirror so readers see a unified view at `~/.sio/sio.db` — resolves PRD §7 Open Q1 without violating Principle V; (2) rewrite the suggestion generator, recall evaluator, and optimizer path as proper `dspy.Module`s with class-based `Signature`s, three selectable teleprompters (**GEPA default**, MIPROv2, BootstrapFewShot), runtime `dspy.Assert` guardrails, native function-calling adapters, centralized LM factory, and `save/load` persistence of optimized artifacts; (3) make all destructive paths atomic + reversible, add observability (hook heartbeat, `sio status`), fix mining correctness (streaming, byte-offset resume, stable slugs, timezone-aware timestamps), and schedule the autoresearch loop with human approval gates.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: DSPy >=3.1.3 (core framework per Constitution V), Click >=8.1 (CLI), Rich >=13.0 (TUI), fastembed >=0.2 (ONNX embeddings for centroids), numpy >=1.24, sqlite3 (stdlib), tomllib (stdlib), `systemd-user` or Claude Code `CronCreate` for scheduling (selected in Phase 0 research)
**Storage**:
- **Per-platform (write target, Constitution V)**: `~/.sio/<platform>/behavior_invocations.db` — currently `~/.sio/claude-code/behavior_invocations.db`
- **Consolidated (read target)**: `~/.sio/sio.db` — canonical store for errors, patterns, datasets, gold standards, suggestions, audit log; holds a synchronized *view* of per-platform invocations
- **Artifacts**: `~/.sio/datasets/` (JSON), `~/.sio/ground_truth/`, `~/.sio/optimized/` (DSPy `program.save()` JSON), `~/.sio/backups/<relpath>.<ts>` (apply-time backups), `~/.sio/hook_health.json` (heartbeat)

**Testing**: pytest (unit + integration), ruff (lint + format), coverage ≥ 72% for new code, Skill Arena regression pass for any optimizer change, crash-injection test for `applier/writer.py`, integration tests against `/tmp/sio-test.db` clone (never live `~/.sio/sio.db` per PRD §10)

**Target Platform**: Linux/WSL2 (primary dev host), macOS (secondary), single-machine
**Project Type**: CLI tool + Python library (`sio` CLI + `src/sio/` package) with platform adapters under `src/sio/adapters/<platform>/`
**Performance Goals**:
- Full mine of 907 JSONL files completes with peak RSS < 500 MB (SC-007)
- `sio suggest` re-run with no new errors < 5 s (SC-011, centroid reuse)
- `sio status` returns in < 2 s on typical store (SC-009)
- Concurrent hook + mine writes succeed without "database busy" (SC-006, via 30s `busy_timeout` + WAL)

**Constraints**:
- **Constitution V (NON-NEGOTIABLE)**: per-platform `behavior_invocations.db` MUST be preserved; consolidation is via *read-side sync*, not writer relocation
- **Constitution IV (NON-NEGOTIABLE)**: TDD — tests before implementation
- **Constitution XI (NON-NEGOTIABLE)**: no stubs/placeholders in production paths (FR-035→FR-041 explicitly demand real DSPy calls)
- **PRD §6.1**: zero deferrals across all 34 findings
- **Atomic writes only**: `sed -i`, `perl -pi`, `awk -i inplace` forbidden (CLAUDE.md rule; documented WSL2 file-wipe)
- **DSPy version floor**: 3.1.3 (needed for `Tool.execute()`, GEPA availability)

**Scale/Scope**:
- Current store: 45,536 `error_records`, 66,567 `flow_events`, 38,091 legacy `behavior_invocations`, 907 JSONL session files, 250 MB DB on disk
- Projected 12-month: ~500k behavior_invocations, ~200k error_records (with dedup), ~150 stable patterns
- 41 FRs, 22 SCs, 10 user stories, 34 tasks across 4 phases (per PRD)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevance | Status | Notes |
|---|---|---|---|
| I. Platform-Native First | HIGH | ✅ Pass | `claude-code` adapter only (spec Out-of-Scope); no generic abstraction introduced |
| II. Closed-Loop Learning | HIGH | ✅ Pass (this feature restores the loop) | FR-001 → FR-007 + FR-005 (gold promotion) + FR-006 (autoresearch) reconnect observe→label→optimize→deploy |
| III. Binary Signals, Pattern Thresholds | MEDIUM | ✅ Pass | `user_satisfied` / `correct_outcome` remain binary; auto-promotion (FR-005) gated on both; arena validation preserved before auto-apply (Risk-mitigation table) |
| IV. Test-First (NON-NEGOTIABLE) | HIGH | ✅ Pass by design | Phase 0 research produces `quickstart.md` with test scaffolding; `/speckit.tasks` enforces TDD task ordering (tests before impl); crash-injection test for FR-004 |
| V. Shared Core, Separate Data | **HIGH — potential conflict** | ✅ Pass via reconciliation | Spec Assumption ("consolidation target") reconciled in Phase 0 research: **per-platform writes preserved**, **read-side sync** into `~/.sio/sio.db`; no cross-platform mixing |
| VI. Observability & Telemetry | HIGH | ✅ Pass | FR-016 (hook heartbeat), expanded `sio status`, SC-009 (degraded within one heartbeat cycle) |
| VII. Simplicity & YAGNI | MEDIUM | ✅ Pass | 41 FRs are all audit-findings-driven or DSPy-idiomaticity-driven; Out-of-Scope explicitly excludes web UI, multi-platform, distributed mining, fine-tuning |
| VIII. Parallel Agent Spawning | MEDIUM | ✅ Pass | `/speckit.tasks` will produce dependency-ordered waves executable in parallel; adversarial re-audit (FR-034) spawns two hunters concurrently |
| IX. Dataset Quality Above All (NON-NEGOTIABLE) | HIGH | ✅ Pass | FR-020 (within-type dedup preserves tool_failure signal), FR-030 (timezone correctness → temporal integrity), FR-036 (`dspy.Example`-only trainset), FR-005 (curated gold promotion) all serve dataset quality |
| X. Programmatic Corpus Mining | MEDIUM | ✅ Pass | FR-009 (streaming parse) + FR-010 (byte-offset resume) keep mining in variable space; no corpus-into-prompt violations introduced |
| XI. No Fake/Stub Production Code (NON-NEGOTIABLE) | HIGH | ✅ Pass | FR-018 (real metric, not string-equality), FR-035 (real `dspy.Module`s), FR-037 (real GEPA/MIPROv2/BootstrapFewShot calls), FR-039 (real `save/load`) — every listed function performs its actual work |

**Verdict**: All gates pass. No Complexity-Tracking entries required. The one potential conflict (Principle V vs. consolidation) is resolved by the "per-platform write + read-side sync" pattern documented in Phase 0 research.

### Post-Design Re-check (after Phase 1 artifacts generated)

| Principle | Re-check Result |
|---|---|
| I. Platform-Native First | ✅ Still passes. `contracts/storage-sync.md` §8 documents the multi-platform extension path *without* introducing a generic abstraction today. |
| V. Shared Core, Separate Data | ✅ Confirmed. `contracts/storage-sync.md` preserves per-platform writes; sync is strictly read-side via ATTACH. |
| IV. Test-First | ✅ `quickstart.md` §3 codifies the TDD loop; `/speckit.tasks` will generate `(test, impl)` pairs. |
| XI. No Stubs | ✅ `contracts/dspy-module-api.md` requires real `dspy.Module`s, `dspy.Evaluate` scoring, real `program.save/load` — every function has a non-trivial body. SC-022 grep-test enforces no bypass. |
| IX. Dataset Quality | ✅ `contracts/dspy-module-api.md` §4 mandates `dspy.Example.with_inputs()` on every training example; registry enforces. |
| X. Programmatic Corpus Mining | ✅ Streaming parse (FR-009) + byte-offset resume (FR-010) keep mining in variable space. No Phase 1 design introduces corpus-into-prompt patterns. |

No new violations introduced by Phase 1 design. Complexity Tracking remains empty.

## Project Structure

### Documentation (this feature)

```text
specs/004-pipeline-integrity-remediation/
├── plan.md                         # This file
├── spec.md                         # Feature spec (41 FR, 22 SC, 10 stories)
├── research.md                     # Phase 0 output — see below
├── data-model.md                   # Phase 1 output — entities + schema deltas
├── quickstart.md                   # Phase 1 output — dev setup + test scaffolding
├── contracts/
│   ├── cli-commands.md             # `sio` command surface contract
│   ├── dspy-module-api.md          # Signature/Module/metric contracts per FR-035/FR-036
│   ├── optimizer-selection.md      # `--optimizer gepa|mipro|bootstrap` contract (FR-037)
│   ├── storage-sync.md             # Per-platform → sio.db sync contract (Principle V reconciliation)
│   └── hook-heartbeat.md           # Heartbeat file schema (FR-016)
├── research/
│   └── dspy-3.x-reference.md       # Canonical DSPy 3.1.3 API reference (pre-existing)
├── checklists/
│   └── requirements.md             # Spec quality gate — all passing
└── tasks.md                        # Phase 2 output — NOT produced by /speckit.plan
```

### Source Code (repository root)

```text
src/sio/
├── core/
│   ├── db/
│   │   ├── schema.py               # FR-017 schema_version + FR-015 indexes + FR-012 busy_timeout=30000
│   │   ├── queries.py              # FR-031 _DEFAULT_PLATFORM single-source
│   │   └── sync.py                 # NEW: per-platform → sio.db sync (Principle V reconciliation)
│   ├── dspy/
│   │   ├── lm_factory.py           # NEW: FR-041 centralized dspy.LM factory
│   │   ├── signatures.py           # NEW/UPDATED: class-based Signatures for all reasoning modules
│   │   ├── modules.py              # NEW/UPDATED: dspy.Module subclasses (FR-035)
│   │   ├── optimizer.py            # UPDATED: GEPA/MIPRO/Bootstrap switch (FR-037); reads from sio.db
│   │   ├── metrics.py              # NEW: FR-018 task-appropriate metric functions
│   │   └── assertions.py           # NEW: FR-038 dspy.Assert helpers + backtrack logging
│   ├── arena/
│   │   └── gold_standards.py       # UPDATED: promote_to_gold called from hook path (FR-005)
│   ├── applier/
│   │   └── writer.py               # FR-004 atomic+backup, FR-019 path allowlist, FR-024 merge consent
│   ├── clustering/
│   │   ├── pattern_clusterer.py    # FR-014 deterministic slugs, H11 centroid reuse (FR-032)
│   │   ├── grader.py               # FR-023 reachable "declining" grade, FR-030 tz-aware
│   │   └── ranker.py               # FR-013 empty-timestamp guard, FR-030 tz-aware
│   ├── mining/
│   │   ├── jsonl_parser.py         # FR-009 streaming parse
│   │   ├── pipeline.py             # FR-010 byte-offset resume, FR-011 subagent linkage, FR-027 warn-on-missing, FR-028 size guard
│   │   ├── flow_pipeline.py        # FR-008 flow dedup honors processed_sessions
│   │   └── flow_extractor.py       # FR-021 explicit positive signal, FR-022 n-gram +1, FR-026 polyglot ext
│   ├── feedback/
│   │   └── labeler.py              # Reads via sio.db sync
│   ├── suggestions/
│   │   └── dspy_generator.py       # UPDATED: dspy.Module + FR-029 dspy.Evaluate + FR-038 dspy.Assert
│   ├── training/
│   │   └── recall_trainer.py       # UPDATED: FR-018 real metric (not string-eq)
│   └── autoresearch/
│       └── scheduler.py            # NEW: FR-006 schedule registration + arena approval gate
├── adapters/
│   └── claude_code/
│       ├── hooks/
│       │   ├── post_tool_use.py    # Writes per-platform; triggers heartbeat (FR-016)
│       │   ├── stop.py             # Writes per-platform; auto-promote to gold (FR-005)
│       │   └── pre_compact.py      # Writes per-platform
│       └── installer.py            # FR-007 idempotent; no legacy DB recreation
└── cli/
    ├── main.py                     # UPDATED: sio suggest non-destructive (FR-003), sio optimize --optimizer (FR-037), sio status health (FR-016), sio purge correct DB (FR-025)
    └── status.py                   # NEW: hook health surface

scripts/
├── migrate_split_brain.py          # NEW: one-time backfill (FR-002)
└── autoresearch_cron.py            # NEW: cron-entry wrapper

tests/
├── unit/
│   ├── dspy/
│   │   ├── test_lm_factory.py      # FR-041 single factory
│   │   ├── test_signatures.py      # FR-035 class-based Signature coverage
│   │   ├── test_metrics.py         # FR-018 metric signatures
│   │   ├── test_assertions.py      # FR-038 Assert triggers backtrack
│   │   └── test_save_load.py       # FR-039 program.save/load round-trip
│   ├── db/
│   │   ├── test_sync.py            # Principle V reconciliation: per-platform → sio.db mirror
│   │   ├── test_schema_version.py  # FR-017
│   │   └── test_migration_resume.py # FR-002 idempotent backfill
│   ├── applier/
│   │   ├── test_atomic_write.py    # FR-004 crash-injection
│   │   ├── test_allowlist.py       # FR-019 path validation
│   │   └── test_merge_consent.py   # FR-024
│   ├── mining/
│   │   ├── test_streaming_parse.py # FR-009 RSS bound
│   │   ├── test_byte_offset.py     # FR-010 growing file
│   │   ├── test_subagent_link.py   # FR-011 parent FK
│   │   └── test_flow_dedup.py      # FR-008 idempotent
│   ├── clustering/
│   │   ├── test_deterministic_slugs.py # FR-014
│   │   ├── test_centroid_reuse.py  # FR-032 (SC-011)
│   │   └── test_declining_grade.py # FR-023
│   └── hooks/
│       └── test_heartbeat.py       # FR-016
├── integration/
│   ├── test_closed_loop.py         # US1: invocation → gold → optimize → suggest
│   ├── test_suggest_non_destructive.py # US2 / FR-003
│   ├── test_apply_safety.py        # US3 / FR-004 / FR-019 / FR-024
│   ├── test_autoresearch_cadence.py # US4 / FR-006
│   ├── test_mining_idempotence.py  # US5 / FR-008 / FR-010
│   ├── test_sio_status_health.py   # US6 / FR-016
│   ├── test_dspy_idiomatic.py      # US9 / FR-035→FR-041
│   └── test_gepa_vs_baseline.py    # SC-018
└── conftest.py                     # tmp DB fixtures, DSPy mocks, fastembed stubs
```

**Structure Decision**: Existing `src/sio/` layout is preserved. New files added under `src/sio/core/dspy/` (DSPy idiomatic layer), `src/sio/core/db/sync.py` (Principle V reconciliation), `src/sio/autoresearch/scheduler.py` (FR-006), and `scripts/` (one-time migrations). All adapters remain under `src/sio/adapters/claude_code/`. Tests mirror the module tree with `unit/` and `integration/` splits. No web/mobile structure applies — this is a CLI + library.

## Complexity Tracking

> No Constitution gate violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | — | — |
