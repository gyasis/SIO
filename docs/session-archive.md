# Session archive (`sio archive`)

SIO keeps its own copy of every coding agent's raw session files, so history
survives whatever retention policy a harness ships with.

## Why

Claude Code deletes transcripts older than `cleanupPeriodDays`, which defaults to
**30 days**. Other harnesses can change their defaults at any time. A tool whose
job is to learn from past sessions cannot depend on the harness keeping them.

This was learned the hard way: a SpecStory job had been keeping the long history,
it stopped running in April 2026, nothing noticed, and five months of full Claude
transcripts were deleted before anyone looked. `sio archive` replaces that job and
needs no other service.

## What it does

```bash
sio archive sync           # one pass; exit 1 if any file failed
sio archive sync --json    # same, machine-readable
sio archive status         # last run + per-agent manifest totals
sio archive install        # schedule the sync daily (see Scheduling)
```

Each pass mirrors every known session store into `~/.sio/archive/<agent>/...`
(override with `SIO_ARCHIVE_DIR`; it follows `SIO_HOME` otherwise), keeping each
store's own layout and the **raw files** (JSONL, JSON, SQLite) rather than a
rendered format.

| Archive path | Source |
|---|---|
| `claude/projects/` | `~/.claude/projects/` |
| `claude/history.jsonl` | `~/.claude/history.jsonl` (every prompt typed) |
| `codex/sessions/`, `codex/history.jsonl` | `~/.codex/` |
| `gemini/tmp/` | `~/.gemini/tmp/` |
| `pi/sessions/` | `~/.pi/agent/sessions/` |
| `kimi/sessions/` | `~/.kimi-code/sessions/` |
| `promptchain/sessions/` | `~/.promptchain/sessions/` |
| `goose/sessions/` | `~/.local/share/goose/sessions/` |
| `opencode/` | `~/.local/share/opencode/` |

A store that does not exist is reported `absent` and skipped.

### Per-file rules

| Situation | Action |
|---|---|
| not archived yet | copy (`new`) |
| same size, same tail bytes | nothing (`unchanged`) |
| grew, and the archived bytes are still its prefix | copy over (`appended`) |
| shrank or was rewritten | keep the old copy as `<name>.~<UTC stamp>`, then copy (`rewritten`) |
| SQLite database | snapshot with the SQLite backup API, WAL included (`sqlite`) |
| `-wal`, `-shm`, `-journal` | skipped; the backup reads through them |
| deleted at the source | **archived copy kept**; manifest row gets `source_gone_at` |

Nothing is ever deleted from the archive. Every write goes to a temp file and is
renamed into place, so an interrupted run never leaves a half-written copy.
Archived databases are switched out of WAL mode so reading them never creates
sidecar files inside the archive.

### Outputs

- `_archive.db` — manifest, one row per file: `agent, rel, size, src_mtime,
  first_seen, last_synced, source_gone_at, versions`.
- `_status.json` — the last run: per-source counts, errors, total bytes, `ok`.

## Search

`sio search` reads each agent's live store **plus** SIO's archived copy, so a
session the harness deleted is still found. Nothing is reported twice:

- a session file that still exists live is read from the live copy only;
- shared stores that exist in both places (Codex `history.jsonl`, the goose and
  opencode SQLite databases) are read from both, and a message already seen live
  is dropped. A row deleted from the live database is still found in the archive.

Archived Claude hits carry `source_kind: "archive"` (ripgrep and Python paths);
archived hits from other agents carry `metadata.archived = true`. Older copies
kept as `<name>.~<UTC>` are not searched. `sio mine` still reads live stores only.

## Scheduling

```bash
sio archive install                 # daily (1440 minutes)
sio archive install --interval 60   # hourly
sio archive install --dry-run       # show the files and commands, change nothing
```

On Linux this writes `~/.config/systemd/user/sio-archive.{service,timer}` and
enables the timer; on macOS it writes `~/Library/LaunchAgents/io.sio.archive.plist`
(log: `~/.sio/archive-sync.log`) and loads it. Both use the absolute path of
`sio`, because schedulers do not read your shell `PATH`. Re-running is safe: files
that already match are left alone. Without systemd or launchd it stops with an
error; schedule `sio archive sync` with cron instead.

Every pass is cheap (about one second for 1.4 GB when little has changed). The
units it writes look like this:

```ini
# ~/.config/systemd/user/sio-archive.service
[Service]
Type=oneshot
# systemd does not read your shell PATH: use the absolute path from `command -v sio`
ExecStart=/absolute/path/to/sio archive sync

# ~/.config/systemd/user/sio-archive.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
```

Hourly is far inside Claude Code's 30-day window, so the harness default can stay
as it is.
