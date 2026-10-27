---
name: sio
description: "SIO Suite — Session Intelligence Observer. Master skill that routes to the right SIO sub-command. Say 'sio' with any question about sessions, errors, patterns, workflows, training data, or recall."
user-invocable: true
requires:
  cli: "sio>=0.5.0"
  skills: [sio-briefing, sio-scan, sio-discover, sio-suggest, sio-validate, sio-review, sio-apply, sio-violations, sio-promote-rule, sio-velocity, sio-budget, sio-feedback, sio-status, sio-flows, sio-distill, sio-promote-flow, sio-codify-workflow, sio-recall, sio-export, sio-search, sio-watch, sio-live]
  hooks: []
  optional: [prd]
---

# SIO — Session Intelligence Observer

## Dependencies
- **CLI:** `sio >= 0.5.0`
- **Skills:** this is the master **router** — it dispatches to the full SIO skill family (see the Quick Reference table below); install the whole `skills/` folder.
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`).

SIO mines your Claude Code session history to find errors, discover workflows, distill playbooks, recall solutions, and export training data. This master skill routes your request to the right tool.

## Quick Reference

| What you want | Slash command | CLI command | Cost |
|--------------|--------------|-------------|------|
| Session-start intel briefing | `/sio-briefing` | `sio briefing` | Free |
| Find what's going wrong | `/sio-scan` | `sio mine` + `sio errors` | Free |
| Find skill candidates for THIS repo | `/sio-discover` | `sio discover` | Free |
| Get improvement suggestions | `/sio-suggest` | `sio suggest` | ~$0.05 (LLM) |
| Generate tool-arg validation rules | `/sio-validate` | (cascade-shield generator) | Free |
| Review pending suggestions | `/sio-review` | `sio suggest-review` | Free |
| Apply a suggestion | `/sio-apply` | `sio apply` | Free |
| Check rules being violated | `/sio-violations` | `sio violations` | Free |
| Promote a violated rule into a runtime hook | `/sio-promote-rule` | `sio promote-rule N --write` | ~$0.01 (LLM) |
| Check if rules are reducing errors | `/sio-velocity` | `sio velocity` | Free |
| Check instruction-file budget | `/sio-budget` | `sio budget` | Free |
| Label last AI action (++ / --) | `/sio-feedback` | `sio feedback` | Free |
| Check pipeline status | `/sio-status` | `sio status` | Free |
| **Discover what works** | `/sio-flows` | `sio flows` | Free |
| **Distill a session** | `/sio-distill` | `sio distill --latest` | Free |
| **Promote a flow into a skill file** | `/sio-promote-flow` | `sio promote-flow` | Free |
| **Codify a successful workflow as a new skill** | `/sio-codify-workflow` | distill + promote + optimize | ~$0.05 (LLM) |
| **Recall a workflow** | `/sio-recall` | `sio recall "query"` | Free / ~$0.02 with --polish |
| **Export training data** | `/sio-export` | `sio export-dataset --task all` | Free |
| **See LIVE sessions / detect collisions** | `/sio-live` | `sio live ls` | Free |
| **Read/attach to a live session** | `/sio-live` | `sio live show`/`attach <id>` | Free |
| **Search INDEXED session history** | `/sio-search` | `sio search "query"` | Free |
| **Tail one session by handle** | `/sio-watch` | `sio watch --session <h>` | Free |

## Bare `/sio` — diagnose, then OFFER (for the agent)

Triggers: the user says just `sio` or `/sio`, or asks "what should I run", "where are we
with sio", "what's next", "anything waiting".

They are not asking for a command list. They are asking **where the pipeline stands and
what to do about it.** Answer in three parts, in this order.

### Step 1 — probe the state (free, read-only, always safe)

```bash
python3 - <<'EOF'
import sqlite3, os, datetime
sio  = os.path.expanduser('~/.sio/sio.db')
plat = os.path.expanduser('~/.sio/claude-code/behavior_invocations.db')
c = sqlite3.connect(f'file:{sio}?mode=ro',  uri=True)
p = sqlite3.connect(f'file:{plat}?mode=ro', uri=True)
now = datetime.datetime.now(datetime.timezone.utc)
def one(conn, q, d=0):
    try: return conn.execute(q).fetchone()[0] or d
    except Exception: return d
def age(ts):
    if not ts: return 'never'
    try: return f"{(now - datetime.datetime.fromisoformat(str(ts).replace('Z','+00:00'))).days}d ago"
    except Exception: return '?'
pending = one(c, "select count(*) from suggestions where status='pending'")
print(f"capture      {one(p,'select count(*) from behavior_invocations'):,} rows, newest {age(one(p,'select max(timestamp) from behavior_invocations',None))}")
print(f"sync         canonical {one(c,'select count(*) from behavior_invocations'):,} / per-platform {one(p,'select count(*) from behavior_invocations'):,}")
print(f"mine         {one(c,'select count(*) from error_records'):,} errors, last {age(one(c,'select max(mined_at) from processed_sessions',None))}")
print(f"flows        {one(c,'select count(*) from flow_events'):,} events, last {age(one(c,'select max(mined_at) from flow_events',None))}")
print(f"patterns     {one(c,'select count(*) from patterns'):,}, last {age(one(c,'select max(created_at) from patterns',None))}")
print(f"suggestions  {one(c,'select count(*) from suggestions'):,} total, {pending} pending")
print(f"applied      {one(c,'select count(*) from applied_changes'):,}")
EOF
```

### Step 2 — say where the pipeline stands

Three or four lines. Name what is current and what is stale, with ages. Do not paste the
raw probe output and leave the user to read it.

### Step 3 — offer exactly ONE next action, and ASK

Walk this table top to bottom. Stop at the first row that matches. Propose that command
and nothing else.

| State | Offer | Why it is first |
|---|---|---|
| pending suggestions > 0 | `sio suggest-review` | work already paid for, sitting unread |
| canonical far below per-platform | `sio sync` | every reader reads canonical |
| mine older than ~2 days | `sio mine --recent 7` | everything downstream needs it |
| flows stale, mine fresh | `sio flows` | cheapest unmined signal |
| patterns stale, mine fresh | `sio discover` | suggestions need patterns |
| patterns fresh, suggestions older | `sio suggest` | **costs ~$0.05 — say so** |
| applied > 0, 2+ weeks old | `sio velocity` | the only read on whether rules worked |
| nothing matches | say the loop is current | do not invent work |

Phrase it as a question: *"95 suggestions have been pending since 19 days ago, and you
have applied none of them. Want me to run `sio suggest-review`?"*

### The rules that make this useful instead of annoying

- **OFFER, do not run.** Stages 4-9 write files, cost money, or both. The probe is the only
  thing to run unprompted.
- **One suggestion, not a menu.** A list of eight options is the thing the user was trying
  to avoid by typing `sio`.
- **A stale stage is usually not a broken stage.** Stages 4-9 are manual by design
  (`docs/SIO_PHILOSOPHY.md`). "Flows are 23 days old" means nobody asked for flows. Never
  report that as a fault or a regression.
- **Always say the cost** before offering `sio suggest`. It is the only command here that
  spends money.
- **Review beats generate.** Never offer `sio suggest` while suggestions are pending.
  Generating more proposals buries the unread ones.
- **`sio search` needs no pipeline.** If the user wants "have I hit this before", route
  straight to `/sio-search`; it reads transcripts and ignores every stage above.

Full reference: `docs/operating-sio.md` in the SIO repo.

## How to Route (for the agent)

When the user asks something SIO-related, determine which sub-command to invoke.
**Read this whole list before defaulting to `/sio-suggest`** — there are 9
specialized parsers below the generic suggestion path that handle specific
questions far better than `sio suggest --grep` ever can.

### "What should I know?" / "any issues to be aware of?" / "session briefing"
→ `/sio-briefing`
Composite session-start intel: violations, budget warnings, declining rules,
pending suggestions — one screen.

### "What's going wrong?" / "scan for errors" / "what patterns?"
→ `/sio-scan`
Mines recent sessions, shows error breakdown, top patterns.

### "What can SIO improve in THIS repo?" / "find improvement opportunities" / "discover skill candidates"
→ `/sio-discover`
Repo-specific skill-candidate finder. Categorises patterns into workflow
skills, guard rules, and tool patterns. **Use this — not `/sio-suggest` —
when the question is repo-scoped.**

### "Generate rules" / "suggest improvements" / "how can I improve my agent?"
→ `/sio-suggest`
Clusters errors into patterns, generates CLAUDE.md rules using DSPy.
Generic, cross-project. Prefer `/sio-discover` for repo-specific work and
`/sio-validate` for tool-arg failures.

### "What tool args keep failing?" / "generate validation rules" / "update validations from errors"
→ `/sio-validate`
Specialized parser for `tool_failure` errors caused by bad arguments.
Proposes deny / auto-fix rules for the cascade-shield `validate-args.js`
hook. Cleaner output than `sio suggest` for this specific class.

### "Review suggestions" / "what did SIO recommend?"
→ `/sio-review`
Shows pending suggestions for user approval.

### "Apply that rule" / "add suggestion N"
→ `/sio-apply`
Applies an approved suggestion to CLAUDE.md or config files.

### "Which rules are being ignored?" / "check for rule violations"
→ `/sio-violations`
Detects when rules already in CLAUDE.md are being violated by the agent.
Different from errors: violations mean the rule exists but isn't being
followed; errors mean no rule exists yet.

### "Promote rule N to a hook" / "make that violated rule actually block" / "hook-ify rule N"
→ `/sio-promote-rule`
Takes a violated rule (by index from `/sio-violations`), extracts a
runtime detection pattern via DSPy, generates a PreToolUse hook
script, and registers it in `~/.claude/settings.json`. Default mode
is `warn` (logs but allows); flip to `block` after confirming the
detection isn't over-firing. Preview-by-default — pass `--write`
to install.

### "Are my rules working?" / "check rule effectiveness"
→ `/sio-velocity`
Per-rule learning velocity — how fast error rates drop after a rule lands.
The closed-loop signal that says whether the SIO loop is actually working.

### "How much budget is left?" / "can I add more rules?" / "check CLAUDE.md size"
→ `/sio-budget`
Per-file instruction-file usage report (lines / cap / status). Run before
`/sio-apply` if CLAUDE.md is getting large.

### "++ / --" / "label the last action" / "give feedback"
→ `/sio-feedback`
Marks the last AI action with satisfaction. Feeds the ground-truth corpus
that DSPy uses for `sio optimize`.

### "What patterns work?" / "show productive workflows" / "what sequences?"
→ `/sio-flows`
Discovers recurring positive tool sequences. Shows what works, not just errors.

### "Distill that session" / "extract the steps" / "create a playbook"
→ `/sio-distill`
Takes a long session, removes failures/retries, outputs a numbered playbook.

### "Promote that flow" / "turn that workflow into a skill"
→ `/sio-promote-flow`
Single-step: take an existing mined flow and write it out as a SKILL.md.

### "Codify this" / "save this workflow as a skill" / "make a skill from what we just did"
→ `/sio-codify-workflow`
One-shot pipeline: distill → promote-flow → optimize, with confirmation
between steps. Use when the *current* session is the one to be codified.

### "How did we do X?" / "recall the dbt setup" / "what was the fix for Y?"
→ `/sio-recall`
Topic-filters a distilled session, detects struggle→fix transitions, optionally polishes via Gemini.

### "Export training data" / "DSPy dataset" / "routing pairs"
→ `/sio-export`
Exports structured JSONL/Parquet datasets for ML training.

### "How is SIO doing?" / "pipeline status"
→ `/sio-status`
Shows current state: errors mined, patterns found, suggestions pending.

### "What sessions are running RIGHT NOW?" / "any concurrent sessions?" / "are we about to collide?" / "attach to session X"
→ `/sio-live`
Discovers **in-progress** (still-being-written) sessions — which `sio search`
can't see, because it only indexes finished transcripts. Shows each live
session's repo/branch/worktree and **flags collisions** when two live sessions
share a working tree (concurrent-edit hazard). `sio live show <id>` reads a
session's tail; `sio live attach <id>` follows a peer read-only from another
session. Distinct from `/sio-search` (INDEXED history) and `/sio-watch` (tail
one known handle).

## The SIO Pipeline (How It All Connects)

```
Session JSONL transcripts
    ↓
sio mine (parse + extract errors + extract flows)
    ↓                              ↓
sio errors (error analysis)    sio flows (positive patterns)
    ↓                              ↓
sio suggest (generate rules)   sio distill (session → playbook)
    ↓                              ↓
sio apply (write rules)        sio recall (topic filter + polish)
                                   ↓
                               sio export-dataset (ML training data)
                                   ↓
                               sio optimize-suggestions (GEPA / MIPROv2 /
                                                         BootstrapFewShot)
```

## Two Tiers

| Tier | Commands | Cost | Engine |
|------|----------|------|--------|
| **Cheap** | mine, errors, flows, distill, recall (no --polish), export | $0 | Regex + SQLite |
| **Expensive** | suggest, recall --polish, optimize-suggestions | ~$0.02-0.50 | LLM (task + reflection for GEPA) |

## Data Locations

| Data | Location |
|------|----------|
| SIO database | `~/.sio/sio.db` |
| Exported datasets | `~/.sio/datasets/` |
| Session transcripts | `~/.claude/projects/*/` |
| SpecStory history | `~/.specstory/history/` |

## Examples

```
/sio scan for errors in the last week
/sio what should I know to start this session?
/sio what can SIO improve in this repo?
/sio what tool args keep failing?
/sio which rules are being ignored?
/sio are my rules actually reducing errors?
/sio how much room is left in CLAUDE.md?
/sio what patterns work well?
/sio distill the latest session
/sio codify the workflow we just did
/sio how did we set up dbt locally?
/sio export training data for DSPy
/sio what's the pipeline status?
```
