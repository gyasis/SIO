# Tasks — SIO Auto-Tag / Experiment Cohort Primitive (Phase 1 MVP)

PRD: `~/dev/prd/scratch/sio_autotag_experiments_2026-05-23.md`
Branch: `feature/sio-experiment-cohort`
Mode: dev-kid lightweight (NOT SpecKit — see PRD §9)
Locked decisions: Q1=new table, Q2=hash all four (CLAUDE.md+skills+rules+settings.json), Q3=separate join table, Q4=allow N concurrent, Q5=text|html|json via --format, Q6=baseline default 7d configurable, Q7=git-tag auto deferred, Q8=optional --project, default global.

CLI surface (per PRD §4):
- `sio experiment start NAME [--note --project]`
- `sio experiment status [NAME]`
- `sio experiment list`
- `sio experiment close NAME [--report --format text|html|json --baseline 7d]`
- Scope filter `--experiment NAME` retro-fitted onto: `sio scan`, `sio suggest`, `sio trend`, `sio flows`, `sio velocity`

Backend module: `src/sio/core/cohort/` (intentionally NOT `experiment` — avoids collision with existing `src/sio/core/arena/experiment.py` which is the git-worktree experiment concept, unrelated).

Mark `[ ]` → `[x]` as each task completes. The wave executor halts before the next wave on any unchecked task.

## Wave 1 — Schema + snapshotter (parallel; different files)

- [x] T001: Add `experiments` table (name, start_ts, close_ts, note, config_hash, project, status) to `src/sio/core/db/schema.py`
- [x] T002: Add `experiment_runs` join table (event_id, experiment_name, source_table) to `src/sio/core/db/schema.py`
- [x] T003: Add `migrate_005_experiments()` function to `src/sio/core/db/schema.py` and wire into `src/sio/core/db/bootstrap.py` migration chain
- [x] T004: Implement config-hash snapshotter at `src/sio/core/cohort/snapshot.py` (reads CLAUDE.md + active skills + active rules + `~/.claude/settings.json` hooks block; produces JSON manifest; returns sha256 hash)
- [x] T005: Add `src/sio/core/cohort/__init__.py` and `src/sio/core/cohort/models.py` (typed dataclasses: `Experiment`, `ExperimentRun`)

## Wave 2 — CLI surface (depends on Wave 1 schema)

- [x] T006: Implement `sio experiment` Click group + `sio experiment start NAME [--note --project]` in `src/sio/cli/main.py` (around line 2864 group region)
- [x] T007: Implement `sio experiment status [NAME]` in `src/sio/cli/main.py`
- [x] T008: Implement `sio experiment list` in `src/sio/cli/main.py`
- [x] T009: Implement `sio experiment close NAME [--report --format]` skeleton (closes timestamp; report deferred to Wave 4) in `src/sio/cli/main.py`
- [x] T010: Add cohort persistence helpers (`create_experiment`, `close_experiment`, `list_experiments`, `get_experiment`) in `src/sio/core/cohort/store.py`

## Wave 3 — Scope-filter integration (depends on Wave 2)

- [x] T011: Add `--experiment NAME` flag to `sio scan` command in `src/sio/cli/main.py` (resolves window from experiments table, applies timestamp filter)
- [x] T012: Add `--experiment NAME` flag to `sio suggest` command in `src/sio/cli/main.py` (composable with `--grep` / `--type`)
- [x] T013: Add `--experiment NAME` flag to `sio trend` command in `src/sio/cli/main.py`
- [x] T014: Add `--experiment NAME` flag to `sio flows` command in `src/sio/cli/main.py`
- [x] T015: Add `--experiment NAME` flag to `sio velocity` command in `src/sio/cli/main.py`
- [x] T016: Extract shared `resolve_experiment_window(name)` helper into `src/sio/core/cohort/window.py` so each command uses one resolver

## Wave 4 — A/B report engine (depends on Wave 3)

- [x] T017: Implement error-rate delta computation in `src/sio/core/cohort/report.py` (experiment_window vs baseline_window; per-hour normalized)
- [x] T018: Implement new-error-class diff in `src/sio/core/cohort/report.py` (clusters present in experiment NOT in baseline)
- [x] T019: Implement flow delta in `src/sio/core/cohort/report.py` (emerged / died flows via `sio flows --since` windows)
- [x] T020: Implement scoped suggestion generator in `src/sio/core/cohort/report.py` (runs `sio suggest --auto` filtered to experiment window)
- [x] T021: Implement text renderer in `src/sio/core/cohort/render_text.py`
- [x] T022: Implement HTML renderer in `src/sio/core/cohort/render_html.py` (mirror `sio report` layout)
- [x] T023: Implement JSON renderer in `src/sio/core/cohort/render_json.py`
- [x] T024: Wire `sio experiment close --report --format {text,html,json}` to the three renderers in `src/sio/cli/main.py`

## Wave 5 — Tests + dogfood (depends on Wave 4)

- [x] T025: Unit tests for cohort store + snapshot in `tests/unit/core/test_cohort.py`
- [x] T026: Unit tests for `sio experiment` CLI in `tests/unit/cli/test_experiment.py`
- [x] T027: Integration test — full lifecycle (start → simulated events → close → report) in `tests/integration/test_experiment_lifecycle.py`
- [x] T028: Run `ruff check --fix .` and resolve any lint findings
- [x] T029: Dogfood self-host — `sio experiment start "sio-experiment-mvp" --project SIO --note "first cohort, this PRD's own development"` and log proof via `prd log sio_autotag_experiments_2026-05-23 note "MVP shipped; first experiment 'sio-experiment-mvp' generated valid report on YYYY-MM-DD"`

## Anti-patterns (do NOT do)

- Do NOT create `.specify/specs/005-sio-experiment/` — the 2026-05-23 10:49 decision was lightweight, not SpecKit
- Do NOT mutate hook DB rows in-place — Q3 decision was separate join table
- Do NOT use `src/sio/core/arena/experiment.py` for cohort logic — it's the git-worktree concept, totally unrelated
- Do NOT commit the screenshots, course dirs, or `.playwright-mcp/` swept into the original dev-kid init commit — those are unrelated assets

## Done when

All five waves complete, lint clean, dogfood experiment closed with a valid A/B report.
