# Live Sessions — `sio live`

Discover and read **in-progress** coding-agent sessions, and get warned before
two of them collide on the same working tree.

> Skill: `/sio-live` · Commands: `sio live ls` · `sio live show` · `sio live attach`
> Siblings: `/sio-search` (indexed history) · `/sio-watch` (tail one session by handle)

---

## The gap this closes

SIO's search over sessions — `sio search`, and the absorbed `session-search` —
works against **finished, indexed** transcripts. That is exactly the wrong tool
for one important question: *what is running **right now**, and am I about to step
on it?*

A live, still-being-written session is invisible to an index that was built from
past transcripts. So `sio live` does not use the index at all. It detects
sessions by a **real-time signal** — a transcript file whose mtime changed in the
last few minutes — and then reads three fields that Claude Code writes on **every
JSONL line** but that SIO's indexers never consumed:

| Field | Used for |
|---|---|
| `cwd` | the session's working directory |
| `gitBranch` | fallback branch label |
| `sessionId` | the canonical session identity |

From `cwd` it resolves — via live `git` — the **working tree root**, the
**repository** (git common dir), and the **current branch (HEAD)**. That is
enough to answer "who is live, where, on what branch" and to flag collisions.

This is a net-new index dimension: SIO keys everything else on the transcript's
filename UUID and never on repo/branch, so nothing competes with the repo/branch
view `sio live` builds.

---

## `sio live ls` — who is live, and are they colliding?

```bash
sio live ls                 # sessions active in the last 60 min (default window)
sio live ls -m 15           # only the last 15 minutes
sio live ls --repo ~/Documents/code/cadastre   # scope to one repo path
sio live ls --as-json       # machine-readable (an agent can poll this)
```

Example:

```
   agent   id       repo        branch                       cwd          age    msgs  last
⚠  claude  aee069b  cadastre    feat/single-source-recurr…   ~/…/cadastre  3s     1693  Bash(gh pr checks 12 …)
◆  claude  44c2467  SIO         main                         ~/…/SIO       5s      204  attachment
   claude  0e3b208  fluxstate   003-pluggable                ~/…/fluxstate 42s    2287  [Bash]
⚠  claude  a2e6a6e  cadastre    feat/single-source-recurr…   ~/…/cadastre  10m    2008  system

⚠ Collisions — same working tree, live:
  ~/Documents/code/cadastre  branch=feat/single-source-recurrence  →  aee069b, a2e6a6e
  Two sessions in one checkout on the same branch WILL collide — split to a worktree or coordinate.
```

### Row markers

| Marker | Meaning |
|---|---|
| `⚠` | **Collision** — shares a live working tree with another session |
| `◆` | Same working tree as the shell you ran `sio live` from |
| `← you` | Your own session — only when a session-id env var is set (see below) |

### The collision model

The collision key is the **working tree root** (`git rev-parse --show-toplevel`),
not just the repo. That is deliberate:

- **Same working tree, ≥2 live sessions → collision (`⚠`).** They are literally
  editing the same files on the same branch; concurrent writes will conflict.
- **Same repo but different git worktrees → not a collision.** Separate worktrees
  have independent working directories and HEADs — that is precisely the
  isolation move the warning recommends. Sessions in `cadastre` and
  `cadastre-epic-b` share a repository but not a working tree, so they do not
  collide.

Branch shown is the **live working-tree HEAD** from `git`, with the transcript's
`gitBranch` only as a fallback — because every session in one working tree shares
that tree's branch, and the live value is what actually governs the collision.

### `--as-json`

Emits `agent, native_id, cwd, branch, toplevel, common_dir, collision, mtime,
msgs, last` per session — so an agent in another session can poll for peers and
collisions programmatically.

---

## `sio live show <id>` — catch me up on one session

A one-shot snapshot of a session's **tail**. Resolves the id (bare, partial,
canonical `claude:<uuid>`, or a file path) and prints its recent events, enriched
so that tool calls (name + salient arg), tool results, and user turns are all
legible — not just assistant prose.

```bash
sio live show aee069b            # last 40 events (default)
sio live show aee069b -n 15      # last 15
sio live show aee069b --tools-only
sio live show aee069b --follow   # snapshot, then keep streaming
```

```
── claude:aee069be-bad9-47fd-a277-bc351073a620  (last 5)
2026-07-03T12:44:35 assistant: PR is MERGEABLE, GitGuardian passed, CI test jobs (3.11, 3.12) running…
2026-07-03T12:44:36 [Bash]: Bash(cd ~/Documents/code/cadastre && gh pr checks 12 --repo gyasis/cadastre --watch)
2026-07-03T12:44:40 user: test (3.11) fail 16s https://github.com/…  test (3.12) fail 22s https://github.com/…
```

---

## `sio live attach <id>` — attach from another session

The companion to `ls`. Once you've spotted a peer, **attach** to keep watching it
as it works: a short context tail, then a live stream. It is a **read-only
observer** — it never writes to the attached session. Ctrl-C detaches.

```bash
sio live attach aee069b          # 15 lines of context, then follow live
sio live attach aee069b -c 40    # 40 lines of context first
sio live attach aee069b --tools-only
```

The intended loop for an agent working in one session:

```
sio live ls                 # spot a same-branch peer (⚠)
sio live attach <peer-id>   # watch what it's doing before you touch shared files
```

---

## Agent coverage

**Claude Code** is covered richly — its JSONL carries `cwd` + `gitBranch` on
every line, so repo/branch/worktree/collision resolution is exact.

Other harnesses (`goose`, `codex`, `gemini`, `aider`) are **listed best-effort**
when active: shown with whatever working-dir signal is trivially available
(e.g. goose's `working_dir`) and without git collision resolution. Live
**follow** (`show --follow`, `attach`) currently supports the Claude harness; the
handle parser accepts other agents but their live adapters raise
`NotImplementedError` until implemented.

---

## How it's built (reuse map)

`sio live` is thin — it reuses the existing session plumbing and adds only the
repo/branch layer:

| Concern | Reused from |
|---|---|
| Handle parsing (`agent:native_id`, path, bare/partial id) | `sio.core.session_handle` |
| Locate a session file / manifest | `sio.adapters.factory.manifest_from_handle` |
| Tail a live file (follow loop) | `sio.adapters.claude_code.ClaudeAdapter.get_live_stream` (same tailer as `sio watch`) |
| Event normalization | `sio.adapters.base.SessionEvent` |
| **Net-new** — read `cwd`/`gitBranch` from the tail, resolve git worktree/branch, dedup, flag collisions | `sio.cli.live` |

Implementation: `src/sio/cli/live.py` (registered in `src/sio/cli/main.py` next
to `sio search`). Tests: `tests/unit/test_live.py`.

### Gotchas encoded in the implementation

- **Sub-agent transcripts** live at `<session-id>/subagents/agent-*.jsonl` and
  carry the **parent** session id. They are the same session, not a concurrent
  one — `sio live` skips them, and dedups any resume/compact continuations that
  share a session id, so one logical session is exactly one row (and never
  collides with itself).
- **`← you` self-marking** reads an optional `SIO_SESSION_ID` / `CLAUDE_SESSION_ID`
  env var. Without it, your own working tree is still marked `◆`.

---

## See also

- `/sio-search` — search **indexed** (finished) session history across harnesses.
- `/sio-watch` — tail a **single** session by handle (`sio watch --session …`).
- `docs/CLI_REFERENCE.md` — auto-generated flags for `sio live` and every command.
