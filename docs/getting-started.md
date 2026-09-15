# Getting Started

## Prerequisites

- Python 3.11 or newer
- pip (or uv)
- Claude Code (or any AI coding CLI that produces SpecStory/JSONL transcripts)

## Installation

### Isolated install (recommended for using SIO)

SIO pulls in DSPy, fastembed, onnxruntime, and friends. Install it in an
**isolated environment** so those never collide with your global or project
Python — the `sio` binary still lands on your PATH:

```bash
# ⭐ uv tool — isolated venv, sio on PATH (uv 0.4+)
uv tool install "self-improving-organism[all] @ git+https://github.com/gyasis/SIO.git@v0.3.1"

# or pipx — equivalent isolation
pipx install "self-improving-organism[all] @ git+https://github.com/gyasis/SIO.git@v0.3.1"

# then stage skills + register hooks, and verify
sio init
sio --version
```

### Plain pip (use a venv to stay isolated)

```bash
python -m venv .venv && . .venv/bin/activate
pip install "git+https://github.com/gyasis/SIO.git@v0.3.1#egg=self-improving-organism[all]"
sio init && sio --version
```

### From source (editable, for development)

```bash
git clone https://github.com/gyasis/SIO.git
cd SIO
pip install -e ".[all,dev]"      # [all] = DSPy + Parquet + Gemini polish; ".[dev]" = core only
sio init                          # stages the SIO skill suite + registers hooks
sio --version
```

> **⚠️ Isolation and data capture — the one rule.** `sio init` writes the
> telemetry hooks into `~/.claude/settings.json` as `<python> -m <module>`,
> pinned to the **interpreter that ran `sio init`**. In an isolated install
> that's the tool's own venv, so capture works perfectly — with two caveats:
>
> | Situation | What happens | Do this |
> |-----------|--------------|---------|
> | Bootstrap with ephemeral **`uvx`** | env is discarded → pinned hook path is dead | use **`uv tool install`** (persistent), not `uvx`, for the capture setup |
> | **`uv tool upgrade`** / **`pipx reinstall`** moves the venv | hooks silently stop firing | **re-run `sio init`** afterward |
> | Not sure if capture is live | — | run **`sio doctor`** (it flags stale hook paths) |
>
> **Data *access* is never affected** by isolation: SIO reads transcripts from
> `~/.claude/projects/` and reads/writes `~/.sio/*.db` — those are HOME paths,
> independent of which Python environment `sio` runs in.

### pi (`@earendil-works/pi-coding-agent`)

`sio init` auto-detects pi when `~/.pi/agent/` exists (or the directory named
by `PI_CODING_AGENT_DIR`); force it with `--harness pi`:

```bash
sio init --harness pi              # → ~/.pi/agent/skills/sio-*/SKILL.md
sio init --harness pi --dry-run    # preview; lists any pi skill SIO will NOT touch
sio init --harness pi --status
sio init --harness pi --uninstall  # removes only what the manifest tracks
```

What pi gets, and what it deliberately does not:

| Bundled asset | On pi |
|---|---|
| Skills (`skills/<name>/SKILL.md` + siblings) | Installed. Each `SKILL.md` is checked against pi's own loader rules (`name` = `[a-z0-9-]{1,64}`, `description` required and ≤ 1024 chars) and transformed only if it would fail — the transform is listed in the install output. |
| Tool rules (`rules/tools/*.md`) | **Not installed** — pi has no rules dir; standing context is your own `AGENTS.md`, which SIO never edits. Reported as unsupported. |
| Hook telemetry | **Not registered** — pi has extensions, not a hooks system. Ingest pi sessions with `sio mine --agent pi` / `sio search --agent pi` instead. |

Skills SIO did not install — including symlinked ones from other tools — are
never modified or removed, even if they share a name with an SIO skill
(`--force` is the only override, and it backs the file up first). Restart pi
after installing; it reads the skills dir at startup.

## First Run

### 1. Mine your recent sessions

SIO looks for session files in two default locations:

| Source | Default Path |
|--------|-------------|
| SpecStory | `~/.specstory/history/` |
| Claude JSONL | `~/.claude/projects/` |

Run your first mining pass:

```bash
sio mine --since "7 days"
```

Expected output:

```
Scanned 42 files
Found 8 errors
```

### 2. View discovered patterns

```bash
sio patterns
```

This clusters similar errors and ranks them by frequency and recency. You'll see a table like:

```
# Pattern                                              Errors Sessions Last Seen  Score
1 Edit tool path not found                                  5        3 2026-02-24  0.85
2 Bash command permission denied                            3        2 2026-02-23  0.62
```

### 3. Review suggestions

```bash
sio suggest-review
```

SIO presents each suggestion with a confidence score and proposed change. You choose:
- **a** — approve (queued for application)
- **r** — reject (dismissed)
- **d** — defer (revisit later)
- **q** — quit review

### 4. Check status

```bash
sio status
```

Shows a summary of your pipeline:

```
SIO v2 Status
------------------------------
Errors mined:      8
Patterns found:    3
Datasets built:    2
Pending reviews:   1
Applied changes:   0
```

## Setting Up Passive Analysis

Install cron jobs that run the pipeline automatically:

```bash
sio schedule install
```

This creates two cron entries:
- **Daily** at midnight — mine last 24 hours, cluster, suggest
- **Weekly** on Sunday — full 7-day analysis with dataset building

Check schedule status:

```bash
sio schedule status
```

### 5. Discover positive tool flows (v2.1)

```bash
sio flows
```

This finds recurring tool sequences that led to successful outcomes — patterns worth repeating.

### 6. Distill a session to a playbook (v2.1)

```bash
sio distill --latest
```

Extracts the winning path from your most recent session, stripping out failed attempts and dead ends.

### 7. Topic-filtered recall (v2.1)

```bash
sio recall "dbt model debugging"
```

Searches your session history for a specific topic, detects struggle-then-fix patterns, and returns a focused playbook. Add `--polish` for a Gemini-cleaned version (~$0.02).

### 8. Quick verification of v2.1 features

```bash
# Verify flows work
sio flows --min-support 1

# Verify distill works
sio distill --latest

# Verify export works
sio export-dataset --task all --dry-run
```

## What Happens Next

SIO runs passively in the background:

1. Daily cron mines yesterday's errors
2. Patterns accumulate over time
3. When a pattern reaches enough occurrences, SIO generates a suggestion
4. Suggestions appear in `~/.sio/suggestions.md` (the home file)
5. You review and approve/reject at your convenience
6. Approved changes are applied to your CLAUDE.md, hooks, or skills
7. If something goes wrong, `sio rollback <id>` reverts the change

## Verification

Run the test suite to verify everything is working:

```bash
pytest
```

All 756 tests should pass.

## Setup on a New Machine (Quick Reference)

```bash
git clone <repo>
cd SIO
pip install -e ".[all,dev]"
bash scripts/install-skills.sh
sio mine --since "7 days"
```

This gives you the full pipeline: error mining, flow discovery, session distillation, recall, dataset export, and DSPy training.

## Next Steps

- [User Guide](user-guide.md) — Full CLI reference
- [Cookbook](cookbook.md) — Recipes for common workflows
- [Configuration](configuration.md) — Customize thresholds and behavior
- [Architecture](architecture.md) — Understand the system design
