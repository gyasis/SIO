# SIO CLI Reference

> Auto-generated from `sio --help` and each subcommand's `--help`.
> Regenerate with `docs/gen_cli_reference.sh`. SIO version: sio, version 0.3.1

## Top-level

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio [OPTIONS] COMMAND [ARGS]...

  SIO: Self-Improving Organism for AI coding CLIs.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  amplify               Amplify a curated JSONL by synthesizing N...
  analyze               Read-only diagnostics over the mined corpus.
  apply                 Apply an approved suggestion to its target file.
  approve               Approve a suggestion by ID and promote to ground...
  autoresearch          Autoresearch pipeline — automated suggestion...
  briefing              Show or refresh the session-start briefing of...
  budget                Show instruction budget usage per file.
  changes               List applied changes and their status.
  collect-recall        Collect a recall example for training.
  config                View and test LLM configuration.
  costs                 Cost transparency commands (Principle XII).
  curate                Produce a curated training dataset (JSONL +...
  datasets              Manage pattern datasets.
  db                    Database schema management commands.
  dedupe                Find and consolidate semantically duplicate rules.
  differential-flows    Find twin flows (same sequence, both success and...
  discover              Discover skill candidates from mined patterns and...
  distill               Distill a long session into a clean playbook of...
  doctor                Diagnose `sio` install / config problems.
  errors                Browse mined errors with optional type and...
  experiment            Cohort tagging — bookmark a config window and...
  export                Export telemetry data.
  export-dataset        Export structured training datasets for DSPy/ML.
  flows                 Discover recurring positive tool sequence patterns.
  gepa-status           Show live GEPA progress for the most recent...
  ground-truth          Manage agent-generated ground truth for DSPy...
  health                Show per-skill health metrics.
  init                  Stage SIO's bundled skills and rules into your AI...
  install               [REMOVED] Use `sio init` instead.
  live                  Discover and read in-progress (live) coding-agent...
  mine                  Mine recent sessions for errors and failures.
  multi-train           Fire N optimize runs in parallel, one per surface...
  optimize              Run prompt optimization against the...
  optimize-ladder       Run the full optimizer ladder (Bootstrap →...
  optimize-suggestions  Optimize the suggestion module using ground truth...
  patterns              Show discovered error patterns ranked by importance.
  promote-flow          Promote a flow pattern to a Claude Code skill file.
  promote-positives     Promote positive_records to...
  promote-rule          Promote a violated CLAUDE.md rule into a runtime...
  promote-to-gold       Promote behavior_invocations to gold_standards...
  purge                 Purge old telemetry records from the main SIO...
  recall                Recall how a specific task was solved in a...
  reject                Reject a suggestion by ID.
  render                Render an optimized DSPy module as a skill /...
  report                Generate a session report (terminal or HTML).
  reproduce             Show the exact sio optimize command that produced...
  review                Batch-review unlabeled invocations.
  rollback              Rollback an applied change by ID.
  rule-audit            Audit a single rule with concrete error samples...
  rule-outcomes         Per-rule outcomes drill-down (PRD Surface 2).
  runs                  Inspect SIO per-invocation run logs.
  schedule              Manage passive analysis schedule.
  search                Search coding-agent session history across all...
  search-discipline     Report search-discipline rates from invocation...
  status                Show 5-section SIO pipeline health status.
  suggest               Run the full pipeline: cluster -> persist ->...
  suggest-review        Review pending improvement suggestions...
  train                 Train DSPy modules on exported datasets.
  trend                 Show growth / decline of pattern clusters over time.
  velocity              Show learning velocity trends — how error rates...
  violations            Show detected rule violations (existing rules the...
  watch                 Watch a session live — tail events as they happen...
```

## Commands

### `sio amplify`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio amplify [OPTIONS]

  Amplify a curated JSONL by synthesizing N variants per row.

  Each input row is passed through Gemini Flash with a "preserve the category"
  prompt to generate variants that vary surface features (paths, tool names,
  phrasing) while keeping the same pattern_id. An LLM-as-judge filter drops
  variants that drift to a different category.

  Output is a JSONL that includes the originals AND the synthesized variants —
  can be consumed directly by ``sio optimize --trainset-file``.

Options:
  -i, --input TEXT                Input JSONL produced by `sio curate`.
                                  [required]
  -o, --output TEXT               Output JSONL path. Defaults to
                                  ~/.sio/amplified/<input>_amplified.jsonl.
  -n, --n-per-row INTEGER         Synthetic variants to generate per input
                                  row.  [default: 10]
  --min-judge-score FLOAT         Drop variants whose LLM-judge score is below
                                  this.  [default: 0.6]
  --max-workers INTEGER           Thread-pool parallelism for LLM calls.
                                  [default: 8]
  --task-mode [work|cheap|free|personal|personal-strong]
                                  LM tier for amplification generation.
                                  Defaults to whatever [llm.task] in
                                  ~/.sio/config.toml resolves to. cheap=Flash
                                  (recommended), work=Pro, free=Ollama,
                                  personal=gpt-4o-mini, personal-strong=gpt-5.
  --budget-override FLOAT         Override 24h spend cap for this invocation
                                  (XII clause 6).
  --no-diversity-filter           Disable cosine-similarity de-duplication of
                                  variants (Step 4 of 2026-05-18 paired-
                                  debate). Default: ENABLED.
  --diversity-threshold FLOAT     Cosine similarity above which variants from
                                  the same source row are deduplicated. Lower
                                  = more aggressive.  [default: 0.95]
  --help                          Show this message and exit.
```

### `sio analyze`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio analyze [OPTIONS] COMMAND [ARGS]...

  Read-only diagnostics over the mined corpus.

Options:
  --help  Show this message and exit.

Commands:
  same-error  Find error signatures repeated >= N times across sessions.
```

### `sio apply`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio apply [OPTIONS] [SUGGESTION_ID]

  Apply an approved suggestion to its target file.

  Checks the instruction budget before applying. Uses delta-based writing
  (merge if >80% similar to an existing rule). If the budget is near capacity,
  triggers automatic consolidation.

  --no-backup is NOT supported (raises BackupRequired). Backups are mandatory
  for safety and cannot be disabled.

  Examples:     sio apply 5                 # Normal apply with budget check
  sio apply 5 --force         # Skip budget check     sio apply 5 --experiment
  # Apply on experiment branch     sio apply --rollback 42     # Roll back
  applied change #42     sio apply 5 --merge         # Consent to merge with
  similar rule     sio apply 5 --yes           # Skip confirmation prompt

Options:
  --experiment                    Apply on experiment branch instead of main.
  --force                         Skip budget check (not recommended).
  --rollback INTEGER              Roll back an applied change by its ID (from
                                  applied_changes table).
  --merge                         Explicit consent to merge with a similar
                                  existing rule (FR-024).
  -y, --yes                       Skip interactive confirmation prompt.
  --no-backup                     [NOT SUPPORTED] Backups are required for
                                  safety; this flag is rejected.
  --auto-threshold FLOAT          BULK MODE: auto-apply ALL pending
                                  suggestions with confidence >= this
                                  threshold. Cannot combine with a positional
                                  SUGGESTION_ID. Recommended: 0.9 for
                                  conservative auto-apply.
  --skip-dupes / --no-skip-dupes  (With --auto-threshold) skip suggestions
                                  whose target rule duplicates an existing one
                                  in ~/.claude/rules/. Reuses sio dedupe logic
                                  at threshold 0.85.  [default: skip-dupes]
  --help                          Show this message and exit.
```

### `sio approve`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio approve [OPTIONS] SUGGESTION_ID

  Approve a suggestion by ID and promote to ground truth.

Options:
  -n, --note TEXT  Optional note.
  --help           Show this message and exit.
```

### `sio autoresearch`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio autoresearch [OPTIONS] COMMAND [ARGS]...

  Autoresearch pipeline — automated suggestion evaluation and scheduling.

Options:
  --help  Show this message and exit.

Commands:
  install-schedule  Install the autoresearch recurring schedule (cron or...
  run-once          Evaluate active suggestions once and record outcomes...
```

### `sio briefing`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio briefing [OPTIONS]

  Show or refresh the session-start briefing of actionable SIO insights.

  Default: read the pre-computed store (instant — this is what session-start
  hooks do).  ``--refresh`` materialises the store off-session; the systemd
  user timer runs ``sio briefing --refresh --if-idle``.

Options:
  --json     Output as JSON.
  --refresh  Recompute the briefing and write the off-session store
             (timer/scheduler path).
  --if-idle  With --refresh: only refresh when the user is idle or the store
             is too stale.
  --live     Force a live compute instead of reading the store (debugging).
  --help     Show this message and exit.
```

### `sio budget`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio budget [OPTIONS]

  Show instruction budget usage per file.

  Scans CLAUDE.md and supplementary rule files, counting meaningful lines
  (non-blank, non-comment) and comparing against the configured caps (default:
  100 for CLAUDE.md, 50 for supplementary files).

  Examples:     sio budget                          # All tracked files
  sio budget --file ~/.claude/CLAUDE.md   # Specific file

Options:
  --file TEXT  Check specific file only.
  --help       Show this message and exit.
```

### `sio changes`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio changes [OPTIONS]

  List applied changes and their status.

Options:
  --help  Show this message and exit.
```

### `sio collect-recall`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio collect-recall [OPTIONS] QUERY

  Collect a recall example for training.

  This is the data collection step: distill a session, optionally attach a
  Gemini-polished runbook, and store as a training example.

  The pipeline: collect → (optional: LLM polish) → label → train

  Examples:     sio collect-recall "dbt setup" --project dev     sio collect-
  recall "dbt setup" --runbook polished.md --label positive

Options:
  --session TEXT                  Session JSONL path.
  --project TEXT                  Filter by project name.
  --runbook TEXT                  Path to polished runbook (from Gemini).
  --label [positive|negative|pending]
                                  Quality label for this example.
  --help                          Show this message and exit.
```

### `sio config`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio config [OPTIONS] COMMAND [ARGS]...

  View and test LLM configuration.

Options:
  --help  Show this message and exit.

Commands:
  show  Display current LLM configuration.
  test  Test LLM connectivity with a simple query.
```

### `sio costs`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio costs [OPTIONS] COMMAND [ARGS]...

  Cost transparency commands (Principle XII).

Options:
  --help  Show this message and exit.

Commands:
  estimate  Pre-flight cost band for a hypothetical optimize run.
  summary   Show LLM spend summary from ~/.sio/usage.log.
```

### `sio curate`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio curate [OPTIONS]

  Produce a curated training dataset (JSONL + preview .md).

  Wraps the filter chain in ``sio.curate``. Outputs a JSONL of canonical
  PatternToRule dspy.Example shapes plus a Markdown preview with row count,
  category distribution, and 10 sample rows.

  The curated file is consumed by ``sio optimize --trainset-file <path>``.

Options:
  --since TEXT                    Time window: "7 days", "30 days", or ISO
                                  date.  [default: 7 days]
  --emphasis                      Require !! or ?? in user_message
                                  (frustration markers).
  --classified                    Require pattern_id NOT NULL (skip
                                  unclassified records).
  --pattern TEXT                  Exact pattern_id slug to filter on.
  --pattern-prefix TEXT           LIKE prefix for pattern_id (e.g.
                                  tool_failure__).
  --error-type TEXT               Restrict to error_type(s). Repeat flag for
                                  multiple.
  --exclude-corrections / --include-corrections
                                  Drop user_correction rows.  [default:
                                  exclude-corrections]
  --exclude-cascade / --include-cascade
                                  Drop cascade-failure rows.  [default:
                                  exclude-cascade]
  --has-positive-recovery         Require a positive_records event within
                                  --recovery-window-seconds.
  --recovery-window-seconds INTEGER
                                  [default: 600]
  --limit INTEGER                 Max rows to emit (DESC by timestamp; newest
                                  first).
  -o, --output TEXT               Output JSONL path. Defaults to
                                  ~/.sio/curated/<timestamp>.jsonl.
  --help                          Show this message and exit.
```

### `sio datasets`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio datasets [OPTIONS] COMMAND [ARGS]...

  Manage pattern datasets.

Options:
  --help  Show this message and exit.

Commands:
  collect  Collect targeted dataset from specific criteria.
  inspect  Inspect dataset for a specific pattern.
```

### `sio db`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio db [OPTIONS] COMMAND [ARGS]...

  Database schema management commands.

Options:
  --help  Show this message and exit.

Commands:
  backfill-sessions  Backfill legacy bare session ids to canonical...
  migrate            Apply any pending schema migrations to the SIO...
  repair             Mark stuck 'applying' migration rows as 'failed'.
```

### `sio dedupe`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio dedupe [OPTIONS]

  Find and consolidate semantically duplicate rules.

  Scans all instruction files (CLAUDE.md + rules/) for rule blocks that are
  semantically similar above the threshold. Shows duplicate pairs with
  proposed merges.

  Examples:     sio dedupe                          # Default: threshold 0.85
  sio dedupe --threshold 0.80         # Lower threshold     sio dedupe --dry-
  run                # Show without applying     sio dedupe --auto
  # Apply all without prompts

Options:
  --threshold FLOAT  Similarity threshold (default: 0.85).
  --dry-run          Show proposals without applying.
  --auto             Apply all proposals without confirmation.
  --help             Show this message and exit.
```

### `sio differential-flows`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio differential-flows [OPTIONS]

  Find twin flows (same sequence, both success and failure outcomes).

  Outputs paired success/failure samples by flow_hash. The differential is the
  cheapest training signal SIO can produce — no LLM call required.

  With --positives-for-builder: emit only successful rows in canonical
  PatternToRule shape so the existing dataset_builder can append them as
  positive examples (T1.V.3).

  Examples:     sio differential-flows     sio differential-flows --min-
  success 5 --per-cohort 10     sio differential-flows --positives-for-builder

Options:
  --min-success INTEGER    Minimum successful events per flow_hash to qualify
                           as a twin.  [default: 3]
  --min-failure INTEGER    Minimum failed events per flow_hash to qualify as a
                           twin.  [default: 3]
  --per-cohort INTEGER     Samples drawn from each cohort (success / failure)
                           per twin.  [default: 5]
  --max-hashes INTEGER     Cap the number of twin-hashes processed (debug).
  -o, --output TEXT        Output JSONL path. Default:
                           ~/.sio/differential/<ts>.jsonl.
  --positives-for-builder  Instead of paired-cohort JSONL, emit FLAT positive
                           examples (one per successful sample) in the shape
                           consumed by src/sio/export/dataset_builder.py.
                           Wires T1.V.3 — populates the long-empty positive
                           side of training datasets.
  --help                   Show this message and exit.
```

### `sio discover`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio discover [OPTIONS]

  Discover skill candidates from mined patterns and flows.

  Cross-references error patterns with positive flow events to find candidates
  worth promoting to Claude Code skills.

  Candidate types:     tool-specific     -- Concentrated on a single tool
  (e.g. "Edit safety")     workflow-sequence  -- Recurring multi-tool flows
  (e.g. "Read -> Edit -> Test")     repo-specific     -- Patterns unique to a
  specific project

  Examples:     sio discover     sio discover --repo /home/user/myproject
  sio discover --format json

Options:
  --repo TEXT            Repository path for repo-specific pattern detection.
  --format [table|json]  Output format (default: table).
  --help                 Show this message and exit.
```

### `sio distill`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio distill [OPTIONS] [SESSION_PATH]

  Distill a long session into a clean playbook of winning steps.

  Takes a messy exploratory session and extracts just the steps that worked,
  removing failures, retries, and dead ends. Outputs a numbered playbook.

  Examples:     sio distill --latest                          # Most recent
  session     sio distill --latest --project jira-issues    # Most recent for
  project     sio distill /path/to/session.jsonl            # Specific session
  file     sio distill --latest -o playbook.md           # Save to file

Options:
  --latest           Distill the most recent JSONL session.
  -o, --output TEXT  Save playbook to file (default: print to stdout).
  --project TEXT     Filter latest session by project name.
  --help             Show this message and exit.
```

### `sio doctor`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio doctor [OPTIONS]

  Diagnose `sio` install / config problems.

  Runs a battery of checks (Python version, package collision, PATH
  visibility, ~/.sio/ data dir, config.toml, bundled bootstrap content,
  harness install state) and prints a color-coded report. Each problem
  detected comes with a one-line fix command. Exits 0 if everything is OK, 1
  if any errors were found.

Options:
  --help  Show this message and exit.
```

### `sio errors`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio errors [OPTIONS]

  Browse mined errors with optional type and content filters.

Options:
  --type [tool_failure|user_correction|repeated_attempt|undo|agent_admission]
                                  Filter by error type.
  -n, --limit INTEGER             Max errors to show.
  -g, --grep TEXT                 Search content for keyword(s). Comma-
                                  separated for OR logic (e.g.
                                  'placeholder,hardcoded,stub').
  --project TEXT                  Filter by project name (substring match on
                                  source path).
  --exclude-type TEXT             Exclude error types. Comma-separated (e.g.
                                  'repeated_attempt,tool_failure').
  --session TEXT                  Scope to ONE session by handle
                                  (agent:native_id, e.g. claude:<uuid>; a bare
                                  id is assumed claude). Matches legacy and
                                  canonical forms. Pipe from search: `sio
                                  search ... --files`.
  --help                          Show this message and exit.
```

### `sio experiment`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio experiment [OPTIONS] COMMAND [ARGS]...

  Cohort tagging — bookmark a config window and analyze it.

  Subcommands: ``start``, ``status``, ``list``, ``close``.

Options:
  --help  Show this message and exit.

Commands:
  close   Close NAME (stamp close_ts → flip status='closed').
  list    List every experiment (newest first).
  start   Open a new experiment cohort named NAME.
  status  Show details for an experiment NAME (or all open experiments).
```

### `sio export`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio export [OPTIONS]

  Export telemetry data.

Options:
  --platform TEXT      Platform filter.
  --format [json|csv]  Export format.
  -o, --output TEXT    Output file path.
  --help               Show this message and exit.
```

### `sio export-dataset`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio export-dataset [OPTIONS]

  Export structured training datasets for DSPy/ML.

  Generates labeled training data from mined sessions: - routing: (user_query,
  tool_choice) pairs - recovery: (error, fix_applied, success) triples - flow:
  (current_state, next_tools) sequence predictions - all: exports all three
  types

  Examples:     sio export-dataset --task routing     sio export-dataset
  --task all --format parquet     sio export-dataset --task recovery --since
  "30 days" -o ./data/recovery.jsonl

Options:
  --task [routing|recovery|flow|all]
                                  Dataset type to export.  [required]
  --since TEXT                    Time window: "7 days", "14 days", "30 days".
  --format [jsonl|parquet]        Output format.
  -o, --output TEXT               Output file path (default:
                                  ~/.sio/datasets/<task>_<date>.<fmt>).
  --help                          Show this message and exit.
```

### `sio flows`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio flows [OPTIONS]

  Discover recurring positive tool sequence patterns.

  Analyzes JSONL session transcripts to find tool sequences that consistently
  lead to successful outcomes. No LLM required — pure regex + sequence
  matching.

  Examples:     sio flows                         # Default: 14 days, min 3
  occurrences     sio flows --since "7 days"        # Last week     sio flows
  --min-count 5           # Only frequent patterns     sio flows --no-mine
  # Skip mining, query existing data     sio flows --experiment sysprompt-v2
  # Scope to a cohort window

Options:
  --since TEXT              Time window: "7 days", "14 days", "30 days".
  --project TEXT            Filter by project name.
  --min-count INTEGER       Minimum occurrence count to show a flow.
  --limit INTEGER           Maximum number of flows to display.
  --mine-first / --no-mine  Mine flow data before querying (default: yes).
  --experiment TEXT         Scope flows to the cohort window of the named
                            experiment.
  --help                    Show this message and exit.
```

### `sio gepa-status`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio gepa-status [OPTIONS]

  Show live GEPA progress for the most recent in-flight optimize run.

  Reads the latest runlog JSON, surfaces:   - current iteration + best valset
  score   - per-iteration score history (last 10)   - iter idle time,
  parse_err / truncation counters   - critical-tier warnings already fired

  Origin: 2026-05-18 paired-debate. Lets the agent answer "where are we?" mid-
  run without grepping stderr or guessing.

Options:
  --watch  Re-print every 5s until process ends.
  --help   Show this message and exit.
```

### `sio ground-truth`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio ground-truth [OPTIONS] COMMAND [ARGS]...

  Manage agent-generated ground truth for DSPy training.

Options:
  --help  Show this message and exit.

Commands:
  generate  Generate ground truth candidates from discovered patterns.
  review    Interactive review of pending ground truth candidates.
  seed      Seed ground truth with representative examples covering all...
  status    Show ground truth statistics.
```

### `sio health`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio health [OPTIONS]

  Show per-skill health metrics.

Options:
  --platform TEXT        Platform filter.
  --skill TEXT           Skill name filter.
  --format [table|json]  Output format.
  --help                 Show this message and exit.
```

### `sio init`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio init [OPTIONS]

  Stage SIO's bundled skills and rules into your AI coding harness.

  By default, copies the package's bootstrap content (skills, tool rules) into
  the user's harness config directory (e.g., ~/.claude/) using a sidecar
  manifest to track managed files. Re-running is idempotent. User-modified
  files are preserved unless --force is set.

  Examples:
      sio init                    # auto-detect harness, install
      sio init --dry-run          # preview only
      sio init --status           # what's installed where
      sio init --uninstall        # remove SIO-managed files
      sio init --harness claude-code --force

Options:
  --harness TEXT  Target harness (claude-code, cursor, windsurf, opencode). If
                  omitted, auto-detects every harness installed on this
                  system.
  --dry-run       Preview the file changes without writing anything.
  --force         Overwrite user-modified files (default: skip + report
                  drift).
  --uninstall     Remove SIO-managed assets instead of installing.
  --status        Show what's installed vs what the package ships, without
                  changing anything.
  --link-path     Append a managed `export PATH=...` block to the user's shell
                  rc file (~/.zshrc, ~/.bashrc, etc.) so the `sio` binary is
                  reachable from subprocesses with sanitized environments
                  (e.g., the Bash tool inside Claude Code). Skipped on
                  --status / --dry-run unless explicit.
  --help          Show this message and exit.
```

### `sio install`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio install [OPTIONS]

  [REMOVED] Use `sio init` instead.

  The legacy `sio install` path silently no-op'd on wheel installs because it
  read skill files from a directory that wasn't packaged into the wheel.
  Removed in v0.1.2 to eliminate that failure mode entirely.

Options:
  --help  Show this message and exit.
```

### `sio live`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio live [OPTIONS] COMMAND [ARGS]...

  Discover and read in-progress (live) coding-agent sessions.

Options:
  --help  Show this message and exit.

Commands:
  attach  ATTACH to a live session from another session and follow it...
  ls      List currently-active sessions across harnesses and flag...
  show    Print the TAIL of one session, locked to its id...
```

### `sio mine`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio mine [OPTIONS]

  Mine recent sessions for errors and failures.

  The user-visible verb in the PRD is ``scan``; ``mine`` is the implementation
  name. ``--experiment NAME`` scopes the window to a cohort recorded by ``sio
  experiment start``. ``--session`` targets a single session (and makes
  ``--since`` optional).

Options:
  --since TEXT                    Time window: "3 days", "2 weeks", "1 month",
                                  "6h", "yesterday", "3 days ago",
                                  "2026-01-15". Required unless --session
                                  targets a single session, or --experiment is
                                  given.
  --project TEXT                  Filter by project name.
  --agent [claude|codex|gemini|goose]
                                  Which coding agent's sessions to mine in
                                  bulk. 'claude' uses the native
                                  JSONL/SpecStory scan; codex/gemini/goose
                                  enumerate that agent's store via the
                                  session-search parsers (content-level errors
                                  only). Ignored when --session is given.
  --source [specstory|jsonl|both]
                                  Source type.
  --exclude-sidechains / --include-sidechains
                                  Filter out sidechain messages before
                                  aggregation (default: on).
  --session TEXT                  Mine ONE session by handle/path/id
                                  (agent:native_id, a search-result path, or
                                  bare id). When set, --since is optional.
                                  Pipe from `sio search ... --files`.
  --experiment TEXT               Scope to the cohort window of the named
                                  experiment (resolves --since from
                                  experiments table).
  --help                          Show this message and exit.
```

### `sio multi-train`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio multi-train [OPTIONS]

  Fire N optimize runs in parallel, one per surface (or LM combo).

Options:
  --surfaces TEXT                 Comma-separated target_surface list, or
                                  'all' for every surface in the catalog
                                  (claude_md_rule, skill_update, hook_config,
                                  mcp_config, settings_config, agent_profile,
                                  project_config).
  --parallelism INTEGER           Max concurrent subprocess optimize runs.
                                  [default: 4]
  --optimizer [gepa|mipro|bootstrap]
                                  [default: gepa]
  --budget [light|medium|heavy]   [default: light]
  --lm-mix [all-work|all-cheap|balanced|free-first]
                                  How to assign task/reflection LMs across
                                  runs. all-work=Pro both; all-cheap=Flash
                                  both; balanced=rotate gpt-5/Pro/Flash; free-
                                  first=Ollama where possible.  [default:
                                  balanced]
  --trainset-file TEXT            Default trainset (per-surface filter applied
                                  via sio curate).
  --dry-run                       Print plan + cost estimate, do not launch
                                  any child.
  --help                          Show this message and exit.
```

### `sio optimize`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio optimize [OPTIONS]

  Run prompt optimization against the gold_standards corpus.

  Uses GEPA (or mipro/bootstrap in Wave 6) to compile an optimized DSPy
  program and save the artifact to ~/.sio/optimized/. Records the run in the
  optimized_modules table.

  Use ``--trainset-file <path>`` to point at a curated JSONL produced by ``sio
  curate`` — this is the recommended path for production runs to avoid
  concept-drift in the trainset.

Options:
  --module TEXT                   Module to optimize (e.g.
                                  suggestion_generator).  [default:
                                  suggestion_generator]
  --optimizer [gepa|mipro|bootstrap]
                                  Prompt optimizer to use.  [default: gepa]
  --trainset-size INTEGER         Max gold_standards rows to use for training.
                                  [default: 200]
  --valset-size INTEGER           Max gold_standards rows to use for
                                  validation.  [default: 50]
  --dry-run                       Print config and exit without running
                                  optimization.
  --trainset-file TEXT            Path to a curated JSONL produced by `sio
                                  curate`. When set, the optimizer reads
                                  trainset from this file instead of the live
                                  ground_truth table — recommended to avoid
                                  concept drift.
  --baseline-against INTEGER      Compare the new optimization score against
                                  an existing optimized_modules.id. If new
                                  score < baseline, refuse to mark active
                                  (treats the new artifact as a candidate, not
                                  a promotion).
  --task-mode [work|cheap|free|personal|personal-strong]
                                  LM tier for the task LM (per-example evals).
                                  work=gemini-pro, cheap=gemini-flash,
                                  free=ollama, personal=gpt-4o-mini, personal-
                                  strong=gpt-5. Overrides SIO_TASK_LM.
  --reflection-mode [work|cheap|free|personal|personal-strong]
                                  LM tier for the reflection LM (GEPA's
                                  critic). Same tiers as --task-mode.
                                  Overrides SIO_REFLECTION_LM. personal-strong
                                  (gpt-5) requires explicit opt-in per
                                  Principle XII.
  --gepa-budget [light|medium|heavy]
                                  GEPA budget tier (auto=light|medium|heavy).
                                  Overrides SIO_GEPA_BUDGET. light=$5-8,
                                  medium=$15-25, heavy=$40-80 (with gpt-5
                                  reflection).
  --budget-override FLOAT         Override the 24h rolling spend cap from
                                  [budget] in ~/.sio/config.toml for this
                                  invocation (XII clause 6 escape hatch). Pass
                                  a USD amount.
  --skip-ladder                   Bypass the optimizer-ladder discipline gate
                                  (Constitution XIV proposed): by default,
                                  `--optimizer gepa` refuses to run on a
                                  registered trainset if no prior MIPROv2 run
                                  exists for the same module on the same
                                  dataset. The ladder is Bootstrap → MIPROv2 →
                                  GEPA; skipping rungs wastes the expensive
                                  Pro/gpt-5 reflection budget on
                                  configurations MIPROv2 may already have
                                  found near-optimum. Pass this flag to
                                  override (a note is logged so SIO mining can
                                  track ladder-skip frequency).
  --skip-data-gate                Bypass the MIPROv2 data-size gate: by
                                  default, `--optimizer mipro` refuses to run
                                  when valset_size < max(25, trainset_size *
                                  0.2). MIPROv2's Bayesian search needs
                                  ~25-50+ valset rows to reliably outperform
                                  Bootstrap; below threshold it often UNDER-
                                  performs (see optimized_modules row #17 vs
                                  #16 on 2026-05-18). Override logged via
                                  runlog so SIO mining can track data-gate-
                                  skip frequency.
  --resume-from MODULE_ID         Resume the optimizer ladder after a prior
                                  successful run. Pass the
                                  optimized_modules.id of the most recent
                                  successful rung (Bootstrap or MIPROv2).
                                  Auto-resolves --trainset-file from that
                                  row's trainset_id so the new run uses the
                                  same dataset. Records the lineage in runlog
                                  metadata for traceability. Useful for crash
                                  recovery in background-SIO cron runs: if
                                  GEPA crashed after Bootstrap+MIPROv2 landed,
                                  rerun with --resume-from <mipro_id> to pick
                                  up at GEPA without re-running the prior
                                  rungs.
  --skip-amplify-gate             Bypass the amplify-first discipline gate: by
                                  default, `--optimizer mipro|gepa` refuses to
                                  run on a trainset with source='curate' (un-
                                  amplified) because the optimizer ladder
                                  discipline is Bootstrap → AMPLIFY → MIPROv2
                                  → GEPA. Empirically: today's GEPA on the
                                  93-row curated baseline timed out at 60 min
                                  ($1.11 wasted) while GEPA #14/#15 on the
                                  same baseline amplified to 372 rows produced
                                  0.7224 / 0.8653 scores. Override logged via
                                  runlog so SIO mining can track amplify-skip
                                  frequency.
  --help                          Show this message and exit.
```

### `sio optimize-ladder`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio optimize-ladder [OPTIONS]

  Run the full optimizer ladder (Bootstrap → AMPLIFY → MIPROv2 → GEPA).

  Auto-magic prereq chain that wraps the three discipline gates shipped today:
  - ladder gate (refuses GEPA without prior MIPROv2)   - data-size gate
  (refuses MIPROv2 below valset floor)   - amplify-first gate (refuses
  MIPROv2/GEPA on curate or <300 rows)

  Skips rungs that already have a successful row in optimized_modules for the
  relevant trainset_id (idempotent on re-run — useful for cron crash
  recovery).

  Empirical basis: GEPA on amplified 372-row trainset produced 0.8653 (#15,
  2026-05-16); GEPA on un-amplified 93-row curate timed out after 60 min
  wasting $1.11 (2026-05-18). This command makes the successful path the
  default.

  Example:     sio optimize-ladder --trainset-file
  ~/.sio/datasets/curated.jsonl --yes

Options:
  --trainset-file TEXT            Input JSONL (curate output). The compound
                                  command will amplify it if rows < --target-
                                  amplified-rows, then run Bootstrap → MIPROv2
                                  → GEPA on the amplified output.  [required]
  --module TEXT                   Module to optimize.  [default:
                                  suggestion_generator]
  --target-amplified-rows INTEGER
                                  Minimum amplified row count required for
                                  MIPROv2/GEPA. Defaults to 300 (GEPA's floor;
                                  satisfies MIPROv2's 200 too).  [default:
                                  300]
  --amplify-n-per-row INTEGER     Variants generated per row during the
                                  amplify step.  [default: 3]
  --task-mode [work|cheap|free|personal|personal-strong]
                                  [default: cheap]
  --reflection-mode [work|cheap|free|personal|personal-strong]
                                  [default: personal-strong]
  --yes                           Skip the cost-confirmation prompt (still
                                  subject to global budget cap from [budget]
                                  in ~/.sio/config.toml).
  --budget-override FLOAT         Per-invocation 24h budget cap override (XII
                                  clause 6).
  --dry-run                       Print the plan + cost estimate, do nothing.
  --rungs TEXT                    Comma-separated subset of rungs to run. Use
                                  'bootstrap' for the 1-min express lane (skip
                                  MIPRO + GEPA when you need a quick baseline
                                  and will revisit later). Example: --rungs
                                  bootstrap.  [default:
                                  bootstrap,amplify,mipro,gepa]
  --help                          Show this message and exit.
```

### `sio optimize-suggestions`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio optimize-suggestions [OPTIONS]

  Optimize the suggestion module using ground truth corpus.

  Uses BootstrapFewShot (<50 examples) or MIPROv2 (>=50 examples) to optimize
  the DSPy SuggestionGenerator (PatternToRule signature per contracts/dspy-
  module-api.md §3) on approved ground truth. Shows before/after metric scores
  and prompts for approval.

Options:
  --optimizer [auto|bootstrap|miprov2]
                                  DSPy optimizer to use. 'auto' selects based
                                  on corpus size.
  --dry-run                       Evaluate metrics without saving.
  --help                          Show this message and exit.
```

### `sio patterns`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio patterns [OPTIONS]

  Show discovered error patterns ranked by importance.

Options:
  --type [tool_failure|user_correction|repeated_attempt|undo|agent_admission]
                                  Filter by error type.
  --project TEXT                  Filter by project name (substring match on
                                  source path).
  --help                          Show this message and exit.
```

### `sio promote-flow`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio promote-flow [OPTIONS] FLOW_HASH

  Promote a flow pattern to a Claude Code skill file.

  Takes a flow hash (from `sio flows` output) and generates a skill Markdown
  file in ~/.claude/skills/ based on the observed tool sequence.

  Examples:     sio promote-flow abc123def456

Options:
  --help  Show this message and exit.
```

### `sio promote-positives`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio promote-positives [OPTIONS]

  Promote positive_records to ground_truth(label='pending').

  Wires up the 1,702-row positive_records table (built but never joined into
  trainsets) so that confirmations/gratitude/session_success events flow into
  the review queue. From there ``sio approve`` lifts them to label='positive'
  and they enter the next ``sio optimize`` trainset.

  Bridges the session_id schema gap (error_records uses bare UUIDs,
  positive_records uses ``<path>:<hash>``) via the shared source_file.

Options:
  --since TEXT            Time window of positive_records to consider.
                          [default: 7 days]
  --min-confidence FLOAT  Drop positives with sentiment_score below this.
                          [default: 0.0]
  --dry-run               Show what would be promoted without writing.
  --help                  Show this message and exit.
```

### `sio promote-rule`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio promote-rule [OPTIONS] RULE_INDEX

  Promote a violated CLAUDE.md rule into a runtime PreToolUse hook.

  Takes the 1-based index from the `sio violations` report:

      sio violations          # prints the indexed report     sio promote-rule
      1      # promotes row #1 to a hook

  Modes:
    warn  (default) — hook prints the rule text + a soft warning, lets
                      the call proceed. Use until the violation count
                      is decisively shrinking.
    block           — hook prevents the violating tool call entirely.
                      Use only after a warn-mode soak.

  Phase 1 scaffold: looks up the rule + prints what would be promoted. Hook
  generation + registration land in subsequent phases. See prds/prd-violated-
  rule-to-pretooluse-hook.md.

Options:
  --mode [warn|block]  warn: hook prints the rule + continues. block: hook
                       prevents the call.
  --since TEXT         Only count violations after this ISO-8601 date.
  --write              Actually write the hook script + register it in
                       ~/.claude/settings.json. Without this flag the command
                       is a preview — extracts the detection pattern and shows
                       what would be promoted, but writes nothing.
  --help               Show this message and exit.
```

### `sio promote-to-gold`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio promote-to-gold [OPTIONS] [INVOCATION_ID]

  Promote behavior_invocations to gold_standards for DSPy training.

  A row is eligible when user_satisfied=1 AND correct_outcome=1. Use --all-
  eligible to bulk-promote, or pass an INVOCATION_ID for one row.

Options:
  --all-eligible  Bulk-promote ALL invocations with user_satisfied=1 AND
                  correct_outcome=1.
  --dry-run       Show what would be promoted without writing.
  --help          Show this message and exit.
```

### `sio purge`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio purge [OPTIONS]

  Purge old telemetry records from the main SIO database.

  By default deletes rows in ``error_records`` and ``flow_events`` from
  ``~/.sio/sio.db`` (or ``$SIO_DB_PATH``) where ``mined_at`` is older than
  *--days* days.

  With ``--behavior-only`` also purges ``behavior_invocations`` rows from both
  the main DB and the per-platform DB.

  Examples:     sio purge --days 30 --yes     sio purge --days 30 --behavior-
  only --yes     sio purge --days 90 --dry-run

Options:
  --platform TEXT  Platform filter.
  --days INTEGER   Purge records older than N days.
  --dry-run        Show count without deleting.
  --behavior-only  Also purge behavior_invocations rows from sio.db AND the
                   per-platform DB (in addition to the default error_records /
                   flow_events purge).
  -y, --yes        Skip confirmation prompt.
  --help           Show this message and exit.
```

### `sio recall`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio recall [OPTIONS] QUERY

  Recall how a specific task was solved in a previous session.

  Topic-filters a distilled session to only the steps matching your query,
  detects struggle→fix transitions, and optionally polishes via an LM.

  Examples:     sio recall "dbt setup"                              # filter +
  format (free)     sio recall "dbt setup" --polish                     #
  polish via ollama (free)     sio recall "dbt setup" --polish --polish-model
  openai/gpt-4o-mini --confirm-cost     sio recall "auth fix" --project my-app
  sio recall "snowflake deploy" -o runbook.md

Options:
  --session TEXT          Path to specific JSONL session. Default: latest.
  --project TEXT          Filter latest session by project name.
  --polish / --no-polish  Polish the runbook via an LM (default:
                          ollama/qwen3-coder:30b — free). Override model with
                          --polish-model or SIO_POLISH_LM env var.
  --polish-model TEXT     Model to use for polishing (e.g.
                          'ollama/qwen3-coder:30b', 'openai/gpt-4o-mini').
                          Default: ollama/qwen3-coder:30b. Paid (non-ollama)
                          models require --confirm-cost.
  --confirm-cost          Pre-confirm cost for paid polish models (skips
                          interactive prompt). Required for non-interactive
                          use with non-ollama models.
  -o, --output TEXT       Save runbook to file.
  --help                  Show this message and exit.
```

### `sio reject`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio reject [OPTIONS] SUGGESTION_ID

  Reject a suggestion by ID.

Options:
  -n, --note TEXT  Optional note.
  --help           Show this message and exit.
```

### `sio render`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio render [OPTIONS] [MODULE_ID]

  Render an optimized DSPy module as a skill / prompt / rule file.

Options:
  --active                        Render the currently-active optimized
                                  module.
  --all-active                    Render EVERY active optimized module (one
                                  skill per module_type).
  --format [skill|system-prompt|claude-md|json-prompt]
                                  [default: skill]
  -o, --output TEXT               Output file. Defaults to
                                  ~/.claude/skills/<name>.md for skill format.
  --name TEXT                     Skill name (used in frontmatter + default
                                  filename).  [default: sio-rule-generator]
  --dry-run                       Print to stdout instead of writing to disk.
  --help                          Show this message and exit.
```

### `sio report`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio report [OPTIONS]

  Generate a session report (terminal or HTML).

  Without --html: show a plain text summary via Rich. With --html: generate a
  self-contained HTML file.

  Examples:     sio report                          # Terminal summary     sio
  report --html                   # HTML report (default path)     sio report
  --html -o my-report.html # Custom output path     sio report --html --open
  # Generate and open in browser

Options:
  --html             Generate HTML report.
  -o, --output TEXT  Output file path (default: ~/.sio/reports/report-
                     YYYYMMDD.html).
  --days INTEGER     Lookback period in days (default: 30).
  --open             Open report in browser after generation.
  --help             Show this message and exit.
```

### `sio reproduce`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio reproduce [OPTIONS] MODULE_ID

  Show the exact sio optimize command that produced MODULE_ID.

  Includes optimizer, trainset path (resolved via trainsets table), task-mode
  + reflection-mode (when LMs match known tiers), seed (if recorded), and
  --baseline-against pointing at the previous active module of the same type.

Options:
  --copy  Print to stdout in a copy-pasteable single-line form.
  --help  Show this message and exit.
```

### `sio review`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio review [OPTIONS]

  Batch-review unlabeled invocations.

Options:
  --platform TEXT  Platform filter.
  --session TEXT   Session ID filter.
  --limit INTEGER  Max items to review.
  --help           Show this message and exit.
```

### `sio rollback`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio rollback [OPTIONS] CHANGE_ID

  Rollback an applied change by ID.

Options:
  --help  Show this message and exit.
```

### `sio rule-audit`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio rule-audit [OPTIONS] RULE_ID

  Audit a single rule with concrete error samples (PRD Surface 3).

  Default: pulls SAMPLES error rows from before & after the rule's first-seen
  window and prints them. With --judge: invokes a paid LLM to score whether
  the rule's prevention_instructions actually apply to each AFTER-window
  error. Cost callout fires before any LLM call.

Options:
  --samples INTEGER     Number of representative errors to display from each
                        side.  [default: 10]
  --window INTEGER      Pre/post window in days around rule first-seen.
                        [default: 7]
  --judge               Run LLM-as-judge on AFTER-window samples. PAID —
                        requires --yes or interactive confirmation.
  --yes                 Skip the cost-confirmation prompt (--judge only).
  --write-report        Write the audit output to
                        ~/.sio/audits/<rule_hash>_<ts>.md.
  --format [text|json]  [default: text]
  --help                Show this message and exit.
```

### `sio rule-outcomes`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio rule-outcomes [OPTIONS] [RULE_ID]

  Per-rule outcomes drill-down (PRD Surface 2).

  Omit RULE_ID to list all rules with outcomes data. Provide a rule_id (format
  ``tools/foo.md#<sha[:12]>``) to print the per-rule detail block with
  before/after counts, confidence, related sibling rules.

Options:
  --window INTEGER      Pre/post window in days around rule first-seen.
                        [default: 7]
  --since TEXT          Only consider error_records on/after this date
                        (ISO-8601 or 'N days').
  --format [text|json]  [default: text]
  --help                Show this message and exit.
```

### `sio runs`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio runs [OPTIONS] [RUN_ID]

  Inspect SIO per-invocation run logs.

  With no RUN_ID, lists the most recent runs in a compact table. With RUN_ID
  (8-char hex prefix), shows the full JSON record.

Options:
  --failed         Only exit_class == error
  --partial        Only exit_class == partial
  --cmd TEXT       Filter by command name
  --since TEXT     e.g. '7 days', '24h', '90 min', 'today'
  --limit INTEGER  Max rows in list view  [default: 20]
  --tail           Follow newest record in real time
  --dspy           When showing one run, also dump dspy capture
  --help           Show this message and exit.
```

### `sio schedule`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio schedule [OPTIONS] COMMAND [ARGS]...

  Manage passive analysis schedule.

Options:
  --help  Show this message and exit.

Commands:
  install             Install daily + weekly cron jobs.
  install-briefing    Install the off-session briefing-store refresh...
  run                 Run passive analysis pipeline (invoked by cron).
  status              Check scheduler status.
  uninstall-briefing  Remove the off-session briefing-store refresh timer.
```

### `sio search`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
usage: session-search [-h]
                      [--agent {claude,codex,goose,opencode,gemini,aider,all}]
                      [--specstory] [--backups] [--all] [--recent RECENT]
                      [--limit LIMIT] [--files] [--count] [--context CONTEXT]
                      [--around N] [--clean] [--format {jsonl,text}]
                      [--skeleton] [--session UUID] [--case-sensitive]
                      [--fast] [--no-fast] [--list-agents] [--refine TERM]
                      [--strategy STRATEGY] [--noise-threshold N]
                      [pattern]

Unified cross-harness coding-agent session search.

positional arguments:
  pattern               Pattern to search for.

options:
  -h, --help            show this help message and exit
  --agent {claude,codex,goose,opencode,gemini,aider,promptchain,all}
                        Which agent's history to search (default: claude).
                        'all' fans out to every harness.
  --specstory           Search SpecStory MD only.
  --backups             Include ~/.claude/backups.
  --all                 Claude: JSONL + SpecStory + backups. Equivalent to
                        bash legacy --all.
  --recent RECENT       Only files whose mtime is within N days (default: 7).
                        Use 0 to search full history (overrides the default
                        window). With --all and no explicit --recent, defaults
                        to full history; an explicit --recent N is still
                        honored alongside --all. Aligns with the Cascade
                        Memory Protocol recency-first gate.
  --limit LIMIT         Cap matches per agent (0=unlimited).
  --files               Emit unique source paths.
  --count               Emit per-file match counts.
  --context CONTEXT     Lines of context (fast/legacy only).
  --around N            Context window: when a search hit is found in a
                        session, return the ±N TURNS around that hit (role-
                        aware: user/assistant/tool), clamped at transcript
                        boundaries. Distinct from --context (raw rg lines) and
                        from --session (full transcript dump). When --around
                        is set the output is the windowed turns around each
                        hit in JSONL Record format. (FR-003 / FR-004)
  --clean               Un-escape JSON escapes in content (text/legacy modes).
  --format {jsonl,text}
                        Output format for python parsers. When omitted,
                        interactive (TTY) claude search shows the session
                        skeleton; piped output defaults to jsonl.
  --skeleton, --sessions
                        Session-level discovery view: one deduped row per
                        session UUID (claude), classified
                        discussed/edited/command/output, search-noise dropped.
                        This is the default for interactive claude search.
  --session UUID        Expand: print the FULL transcript of one or more
                        sessions by UUID (comma-separated, or repeat the
                        flag). Pairs with --skeleton.
  --case-sensitive      Case-sensitive match.
  --fast                Force ripgrep fast path (claude only).
  --no-fast             Disable ripgrep fast path even when claude-only.
  --list-agents         Print inventory of agents with on-disk history, then
                        exit.
  --refine TERM         Hop-2 refinement: AND-narrow the search result set by
                        a second filter term (comma-separated for OR within
                        Hop-2). Applied after records are collected and
                        sorted: only records whose content contains the refine
                        term(s) are emitted. (FR-005 / US3)
  --strategy STRATEGY   Hop-2 narrowing strategy (used with --refine).
                        'filter' (default): keep only records containing the
                        refine term. Fast, no embeddings. 'recluster' and
                        'hybrid' are not supported for sio search (session
                        records do not map to the error-DB cluster schema);
                        passing either raises an error. (FR-005 / US3)
  --noise-threshold N   When the first-hop result count exceeds N, emit a
                        Hop-2 refine suggestion to stderr (non-blocking).
                        Default: 20. (FR-006 / US3)
```

### `sio search-discipline`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio search-discipline [OPTIONS]

  Report search-discipline rates from invocation telemetry.

  Emits recency-rate, multi-hop-rate, files-first-rate, and context-walk-rate
  over a time window.  Rates with a BASELINE target are flagged when below
  that target.

  Rate definitions (from research.md §B):   recency-rate       = --recent /
  total search invocations

    multi-hop-rate     = --refine|--within|--use-cache|--strategy / total

    files-first-rate   = --files / total

    context-walk-rate  = --context / total

  Targets (BASELINE.md): recency >= 85%, multi-hop >= 5%, context-walk >= 15%.

Options:
  --window INTEGER  Look-back window in days. Pass 0 for full history.
                    [default: 14]
  --db-path TEXT    Path to behavior_invocations.db. Defaults to
                    ~/.sio/<platform>/behavior_invocations.db.
  --json            Output as JSON.
  --help            Show this message and exit.
```

### `sio status`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio status [OPTIONS]

  Show 5-section SIO pipeline health status.

  Sections: Hooks, Mining, Training, Audit, Database. Exit 0 if all
  healthy/warn; exit 1 if any error. Latency target: < 2s (SC-009).

Options:
  --plain  Plain text output (no Rich tables).
  --help   Show this message and exit.
```

### `sio suggest`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio suggest [OPTIONS]

  Run the full pipeline: cluster -> persist -> dataset -> suggestions.

Options:
  --type [tool_failure|user_correction|repeated_attempt|undo|agent_admission]
                                  Only analyze errors of this type.
  --min-examples INTEGER          Min examples to build a dataset.
  -g, --grep TEXT                 Filter errors by keyword(s) in content.
                                  Comma-separated for OR logic (e.g.
                                  'placeholder,hardcoded,stub').
  -v, --verbose                   Enable verbose DSPy trace logging.
  --auto                          Force automated mode for all patterns (skip
                                  interactive review).
  --analyze                       Force HITL (human-in-the-loop) mode for all
                                  patterns.
  --project TEXT                  Filter by project name (substring match on
                                  source path).
  --exclude-type TEXT             Exclude error types. Comma-separated (e.g.
                                  'repeated_attempt,tool_failure').
  --preview                       Preview: filter + cluster + show pattern
                                  groupings, then stop. No generation.
  --refine TEXT                   Hop-2 refinement: narrow Hop-1's (--grep)
                                  error set by a second AND-filter. Comma-
                                  separated terms use OR logic within Hop-2,
                                  AND-composed with Hop-1. See --strategy for
                                  how narrowing is applied.
  --strategy [filter|recluster|hybrid]
                                  Hop-2 narrowing strategy (used with
                                  --refine). 'filter' (default): narrow errors
                                  by --refine, feed subset to DSPy. Fast,
                                  shallow. 'recluster': re-cluster Hop-1's
                                  errors and select sub-clusters matching
                                  --refine. Slower, deep. 'hybrid': filter by
                                  --refine, then re-cluster the survivors.
                                  Balance.
  --recluster-threshold FLOAT RANGE
                                  Cosine-similarity threshold for the second
                                  clustering pass under --strategy
                                  recluster|hybrid. Higher = tighter sub-
                                  clusters. First pass uses 0.70; recluster
                                  uses 0.85 by default since the Hop-1 error
                                  set is already theme-coherent.  [default:
                                  0.85; 0.5<=x<=0.99]
  --within TEXT                   Path to a Hop-1 errors CSV (from a previous
                                  --preview run). Skips DB load + --grep /
                                  --project / --type filters (those were
                                  applied in Hop-1). Feeds the cached errors
                                  directly into clustering + Hop-2. Use
                                  '~/.sio/previews/errors_preview.csv' (latest
                                  preview) by default if --use-cache is set.
  --use-cache                     Use the most recent Hop-1 preview CSV at
                                  ~/.sio/previews/errors_preview.csv. Warns if
                                  the cache is older than --cache-ttl hours
                                  (default 24).
  --cache-ttl INTEGER             Max age in hours for --use-cache to accept
                                  without warning. Default: 24.
  --session TEXT                  Scope the whole pipeline to ONE session
                                  (agent:native_id, e.g. claude:<uuid>; bare
                                  id assumed claude). Turns suggest into a
                                  targeted single-session analyzer. Pipe from
                                  `sio search ... --files`.
  --experiment TEXT               Scope errors to the cohort window of the
                                  named experiment. Composable with --grep /
                                  --type / --project.
  --since TEXT                    Load only errors newer than this cutoff.
                                  Accepts an ISO date ('2024-01-01'), a
                                  relative spec ('90d', '7d', '2w'), or '0' /
                                  'all' to disable the default 30-day window
                                  and load full history. Default: 30d
                                  (FR-007).
  --max-rows INTEGER              Maximum error rows to load from the DB per
                                  run. Default: 5000. Pass 0 to remove the row
                                  cap (loads all rows within --since window).
                                  FR-008.
  --harness [claude-code|codex|gemini|goose]
                                  Target harness for generated suggestions.
                                  claude-code uses the tiered config surface
                                  (CLAUDE.md + rules/ + skills/);
                                  codex/gemini/goose each route to their
                                  single instruction file (AGENTS.md /
                                  GEMINI.md / .goosehints).
  --help                          Show this message and exit.
```

### `sio suggest-review`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio suggest-review [OPTIONS]

  Review pending improvement suggestions interactively.

Options:
  --help  Show this message and exit.
```

### `sio train`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio train [OPTIONS]

  Train DSPy modules on exported datasets.

  Uses BootstrapFewShot (<50 examples) or GEPA (50+) to optimize recall
  modules. Trained models are saved to ~/.sio/models/ and used by `sio recall`
  for inference.

  Prerequisites:     1. Run `sio mine --since "14 days"` to mine sessions
  2. Run `sio flows --since "14 days"` to extract flow patterns     3. Run
  `sio export-dataset --task all` to create training data     4. Run `sio
  train` to optimize modules

  Examples:     sio train                             # Train all modules
  sio train --task distiller            # Train only the recall distiller
  sio train --optimizer gepa            # Use GEPA optimizer     sio train
  --model gpt-4o             # Use specific model

Options:
  --task [router|distiller|recovery|flow|all]
                                  Which module to train.
  --optimizer [bootstrap|gepa]    DSPy optimizer (bootstrap for <50 examples,
                                  gepa for 50+).
  --model TEXT                    LLM model for training (default: DSPY_MODEL
                                  env or gpt-4o-mini).
  --max-examples INTEGER          Maximum training examples per task.
  --help                          Show this message and exit.
```

### `sio trend`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio trend [OPTIONS]

  Show growth / decline of pattern clusters over time.

  Uses the `error_records.timestamp` column joined via `pattern_errors` to
  bucket errors per pattern per time window. Produces a compact table with a
  trend arrow (↑ growing, ↓ shrinking, → stable) based on the last two
  windows.

Options:
  --weekly           Weekly buckets (default).
  --daily            Daily buckets.
  --monthly          Monthly buckets.
  --top INTEGER      Show top-N patterns by total error count over the window.
                     Default: 10.
  --windows INTEGER  How many time windows (weeks / days / months) to include.
                     Default: 6. Counted backwards from now.
  --pattern TEXT     Filter to a single pattern by id or slug (pattern_id).
                     Optional.
  --grep TEXT        Filter patterns by substring match on description (comma-
                     separated OR).
  --experiment TEXT  Scope buckets to the cohort window of the named
                     experiment.
  --help             Show this message and exit.
```

### `sio velocity`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio velocity [OPTIONS]

  Show learning velocity trends — how error rates change after rules.

  Computes error frequency per type over a rolling window, measures correction
  decay after rule application, and flags ineffective rules.

  With --by-rule: switches mode entirely — instead of per-error-type trends,
  computes per-rule attribution from the active_rules column on error_records
  (T1.L.3, PRD sio_backend_dead_loop_2026-05-15).

  Examples:     sio velocity                          # All error types, 7-day
  window     sio velocity --error-type unused_import     sio velocity --by-
  rule                # per-rule attribution     sio velocity --by-rule
  --format json  # machine-readable

Options:
  --error-type TEXT      Filter to specific error type.
  --window INTEGER       Rolling window in days (default: 7).
  --format [table|json]  Output format (default: table).
  --skills               Show per-skill effectiveness metrics.
  --by-rule              Show per-rule error-rate attribution (T1.L.3). For
                         each rule in ~/.claude/rules/, compute the error rate
                         of records where that rule was active vs not-active.
                         Requires active_rules column populated by recent
                         mining.
  --min-records INTEGER  (With --by-rule) minimum 'with rule' records to
                         include a rule.  [default: 10]
  --experiment TEXT      Scope the velocity window to the named experiment's
                         cohort (overrides --window). Not compatible with
                         --by-rule.
  --help                 Show this message and exit.
```

### `sio violations`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio violations [OPTIONS]

  Show detected rule violations (existing rules the assistant ignored).

  Scans CLAUDE.md and all files in the rules/ directory for imperative
  constraints (NEVER, ALWAYS, MUST, DO NOT), then compares mined errors
  against them to detect enforcement failures.

  Violations are flagged at higher priority than new patterns since they
  indicate the rule text is insufficient or the assistant is failing to follow
  it.

  Examples:     sio violations                          # Default: scan all
  rule files     sio violations --since 2026-03-01       # Only recent errors
  sio violations --format json            # JSON output for piping

Options:
  --since TEXT           Filter errors after this date (ISO-8601).
  --format [table|json]  Output format.
  --help                 Show this message and exit.
```

### `sio watch`

```
[sio-wrapper] ✓ ollama @ http://192.168.0.159:11434  task=ollama/qwen3-coder:30b  reflection=ollama/deepseek-r1:32b
Usage: sio watch [OPTIONS]

  Watch a session live — tail events as they happen (Phase B).

Options:
  --session TEXT  Session to watch (agent:native_id, a search-result path, `-`
                  for stdin, or a bare id). Live watch supports claude so far.
                  [required]
  --from-start    Replay existing events first, then follow new ones.
  --tools-only    Only surface tool_use events.
  --help          Show this message and exit.
```

