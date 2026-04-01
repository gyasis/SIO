# Implementation Plan: SIO Competitive Enhancement

**Branch**: `001-competitive-enhancement` | **Date**: 2026-04-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-competitive-enhancement/spec.md`

## Summary

SIO currently extracts ~35% of available session metadata and only captures errors, not successes. This enhancement brings SIO to feature parity with the best self-improving agent tools by adding: complete JSONL metadata extraction (tokens, costs, cache ratios), positive signal capture, learning velocity tracking, instruction budget management with semantic consolidation, temporal confidence decay, pattern grading lifecycle, real-time lifecycle hooks (PreCompact, Stop, UserPromptSubmit), git-backed experimentation with binary assertions, an autonomous optimization loop with human promotion gates, anomaly detection, and interactive HTML reporting.

The approach extends existing modules rather than building parallel systems, per the PRD's "import, don't imitate" principle.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click >=8.1 (CLI), Rich >=13.0 (terminal UI), fastembed >=0.2 (embeddings), numpy >=1.24, DSPy >=3.1.3 (optimization)
**Storage**: SQLite with WAL mode at `~/.sio/sio.db` (14 existing tables, adding 5 new)
**Testing**: pytest with existing test infrastructure in `tests/`
**Target Platform**: Linux/macOS CLI (Claude Code extension)
**Project Type**: CLI tool + hook adapters
**Performance Goals**: Mining speed: process 100 sessions in <30 seconds; hook latency: <500ms per invocation; report generation: <5 seconds
**Constraints**: Local-only (no cloud/SaaS); hooks must never block host process; autonomous loop max 1 rule per cycle
**Scale/Scope**: Hundreds of sessions over months; <30MB database growth per month of active use

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Platform-Native First | PASS | All hooks use Claude Code native hook events (PreCompact, Stop, UserPromptSubmit). No generic wrapper. |
| II. Closed-Loop Learning | PASS | Positive signals (FR-007–FR-013) complete the observe→label→optimize→deploy loop by capturing what works, not just what fails. |
| III. Binary Signals, Pattern Thresholds | PASS | Sentiment scoring (-1.0 to +1.0) is internal analytics metadata, not a feedback signal. Binary approval/rejection (FR-010) and binary assertions (FR-037) maintain the binary signal principle. Pattern grading requires 3+ occurrences across 3+ sessions before promotion. |
| IV. Test-First (NON-NEGOTIABLE) | PASS | All new modules will follow Red-Green-Refactor. Tests written before implementation per spec acceptance scenarios. |
| V. Shared Core, Separate Data | PASS | New tables (session_metrics, positive_records, velocity_snapshots, processed_sessions, txlog) go in shared schema.py. Per-platform database separation maintained. |
| VI. Observability & Telemetry | PASS | Three new hooks add real-time telemetry (FR-031–FR-036). Session metrics table captures comprehensive per-session data (FR-004). |
| VII. Simplicity & YAGNI | PASS | Features are prioritized P1-P3. Implementation follows the existing module patterns (extractors, pipeline, writer). No new frameworks introduced. |
| VIII. Parallel Agent Spawning | PASS | Independent modules (positive extractor, sentiment scorer, approval detector) can be developed and tested in parallel. |
| IX. Dataset Quality Above All | PASS | Processed session tracking prevents duplicate mining (FR-003). Smart filtering removes low-signal sessions (FR-005). Anomaly detection flags outliers (FR-046). |
| X. Programmatic Corpus Mining | PASS | All extraction uses code-based parsing (JSONL parser, regex patterns). No session data stuffed into LLM context. Session facets (FR-049) may use sub-LLM on small extracted snippets, not raw sessions. |
| XI. No Fake/Stub/Placeholder | PASS | All features must perform real work. Sentiment scoring uses real keyword matching. Assertions produce real pass/fail results. AutoResearch loop performs real mining cycles. |

**Constitution Gate: PASSED — no violations detected.**

## Project Structure

### Documentation (this feature)

```text
specs/001-competitive-enhancement/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI contracts)
│   └── cli-commands.md
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
src/sio/
├── adapters/claude_code/
│   ├── hooks/
│   │   ├── post_tool_use.py          # EXISTING — reference pattern
│   │   ├── pre_compact.py            # NEW (FR-031, FR-032)
│   │   ├── stop.py                   # NEW (FR-033)
│   │   └── user_prompt_submit.py     # NEW (FR-034, FR-035)
│   └── installer.py                  # MODIFY (FR-036)
├── applier/
│   ├── writer.py                     # MODIFY (FR-029, FR-030) — delta-based writing
│   ├── budget.py                     # NEW (FR-021–FR-025)
│   ├── deduplicator.py               # NEW (FR-028)
│   └── changelog.py                  # EXISTING
├── cli/
│   └── main.py                       # MODIFY — add velocity, budget, violations, dedupe, autoresearch, report commands
├── clustering/
│   ├── pattern_clusterer.py          # EXISTING — reuse _get_backend() singleton
│   └── grader.py                     # NEW (FR-019, FR-020)
├── core/
│   ├── arena/
│   │   ├── assertions.py             # NEW (FR-037, FR-038)
│   │   ├── experiment.py             # NEW (FR-039–FR-041)
│   │   ├── autoresearch.py           # NEW (FR-042–FR-045)
│   │   ├── txlog.py                  # NEW (FR-044)
│   │   ├── anomaly.py                # NEW (FR-046)
│   │   ├── regression.py             # EXISTING
│   │   ├── drift_detector.py         # EXISTING
│   │   ├── collision.py              # EXISTING
│   │   └── gold_standards.py         # EXISTING
│   ├── db/
│   │   └── schema.py                 # MODIFY — add 5 new tables
│   ├── metrics/
│   │   └── velocity.py               # NEW (FR-014–FR-016)
│   └── config.py                     # MODIFY — add budget caps, decay params, loop config
├── mining/
│   ├── jsonl_parser.py               # MODIFY (FR-001) — extract usage, cost, model, sidechain
│   ├── pipeline.py                   # MODIFY (FR-002) — processed_sessions check, session_metrics insert
│   ├── error_extractor.py            # EXISTING
│   ├── flow_extractor.py             # EXISTING — has _POSITIVE_KEYWORDS to coordinate with
│   ├── positive_extractor.py         # NEW (FR-007–FR-009)
│   ├── approval_detector.py          # NEW (FR-010, FR-011)
│   ├── sentiment_scorer.py           # NEW (FR-012, FR-013)
│   ├── violation_detector.py         # NEW (FR-026, FR-027)
│   └── facet_extractor.py            # NEW (FR-049, FR-050)
├── reports/
│   └── html_report.py                # NEW (FR-047, FR-048)
├── suggestions/
│   └── confidence.py                 # MODIFY (FR-017, FR-018) — add temporal decay
└── ...                               # Other existing modules unchanged

tests/
├── test_jsonl_parser_enhanced.py     # NEW
├── test_positive_extractor.py        # NEW
├── test_approval_detector.py         # NEW
├── test_sentiment_scorer.py          # NEW
├── test_velocity.py                  # NEW
├── test_confidence_decay.py          # NEW
├── test_grader.py                    # NEW
├── test_budget.py                    # NEW
├── test_violation_detector.py        # NEW
├── test_deduplicator.py              # NEW
├── test_delta_writer.py              # NEW
├── test_hooks.py                     # NEW
├── test_assertions.py                # NEW
├── test_experiment.py                # NEW
├── test_autoresearch.py              # NEW
├── test_anomaly.py                   # NEW
├── test_html_report.py              # NEW
└── test_facet_extractor.py           # NEW
```

**Structure Decision**: Extends the existing `src/sio/` package structure. New modules follow established naming conventions (e.g., `*_extractor.py` in mining/, `*.py` in core/arena/). No new top-level packages needed. The 15 new files integrate into 7 existing subpackages.

## Complexity Tracking

> No constitution violations — this section is empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
