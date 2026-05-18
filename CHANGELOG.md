# Changelog

All notable changes to **self-improving-organism** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

GitHub release pages (with full asset downloads) live at
<https://github.com/gyasis/SIO/releases>.

## [Unreleased]

Substantial discipline + observability layer added 2026-05-18 (will
roll into v0.2.1 or v0.3.0 once a real GEPA run validates the stack).

### Added — Optimizer ladder discipline (Constitution XIV proposed)

- **`sio optimize --skip-ladder`** — bypass the Tier 5 ladder gate.
  By default, `--optimizer gepa` refuses to run on a registered trainset
  if no prior MIPROv2 run exists for the same module + dataset. Override
  logs `LADDER_SKIP` via runlog for SIO mining.
- **`sio optimize --skip-data-gate`** — bypass the MIPROv2 data-size
  gate. By default, `--optimizer mipro` refuses when
  `valset_size < max(25, trainset_size * 0.2)`. Empirically grounded:
  MIPROv2 #17 with valset=5 scored 0.6970 vs Bootstrap #16's 0.7154
  (under-performed). Override logs `DATA_SIZE_SKIP`.
- **`sio optimize --skip-amplify-gate`** — bypass the amplify-first
  + row-floor gate. By default, MIPROv2/GEPA refuse on `source='curate'`
  trainsets OR row_count below the per-optimizer floor (MIPROv2 ≥200,
  GEPA ≥300). Empirically grounded: today's GEPA on the 93-row curated
  baseline timed out at 60 min with $1.11 wasted in gpt-5 reflection
  that never reached evaluation, while GEPA #14/#15 on the SAME baseline
  amplified to 372 rows produced 0.7224/0.8653. Override logs
  `AMPLIFY_SKIP`.
- **`sio optimize --resume-from <module_id>`** — auto-resolves
  `--trainset-file` from the prior row's `trainset_id`, records lineage
  in runlog metadata for crash recovery in cron-driven SIO runs.

### Added — Compound command

- **`sio optimize-ladder`** — auto-magic Bootstrap → AMPLIFY → MIPROv2 →
  GEPA chain. Resolves the trainset, plans rungs (skips already-done ones
  via DB lookup), estimates total cost, prompts for confirmation
  (or `--yes`), executes each rung via subprocess so the discipline gates
  fire. Crash-safe: re-running detects existing optimized_modules rows
  and skips completed rungs.
- **`sio amplify`** auto-registers output in `trainsets` table with
  `source='amplify'` and `parent_dataset_id` pointing at input dataset.
- **`sio optimize --trainset-file <X>`** auto-resolves sha into a
  `trainsets` row and links `optimized_modules.trainset_id` on the new
  row. Auto-promotes unregistered files as `source='manual'`.

### Added — Lineage wiring (Principle XV proposed)

- **`sio promote-positives`** writes a JSONL snapshot of the promoted
  batch to `~/.sio/promoted/promote_positives_<ts>.jsonl` and registers
  it in `trainsets` with `source='promote-positives'`. Closes the audit
  chain from optimize → ground_truth slice → promotion batch → original
  positive_records.
- **`sio differential-flows`** auto-registers its JSONL output in
  `trainsets` with `source='differential-flows-pairs'` or
  `'differential-flows-positives'`.
- **`sio analyze same-error`** now carries `@runlogged` (was the only
  `sio analyze` subcommand without it).

### Added — Observability stack (Principle XIII)

- **Stuck-in-reflection runtime monitor**: `Heartbeat` extension reads
  the active `dspy_capture` sidecar each tick, classifies calls by
  model class (Flash/Pro/gpt-5), emits `REFLECTION_STUCK` warn at 15 min
  reflection-only and `REFLECTION_STUCK_CRITICAL` at 40 min.
- **`sio doctor` stuck-in-reflection retrospective audit**: walks
  `~/.sio/runs/*_dspy.jsonl` (last 14d), flags any historical run with
  ≥5 reflection calls + 0 task calls + ≥15 min wall-clock.
- **`~/.sio/state/ladder_status.json`** — written by
  `sio optimize-ladder` after each rung (status: in_flight/complete/failed,
  per-rung exit codes, process_id, total_est_usd). Cron monitor polls
  this one file to know "is the ladder alive?".
- **`sio doctor` ladder-state check** — reads the state file +
  `os.kill(pid, 0)` liveness probe. Flags `in_flight + dead PID > 6h`
  as "stale crash", proposes `sio optimize-ladder` resume command in
  `fix_hint`.

### Fixed

- `optimize_cmd` rich `INSERT INTO optimized_modules` referenced two
  phantom columns (`active`, `module_name`) that never existed on the
  production schema. Every modern call silently fell to the minimal
  fallback INSERT, dropping `optimizer_name` / `score` / `task_lm` /
  `reflection_lm` to NULL. Runs #11-#15 in production showed this.
  Dropped the phantom refs; the rich INSERT now writes full attribution.
- 42 sites in `cli/main.py` hardcoded
  `os.path.expanduser("~/.sio/sio.db")` instead of honoring
  `SIO_DB_PATH`. Swept all to `os.environ.get("SIO_DB_PATH", ...)` so
  unit tests can run hermetically against `tmp_path` DBs.
- Removed `backfill_known_trainsets()` one-shot helper now that
  `register_dataset()` is wired into curate/amplify/optimize.

### Changed

- Project-level `.claude/hooks/stop.sh` and `.claude/hooks/task-completed.sh`
  deleted. The `dev-kid finalize` auto-commit was firing on every
  Claude Code stop event in this repo (where no dev-kid orchestration
  was active), overwriting commit messages with the generic
  `[CHECKPOINT] Session finalized - 130/130 tasks complete`. Three
  earlier-today commits (CHANGELOG v0.2.0 prep, P2 INSERT fix, curate
  auto-register) landed under that generic message before deletion.
  Proper fix queued as `prd/scratch/devkid_session_scope_gate_2026-05-17.md`
  (devkid hooks should check `.claude/devkid_active.lock` sentinel).

### Docs

- **`docs/SIO_PHILOSOPHY.md`** — "measured assist, not autonomous override"
  design stance. Cross-referenced from README. Anti-patterns explicitly
  not built (auto-deprecate, auto-promote, A/B-test rules, auto-retrain
  on outcomes) with rationale.

(PyPI publish remains queued — recreate a fresh PRD when it becomes the
active sprint goal. `sio_pypi_token_setup_2026-05-11` was resolved as an
empty template on 2026-05-17.)

## [0.2.0] — 2026-05-17

**Pipeline + observability release.** The scope grew past patch-level: instead
of v0.1.4 / .5 / .6 / .7 individual bumps, ~2 weeks of dense work landed as a
single minor cut. Headline: Principle XIII (Transparent Machine)
instrumentation, optimizer ladder climbed (Bootstrap → MIPROv2 → GEPA, new top
score 0.8653), full curate / amplify / optimize / promote-rule pipeline,
real `suggestion_quality_metric` (was trivially passing 1.0), and the
back-end loop closures from PRD `sio_backend_dead_loop_2026-05-15`.

### Added

#### Principle XIII — Transparent Machine (observability)
- `src/sio/core/runlog/` subsystem (~825 LOC): `@runlogged` decorator, RunLog /
  Stage writer (JSONL at `~/.sio/runs/<UTC>_<cmd>_<id>.jsonl`), Heartbeat
  helper, stdlib-logging bridge, tqdm progress hook, and `dspy_capture`
  monkey-patch on `dspy.LM.__call__` writing `<run>_dspy.jsonl` sidecars
  (prompt, completion, latency, tokens per call).
- 42+ CLI commands carry `@runlogged`. `sio runs` viewer surfaces per-stage
  timings and LLM call accounting.
- `sio doctor` DSPy-alive check (`_check_dspy_alive`) — distinguishes "module
  exists" from "module is actually trained and routes traffic".

#### promote-rule subsystem (5 commits — feature)
- `sio promote-rule` scaffold + `promoted_hooks` schema.
- DSPy-based detection-pattern extractor.
- Violating tool-call sample collector + display.
- Hook generator writes to `~/.claude/hooks/` and updates `settings.json`.
- Historical-violation verifier + coverage gate before promotion.

#### Install lifecycle (4 commits)
- `pre_install` + `post_install` lifecycle hooks on harnesses.
- Canonical DB bootstrap + per-platform DB initialization.
- `claude-code` `post_install` registers SIO hooks in `settings.json` and
  records platform_config metadata.
- Six install-orchestration regression test cases (`tests/install/`).

#### Back-end loop closures (from `sio_backend_dead_loop_2026-05-15`)
- `error_records.active_rules` column + `rules_snapshot` module — records
  which CLAUDE.md/rules were active when each error was captured.
- `sio velocity --by-rule` — per-rule error-rate deltas pre/post application
  (read-only diagnostic, needs ~2 weeks of organic rule churn for first signal).
- `sio differential-flows` — twin-flow finder; pairs success+failure flows
  with same goal-vector (269 twins on current DB). `export_positives_for_dataset_builder`
  fills the long-empty positive side of training datasets (807 positives).
- `sio analyze same-error` — normalizes error text + sha256 hashes + groups
  by signature. First run revealed the canonical Edit-bug pattern (220
  "File has not been read yet" occurrences across 160 sessions in 30 days).
- `sio promote-to-gold` CLI subcommand — promotes approved suggestions
  into `gold_standards` for optimizer training.
- `~/.claude/hooks/HOOKS_INVENTORY.md` + `~/.claude/hooks/disable-all-blocking`
  one-shot emergency unblock script.
- `retry-guard-pre.sh` now journals every `.bypass_next` consumption with
  timestamp + session_id to `~/.claude/hooks/retry-guard/state/bypass_consumed.log`
  — enables downstream classifier to distinguish user-authorized retries
  from genuine cognitive cascades.

#### Optimizer ladder (Bootstrap → MIPROv2 → GEPA)
- MIPROv2 baseline (run #13, score 0.7713) — never previously run.
- GEPA proper (runs #14, #15) — #15 hit **0.8653**, +28% over Bootstrap
  baseline #8 (0.6768).
- Per-role LM column scaffolding in `optimized_modules` (`task_lm`,
  `reflection_lm` — currently NULL, populated by Tier 2 in the
  optimizer_ladder PRD).
- Real `suggestion_quality_metric` (specificity + actionability +
  surface_accuracy, weighted) — replaces the trivial `1.0 if pred.rule_body
  else 0.0` that was wrapping the wrong signature.
- `_build_trainset` now branches on `module_name` and reads from
  `ground_truth` for `suggestion_generator` (where 26 positive-labeled
  rows already lived).

#### Suggest pipeline (multi-hop targeted search)
- `--strategy recluster` now performs **true sub-cluster decomposition**:
  collects theme-coherent errors from matching patterns and runs a tighter
  second clustering pass at `--recluster-threshold` (default 0.85). Resolves
  the design/implementation drift from `sio_ship_pickup_tomorrow_2026-05-02`
  §B7.
- CLI contract tests for `--recluster-threshold` + new help text.

#### Documentation distribution
- `docs/` directory bundled into the wheel via `pyproject.toml` hatch
  `force-include`. `sio init` stages docs into `~/.sio/docs/` idempotently.
  Offline-first; GH Pages remains the canonical online source.
- `docs/cookbook-2026-05-15.md` — new curate / amplify / optimize pipeline
  cookbook.
- 19-skill master `/sio` router + canonical rule entry wired in `_bootstrap/`.

#### Flow confidence + observability
- Flow confidence tiers re-calibrated (HIGH count≥20+rate≥40%, MEDIUM
  count≥10+rate≥20%). 17/17 LOW → 3 HIGH + 11+ MEDIUM + 2 LOW. Configurable
  via `SIO_FLOW_CONFIDENCE_HIGH/MEDIUM` env vars.

### Fixed
- `cycle_id` schema column on `datasets` + `suggestions` DDLs (PR #1 merged
  from `fix/datasets-suggestions-cycle-id`).
- Specstory parser `NameError` + date-rot in `tests/mining/test_mine.py`.
- `_record_optimization_run` deactivate sweep — was using non-existent
  `active` column; now uses `is_active`.

### Changed
- Test suite hardened post-Azure-removal: AST-based SC-022 scan (docstrings
  may mention `dspy.LM`), azure → unknown-provider fallback pin, harness
  `name` literal whitelist.
- README sweep — removed stale `sio install` references + the legacy
  "10 skills" count.
- `prds/` slug convention: `NNN-slug` → `prd-slug` (avoids SpecKit number
  collision).

### Removed
- Legacy `TestInstallerHooks` (module gone since v0.1.2).

## [0.1.3] — 2026-05-02

**Coverage fix release.** Ships the other 9 canonical skills that were
referenced by `rules/tools/sio.md` but missing from the v0.1.2 wheel.

### Added
- 9 canonical skills bundled into `_bootstrap/skills/`:
  `/sio-briefing`, `/sio-budget`, `/sio-codify-workflow`, `/sio-discover`,
  `/sio-feedback`, `/sio-promote-flow`, `/sio-validate`, `/sio-velocity`,
  `/sio-violations`. `iter_bootstrap_files()` now yields 20 files.

### Changed
- README sweep removes stale `sio install` references and the legacy
  "10 skills" count.

## [0.1.2] — 2026-05-02

**Install hardening release.** Closes five silent-failure paths surfaced by
two adversarial bug-hunter passes on v0.1.1.

### Added
- `sio doctor` subcommand — seven-check battery with copy-pasteable fix
  commands for each failure.
- `sio init --link-path` — explicit override for harness install location.

### Fixed
- **C2** — `iter_bootstrap_files()` raises `BootstrapMissingError` instead
  of silently yielding zero and printing "install complete — 0 changes."
- **C3** — Added `src/sio/_bootstrap/__init__.py` so `importlib.resources`
  resolves to a real subpackage and isn't shadowed by another `sio/` on
  `sys.path`.
- **R2** — `sio init` prints a yellow restart-Claude-Code banner so
  partners don't think slash commands missing live = install failed.
- **R3** — `sio init --harness claude-code` auto-creates `~/.claude/` on
  fresh boxes where Claude Code has never launched.

### Removed
- Legacy `sio install` command and `adapters/claude_code/installer.py`
  (C1). The legacy installer silently skipped every skill via
  `if not src.exists(): continue` and reported success. Replaced with a
  stub that raises `ClickException` pointing at `sio init`.

## [0.1.1] — 2026-05-02

**Fresh-install patch.** `sio init` now creates `~/.sio/` and seeds
`~/.sio/config.toml` before staging skills, so a fresh
`pip install` + `sio init` leaves the user with everything the suggestion
pipeline needs.

### Added
- `sio init` Step 0: creates `~/.sio/` and the `datasets/`, `previews/`,
  `backups/`, `ground_truth/`, `optimized/` subdirs.
- `~/.sio/config.toml` template seeded on first run, with all four LM
  provider blocks (OpenAI, Anthropic, Azure, local Ollama) commented out
  under a `# Quick start: uncomment ONE block` header.
- `$SIO_HOME` honored for tests / alternate-config setups.

### Fixed
- v0.1.0 produced "no LM available" because `~/.sio/config.toml` was
  never created. Failure mode of a fresh install is now a loud, clear
  error from `lm_factory`, not silent.

### Changed
- `sio init` is non-destructive: never clobbers an existing user-edited
  config.

## [0.1.0] — 2026-05-02

**First public release.** A closed-loop optimization layer for AI coding
agents: mines session transcripts for recurring failure patterns,
generates targeted improvement rules via DSPy, drops them back into the
harness's instruction file — idempotent, reversible, observable.

### Added
- `sio init` harness bootstrap for Claude Code (stubs for cursor /
  windsurf / opencode included).
- Suggestion pipeline: `sio scan` (mine errors), `sio suggest` (generate
  rules via DSPy), `sio review` / `sio apply` for human-in-the-loop.
- Multi-hop search with `--strategy filter|recluster|hybrid` and
  `--refine` terms (designed in graduated PRD `L003_sio_multi_hop_search`).
- `sio trend` pattern-cluster growth view.
- Distribution: `pip install git+https://github.com/gyasis/SIO.git@v0.1.0`
  or direct wheel install from release assets.

[Unreleased]: https://github.com/gyasis/SIO/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/gyasis/SIO/releases/tag/v0.2.0
[0.1.3]: https://github.com/gyasis/SIO/releases/tag/v0.1.3
[0.1.2]: https://github.com/gyasis/SIO/releases/tag/v0.1.2
[0.1.1]: https://github.com/gyasis/SIO/releases/tag/v0.1.1
[0.1.0]: https://github.com/gyasis/SIO/releases/tag/v0.1.0
