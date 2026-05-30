# Contributing to SIO

**Self-Improving Organism** — a Python 3.11+ CLI that mines Claude Code sessions, clusters
errors into patterns, and generates CLAUDE.md rules via DSPy.

> This guide is for **human contributors**. If you are an AI agent, start with
> `CLAUDE.md` (project-level rules) and `~/.claude/rules/tools/sio.md` (tool rules).

---

## Requirements

- Python 3.11 or 3.12
- `uv` (preferred) or plain `pip` — check for a `.venv` folder before installing globally
- Git

Optional extras activate more pipeline surfaces:

| Extra | Installs | Needed for |
|---|---|---|
| `[torch]` | torch ≥ 2.0 | GPU-accelerated fastembed |
| `[openai]` | openai ≥ 1.0 | OpenAI-backed optimizers |
| `[parquet]` | pandas ≥ 2.0 | Parquet dataset export |
| `[all]` | all three | Full local stack |

---

## Local dev setup

```bash
git clone https://github.com/gyasis/SIO.git
cd SIO

# Create a virtual environment (uv preferred)
uv venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# Editable install with dev extras
uv pip install -e ".[dev]"       # includes pytest, ruff
# or: pip install -e ".[dev]"    # if uv is not available
```

### First-time initialization

After installing, bootstrap the `~/.sio/` data directory and stage skills/rules into
your Claude Code config:

```bash
sio init                         # auto-detects harness, idempotent
sio init --dry-run               # preview changes without writing
sio init --status                # show what is installed vs packaged
```

`sio init` copies the bundled content from `skills/`, `rules/`, and `docs/` (bundled
via `pyproject.toml`'s `force-include`) into your harness config (e.g. `~/.claude/`).
Re-running is safe — user-modified files are preserved unless `--force` is passed.

---

## Running tests

```bash
pytest                           # all tests (unit + integration)
pytest tests/unit/               # unit tests only
pytest tests/integration/        # integration tests only (may need API keys)
pytest -k "test_metrics"         # filter by name
pytest --cov=sio                 # with coverage
```

Test layout:

| Path | What lives there |
|---|---|
| `tests/unit/` | Isolated unit tests per module, organized by subdirectory (cli/, db/, dspy/, clustering/, ...) |
| `tests/integration/` | End-to-end pipeline tests (closed loop, arena, passive mining) |
| `tests/contract/` | Contract tests for the DSPy module API |
| `tests/conftest.py` | Shared fixtures (`fake_fastembed`, `tmp_path` DB, etc.) |

The `pytest.ini_options` in `pyproject.toml` sets `pythonpath = ["src"]` and
`testpaths = ["tests/unit", "tests/integration"]` — you do not need to fiddle with
`PYTHONPATH` manually.

### Lint

```bash
ruff check .                     # check
ruff check --fix .               # auto-fix safe violations
```

Rules are the ruff defaults. Before opening a PR, `ruff check .` must exit 0.

---

## Repo layout

```
SIO/
├── src/sio/             # All Python source (importable as sio.*)
│   ├── cli/             # Click commands — main.py is the root group
│   ├── core/            # Shared infrastructure (DB, runlog, metrics, DSPy)
│   │   └── dspy/        # DSPy factory, optimizer, metrics, signatures
│   ├── amplify.py       # Trainset amplification via Flash
│   ├── clustering/      # Error clustering (fastembed + HDBSCAN)
│   ├── ground_truth/    # Corpus loader and promotion helpers
│   ├── harnesses/       # Harness adapters (claude-code, cursor, windsurf...)
│   ├── mining/          # Session transcript ingest
│   ├── suggestions/     # DSPy SuggestionGenerator module
│   └── _bootstrap/      # Bundled skills, rules, docs (staged by `sio init`)
├── tests/
│   ├── unit/            # Unit tests, mirroring src/sio/ structure
│   ├── integration/     # End-to-end tests
│   └── contract/        # DSPy API contract tests
├── specs/               # Feature plans (000-004 numbered directories)
│   └── NNN-<name>/      # Spec dir: PRD, tasks, contracts, ADRs
├── docs/                # User-facing documentation
├── skills/              # Bundled Claude Code skills (staged by sio init)
├── rules/               # Bundled Claude Code rules (staged by sio init)
├── CHANGELOG.md         # Version history (Keep a Changelog format)
├── pyproject.toml       # Build config, deps, entry points
└── CLAUDE.md            # AI-agent project instructions
```

The package entry point is `sio = "sio.cli.main:cli"` (pyproject.toml line 54).

---

## Spec-driven workflow

New features start in `specs/`. The numbering (`000`, `001`, ...) is sequential and
matches the `CLAUDE.md` "Active Technologies" section.

To add a new feature:

1. Create `specs/NNN-<short-name>/` with at minimum:
   - `PRD.md` — problem statement, user stories, acceptance criteria
   - `tasks.md` — implementation tasks (used by the dev-kid orchestrator)
   - `contracts/` — API contracts for new DSPy modules or CLI commands
2. Implement in `src/sio/`; wire the new CLI command into `src/sio/cli/main.py`.
3. Add tests in `tests/unit/` and `tests/integration/`.
4. Update `CHANGELOG.md` under `## [Unreleased]`.

---

## Adding a new CLI command

All commands are registered on the `cli` Click group in `src/sio/cli/main.py`.
The pattern from existing commands:

```python
@cli.command("my-command")
@click.option("--thing", required=True, help="What to act on.")
@click.option("--dry-run", is_flag=True)
@runlogged("my-command")           # wraps with a run-log at ~/.sio/runs/
def my_command_cmd(thing: str, dry_run: bool) -> None:
    """One-line summary shown in `sio --help`.

    Longer description here.

    Example:
        sio my-command --thing foo
    """
    from sio.core.db.schema import init_db      # import inside function
    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    ...
```

Key conventions:

- **Import inside the function body** — `sio.cli.main` is imported at startup; lazy
  imports keep cold-start fast and prevent circular import errors.
- **Always honor `SIO_DB_PATH`** — use `os.environ.get("SIO_DB_PATH", ...)` rather than
  hardcoding `~/.sio/sio.db` (42 sites were swept for exactly this in v0.3.0).
- **Decorate with `@runlogged`** — this writes a JSONL run-log to `~/.sio/runs/` and
  enables `sio runs` to surface per-stage timings and LLM call accounting.
- **Sub-groups** (e.g. `ground-truth`, `datasets`, `config`, `schedule`) use
  `@cli.group(name="...")` + `@<group>.command(...)`.

---

## The `lm_factory` rule (BLOCKING)

**All `dspy.LM(...)` construction must go through `src/sio/core/dspy/lm_factory.py`.**
No other file in `src/sio/` may call `dspy.LM(...)` directly.

The test `tests/unit/dspy/test_lm_factory.py` enforces this with a source-grep
assertion — a bare `dspy.LM(` outside `lm_factory.py` will cause a test failure.

The factory exposes two primary role functions:

```python
from sio.core.dspy.lm_factory import get_task_lm, get_reflection_lm, get_adapter

task_lm       = get_task_lm()          # cheap, cached, for forward passes
reflection_lm = get_reflection_lm()    # strong, uncached, for GEPA reflection
adapter       = get_adapter(task_lm)   # provider-aware (OpenAI→Chat, Ollama→JSON)

import dspy
dspy.configure(lm=task_lm, adapter=adapter)
```

Resolution order for each role (env override → `~/.sio/config.toml` → hard default):

| Role | Env var | Default model |
|---|---|---|
| task | `SIO_TASK_LM` | `gemini/gemini-flash-latest` |
| reflection | `SIO_REFLECTION_LM` | `gemini/gemini-pro-latest` |

Models listed in `[llm.banned].models` in `~/.sio/config.toml` are refused at
construction time (see `cost-control.md` — `gpt-4o` is permanently banned).

---

## The self-mining loop

SIO mines its own Claude Code sessions. This is intentional — SIO-the-tool is also
SIO's primary test subject. During active development:

1. Hooks in `.claude/hooks/` capture tool calls to `~/.sio/<platform>/behavior_invocations.db`.
2. `sio mine` ingests recent sessions and populates `error_records`.
3. `sio suggest` clusters errors and generates candidate rules.
4. `sio apply` (with user review) updates `CLAUDE.md` or relevant rule files.

The `sio init` command stages the hooks into your harness config. Re-run after
pulling new hook changes.

---

## PR checklist

Before opening a pull request:

- [ ] `pytest` passes (unit + integration)
- [ ] `ruff check .` exits 0 (no lint violations)
- [ ] New CLI commands have `@runlogged` and honor `SIO_DB_PATH`
- [ ] No bare `dspy.LM(...)` calls outside `lm_factory.py`
- [ ] CHANGELOG.md updated under `## [Unreleased]` with a brief entry
- [ ] If the change adds or modifies a DSPy module, the `tests/contract/` tests cover it
- [ ] If the change touches a spec feature, the corresponding `specs/NNN-*/` task is updated

---

## Database paths (do not guess)

| Database | Path | Purpose |
|---|---|---|
| Main DB | `~/.sio/sio.db` | Canonical store: errors, patterns, suggestions, optimized modules |
| Per-platform DB | `~/.sio/<platform>/behavior_invocations.db` | Hook-written tool-call telemetry |

Never hardcode `~/.sio/sio.db` in source — always use
`os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))`.
Tests set `SIO_DB_PATH` to a `tmp_path` fixture to stay hermetic.

---

## Getting help

- Open an issue at <https://github.com/gyasis/SIO/issues>
- See `docs/` for user-facing guides (philosophy, optimizer ladder, cookbooks)
- See `specs/` for the feature plan archive
