# SIO Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-02-25

## Active Technologies
- SQLite with WAL mode, per-platform at `~/.sio/<platform>/behavior_invocations.db` (001-self-improving-organism)
- Python 3.11+ + Click (CLI), Rich (terminal UI), fastembed (embeddings), numpy (002-sio-redesign)
- SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/` (002-sio-redesign)
- Python 3.11+ + DSPy >=3.1.3, Click >=8.1, Rich >=13.0, fastembed >=0.2, numpy >=1.24, tomllib (stdlib) (003-dspy-suggestion-engine)
- SQLite with WAL mode at `~/.sio/sio.db`; JSON files at `~/.sio/datasets/`; Ground truth corpus at `~/.sio/ground_truth/`; Optimized modules at `~/.sio/optimized/` (003-dspy-suggestion-engine)

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
- 003-dspy-suggestion-engine: Added Python 3.11+ + DSPy >=3.1.3, Click >=8.1, Rich >=13.0, fastembed >=0.2, numpy >=1.24, tomllib (stdlib)
- 002-sio-redesign: Added Python 3.11+ + Click (CLI), Rich (terminal UI), fastembed (embeddings), numpy
- 001-self-improving-organism: Added Python 3.11+ + DSPy (latest, currently 3.1.3), fastembed, numpy, sqlite3 (stdlib), Click (CLI), Rich (terminal UI)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
