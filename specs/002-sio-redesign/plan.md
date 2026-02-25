# Implementation Plan: SIO v2 — Mine, Cluster, Improve

**Branch**: `002-sio-redesign` | **Date**: 2026-02-25 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-sio-redesign/spec.md`

## Summary

Redesign SIO to mine existing SpecStory + Claude JSONL session data instead of capturing via hooks. The pipeline: mine errors → cluster into patterns → build datasets → generate suggestions → passive background scheduler writes a home file → human reviews and approves → changes applied to Claude Code config. Builds on v1's shared core (embeddings, config, CLI framework, arena).

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI), Rich (terminal UI), fastembed (embeddings), numpy
**Storage**: SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/`
**Testing**: pytest, ruff
**Target Platform**: Linux/macOS (WSL2 supported)
**Project Type**: CLI tool
**Performance Goals**: Mine 30 days of sessions in <60 seconds; cluster 1000 errors in <10 seconds
**Constraints**: All local — no external API calls; must not interfere with active Claude sessions
**Scale/Scope**: Single developer's session history (typically 50-500 sessions/month)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Platform-Native First | PASS | Reads Claude Code's native SpecStory + JSONL formats |
| II. Closed-Loop Learning | PASS | Mine → Cluster → Dataset → Suggest → Review → Apply = closed loop |
| III. Binary Signals, Pattern Thresholds | PASS | Patterns require 3+ occurrences; single incidents don't trigger suggestions |
| IV. Test-First (NON-NEGOTIABLE) | PASS | TDD enforced per constitution |
| V. Shared Core, Separate Data | PASS | Core mining/clustering is platform-agnostic; data sources are adapter-specific |
| VI. Observability & Telemetry | PASS | Mines existing telemetry rather than creating new capture |
| VII. Simplicity & YAGNI | PASS | Reuses existing data; no new hooks or capture infrastructure |
| VIII. Parallel Agent Spawning | PASS | Mining, clustering, dataset building can parallelize |
| IX. Dataset Quality (NON-NEGOTIABLE) | PASS | Positive + negative examples required; lineage tracked; incremental updates |
| X. Programmatic Corpus Mining | PASS | SpecStory files parsed programmatically, not stuffed into LLM context |

## Project Structure

### Documentation (this feature)

```text
specs/002-sio-redesign/
├── spec.md
├── plan.md              # This file
├── PRD.md               # Product requirements
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
└── quickstart.md        # Phase 1 output
```

### Source Code (repository root)

```text
src/sio/
├── mining/                          # NEW — v2 session mining
│   ├── __init__.py
│   ├── specstory_parser.py          # Parse SpecStory .md files (US1)
│   ├── jsonl_parser.py              # Parse Claude JSONL transcripts (US1)
│   ├── error_extractor.py           # Extract errors, corrections, undos (US1)
│   └── time_filter.py               # Time-window filtering (US1)
├── clustering/                      # NEW — v2 pattern clustering
│   ├── __init__.py
│   ├── pattern_clusterer.py         # Embedding-based error clustering (US2)
│   └── ranker.py                    # Frequency × recency ranking (US2)
├── datasets/                        # NEW — v2 dataset building
│   ├── __init__.py
│   ├── builder.py                   # Build pos/neg datasets per pattern (US3)
│   └── lineage.py                   # Track dataset provenance (US3)
├── suggestions/                     # NEW — v2 suggestion generation
│   ├── __init__.py
│   ├── generator.py                 # Generate fix proposals (US4)
│   ├── home_file.py                 # Write/update suggestions.md (US4)
│   └── confidence.py                # Score suggestion confidence (US4)
├── review/                          # NEW — v2 human review
│   ├── __init__.py
│   ├── reviewer.py                  # Interactive review logic (US5)
│   └── tagger.py                    # Human + AI-assisted tagging (US5)
├── applier/                         # NEW — v2 change application
│   ├── __init__.py
│   ├── writer.py                    # Write changes to config files (US6)
│   ├── rollback.py                  # Revert applied changes (US6)
│   └── changelog.py                 # Maintain change log (US6)
├── scheduler/                       # NEW — v2 passive scheduling
│   ├── __init__.py
│   ├── cron.py                      # Install/manage cron entries (US4)
│   └── runner.py                    # Passive analysis orchestrator (US4)
├── core/                            # REUSE from v1
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.py                # EXTEND — add v2 tables alongside v1 tables
│   │   └── queries.py               # EXTEND — add v2 query functions
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── provider.py              # REUSE — EmbeddingBackend ABC
│   │   └── local_model.py           # REUSE — fastembed backend
│   ├── config.py                    # EXTEND — add v2 config keys
│   └── arena/                       # REUSE — gold standards, drift, collision
│       ├── __init__.py
│       ├── gold_standards.py
│       ├── drift_detector.py
│       └── collision.py
├── adapters/                        # KEEP from v1 (not modified by v2)
│   └── claude_code/
└── cli/
    ├── __init__.py
    └── main.py                      # EXTEND — add mine, patterns, review, etc.

tests/
├── conftest.py                      # EXTEND — add v2 fixtures
├── unit/
│   ├── test_specstory_parser.py     # NEW
│   ├── test_jsonl_parser.py         # NEW
│   ├── test_error_extractor.py      # NEW
│   ├── test_time_filter.py          # NEW
│   ├── test_pattern_clusterer.py    # NEW
│   ├── test_ranker.py               # NEW
│   ├── test_dataset_builder.py      # NEW
│   ├── test_suggestion_generator.py # NEW
│   ├── test_home_file.py            # NEW
│   ├── test_reviewer.py             # NEW
│   ├── test_tagger.py               # NEW
│   ├── test_writer.py               # NEW
│   ├── test_rollback.py             # NEW
│   ├── test_cron.py                 # NEW
│   └── test_config.py               # EXISTING — extend for v2 keys
├── integration/
│   ├── test_mine_to_cluster.py      # NEW — US1+US2 pipeline
│   ├── test_cluster_to_dataset.py   # NEW — US2+US3 pipeline
│   ├── test_suggest_to_apply.py     # NEW — US4+US5+US6 pipeline
│   └── test_e2e_passive.py          # NEW — Full passive loop
└── contract/
    └── test_cli_commands.py         # EXTEND — add v2 CLI contracts
```

**Structure Decision**: v2 adds new subpackages (mining, clustering, datasets, suggestions, review, applier, scheduler) alongside existing v1 infrastructure (core, adapters, cli). The v1 core modules (embeddings, config, arena) are reused directly. The v1 DB schema is extended with new tables for v2 entities.

## Reuse from Branch 001

These modules carry over from `001-self-improving-organism`:

| Module | Source | Reuse Strategy |
|--------|--------|----------------|
| SQLite schema | `core/db/schema.py` | EXTEND — add v2 tables (error_records, patterns, datasets, suggestions, applied_changes) alongside existing v1 tables |
| Query layer | `core/db/queries.py` | EXTEND — add v2 query functions |
| Config loader | `core/config.py` | EXTEND — add new config keys (similarity_threshold, min_pattern_occurrences, etc.) |
| CLI framework | `cli/main.py` | EXTEND — add new commands (mine, patterns, review, approve, reject, rollback, schedule, status, datasets) |
| fastembed provider | `core/embeddings/` | REUSE as-is — used for pattern clustering |
| Gold standards + arena | `core/arena/` | REUSE — validate changes before applying (FR-010, FR-022, FR-023) |
| Drift + collision | `core/arena/` | REUSE — check proposed changes for conflicts |

## Complexity Tracking

No constitution violations. No complexity justifications needed.
