---
name: sio-promote-rule
description: Promote a violated CLAUDE.md rule (from `sio violations`) into a runtime PreToolUse hook so the harness enforces it instead of relying on the agent reading the rule. Ask naturally like "promote rule 1 to a hook" or "make that violated rule actually block".
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-violations]
  hooks: []
  optional: []
---

# SIO Promote-Rule — Turn Violated Text Rules Into Runtime Hooks

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-violations` — supplies the violated rule (by index) that this skill promotes
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`); note this skill *creates* a new PreToolUse hook as its output

When a rule in CLAUDE.md is being broken by the agent at scale (visible
via `/sio-violations`), text alone isn't enforcing it. Promote it to a
PreToolUse hook so the harness fires the check before the violating
tool call runs.

## Triggers

- "promote rule N to a hook"
- "make rule N enforceable"
- "stop the agent from ignoring rule N"
- "hook-ify rule N"
- "the agent keeps ignoring this rule — make it block"

## Workflow

```text
/sio-violations         → see rules being violated and their indices
/sio-promote-rule N     → preview: extract detection + check coverage
                          (no writes — just shows what would happen)
/sio-promote-rule N --write    → install hook (default warn mode)
/sio-promote-rule N --write --mode block   → escalate to blocking
```

The default is **preview** so the agent + user can audit the LM's
extracted detection expression and the historical coverage **before**
anything lands on disk.

## Execution

```bash
#!/bin/bash
set -e

RULE_INDEX="${SIO_RULE_INDEX:-}"
MODE="${SIO_MODE:-warn}"
WRITE="${SIO_WRITE:-}"

if [ -z "$RULE_INDEX" ]; then
    echo "Usage: SIO_RULE_INDEX=<n> /sio-promote-rule"
    echo ""
    echo "Run /sio-violations first to see available rule indices."
    exit 1
fi

CMD="sio promote-rule $RULE_INDEX --mode $MODE"
if [ -n "$WRITE" ]; then
    CMD="$CMD --write"
fi

echo "Running: $CMD"
$CMD
```

## What `sio promote-rule N` shows you

1. **Promotion target panel** — the exact rule text + source file:line
2. **Representative violations table** — up to 10 actual violating tool
   calls from past sessions, with tool name + input excerpt + error
   excerpt, sampled across distinct sessions for diversity
3. **Extracted detection pattern panel** — what the LM produced:
   - `Matcher tools` — comma-separated list (becomes the harness matcher)
   - `Detection expr` — single Python expression with locals
     `tool_name`, `tool_input`, `recent_tool_names`, `recent_tool_inputs`
   - `Rationale` — one-sentence explanation
   - `Status` — promotable / not promotable
4. **Detection coverage on historical data panel** — replays the
   detection against ALL past violations of this rule:
   - Coverage % (color-coded: ≥60% green, 30-60% yellow, <30% red)
   - Per-session breakdown
   - Sampled fired + missed examples

If you pass `--write`, the agent installs:
- The hook script at `~/.claude/hooks/sio-promoted/<slug>.py`
- A `PreToolUse` registration in `~/.claude/settings.json` with the
  matcher set to the LM-extracted tool list
- An audit row in `promoted_hooks` (canonical sio.db)

## When to use warn vs block

- **warn** (default): hook prints to stderr but lets the call through.
  Use until `/sio-velocity` shows the violation count for this rule
  decisively shrinking. Watch for false positives.
- **block**: hook returns `{"action": "block"}` so the harness refuses
  the call. Only after warn-mode confirms the detection isn't
  over-firing on legitimate work.

## Reading coverage <30%

A red coverage number doesn't mean the hook is broken — it can mean
the keyword-based violation matcher in `/sio-violations` had false
positives that the LLM-extracted detection correctly excludes. Look
at the rendered fired vs missed examples and decide. The gate is
soft: `--write` still works, but with a yellow warning.

## After Running

If `--write` succeeded:
- Tell the user to **restart Claude Code** so the harness picks up the
  new `PreToolUse` registration. Hooks are only loaded at session start.
- Mention the audit row id (printed in the success panel) in case they
  want to roll back later.

## Rollback

Currently manual: delete the script under `~/.claude/hooks/sio-promoted/`
and remove the matching entry from `~/.claude/settings.json`. A
`sio rollback-hook <id>` verb is tracked as a follow-up.

## Files

- Generated hook scripts: `~/.claude/hooks/sio-promoted/<slug>.py`
- Per-session state cache: `~/.claude/.sio-promoted-state/<session_id>.json`
- Audit table: `promoted_hooks` in `~/.sio/sio.db`
- PRD: `prds/prd-violated-rule-to-pretooluse-hook.md`
