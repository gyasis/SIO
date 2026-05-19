# Use Case: Onboarding (Yourself or an Agent) to an Unfamiliar Codebase

**Scenario class:** You're returning to a `<repo>` you haven't touched in six weeks. Or you've inherited a colleague's project. Or a fresh agent session has been dropped into a directory with thirty modules, an opinionated `CLAUDE.md`, and no obvious entry point. `git log` tells you *what* changed, but not *why* it was tried, what was abandoned, or which sequence of tools actually got it to compile last time.

SIO sits one layer above git: **it remembers what the agent did, what the agent tried, and what the agent gave up on.** That's the layer onboarding needs.

---

## The story

You open `<repo>` after six weeks. You remember shipping a `<feature area>` PRD, but you don't remember:

- Which dead ends you ruled out (and why)
- Whether the integration with `<external system>` ever stabilized
- What's still TODO vs what's actually done vs what's been silently abandoned
- Which CLI invocation actually works for the local test loop (you tried four; one of them worked)

`README.md` tells you the happy path. `CLAUDE.md` tells you the rules. Neither tells you the *history* — the lived experience of the last person (or agent) who sat in this directory and tried to make it move.

That's the SIO read.

---

## Phase 1 — Map the territory

Before reading a single source file, get the shape of past work.

### 1. Which sessions ever touched this code?

```bash
session-search "<repo>" --all --files
```

`session-search --files` returns just the JSONL paths — a list of every session that mentioned `<repo>`. Sort by mtime. The most recent five or six sessions are your "what's the current state" set; the older ones are archaeological.

> **Rule of thumb:** start with `--files` always. You're looking at a histogram of activity, not reading content yet.

### 2. What was the last substantive session about?

```bash
session-search "<repo>" --recent 30 --files
# Read the top 1-2 JSONL files directly with Read tool
```

The most recent session is your handoff note from past-you. Read it for: the last task attempted, the last error hit, the last decision made. If there's a `Next pickup:` line in a PRD diary or a session note, that's where you actually resume.

### 3. Day-by-day, what happened?

```
/work-recap
```

`/work-recap` (sibling skill, not strictly SIO core but adjacent) gives you a chronological summary of recent sessions — what was done, what was decided, what files moved. Useful as a "headline reel" before you dive into any specific session.

**Phase 1 combo (~1 minute):**
- `session-search --files` to enumerate
- Read the top JSONL for the last substantive turn
- `/work-recap` for the multi-day arc

You now know the *shape* of past work without having read a line of source code.

---

## Phase 2 — Find codified knowledge

Past sessions are raw. Codified playbooks and mined patterns are processed — the agent (or you) already extracted the useful bits.

### 4. Has anyone written a playbook for this kind of task?

```
/sio-recall "<task description>"
# e.g. "running the local test loop in <repo>"
# e.g. "regenerating <feature area> after schema change"
```

`/sio-recall` searches distilled sessions — past successful flows that were compressed into reusable recipes. If you (or a previous agent) ever ran `/sio-codify-workflow` on a task in this repo, it'll surface here. The output is the exact tool sequence and command order that worked.

If nothing returns, that's information too: this repo has no codified playbooks yet. Consider running `/sio-codify-workflow` at the end of your session to leave a breadcrumb for the next person.

### 5. What tool sequences actually work in this repo?

```
/sio-flows --query "<repo>"
# or narrower: /sio-flows --query "<feature area>"
```

`/sio-flows` mines positive tool patterns — recurring successful sequences across all sessions, not curated. If you're trying to figure out "what's the *normal* way to do `<X>` in this repo," `/sio-flows` answers it empirically. Different from `/sio-recall` (curated, prose) — this one is the raw mined evidence.

### 6. Are there mined skill candidates specific to this repo?

```
/sio-discover
```

`/sio-discover` looks across mined patterns for tool sequences that occur often enough to be worth promoting into a named skill. If a workflow happens four times in this repo's sessions but has never been codified, it shows up here. Read it as a list of "things this repo has clearly *wanted* a skill for, but never got one."

### 7. Project-specific notes — the file-memory layer

```bash
ls ~/.claude/projects/-home-gyasisutton-dev-projects-<repo>/memory/
# Then Read MEMORY.md if it exists
```

File memory (`~/.claude/projects/.../memory/`) is where project-specific facts get pinned — things too narrow for Graphiti and too important for a session note. If the previous agent (or you) was disciplined, there's an index here.

**Phase 2 gate:** if `/sio-recall` and `/sio-flows` both return rich results, the repo has institutional memory and you can resume work confidently. If both are sparse, you're in pioneer territory — proceed cautiously and codify as you go.

---

## Phase 3 — Identify gaps (the rough edges)

Where the agent struggled before is where *you* will struggle now. SIO surfaces the friction.

### 8. What error clusters exist in this codebase's history?

```
/sio-scan --grep "<repo>"
# or narrower by feature area
```

`/sio-scan` returns error events, repeated attempts, and `user_correction` markers. Filtered to this repo, it's a map of historical pain. The clusters tell you:

- **A repeated error** → an unsolved problem. The previous session didn't fix it; it just stopped that day.
- **Repeated attempts on the same command** → an undocumented gotcha. The fix is somewhere in the JSONL — read the resolving turn.
- **`user_correction` cluster** → the agent's defaults are wrong for this repo. There's a missing rule or skill.

Each cluster is a known rough edge. Going in armed with the list is the difference between "stepping on the rake" and "knowing where the rakes are."

### 9. Are existing rules being violated here?

```
/sio-violations
```

If the repo has CLAUDE.md rules (project or global), `/sio-violations` shows which sessions ignored them. A rule that's been violated three times in this repo without anyone fixing it is either: (a) a rule that needs rewording, or (b) a real ongoing problem the previous sessions punted on. Either way, you want to know before you write your first line.

---

## Putting it together — onboarding checklist

```bash
# Phase 1 — Map (1 min)
session-search "<repo>" --all --files
# Read top 1-2 most recent JSONLs
/work-recap

# Phase 2 — Codified knowledge (parallel, 1 min)
/sio-recall "<task you're picking up>"
/sio-flows --query "<repo or feature area>"
/sio-discover
ls ~/.claude/projects/-home-gyasisutton-dev-projects-<repo>/memory/

# Phase 3 — Gaps (1 min)
/sio-scan --grep "<repo>"
/sio-violations
```

**Total time:** about three minutes. **Output:** a map of what was tried, what worked, what's codified, and where the rakes are.

Now read the code.

---

## Why this loop matters

The thing onboarding *misses* without SIO: **the abandoned attempts.** `git log` shows what landed. The README shows the happy path. CLAUDE.md shows the rules. None of them show the three approaches you tried last month before settling on the fourth — and none of them stop you from re-trying the first three.

A fresh agent in particular has no episodic memory across sessions. Without SIO, every fresh session re-discovers the same dead ends. With SIO, the agent can read what its past selves tried and skip the re-litigation.

This is true whether the "future self" is:

- **You, in six weeks**, having forgotten which CLI flag actually works
- **A colleague** picking up your repo cold
- **A fresh agent session** dropped into the directory with no scrollback

All three benefit from the same three-minute read.

---

## What this use case is *not*

- Not a replacement for reading `README.md`, `CLAUDE.md`, or the actual source. SIO maps the *agent's history* with the code; it does not teach you the code itself.
- Not a replacement for a PRD or design doc. If the previous session was mid-plan, the PRD is the canonical artifact — SIO points you to it, doesn't replicate it.
- Not useful on a brand-new repo with no session history. SIO needs sessions to mine; on day one of a greenfield project, there's nothing to recall. (Codify as you go and the next person benefits.)
- Not real-time. The signal is mined from accumulated transcripts. If you started this repo this morning, come back in a week.

---

## Cross-references

- `docs/use-cases/validating-a-config-change.md` — the before/after loop for risky changes
- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/user-guide.md` — every CLI surface
- `~/.claude/rules/tools/sio.md` — when to route the user to which SIO skill
- `~/.claude/rules/domains/memory-search.md` — the cascade memory protocol (`session-search` first, always)
