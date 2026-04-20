# Quickstart — SIO Pipeline Integrity & Training-Data Remediation

**Branch**: `004-pipeline-integrity-remediation`
**Audience**: Developer picking up this feature. TDD-first (Constitution IV NON-NEGOTIABLE).

---

## 0. Prerequisites

```bash
# Python 3.11+
python --version

# uv is the package manager
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh

# WSL2 / macOS / Linux. WAL-capable SQLite.
sqlite3 --version
```

---

## 1. First-Time Setup

```bash
cd ~/dev/projects/SIO
git checkout 004-pipeline-integrity-remediation

# Isolated environment via uv — never global
uv sync --all-extras

# Confirm DSPy version floor (3.1.3)
uv pip show dspy-ai | grep -E '^Version:'
# Version: 3.1.3  (or higher)

# Run existing tests against the current main before any changes
uv run pytest -q
```

---

## 2. Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `SIO_TASK_LM` | Task-side LM for DSPy modules | `openai/gpt-4o-mini` |
| `SIO_REFLECTION_LM` | Reflection LM for GEPA | `openai/gpt-5` |
| `SIO_FORCE_ADAPTER` | Override adapter auto-selection | unset → provider-aware |
| `SIO_FORCE_NATIVE_FC` | Force native function calling on/off | unset |
| `SIO_APPLY_EXTRA_ROOTS` | `:`-sep extra write-allowlist roots | empty |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Provider credentials | required for real runs |

For local test runs (no network):

```bash
export SIO_TASK_LM="openai/gpt-4o-mini"   # real; cached via dspy.LM(cache=True)
export OPENAI_API_KEY="$(cat ~/.secrets/openai.key)"
```

Tests that need an LM use `tests/conftest.py`'s `mock_lm` fixture — zero network in CI.

---

## 3. TDD Workflow (Constitution IV)

```text
For each task in tasks.md:
  1. Write the failing test first        ← REQUIRED before impl
  2. Get user approval on test design    ← gate
  3. Confirm RED:    uv run pytest <test>    → FAIL
  4. Implement minimum to pass
  5. Confirm GREEN:  uv run pytest <test>    → PASS
  6. Refactor; re-run full suite           → still GREEN
  7. Commit with message "feat(xxx): <what>" or "fix(xxx): <what>"
```

**No code is written until a failing test exists for it.** The `/speckit.tasks` output orders tasks so that `write_test_X` precedes `implement_X`.

---

## 4. Test Scaffolding

### 4.1 Database fixtures

```python
# tests/conftest.py  (sketch)
import pytest, shutil
from pathlib import Path

@pytest.fixture
def tmp_sio_db(tmp_path, monkeypatch):
    """Clone of ~/.sio/sio.db minus heavy tables, sandboxed in tmp_path."""
    src = Path.home() / ".sio" / "sio.db"
    if not src.exists():
        pytest.skip("no live sio.db to clone from")
    dst = tmp_path / "sio.db"
    shutil.copy2(src, dst)
    # trim heavy tables
    import sqlite3
    con = sqlite3.connect(dst)
    con.execute("DELETE FROM error_records WHERE rowid > 1000")
    con.execute("DELETE FROM flow_events  WHERE rowid > 1000")
    con.commit(); con.close()
    monkeypatch.setenv("SIO_DB_PATH", str(dst))
    return dst

@pytest.fixture
def tmp_platform_db(tmp_path, monkeypatch):
    """Empty per-platform behavior_invocations.db under tmp_path."""
    path = tmp_path / "claude-code" / "behavior_invocations.db"
    path.parent.mkdir(parents=True)
    from sio.core.db.schema import create_platform_schema
    create_platform_schema(path)
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(path))
    return path
```

### 4.2 DSPy mocks

```python
# tests/conftest.py  (cont.)
@pytest.fixture
def mock_lm(monkeypatch):
    import dspy
    responses = {}
    def fake_call(self, prompt, **kwargs):
        return [responses.get(prompt[:80], "mocked")]
    monkeypatch.setattr(dspy.LM, "__call__", fake_call)
    return responses

@pytest.fixture
def fake_fastembed(monkeypatch):
    import numpy as np
    def fake_embed(texts):
        return np.ones((len(texts), 384), dtype=np.float32)
    import sio.core.clustering.embedder as emb
    monkeypatch.setattr(emb, "embed_texts", fake_embed)
```

### 4.3 Crash-injection test (FR-004)

```python
# tests/integration/test_apply_safety.py (sketch)
import os, signal, subprocess, sys, pytest

def test_crash_mid_write_leaves_target_intact(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("ORIGINAL")
    # Spawn a child that opens target.tmp, writes partial, then SIGKILL itself
    cmd = [sys.executable, "-c",
           "from sio.core.applier.writer import atomic_write;"
           "import os, signal;"
           "atomic_write_with_injected_crash(...)"]
    p = subprocess.Popen(cmd)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    assert target.read_text() == "ORIGINAL"
    # Backup must exist
    backups = list((tmp_path.parent / ".sio" / "backups").rglob("CLAUDE.md*.bak"))
    assert len(backups) >= 1
```

---

## 5. Running the Feature End-to-End

Once Phases 1–3 are implemented:

```bash
# 1. Migrate the schema
uv run sio db migrate

# 2. Install hooks + backfill legacy rows
uv run sio install --yes

# 3. Mine existing sessions (idempotent, bounded memory)
uv run sio mine

# 4. Extract flows (idempotent)
uv run sio flows

# 5. Generate suggestions (non-destructive)
uv run sio suggest

# 6. Optimize a module with GEPA (default)
uv run sio optimize --module suggestion_generator

#    …or override to MIPRO:
uv run sio optimize --module suggestion_generator --optimizer mipro --auto medium

#    …or BootstrapFewShot:
uv run sio optimize --module recall_evaluator --optimizer bootstrap

# 7. Review and apply a suggestion (atomic + backup)
uv run sio suggest --dry-run    # see candidates
uv run sio apply <suggestion_id>

# 8. Roll back
uv run sio apply --rollback <applied_change_id>

# 9. Health check
uv run sio status

# 10. Install the autoresearch schedule (Claude Code CronCreate default)
uv run sio autoresearch --install-schedule cron
```

---

## 6. Verifying Each User Story

| Story | Verification |
|---|---|
| US1 (data flow) | After one hook firing: `sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM behavior_invocations"` grows; `sio optimize` produces `optimized_modules` row (SC-004) |
| US2 (audit preserved) | Run `sio suggest` twice; `SELECT COUNT(*) FROM applied_changes WHERE superseded_at IS NULL` unchanged (SC-002) |
| US3 (safe apply) | Run `tests/integration/test_apply_safety.py`; crash-injection leaves target intact, backup present (SC-003) |
| US4 (autoresearch) | After 24 h: `SELECT COUNT(*) FROM autoresearch_txlog WHERE fired_at > datetime('now', '-1 day')` ≥ 5 (SC-005) |
| US5 (mining idempotent) | Run `sio mine` twice; `SELECT COUNT(*)` before/after unchanged (SC-006) |
| US6 (observability) | `sio status` within 2 s; inject hook failure, verify `warn` state within one heartbeat (SC-009) |
| US7 (stable slugs) | `sio suggest` twice with different input orders; pattern_ids identical (SC-010) |
| US8 (suggestion quality) | GEPA-optimized `SuggestionGenerator` devset score > baseline (SC-018); approval rate > 30% on next batch (SC-012) |
| US9 (DSPy idiomatic) | All 3 optimizers produce loadable artifacts (SC-017); grep `dspy.LM(` returns zero non-factory results (SC-022); every training example is `dspy.Example` (SC-020) |
| US10 (re-audit clean) | Spawn 2 adversarial-bug-hunter agents; both return zero CRITICAL/HIGH (SC-013) |

---

## 7. Lint, Type, Coverage

```bash
uv run ruff check .              # all rules
uv run ruff check --fix .        # autofix safe issues
uv run ruff format .             # formatter

uv run pytest -q --cov=src/sio --cov-report=term-missing
# Coverage ≥ 72% required on new code (project default)
```

---

## 8. Safety — Never-Do List (from CLAUDE.md)

- ❌ `sed -i`, `perl -pi -e`, `awk -i inplace` — confirmed WSL2 file wipe.
- ❌ Parallel Bash + MCP + Edit in one tool-call batch — cascade kills siblings.
- ❌ Destructive ops (DROP, DELETE) against `~/.sio/sio.db` — always clone to `/tmp/sio-test.db` first.
- ❌ Stub functions that pretend to optimize/generate (Constitution XI).
- ❌ Blanket `except Exception: pass` in hook paths (finding H8).

---

## 9. Next Steps

- Run `/speckit.tasks` to produce `tasks.md` (dependency-ordered TDD task pairs).
- Run `/speckit.analyze` to cross-check spec/plan/tasks for drift.
- Execute via `/speckit.implement` (or `devkid.orchestrate` + `devkid.execute` for parallel waves).
