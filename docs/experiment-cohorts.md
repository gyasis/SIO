# Experiment Cohorts (`sio experiment`)

Bookmark a window around a config/prompt/tooling change, then measure how
it affected your agent — error rates, error classes, and tool-flow
patterns — A/B against a prior baseline. **No debug instrumentation
required:** SIO already captures every tool call (hook DB) and every
session (JSONL); this feature just *labels* a slice of that stream so you
can talk about it cleanly.

> **Naming caution.** SIO has two unrelated things called "experiment":
> - **`sio experiment` (this doc)** — a *telemetry cohort*: a named time
>   window for A/B measuring a config change. Backend: `src/sio/core/cohort/`.
> - **`sio apply --experiment` / `sio autoresearch`** — the older
>   *git-worktree* concept: test a candidate rule on an isolated branch
>   before promoting. Backend: `src/sio/core/arena/experiment.py`.
>
> The `--experiment NAME` scope flag refers to the **cohort**.

---

## The problem it solves

You change your system prompt / CLAUDE.md, add a skill, or wire a new
hook, and you want clean data on whether it helped — *without* hand-rolling
debug scripts or remembering the exact date and grep tokens later.

The raw telemetry is already there. What was missing was a first-class way
to say *"this dataset is about my new thing,"* snapshot the config it ran
under, and diff it against how things looked before. That's the cohort.

---

## Lifecycle

```bash
# 1. Open a cohort right before the change goes live.
#    Snapshots a sha256 of CLAUDE.md + active skills + active rules +
#    ~/.claude/settings.json hooks, so later you can tell whether config drifted.
sio experiment start "sysprompt-v2" --note "skills-first layout" --project SIO

# 2. Just work. Every tool call / error / flow in the window is auto-scoped
#    to the cohort by timestamp — nothing else to do.

# 3. Check on it any time.
sio experiment status sysprompt-v2          # one cohort
sio experiment status                        # all open cohorts
sio experiment list                          # everything, newest first

# 4. Close it and get the A/B report vs the prior baseline.
sio experiment close "sysprompt-v2" --report --baseline 7d
sio experiment close "sysprompt-v2" --report --format html   # writes ~/.sio/reports/
sio experiment close "sysprompt-v2" --report --format json   # pipeable
```

Multiple cohorts can be open at once (e.g. `sysprompt-v2` and
`new-tool-x` overlapping) — they're joined by window, not serialized.

---

## The A/B report

`close --report` compares the **experiment window** (`start_ts → close_ts`)
against a **baseline window** (the `--baseline` span immediately before
`start_ts`; default `7d`, also accepts `14d`, `48h`, `2w`). Four sections:

| Section | What it shows |
|---|---|
| **Error-rate delta** | Errors-per-hour in each window (normalized — windows are rarely equal length) and the % change. A negative delta means fewer errors per hour under the change. |
| **New error classes** | `error_type`s present in the experiment window but **not** in the baseline — regressions the change may have introduced. |
| **Flow delta** | Tool-sequence flows that **emerged** (new positive patterns) or **died** (stopped happening) between the windows. |
| **Scoped suggestions** | Existing suggestions whose underlying errors fall inside the window. *(Does not invoke the DSPy generator — report generation stays cheap. Run `sio suggest --experiment NAME` for fresh ones.)* |

Three formats via `--format`:
- `text` (default) — printed to the terminal
- `html` — written to `~/.sio/reports/experiment_<NAME>.html` (mirrors the `sio report` dark theme)
- `json` — emitted to stdout, pipeable (no human preamble)

---

## Scope filter — `--experiment NAME`

Any of these commands can be narrowed to a cohort's window:

```bash
sio mine     --experiment sysprompt-v2     # mine just that window (resolves --since for you)
sio suggest  --experiment sysprompt-v2 --grep 'tool,error'   # composable with --grep/--type
sio trend    --experiment sysprompt-v2     # bucket only in-window errors
sio flows    --experiment sysprompt-v2     # flows within the window (honors close_ts)
sio velocity --experiment sysprompt-v2     # velocity over the exact window (not rolling-from-now)
```

For an **open** cohort the window end is "now"; for a **closed** one it's
the recorded `close_ts`. (`sio velocity --experiment` is mutually exclusive
with `--by-rule`.)

---

## Data model

| Table | Columns | Notes |
|---|---|---|
| `experiments` | `name (unique)`, `start_ts`, `close_ts`, `note`, `config_hash`, `project`, `status` | One row per cohort. `status ∈ {open, closed}`. |
| `experiment_runs` | `event_id`, `experiment_name`, `source_table` | Join table. Events are tagged by **timestamp window at query time** — the source tables (`behavior_invocations`, `error_records`, `flow_events`, …) are never mutated in place. |

Schema is brought up by `migrate_005_experiments()` (wired into
`src/sio/core/db/bootstrap.py`); `init_db()` also creates the tables on
fresh installs.

### Config-hash snapshot

At `start`, SIO hashes a JSON manifest of four behavior-affecting surfaces:
`~/.claude/CLAUDE.md`, the `skills/` tree, the `rules/` tree, and the
`hooks` block of `~/.claude/settings.json`. Two cohorts with the same
`config_hash` ran under identical config; a hash diff is a structural
signal that something the agent reads changed. (Implementation:
`src/sio/core/cohort/snapshot.py`.)

---

## Backend layout (`src/sio/core/cohort/`)

| Module | Role |
|---|---|
| `models.py` | `Experiment` / `ExperimentRun` dataclasses |
| `snapshot.py` | config-hash snapshotter |
| `store.py` | create / get / list / close persistence |
| `window.py` | `resolve_experiment_window(name)` — shared by every scope-filtered command |
| `report.py` | A/B report engine (error-rate / new-class / flow / suggestions) |
| `render_text.py` / `render_html.py` / `render_json.py` | the three report renderers |

---

## Worked example

```bash
# Before flipping on a new "skills-first" CLAUDE.md layout:
sio experiment start "skills-first" --note "moved tool rules to Tier 2" --project SIO

# ...a few days of normal work...

sio experiment close "skills-first" --report --baseline 7d
# → error-rate: 0.31/h (exp) vs 0.48/h (baseline)  ↓ -35%
#   new error classes: (none)
#   flow delta: + Read→Edit→Bash (12×) emerged
#   scoped suggestions: 2 pending
```

A negative error-rate delta with no new error classes is the signal you
want: the change reduced friction without introducing regressions.
