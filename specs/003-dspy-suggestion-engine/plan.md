# Implementation Plan: DSPy Suggestion Engine

**Branch**: `003-dspy-suggestion-engine` | **Date**: 2026-02-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-dspy-suggestion-engine/spec.md`

## Summary

Replace the stub string-template suggestion generator with real DSPy Signatures, Modules, and Optimizers that call an LLM to generate targeted improvements across all 7 Claude Code agent behavior surfaces. The existing pipeline (mine → cluster → dataset) remains unchanged; only the **suggest** step is replaced with DSPy. Agent-generated synthetic ground truth with human validation provides the training data for DSPy optimizers (BootstrapFewShot, MIPROv2). Two pipeline modes (automated and HITL) control the level of human involvement.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: DSPy >=3.1.3, Click >=8.1, Rich >=13.0, fastembed >=0.2, numpy >=1.24, tomllib (stdlib)
**Storage**: SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/`; Ground truth corpus at `~/.sio/ground_truth/`; Optimized modules at `~/.sio/optimized/`
**Testing**: pytest >=7.0, pytest-cov, ruff (linting)
**Target Platform**: Linux (WSL2), macOS — anywhere Claude Code runs
**Project Type**: CLI tool + Python library (pip-installable)
**Performance Goals**: Suggestion pipeline completes in <60s for 20 patterns (SC-003)
**Constraints**: LLM calls are the bottleneck; sanitize before sending; graceful fallback when no LLM available
**Scale/Scope**: Single-user CLI tool; datasets grow over months of usage; optimizer runs on 10-200 labeled examples

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| I | Platform-Native First | PASS | Generates Claude Code-native artifacts (CLAUDE.md, SKILL.md, hooks, mcp.json, settings.json) |
| II | Closed-Loop Learning | PASS | Approved/rejected suggestions feed back as training data (FR-018, FR-019, FR-020) |
| III | Binary Signals, Pattern Thresholds | PASS | Quality metric is 0-1 float; optimizer thresholds at 10+/50+ labeled examples; pattern thresholds preserved |
| IV | Test-First (NON-NEGOTIABLE) | PASS | Will follow TDD — tests before implementation per wave |
| V | Shared Core, Separate Data | PASS | DSPy engine is in shared `src/sio/` core; per-platform data untouched |
| VI | Observability & Telemetry | PASS | FR-014 requires verbose DSPy traces; all calls logged |
| VII | Simplicity & YAGNI | PASS | Builds on existing pipeline; only replacing the suggest step + adding ground truth |
| VIII | Parallel Agent Spawning | PASS | Independent tasks will be parallelized in execution waves |
| IX | Dataset Quality Above All (NON-NEGOTIABLE) | PASS | Agent-generated ground truth with human validation (US-6); two modes for quality control (US-7) |
| X | Programmatic Corpus Mining | N/A | This feature does not mine corpora — it consumes already-mined datasets |
| XI | No Fake/Stub Production Code (NON-NEGOTIABLE) | PASS | FR-016 explicitly enforces this; every function calls real DSPy with real LLM |

**Gate Result**: ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/003-dspy-suggestion-engine/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: DSPy API patterns, config design, ground truth format
├── data-model.md        # Phase 1: New tables, data flows, entity relationships
├── quickstart.md        # Phase 1: Developer setup guide
├── contracts/           # Phase 1: DSPy Signature contracts, CLI interface contracts
│   ├── dspy-signatures.md
│   ├── cli-commands.md
│   └── ground-truth-schema.md
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2: Implementation tasks (from /speckit.tasks)
```

### Source Code (repository root)

```text
src/sio/
├── core/
│   ├── config.py                    # MODIFY: Add [llm] section parsing
│   ├── db/
│   │   ├── schema.py                # MODIFY: Add ground_truth table
│   │   └── queries.py               # MODIFY: Add ground truth CRUD ops
│   └── dspy/
│       ├── __init__.py
│       ├── optimizer.py             # MODIFY: Replace _run_dspy_optimization() stub with real DSPy calls
│       ├── rlm_miner.py             # MODIFY: Replace mine_failure_context() stub with real dspy.RLM
│       ├── corpus_indexer.py         # EXISTS: BM25 search over conversation history (enhance for RLM)
│       ├── pattern_surface.py        # EXISTS: Surfaces recurring failure patterns from v1 telemetry
│       ├── signatures.py            # NEW: DSPy Signature definitions (suggestion + ground truth + surface routing)
│       ├── modules.py               # NEW: DSPy Module (ChainOfThought) wrappers
│       ├── metrics.py               # NEW: Quality metric function (specificity, actionability, surface accuracy)
│       ├── lm_factory.py            # NEW: LM backend factory (config → dspy.LM)
│       └── module_store.py          # NEW: Save/load optimized modules to disk
│
├── suggestions/
│   ├── generator.py                 # MODIFY: Add DSPy path alongside template fallback
│   ├── confidence.py                # MODIFY: Use LLM metric when available
│   └── dspy_generator.py            # NEW: DSPy-powered suggestion generation
│
├── ground_truth/                    # NEW: Ground truth management
│   ├── __init__.py
│   ├── generator.py                 # NEW: Agent-generated candidate ground truth
│   ├── reviewer.py                  # NEW: Human review interface (approve/reject/edit)
│   ├── corpus.py                    # NEW: Ground truth corpus management
│   └── seeder.py                    # NEW: Initial seed ground truth generation
│
├── cli/
│   └── main.py                      # MODIFY: Add ground-truth, optimize commands
│
└── adapters/claude_code/
    └── installer.py                 # MODIFY: Add config.toml template creation

tests/
├── unit/
│   ├── test_dspy_signatures.py      # NEW
│   ├── test_dspy_modules.py         # NEW
│   ├── test_dspy_metrics.py         # NEW
│   ├── test_lm_factory.py           # NEW
│   ├── test_module_store.py         # NEW
│   ├── test_dspy_generator.py       # NEW
│   ├── test_ground_truth_gen.py     # NEW
│   ├── test_ground_truth_review.py  # NEW
│   ├── test_ground_truth_corpus.py  # NEW
│   ├── test_ground_truth_seeder.py  # NEW
│   └── test_config_llm.py          # NEW
├── integration/
│   ├── test_dspy_pipeline.py        # NEW: Full suggest pipeline with DSPy
│   ├── test_ground_truth_flow.py    # NEW: Generate → review → train cycle
│   └── test_optimizer_real.py       # NEW: Real optimizer with mock LLM
└── contract/
    └── test_dspy_contracts.py       # NEW: Signature input/output contracts
```

**Structure Decision**: Extends the existing `src/sio/` single-project layout. New modules added under `core/dspy/` (DSPy primitives), `suggestions/` (generator replacement), and a new `ground_truth/` package (training data lifecycle). No new top-level packages needed.

## Complexity Tracking

> No constitution violations to justify. All design fits within existing architecture.
