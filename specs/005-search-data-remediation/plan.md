# Implementation Plan: SIO Search & Data-Sourcing Remediation

**Branch**: `005-search-data-remediation`
**Spec**: `specs/005-search-data-remediation/spec.md`
**Evidence**: `specs/005-search-data-remediation/research.md`

## Summary

Close four implementation gaps in SIO's search/data surface (G1 unbounded suggest load, G2 siloed multi-hop, G3 non-default recency, G4 ±1-only context-walk) and flip the matching disciplines from opt-in to default, so the healthy search behavior happens without the agent needing to remember a flag. Add a self-measurement report so the gap cannot silently re-open. No schema change; this is read-path behavior, defaults, ergonomics, one economy guard, and one stub removal.

## Technical Context

- **Language**: Python ≥ 3.11 (existing SIO toolchain).
- **Primary surfaces touched**: `src/sio/search/cli.py` (session-search core, recency default + context window + cascade reach), `src/sio/cli/search.py` (CLI wiring), `src/sio/main.py` (`suggest` bound + `recall --polish`), `src/sio/core/db/queries.py` (`get_error_records` cap), `src/sio/mining/recall.py` (context expansion), `src/sio/suggestions/confidence.py` (unchanged — decay is scoring, noted for context).
- **Data stores**: `~/.sio/sio.db` (951 MB), `~/.sio/<platform>/behavior_invocations.db`, `~/.sio/previews/*.csv`. No migration.
- **Testing**: pytest, test-first (Constitution IV NON-NEGOTIABLE). Fixtures: small fake JSONL corpus spanning dates; small fake error DB; `freeze_utc_now`.
- **Compat contract**: the Cascade-Memory-Protocol callers (`--files`/`--count`/`--context`/`--clean`/`--all`/`--specstory`/`--backups`) must behave unchanged except newest-first ordering + recency default (both overridable). FR-014/SC-007.

## Constitution Check

- **ECONOMY-FIRST (memory.md)** — US4/FR-007..009 directly implement the bounded-load mandate. PASS by design.
- **Cost-control (cost-control.md)** — FR-010/FR-013: `recall --polish` must state model + estimate + confirm before any paid call; no approved-tier violation. PASS by design.
- **Recency-first (memory-search.md)** — US1 makes the advisory rule the tool default; `--recent 0`/`--all` preserved as explicit overrides + zero-result widen-hint (FR-002) so recency-first never becomes recency-only. PASS by design.
- **Test-First (Constitution IV)** — every implementation task paired with a preceding test task in `tasks.md`. PASS.
- **No placeholder code** — US5/FR-010 removes a live stub (`recall --polish` prompt-string emit). PASS.

### Post-design re-check
- No new external dependency. No DB schema change. No new paid-LLM default path (polish remains opt-in + gated). Re-check clean.

## Project Structure

### Documentation (this feature)
```
specs/005-search-data-remediation/
├── spec.md          # the full story + user stories + FR + SC
├── research.md      # capability audit (file:line) + behavioral mining
├── plan.md          # this file
└── tasks.md         # dev-kid wave-organized, test-first
```

### Source code (touch list)
```
src/sio/
├── search/cli.py            # FR-001/002 recency default + ordering; FR-003/004 context window; FR-005/006 cascade reach
├── cli/search.py            # CLI flag wiring for the above
├── main.py                  # FR-007/008 suggest bound + --since; FR-010 recall --polish; FR-011/012 discipline report + briefing hook
├── core/db/queries.py       # FR-007 get_error_records default cap
├── mining/recall.py         # FR-004 forward-walk into fix turns
└── (new) reporting/search_discipline.py   # FR-011 metric over invocation telemetry
tests/
├── unit/search/             # recency default, ordering, context window, cascade reach
├── unit/suggest/            # bounded load, --since
├── unit/reporting/          # discipline metric
└── integration/             # CMP-caller no-regression (SC-007)
```

## Complexity Tracking

- **Highest-risk change**: US1 default recency on `sio search` — it alters the default behavior of a hardwired CMP caller. Mitigated by FR-014 compat contract + SC-007 no-regression integration test + preserved `--recent 0`/`--all` overrides.
- **Lowest-risk**: US5 stub removal, US6 report (additive).
- **Reused, not rebuilt**: US3 surfaces the existing, audited-correct `suggest` cascade (`cluster_errors()`); no new search algorithm.
