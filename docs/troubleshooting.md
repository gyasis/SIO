# Troubleshooting

This page covers the most common failure modes when running SIO. For each problem,
a **symptom**, **cause**, and **fix** are given. Many of these are also caught
automatically by `sio doctor`.

---

## `sio suggest` fails: no LLM configured

**Symptom**

```
sio suggest
# Returns 0 suggestions, or exits with:
# "No LLM backend available"
# OR all providers return None / empty
```

**Cause**

SIO needs at least one LLM provider to generate suggestions. The model is resolved
in priority order:

1. `SIO_TASK_LM` env var (any DSPy-compatible model string)
2. `[llm.task]` block in `~/.sio/config.toml`
3. Hard default: `gemini/gemini-flash-latest` (requires `GEMINI_API_KEY`)

If none of these resolve to a working model, suggestions cannot be generated.

`sio doctor` → `config.toml` check will report `warn` if every `[llm]` block
is commented out.

**Fix**

Option A — set an API key directly (fastest):

```bash
# Gemini (default backend)
export GEMINI_API_KEY=your-key
sio suggest

# OpenAI
export OPENAI_API_KEY=your-key
export SIO_TASK_LM=openai/gpt-4o-mini
sio suggest

# Anthropic
export ANTHROPIC_API_KEY=your-key
export SIO_TASK_LM=anthropic/claude-haiku-4-20250514
sio suggest
```

Option B — configure via `~/.sio/config.toml`:

```toml
[llm.task]
model = "gemini/gemini-flash-latest"
api_key_env = "GEMINI_API_KEY"    # reads key from this env var at call time
```

Option C — local/offline (no API key):

```bash
export SIO_TASK_LM=ollama_chat/qwen3-coder:30b
# requires `ollama serve` and the model pulled locally
sio suggest
```

---

## `sio mine` (or `sio scan`) finds 0 errors

**Symptom**

```
sio mine
# "0 sessions processed" or "0 errors extracted"
```

**Cause — wrong or missing session paths**

SIO reads Claude Code session transcripts from `~/.claude/projects/` (JSONL files)
and SpecStory history from `~/.specstory/history/`. If neither exists, or the files
are in a non-standard location, the miner finds nothing.

**Cause — empty or very new database**

The `sio.db` at `~/.sio/sio.db` (or wherever `SIO_DB_PATH` points) may be freshly
initialized and contain no error records yet. Mine a specific session to populate it.

**Fix**

Check which paths SIO is scanning:

```bash
sio status          # shows session/error counts per source
sio doctor          # checks ~/.sio/ dir and harness install
```

If the paths are wrong:

```bash
# Verify the JSONL files exist
ls ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null | head -5
ls ~/.specstory/history/*.md 2>/dev/null | head -5

# Point to an alternate data dir
SIO_HOME=/path/to/alternate/sio sio mine
```

If the database is fresh (normal on first install), run a manual mine:

```bash
sio mine --all        # process all available sessions
sio status            # should now show non-zero error counts
```

---

## `fastembed` ONNX model downloads on first run

**Symptom**

On the first `sio mine` or `sio suggest`, SIO pauses and prints something like:

```
Fetching 9 files: 100%|████████████████████| 9/9 [00:12<00:00]
```

**Cause**

SIO uses `fastembed` with the `sentence-transformers/all-MiniLM-L6-v2` model (384
dimensions) for embedding error text into vectors for clustering. On first run,
`fastembed` downloads the ONNX model (~22 MB) from HuggingFace and caches it at
`~/.cache/huggingface/hub/` (or `~/.cache/fastembed/`). This is a one-time download.

**Fix**

Nothing to do — wait for the download to finish. On subsequent runs the model loads
from cache in under a second.

If you are on an air-gapped machine or want to use a different model:

```toml
# ~/.sio/config.toml
embedding_model = "sentence-transformers/paraphrase-MiniLM-L3-v2"   # smaller alternative
# Or provide a remote endpoint instead:
embedding_backend = "api"
embedding_api_url  = "https://your-endpoint/embeddings"
embedding_api_key  = "your-key"
```

---

## `sio init` or hook registration does nothing / hooks are missing

**Symptom**

```
sio init
# "0 hooks registered" or "already installed" but hooks are not firing
```

Or `sio doctor` reports:

```
Harness install   WARN   claude-code: 0 installed, 5 missing, 0 drifted
```

**Cause — `~/.claude/` does not exist**

The Claude Code adapter checks for `~/.claude/` before auto-detecting the harness.
On a fresh machine where Claude Code has never been launched, the directory is
absent and the adapter silently skips.

**Cause — hooks registered but not wired into settings.json**

`sio init` writes hook entries into `~/.claude/settings.json`. If that file is
missing or unreadable, registration silently fails.

**Fix**

```bash
# Launch Claude Code at least once (creates ~/.claude/)
# Then re-run init with an explicit harness:
sio init --harness claude-code

# Verify hooks are registered
sio doctor                        # → "Harness install: OK"
cat ~/.claude/settings.json | python3 -m json.tool | grep -A3 "PostToolUse"

# If settings.json is corrupt, remove it and re-init:
rm ~/.claude/settings.json
sio init --harness claude-code
```

Force-reinstall (overwrites drifted files):

```bash
sio init --harness claude-code --force
```

---

## Data capture stopped after upgrading / reinstalling `sio` (isolated installs)

**Symptom**

```
# Capture worked, then after `uv tool upgrade` / `pipx reinstall` / moving the
# install, no new rows land in ~/.sio/<platform>/behavior_invocations.db.
sio doctor
# Harness install   WARN   claude-code: hook command points at a missing interpreter
```

**Cause — the hook command is pinned to the install's interpreter**

`sio init` registers each hook in `~/.claude/settings.json` as
`<sys.executable> -m <module>` — the **absolute path of the Python that ran
`sio init`**. With an isolated install (`uv tool`, `pipx`) that path lives
inside the tool's venv. If the venv is rebuilt at a new path (a `uv tool
upgrade`/`--reinstall`, a `pipx reinstall`) or removed, the pinned command no
longer resolves and Claude Code silently skips the hook — capture stops.

A special case: bootstrapping with **ephemeral `uvx`** writes a path that is
discarded the moment the command finishes, so capture never starts.

**Fix**

```bash
# Re-pin the hooks to the current interpreter
sio init --harness claude-code --force

# Confirm the command now points at a python that exists
sio doctor
grep -A3 PostToolUse ~/.claude/settings.json
```

**Avoid the recurrence**

- Use **`uv tool install`** (persistent), not `uvx`, for the capture setup.
- Make **`sio init`** part of your upgrade routine: `uv tool upgrade … && sio init --force`.
- Data *access* (reading transcripts, `~/.sio/*.db`) is never affected — only the hook-invocation path is.

---

## DSPy import errors or version conflicts

**Symptom**

```
ImportError: cannot import name 'GEPA' from 'dspy'
# or
AttributeError: module 'dspy' has no attribute 'JSONAdapter'
```

**Cause**

SIO requires `dspy >= 3.1.3`. Older installs of DSPy (v2.x) have a different API
and are missing `dspy.GEPA`, `dspy.JSONAdapter`, and other symbols SIO depends on.

**Fix**

```bash
pip show dspy-ai   # check installed version
# If < 3.1.3:
pip install "dspy>=3.1.3"

# Or upgrade the whole SIO install (pulls pinned deps):
pip install --upgrade self-improving-organism
```

If you are running inside a virtual environment or `uv`:

```bash
uv pip install "dspy>=3.1.3"
# or
source .venv/bin/activate && pip install "dspy>=3.1.3"
```

Run `sio doctor` after upgrading — it checks `_check_dspy_alive()` and reports
whether DSPy's core types load correctly.

---

## Cron/schedule not firing

**Symptom**

`sio schedule install` reports success, but `sio mine` is never triggered automatically.

**Fix — verify the cron entries exist**

```bash
crontab -l | grep "SIO passive analysis"
# Expected output:
# @daily  python3 -m sio schedule run --mode daily  # SIO passive analysis
# @weekly python3 -m sio schedule run --mode weekly # SIO passive analysis
```

If the entries are missing, reinstall:

```bash
sio schedule install
```

**Fix — PATH not visible to cron**

Cron jobs run with a minimal environment — `~/.local/bin` (where pip user-installs
land) is often not on cron's `PATH`. The cron entry uses `sys.executable -m sio`
to bypass this (hardcodes the full Python interpreter path), but if the venv was
deleted or rebuilt, the path may be stale.

```bash
# Reinstall after any venv rebuild:
sio schedule uninstall
sio schedule install

# Verify the Python path in the cron line is correct:
crontab -l | grep sio
which python3   # compare to the interpreter in the cron line
```

**Fix — cron not available (Windows)**

SIO's scheduler requires `crontab` (POSIX). On Windows, use Task Scheduler instead.
`sio schedule install` will raise `RuntimeError: Cron scheduling is not supported on Windows`.

---

## Database locked (WAL mode error)

**Symptom**

```
sqlite3.OperationalError: database is locked
```

**Cause**

SIO opens `~/.sio/sio.db` in WAL (Write-Ahead Logging) mode. This is safe for
multiple readers + one writer. A lock error usually means:

- Two concurrent `sio optimize` runs are open simultaneously (each tries to write).
- A previous run crashed and left a stale lock file (`sio.db-wal` or `sio.db-shm`).

**Fix**

```bash
# Check for stale WAL files:
ls -lh ~/.sio/sio.db*
# If sio.db-wal exists and there is no sio process running:
rm ~/.sio/sio.db-wal ~/.sio/sio.db-shm 2>/dev/null || true

# Verify no other sio processes are running:
pgrep -a python | grep sio
```

If you regularly run background mining and optimization simultaneously, ensure
they target different DB paths or run them sequentially via cron.

---

## Wrong DB path (SIO reads the wrong database)

**Symptom**

`sio status` shows 0 errors even though sessions have been mined before. Or `sio doctor`
reports `sio.db` not found even after `sio init` ran successfully.

**Cause**

`SIO_DB_PATH` is set in your environment (or shell config) pointing to a different
path than `~/.sio/sio.db`. The global rules file `~/.claude/rules/tools/sio.md`
lists a known stale legacy path (`~/.claude/sio.db`) that some early installs wrote.

**Fix**

```bash
# See which DB path SIO is currently using:
echo "${SIO_DB_PATH:-~/.sio/sio.db (default)}"

# Check for the legacy empty file:
ls -lh ~/.claude/sio.db 2>/dev/null   # should not exist

# If SIO_DB_PATH is set to something wrong, unset it:
unset SIO_DB_PATH

# Confirm the canonical DB is populated:
ls -lh ~/.sio/sio.db
sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM error_records;" 2>/dev/null
```

The canonical path is always `~/.sio/sio.db`. The `SIO_DB_PATH` variable is
intended only for test environments and alternate installs.

---

## `sio suggest` budget error

**Symptom**

```
BUDGET EXCEEDED: 24h spend X.XXXX > cap Y.YYYY.
Set [budget].rolling_24h_usd higher in ~/.sio/config.toml,
or pass --budget-override <USD> to escape this run.
```

**Cause**

SIO tracks rolling 24 h LLM spend in `~/.sio/usage.log` and enforces a cap of $5.00
by default (or `[budget].rolling_24h_usd` from config.toml). Running GEPA on a large
dataset can exceed this quickly.

**Fix**

Raise the cap in config.toml:

```toml
# ~/.sio/config.toml
[budget]
rolling_24h_usd = 20.0   # allow up to $20/day
```

Or override for a single run:

```bash
sio optimize --budget-override 25
```

Or set the env var:

```bash
export SIO_BUDGET_OVERRIDE=25
sio suggest
```

---

## Still stuck? Run `sio doctor`

`sio doctor` runs all diagnostic checks in one command and prints a structured
table of results with copy-pasteable fix hints:

```bash
sio doctor
```

Checks include: Python version, package collision (`sio` vs `self-improving-organism`),
`~/.sio/` directory and subdirs, `config.toml` LLM configuration, bundled bootstrap
content, harness/hook install health, DSPy availability, run log health, optimizer
ladder discipline, budget state, and stuck reflection runs.

If `sio doctor` passes but a command still fails, check the run log:

```bash
sio runs list          # see recent optimize/suggest runs
sio runs show <id>     # inspect a specific run's output
```
