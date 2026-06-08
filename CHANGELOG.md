# Changelog

All notable changes to **self-improving-organism** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

GitHub release pages (with full asset downloads) live at
<https://github.com/gyasis/SIO/releases>.

## [Unreleased]

### Session Intelligence — absorb session-search + cross-agent, session-scoped analysis

Slated for the next minor (0.4.0). SIO absorbs the standalone `session-search`
tool and grows from an ambient corpus miner into a **targeted, cross-agent
session debugger**: point it at ONE session — regardless of which coding-agent
harness produced it — and search, mine, analyze, or live-watch it.

#### Added

- **`sio search`** — the standalone `session-search` tool, absorbed into SIO as a
  subcommand (one tool, one install). Cross-harness session search across Claude,
  Codex, Goose, OpenCode, Gemini, and Aider. Ships inside the wheel (stdlib-only),
  so `pip install` carries it to any machine. `session-search` remains a packaged
  deprecation-shim entry point that warns and forwards.
- **`--session <handle>` scoping** on `sio errors`, `sio suggest`, and `sio mine`.
  Accepts a canonical `agent:native_id` handle, a bare id, a session **file path**
  (as emitted by `sio search --files`), `-` to read the handle from **stdin**, or
  a **fuzzy partial** id (resolves if unambiguous, else lists candidates). Enables
  `sio search "x" --files | sio errors --session -`.
- **`sio watch --session <handle>`** — Phase B live watcher. Tails a session's
  events in real time (mtime-poll + append-read); `--from-start` replays then
  follows, `--tools-only` filters to tool calls. Claude implemented; other agents
  report an honest not-yet message.
- **`sio db backfill-sessions`** — non-destructive, idempotent migration of legacy
  bare session ids to canonical `claude:<id>` across all session-keyed tables.
  Schema-driven, auto-backup, `--dry-run`. (Applied to the maintainer's DB:
  923,137 rows.)
- **Cross-agent adapter layer** (`sio/adapters/`) — the EXTRACT contract:
  `SessionManifest`, `SessionEvent`, `SessionAdapter` Protocol; a real
  `ClaudeAdapter` (JSONL parse + live tail) and a `SearchBackedAdapter` that reuses
  the absorbed per-agent parsers so all six harnesses extract through one interface.
  `factory.adapter_for()` / `manifest_from_handle()` route by agent prefix.
- **`sio/core/session_handle.py`** — canonical `agent:native_id` "Session URI"
  helpers: `parse_handle`, `to_canonical`, `ensure_canonical`, transition-safe
  `session_match_clause`, path/stdin coercion.

#### Changed

- **Write path is now canonical** — `insert_invocation`, `insert_error_record`, and
  `insert_session_metrics` namespace `session_id` / `parent_session_id` to
  `claude:<id>` on write, keeping new rows consistent with the backfilled DB
  (idempotent; transition-safe matching reads both forms during any mix).
- `sio mine` — `--since` is now optional when `--session` targets a single session;
  a non-Claude `--session` routes through the adapter EXTRACT layer (Claude keeps
  its richer file-scan parser — no degradation).
- `get_error_records()` session filter upgraded to the transition-safe clause.

#### Notes / known limits

- Non-Claude error extraction is currently content-level (the search-backed
  adapters do not carry `tool_input`/`tool_output`), so deep `tool_failure`
  detection is Claude-only until `SessionEvent` is enriched.
- Non-Claude **live** watch and the migration of `~/.claude` `session-search`
  references → `sio search` (then sunsetting the standalone tool) are tracked
  follow-ups.

### Added — Experiment cohort primitive
- **Experiment cohort primitive (`sio experiment`)** — bookmark a named
  time window tagged with a config-hash snapshot (CLAUDE.md + active
  skills + rules + `~/.claude/settings.json` hooks), then analyze it A/B
  against a prior baseline. No manual debug instrumentation required —
  the existing hook/JSONL telemetry is auto-scoped to the window.
  - `sio experiment start NAME [--note --project]` — open a cohort,
    snapshotting the active config hash
  - `sio experiment status [NAME]` — show one cohort, or all open ones
  - `sio experiment list [--status --project]` — list cohorts (newest first)
  - `sio experiment close NAME [--report --format text|html|json --baseline 7d]`
    — close + generate an A/B report (error-rate delta per-hour normalized,
    new-error-class diff, flow emerged/died delta, scoped suggestions)
  - Scope filter `--experiment NAME` retrofitted onto `sio mine`,
    `suggest`, `trend`, `flows`, `velocity`
  - New tables `experiments` + `experiment_runs` (schema v5,
    `migrate_005_experiments`); backend in `src/sio/core/cohort/`
  - Docs: `docs/experiment-cohorts.md`
  - **Naming note:** distinct from the older git-worktree rule-testing
    "experiment" (`sio apply --experiment`, autoresearch). This is the
    telemetry-cohort concept; backend module is named `cohort` to avoid
    collision with `core/arena/experiment.py`.

### Search & Data-Sourcing Remediation (005-search-data-remediation, US1–US6)

#### Changed

- **`sio search` now defaults to recency-gated, newest-first output (US1 / FR-001).**
  The default window is 7 days (`--recent 7` implicit). Raw/text/JSONL output is
  sorted newest-first within each source. `--recent 0` and `--all` remain explicit
  full-history overrides. When the default window returns 0 results the tool emits
  a `"widen with --recent 0"` hint (FR-002). Zero regression for CMP callers
  (`--files`, `--count`) — ordering changes, result sets do not (SC-007).
- **`sio suggest` now bounded-loads the error DB (US4 / FR-007–009).**
  Default cap: 30-day window + 5 000-row maximum. `--since` and an explicit row cap
  override the bound and surface it in the log. The preview cache is size-bounded
  with a TTL check; `--use-cache` respects `--cache-ttl`.

#### Added

- **`--around N` context window on `sio search` (US2 / FR-003–004).** Given a hit,
  returns ±N turns around the matching turn — role-aware (user/assistant/tool), not
  raw lines (`rg -C`). Clamps gracefully at transcript start and end. Enables
  "forward-walk from a struggle hit to the subsequent fix turns" without a second
  search call. Kept orthogonal to the existing `--context N` (raw line context)
  and `--session <uuid>` (full-session dump).
- **Hop-2 cascade reachable from `sio search` (US3 / FR-005–006).** Flags
  `--refine <term>`, `--strategy filter|recluster|hybrid`, `--within <csv>`, and
  `--use-cache` are now surfaced on `sio search` (were previously `sio suggest`-only)
  via the shared `sio.clustering.hop2` module. A configurable `--noise-threshold N`
  triggers a non-blocking Hop-2 suggestion when Hop-1 returns more than N hits.
- **`recall --polish` is real (US5 / FR-010–013).** `sio recall --polish` now calls
  the configured LLM (default: free Ollama model; `--polish-model` to override) and
  emits cost before the call (cost-control.md compliant). Without a configured model
  it fails loudly instead of emitting a raw prompt string.
- **`sio search-discipline` report (US6 / FR-011–012).** Computes per-discipline
  rates (recency-first, multi-hop/refine, files-first, context-walk-back) from
  invocation telemetry over a configurable window. Sub-target rates are flagged in
  `sio briefing` as a regression signal (FR-012).

(Next minor will also add the DSPy callback-based unified
optimizer-progress emitter, JudgeVariants Tier 2 meta-optimization if
needed, and the distilabel attic move.)

## [0.3.1] — 2026-05-19

Documentation-only patch. Adds eight scenario-driven use-case walkthroughs
under `docs/use-cases/`, each generic and sanitized, showing how to wire
SIO's CLI surfaces into common day-to-day work. Index at
`docs/use-cases/README.md` sorts them by trigger and suggests reading order.

### Added
- `docs/use-cases/README.md` — index + reading order + common operational pattern
- `docs/use-cases/validating-a-config-change.md` — 3-phase before/after/steady-state loop
- `docs/use-cases/debugging-flaky-tool.md` — circuit-breaker recovery → recall → codify
- `docs/use-cases/pre-merge-pr-safety.md` — SIO as prior-art layer alongside `/adversarial-audit`
- `docs/use-cases/onboarding-to-codebase.md` — return-to-project / fresh-agent territory mapping
- `docs/use-cases/cost-performance-regression-hunt.md` — `sio trend` + behavior_invocations.db forensics
- `docs/use-cases/rule-lifecycle.md` — PROPOSED → ACTIVE → DECLINING → RETIRED lifecycle
- `docs/use-cases/cross-session-continuity.md` — `/compact` and `/clear` recovery loop
- `docs/use-cases/workflow-discovery-and-promotion.md` — positive-signal use case (`/sio-flows` → skill)

## [0.3.0] — 2026-05-18

Largest release since 0.1.0 — three Constitution articles supported in
code (XIV Optimizer Ladder Discipline, XVI Background Runs Resumability,
XVII Outcome Surfacing Mandatory), 56 real-work commits since v0.1.3,
eight new top-level CLI commands. The discipline + observability layer
shipped today (2026-05-18 paired-debate); the empirical validation of
the full ladder stack on Phase 1 graded-judge trainset (id=15) is
deferred to a separate session — see PRD
`sio_full_e2e_pipeline_verification_2026-05-18`.

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

### Added — Rule-outcome surfaces (Principle XIII observability + measured assist)

Three commands that turn `error_records.active_rules` (the rule-id
snapshot column added 2026-05-15) into actionable per-rule outcome
metrics. Aligned with `docs/SIO_PHILOSOPHY.md`: all three are math-backed
DECISION AIDS — no auto-action.

- **`sio velocity --by-rule`** — extended overview. Per-(rule_id,
  error_type, target_surface) breakdown with 7-day pre/post windows
  from `min(timestamp)` where rule_id first appears. Confidence tier
  (low/medium/high on n_after) + `recommend` text hint. `--json` flag
  preserved.
- **`sio rule-outcomes [<rule_id>] [--window N] [--since "N days"]`** —
  drill-down. Omit rule_id to list all; provide it for the per-rule
  Rich Panel (title resolved from `~/.claude/rules/<path>`, first-seen,
  target surface, by-type breakdown, confound-flagged sibling rules,
  plain-text "Verdict" line — informational only).
- **`sio rule-audit <rule_id> [--judge] [--samples N] [--yes]
  [--write-report]`** — deep dive. Deterministic-seed sample of
  before/after errors with text + session IDs. With `--judge`:
  cost-callout fires (per cost-control rule), `click.confirm()` or
  `--yes` required, then Gemini Flash scores "does the rule's
  prevention_instructions apply to this error?" Aggregates
  applicability percentage. Optional audit report at
  `~/.sio/audits/<sha1[:10]>_<ts>.md`.

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

### Added — Phase 1 paired-debate amplify (2026-05-18)

- **Generator preserves domain** via `_extract_domain_keywords` — an
  HH-domain lexicon (athenahealth, dbt, databricks, cube, zeno, ccm,
  careplan, raf, hcc, etc.) with generic-tech fallback (k8s, docker,
  postgres, etc.). Closes the mode-collapse-to-salesforce/s3/bigquery
  failure observed in trainset id=10.
- **Graded judge** with 5-tier rubric anchors:
  - 1.0 GOLD (category + domain preserved)
  - 0.7 SILVER (category preserved, domain drifted)
  - 0.5 BRONZE (sibling category)
  - 0.2 DRIFT (different category)
  - 0.0 HALLUCINATION (smoke-test anchor for prompt injection /
    malformed output)
  Synthetic-Extremes pattern — no manual labeled corpus needed.
- **`[JUDGE_CALIBRATION_WARN]` / `_OK`** post-run signal in stderr when
  score distribution collapses to binary (was today's silent-failure
  mode that today's session caught and fixed).
- **Diversity filter** via fastembed cosine similarity ≥0.95 — per-source-row
  dedup, keeps highest-judge-score in each cluster. Toggle via
  `--no-diversity-filter`, threshold via `--diversity-threshold 0.95`.
- Verified on 454-variant smoke test (2026-05-18): 5-bucket distribution
  (1.0/0.9/0.8/0.7/0.6), was bimodal pre-fix.

### Added — Live optimizer progress (2026-05-18 black-box fix)

- **GEPA live-progress** in heartbeat lines:
  `[HB ... gepa_iter=17 iter_score=0.6900 best_valset=0.7262 trend=↑]`.
  Per-iteration `Selected program score` parsed via Python `logging`
  handler hooked into `dspy.teleprompt.gepa.gepa`. No more black-box
  "wait for the end-of-run number." 10-entry score_history deque for
  trend (last vs 3-back, ±0.005 threshold).
- **MIPRO live-progress** (regex stopgap):
  `[HB ... mipro_trial=3/10 trial_score=0.7540 best_trial=0.7540 trend=↑]`.
  Same Python logging hook for `== Trial N / M ==` and `Best score so far`
  lines, with %→0-1 ratio conversion. Proper DSPy callback-based emitter
  PRD'd in `sio_unified_optimizer_emitter_2026-05-18.md` for v0.4.0.
- **`sio gepa-status` CLI** — agent + operator readable live state.
  Renders active optimizer block (GEPA or MIPRO), score history (last 10),
  trend arrow, parse_err / truncation counters, plus any T1-T4 abort
  warnings already emitted. `--watch` flag re-prints every 5s.
- **`stage.gepa_snapshot`** stashed in runlog JSON each heartbeat tick
  so external readers (CLI, agent) see same data as stderr.

### Added — Abort tiers (Article XIII clause 8 — Loud Failure)

- **T1 iter-idle 8min** → WARN one-shot (`GEPA_ITER_STALL_WARN`)
- **T2 iter-idle 15min** → CRITICAL ABORT signal (`GEPA_ITER_STALLED_CRITICAL`)
- **T3 ≥3 AdapterParseError / 5min** → CRITICAL ABORT signal
  (`GEPA_ADAPTER_PARSE_STREAK`) — catches task-LM emitting malformed outputs
- **T4 ≥3 max_tokens truncations / 5min** → CRITICAL ABORT signal
  (`GEPA_TRUNCATION_STREAK`) — catches token-cap walls
- **T5 reflection-stuck 40min** (existing backstop, kept) →
  `REFLECTION_STUCK_CRITICAL`
- Operator-decides philosophy — no auto-SIGTERM from daemon thread (Python
  signals across threads are fragile). All warnings appear in run.warns +
  stderr `[CRITICAL]` line. Today's stuck GEPA at iter 17 would have hit
  T3/T4 within seconds vs T5's 40-min backstop.

### Added — Compound command discipline + --rungs

- **`sio optimize-ladder --rungs <subset>`** — comma-separated rung filter.
  Express lane: `--rungs bootstrap` runs only Bootstrap (~1 min, $0.01).
  Validates unknown rung names with allowed-list error. Skips amplify
  when no downstream rung needs it. Idempotent: skips rungs that already
  have scored rows in `optimized_modules`.
- **LADDER_VERDICT auto-emission** at ladder completion. Verdicts:
  - `gepa_justified` — GEPA - MIPRO ≥ 0.03 → ship GEPA
  - `mipro_wins_on_economics` — within 0.03 → ship MIPRO (30× cheaper)
  - `both_fail` — neither passes bars → fix trainset upstream
  - `gepa_no_score` — GEPA aborted/stuck → ship MIPRO
  - `mipro_dead_weight` (warning overlay) — MIPRO < Bootstrap
- **Per-rung scores in ladder_status.json** (Article XVII compliance) —
  pulled from `optimized_modules` after each rung. Includes
  `optimized_module_id`, `task_lm`, `reflection_lm` for full attribution.

### Added — Token-length audit fixes (2026-05-18)

- `amplify.py` gen LM: `max_tokens` 4000 → **6000** for content-heavy
  patterns with high n_per_row.
- `amplify.py` judge LM: `max_tokens` 500 → **2000** (fixes silent-bypass
  bug where Gemini-Flash truncated `scores_json` mid-array, falling
  through to placeholder 0.5 for all variants).
- `classifier.py` Gemini-Flash classifier: `max_tokens` 500 → **1000**
  (same bug class).
- `refiner.py` Anthropic + OpenAI refinement: `max_tokens` 300 → **1500**
  (refiner writes rules DIRECTLY to CLAUDE.md; truncated rules would ship).
- LOUD warnings `[REFINER_FALLBACK_*]` on the 3 fallback paths (was silent
  `logger.debug` only).

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
