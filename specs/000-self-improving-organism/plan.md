# Implementation Plan: Self-Improving Organism (SIO)

**Branch**: `001-self-improving-organism` | **Date**: 2026-02-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-self-improving-organism/spec.md`

## Summary

Build a closed-loop self-improvement system for AI coding CLIs. V0.1 targets Claude Code as the first platform: native hooks capture telemetry into a local SQLite database, users provide binary satisfaction labels (`++`/`--`), and DSPy optimizers evolve skill prompts from labeled failure data. The Skill Arena prevents regressions by replaying gold-standard test cases before deploying optimized prompts.

**Dual-Space Architecture (Constitution X)**: Conversation corpora (SpecStory history) are processed in _variable space_ via RLM's sandboxed REPL — never stuffed into LLM context windows. The root LM writes Python code to search/filter/extract from the corpus; a cheap sub-LM handles semantic analysis on found snippets via `llm_query()`. This lets SIO scale to any corpus size while keeping LLM costs low. All corpus mining runs log full execution trajectories for audit.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: DSPy (latest, currently 3.1.3), fastembed (ONNX-based embeddings), numpy, sqlite3 (stdlib), Click (CLI), Rich (terminal UI)
**Runtime Dependency**: Deno (for RLM WASM sandbox — `curl -fsSL https://deno.land/install.sh | sh`)
**Storage**: SQLite with WAL mode, per-platform at `~/.sio/<platform>/behavior_invocations.db`
**Testing**: pytest + pytest-cov
**Target Platform**: Linux, macOS, Windows (WSL) — local CLI tool
**Project Type**: Library + CLI (pip-installable package with `sio` command)
**Performance Goals**: Telemetry logging <2s overhead per tool call; feedback entry <1s; optimization run <5min for 100 examples
**Constraints**: Offline-capable (default local embedding model), no cloud dependencies for core loop, <100MB disk per platform DB (90-day rolling window)
**Scale/Scope**: Single user, 5 platforms, ~100-500 invocations/day per platform

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| I | Platform-Native First | PASS | Claude Code adapter uses hooks + skills + CLAUDE.md natively. No generic wrapper. |
| II | Closed-Loop Learning | PASS | Full cycle: hooks → DB → labels → DSPy → artifact update → hooks. |
| III | Binary Signals, Pattern Thresholds | PASS | `user_satisfied` is 0/1. Agent fields are 0/1. No Likert scales. Optimization requires 3-10 recurring pattern occurrences (FR-028), not single incidents. User-flagged patterns accelerate but still pass quality gates (FR-029). Deployment requires user acknowledgment (FR-030). |
| IV | Test-First (NON-NEGOTIABLE) | PASS | pytest test suite written before implementation per TDD. |
| V | Shared Core, Separate Data | PASS | `sio/core/` is platform-agnostic. Each adapter gets its own DB at `~/.sio/<platform>/`. |
| VI | Observability & Telemetry | PASS | Every action logged to `behavior_invocations` with session_id, timestamp, pointers. |
| VII | Simplicity & YAGNI | PASS | V0.1 targets Claude Code only (Tier 1). Other platforms deferred. |
| VIII | Parallel Agent Spawning | PASS | Research, testing, linting tasks run as parallel agents. |
| IX | Dataset Quality Above All (NON-NEGOTIABLE) | PASS | Schema validation at write time, secret scrubbing, balanced label checks, temporal ordering, recency weighting. FR-025/026/027 enforce this. |
| X | Programmatic Corpus Mining (Variable Space) | PASS | RLM processes SpecStory corpora in variable space (REPL), not token space (LLM context). Root LM writes strategy code; sub-LM handles `llm_query()` extraction. Trajectory logging required. Deno WASM sandbox. |

No violations. Gate PASSED.

## Project Structure

### Documentation (this feature)

```text
specs/001-self-improving-organism/
├── plan.md              # This file
├── research.md          # Phase 0: technology research
├── data-model.md        # Phase 1: entity schemas
├── quickstart.md        # Phase 1: getting started guide
├── contracts/           # Phase 1: CLI command schemas
│   ├── cli-commands.md  # sio CLI command interface
│   └── hook-contracts.md # Hook JSON input/output schemas
└── tasks.md             # Phase 2: implementation tasks
```

### Source Code (repository root)

```text
src/sio/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.py          # DDL, migrations, WAL setup
│   │   ├── queries.py         # Insert/select/aggregate queries
│   │   └── retention.py       # 90-day rolling purge
│   ├── telemetry/
│   │   ├── __init__.py
│   │   ├── logger.py          # Write invocation records
│   │   ├── auto_labeler.py    # Agent-inferred binary labels
│   │   ├── passive_signals.py # Undo/correction detection
│   │   └── secret_scrubber.py # Regex-based secret redaction
│   ├── feedback/
│   │   ├── __init__.py
│   │   ├── labeler.py         # User binary feedback (++/--)
│   │   └── batch_review.py    # Sequential review of unlabeled
│   ├── dspy/
│   │   ├── __init__.py
│   │   ├── optimizer.py       # GEPA/MIPROv2/BootstrapFewShot
│   │   ├── rlm_miner.py       # Variable-space corpus mining (Constitution X)
│   │   └── corpus_indexer.py  # BM25 + embeddings index
│   ├── arena/
│   │   ├── __init__.py
│   │   ├── gold_standards.py  # Gold-standard test cases
│   │   ├── regression.py      # Replay and validate
│   │   ├── drift_detector.py  # Semantic drift (40% threshold)
│   │   └── collision.py       # Trigger collision detection
│   ├── health/
│   │   ├── __init__.py
│   │   └── aggregator.py      # Per-skill health metrics
│   └── embeddings/
│       ├── __init__.py
│       ├── provider.py        # Abstract embedding interface
│       ├── local_model.py     # fastembed ONNX default
│       └── api_model.py       # External API override (FR-024)
├── adapters/
│   └── claude_code/
│       ├── __init__.py
│       ├── hooks/
│       │   ├── post_tool_use.sh   # Telemetry capture hook
│       │   ├── pre_tool_use.sh    # Real-time correction hook
│       │   └── notification.sh    # Feedback entry hook
│       ├── skills/
│       │   ├── sio-feedback/SKILL.md
│       │   ├── sio-optimize/SKILL.md
│       │   ├── sio-health/SKILL.md
│       │   └── sio-review/SKILL.md
│       ├── installer.py       # Claude Code adapter setup
│       └── artifact_writer.py # Write to CLAUDE.md/SKILL.md
└── cli/
    ├── __init__.py
    └── main.py                # Click CLI: sio install/health/optimize/review

tests/
├── conftest.py
├── unit/
│   ├── test_schema.py
│   ├── test_queries.py
│   ├── test_logger.py
│   ├── test_labeler.py
│   ├── test_auto_labeler.py
│   ├── test_passive_signals.py
│   ├── test_secret_scrubber.py
│   ├── test_retention.py
│   ├── test_optimizer.py
│   ├── test_gold_standards.py
│   ├── test_regression.py
│   ├── test_drift_detector.py
│   ├── test_collision.py
│   ├── test_aggregator.py
│   └── test_embeddings.py
├── integration/
│   ├── test_telemetry_pipeline.py
│   ├── test_feedback_loop.py
│   ├── test_optimization_cycle.py
│   └── test_arena_validation.py
└── contract/
    ├── test_hook_contracts.py
    └── test_cli_commands.py
```

**Structure Decision**: Single project layout. SIO is a pip-installable Python package (`src/sio/`) with platform adapters as subpackages. Claude Code is the only adapter for V0.1. The `core/` package is platform-agnostic (Constitution V); the `adapters/` package contains platform-native code (Constitution I).

## Complexity Tracking

No violations to justify. Structure follows the three-layer architecture from the constitution directly.
