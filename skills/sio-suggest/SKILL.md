---
name: sio-suggest
description: Generate targeted CLAUDE.md rules from mined error patterns. Ask naturally like "how can I improve my agent?" or "generate suggestions from my errors".
---

# SIO Suggest — Turn Errors into Improvement Rules

Natural language interface to the SIO pipeline. The agent translates what you say into
filters, shows you the dataset FIRST with groupings, lets you refine, THEN generates.

## User Input

```text

```

You **MUST** consider the user input before proceeding (if not empty).

## Workflow: Show → Refine → Generate

**CRITICAL: This is a CONVERSATIONAL workflow, not a fire-and-forget script.**

### Phase 1: SHOW the dataset (ALWAYS start here)

1. Auto-detect project from cwd
2. Parse user intent into initial filters (see Filter Translation below)
3. Run `sio suggest --preview` with those filters
4. This outputs:
   - Pattern groupings table (rank, description, error count, sessions, top type, score, sample)
   - CSV exports at `~/.sio/previews/patterns_preview.csv` and `errors_preview.csv`
5. Present the groupings to the user with commentary:
   - Which patterns look like real development gaps vs noise
   - Which error types dominate
   - Suggest refinements if the dataset looks noisy

Example output to user:
```
Here are the 49 pattern groupings from 493 dev gap errors:

Quality signals (worth generating suggestions for):
- Pattern #9: Undo "implement error extractor" (20 errors) — agent deliverable was rejected
- Pattern #40: Agent admission "test hanging" (3 errors) — agent knew it was stuck
- Pattern #41: Undo "Phase 7 US4 implementation" (3 errors) — whole phase was reverted

Noise (probably should exclude):
- Patterns #4,7,11: Import sorting (I001) — linting, not dev gaps
- Patterns #14,18,19,22,43,44: Line too long (E501) — formatting noise
- Patterns #27,28,30: Git local changes — git workflow, not code quality

Want to:
A) Exclude linting noise and run on quality signals only
B) Narrow to just undo + agent_admission types
C) Add/remove search terms
D) View the exported CSVs for deeper analysis
E) Looks good, generate suggestions on all 49 patterns
```

### Phase 2: REFINE (iterate with user)

Wait for user input. They may say:
- "B" → re-run with `--exclude-type repeated_attempt,tool_failure`
- "drop the linting patterns" → add `--grep` exclusions or narrow terms
- "just the undos" → `--type undo`
- "add 'mock' to the search" → update grep terms, re-preview
- "looks good" → proceed to Phase 3

**Re-run `--preview` after each refinement so user sees updated groupings.**

### Phase 3: GENERATE (only after user approves)

User says "run it" / "generate" / "looks good" → run without `--preview`:
```bash
sio suggest --project <PROJECT> --grep "<TERMS>" --exclude-type "<EXCLUDED>" --auto
```

Present generated suggestions and recommend `/sio-review`.

## Natural Language → Filter Translation

### Search terms → `--grep` (comma-separated OR logic)

| User says | --grep value |
|---|---|
| "find development gaps" | `placeholder,hardcoded,stub,empty` |
| "placeholder and empty method issues" | `placeholder,empty` |
| "find fake or mocked code" | `fake,mock,stub,placeholder` |
| "what Snowflake queries fail?" | `snowflake` |
| (nothing specific) | omit --grep |

### Error types → `--type` or `--exclude-type`

| User says | Filter |
|---|---|
| "only quality signals" | `--exclude-type repeated_attempt,tool_failure` |
| "find development gaps" (implied) | `--exclude-type repeated_attempt` |
| "just undos and corrections" | needs multiple runs or exclude others |
| "agent admissions" | `--type agent_admission` |

### Project → `--project` (auto-detected from cwd, user can override)

## CLI Reference

| Flag | What it does |
|---|---|
| `--preview` | Show pattern groupings + export CSVs, stop before generation |
| `--project NAME` | Substring match on source_file |
| `--type TYPE` | Include only this error type |
| `--exclude-type TYPES` | Exclude types (comma-sep) |
| `--grep TERMS` | OR search across content (comma-sep) |
| `--min-examples N` | Min errors to build a dataset (default 3) |
| `--auto` | Fully automated generation, no interactive prompts |

## Exported Files (from --preview)

| File | Contents |
|---|---|
| `~/.sio/previews/patterns_preview.csv` | Pattern rank, ID, description, error count, sessions, score |
| `~/.sio/previews/errors_preview.csv` | Error ID, type, text, tool, session, timestamp, source, user message |

Use these for external analysis — open in a spreadsheet, load into DuckDB, etc.

## After Generation

1. `/sio-review` — Approve, reject, or defer each suggestion
2. `/sio-apply` — Write approved rules to CLAUDE.md or target files
