# Off-session briefing store

## Problem

The session-start briefing (`sio briefing`, injected by each harness adapter's
SessionStart hook) is built by `build_session_briefing()`, which scans the SIO
DBs — now ~93 MB (`sio.db`) + ~131 MB (`behavior_invocations.db`). The original
design (commit `3de1a15`, 2026-04-03) assumed `<500 chars, <3s`, so it computed
the briefing **synchronously on every session start** with no cache and no
timeout. As the corpus grew (and after the 14-day search-discipline scan landed
in `4a3df6f`), that synchronous compute grew to **2+ minutes**, blocking every
interactive session start (Claude Code and any other adapter).

## Design: compute off-session, read on-session

Heavy work moves off the hot path entirely.

```
  systemd user timer / passive-analysis cron          session start (any agent)
  ─────────────────────────────────────────           ─────────────────────────
  sio briefing --refresh --if-idle                     hook -> briefing_store.read_store()
        │  (off-hours / idle, niced, hard-capped)              │  (instant file read)
        ▼                                                       ▼
   ~/.sio/cache/session_briefing.txt  ◄──── the STORE ────►  injected verbatim
```

- **Store** — `~/.sio/cache/session_briefing.txt` (`sio.suggestions.briefing_store`).
  Written atomically; read is a `stat` + small file read that never raises.
- **Session-start hooks are pure readers.** `read_store()` — zero compute, zero
  subprocess, instant. Missing/empty store → inject nothing (warms next cycle).
  Lives in core, so **every** adapter (Claude Code, Codex, Goose, …) reads the
  same store; not Claude-specific.
- **Off-session writers** call `refresh_store()`:
  - the **systemd user timer** (`sio briefing --refresh --if-idle`), and
  - the existing passive-analysis pipeline (`run_analysis()` — daily/weekly),
    which already runs mine→cluster→suggest off-session.
  `refresh_store()` is guarded by a lock (mutual exclusion — never two heavy
  computes at once) and a hard SIGALRM ceiling (`SIO_BRIEFING_BUILD_TIMEOUT`,
  default 15 min — a *hung-compute* guard, not a perf cap; Phase B makes the real
  compute milliseconds).

## Why a systemd **user** timer (not crontab, not Prefect/Airflow)

- **Laptop catch-up.** `crontab @daily` silently skips a run when the machine is
  asleep/off and never catches up. The timer uses **`OnCalendar` + `Persistent=true`**,
  which runs a missed run shortly after the next boot — so the worker always
  returns to a fresh briefing. (`Persistent` catch-up only applies to realtime
  `OnCalendar` timers, *not* monotonic `OnUnitInactiveState`.) `OnBootSec=3min`
  adds a fresh-boot refresh.
- **No always-on daemon / no new dependency.** The timer fires a oneshot service
  that exits — unlike Prefect/Airflow/Dagster, which want a persistent server +
  DB + UI and are built for multi-machine DAG orchestration, not "refresh a
  cached file on a laptop." (An orchestrator would *add* the resource-burden
  problem this fix removes.)
- **Non-disruptive.** Service is `Nice=19` + `IOSchedulingClass=idle` and
  `--if-idle` gated (refresh when the user is idle or the store is too stale;
  skip when active and fresh — with a staleness backstop so it never goes
  stale for long).

## Portability — wired into install

The timer is installed by **`sio init`** (and `sio schedule install-briefing`),
so moving to a new machine and running the normal SIO setup wires up the
off-session refresh automatically — no manual per-machine step. Where
`systemctl --user` is unavailable the installer degrades gracefully (the store
still works, refreshed by whatever else runs `sio briefing --refresh`).

## CLI

| Command | Effect |
|---|---|
| `sio briefing` | Read the store (instant). What hooks do. |
| `sio briefing --refresh` | Recompute + write the store (off-session path). |
| `sio briefing --refresh --if-idle` | As above, only when idle / too stale (the timer). |
| `sio briefing --live` | Force a live compute (debugging). |
| `sio schedule install-briefing` / `uninstall-briefing` | Manage the user timer. |
| `sio schedule status` | Shows the briefing-timer state. |

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `SIO_BRIEFING_TTL` | `21600` (6h) | Store freshness window. |
| `SIO_BRIEFING_BUILD_TIMEOUT` | `900` (15m) | Hard ceiling on a refresh compute. |
| `SIO_BRIEFING_STORE` | `~/.sio/cache/session_briefing.txt` | Store path override. |
| `SIO_BRIEFING_DISABLED` | unset | `1` disables the briefing entirely. |

## Phase B: fix the actual bottleneck (algorithmic, not a rollup)

The plan was a `briefing_rollup` delta table to avoid "recomputing the whole
thing." Profiling the 6 sections first showed that was the **wrong fix**:

| section | time |
|---|---|
| budget / pending / session_stats / declining_rules | < 0.9s total |
| search_discipline (131 MB scan) | **0.13s** (indexed) |
| **violations** | **minutes** |

The DBs are small and well-indexed (error_records = 5,466 rows). The entire
cost was in **`detect_violations`**, which recomputed rule-invariant work inside
its `for error × for rule` double loop: it rebuilt each rule's content-word set
and **recompiled a `\bword\b` regex per term, per rule, per error** — tens of
millions of `re.compile`/`.search` calls (420 rules × ~600 records).

A rollup would not have helped (the cost isn't a DB scan) and would have been
*invalidated on every rule-file edit* (violation counts depend on the mutable
rule set). The correct fix is algorithmic and behaviour-preserving:

1. **Hoist** all rule-invariant work out of the error loop (compute per rule once).
2. **Tokenise** each record once (`set(re.findall(r"\w+", text))`) and match plain
   words by **token-set membership** — a `\bword\b` match for a pure-`\w+` term is
   exactly "the term is a token of the text". Replaces ~290k regex `.search`
   calls with O(1) set lookups.

Verified byte-identical to the old algorithm (same violation set, fingerprinted
with `PYTHONHASHSEED=0`). Result: **violations minutes → ~1.9s** (283× on the
per-record cost), full off-session briefing compute **2–10 min → ~10s**. Even a
full recompute is now cheap, so no delta table is needed.
