# SIO Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-02-25

## Active Technologies
- SQLite with WAL mode, per-platform at `~/.sio/<platform>/behavior_invocations.db` (001-self-improving-organism)
- Python 3.11+ + Click (CLI), Rich (terminal UI), fastembed (embeddings), numpy (002-sio-redesign)
- SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/` (002-sio-redesign)
- Python 3.11+ + DSPy >=3.1.3, Click >=8.1, Rich >=13.0, fastembed >=0.2, numpy >=1.24, tomllib (stdlib) (003-dspy-suggestion-engine)
- SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/`; Ground truth corpus at `~/.sio/ground_truth/`; Optimized modules at `~/.sio/optimized/` (003-dspy-suggestion-engine)
- Python 3.11+ + Click >=8.1 (CLI), Rich >=13.0 (terminal UI), fastembed >=0.2 (embeddings), numpy >=1.24, DSPy >=3.1.3 (optimization) (001-competitive-enhancement)
- SQLite with WAL mode at `~/.sio/sio.db` (14 existing tables, adding 5 new) (001-competitive-enhancement)
- Python 3.11+ + DSPy >=3.1.3 (core framework per Constitution V), Click >=8.1 (CLI), Rich >=13.0 (TUI), fastembed >=0.2 (ONNX embeddings for centroids), numpy >=1.24, sqlite3 (stdlib), tomllib (stdlib), `systemd-user` or Claude Code `CronCreate` for scheduling (selected in Phase 0 research) (004-pipeline-integrity-remediation)

- Python 3.11+ + DSPy (latest, currently 3.1.3), fastembed, numpy, sqlite3 (stdlib), Click (CLI), Rich (terminal UI) (001-self-improving-organism)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes
- 004-pipeline-integrity-remediation: Added Python 3.11+ + DSPy >=3.1.3 (core framework per Constitution V), Click >=8.1 (CLI), Rich >=13.0 (TUI), fastembed >=0.2 (ONNX embeddings for centroids), numpy >=1.24, sqlite3 (stdlib), tomllib (stdlib), `systemd-user` or Claude Code `CronCreate` for scheduling (selected in Phase 0 research)
- 001-competitive-enhancement: Added Python 3.11+ + Click >=8.1 (CLI), Rich >=13.0 (terminal UI), fastembed >=0.2 (embeddings), numpy >=1.24, DSPy >=3.1.3 (optimization)
- 001-competitive-enhancement: Added Python 3.11+ + Click >=8.1 (CLI), Rich >=13.0 (terminal UI), fastembed >=0.2 (embeddings), numpy >=1.24, DSPy >=3.1.3 (optimization)

<!-- MANUAL ADDITIONS START -->

## SIO-Generated Rules (from error pattern analysis)

### Sequential Tool Execution
Never call Bash in parallel with Write or Edit. State-modifying tools must run sequentially — parallel execution causes "Sibling tool call errored" cascade failures.
- Chain shell commands with `&&` in a single Bash call
- Run file writes AFTER Bash completes, not alongside it

### Import & Lint Compliance
Before finishing any Python file edit, mentally verify:
1. Imports are sorted: stdlib → third-party → local, alphabetical within groups
2. No unused imports (F401)
3. No lines exceed 99 characters (E501) — break with parentheses or intermediate variables
4. Run `ruff check --fix .` after multi-file changes

### Session Continuation
When resuming after context truncation:
1. Read `session_state.json` or `.memory/session.json` first
2. Run `git status` to see uncommitted work
3. Confirm with user what to continue before acting
4. Never re-do work that was already committed

### Test Generation Protocol
When writing tests from specifications:
1. Decompose requirements into discrete test functions before writing code
2. Each test: setup → execute → assert (no shared mutable state)
3. Verify every requirement from the spec has a corresponding test
4. Run `ruff check --fix` on test files before executing them
5. If tests fail, fix the code not the tests (unless test is wrong)

<!-- MANUAL ADDITIONS END -->
