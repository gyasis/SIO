# PRD violated-rule-to-pretooluse-hook — Promote violated rule → PreToolUse hook

**Status:** draft
**Created:** 2026-05-03
**Owner:** unassigned
**Blocked by:** PRD install-orchestration-regression (hooks must exist before we can promote rules to hooks)

## Problem

`sio violations` already finds rules in `CLAUDE.md` that the agent
ignores at scale — verified today against this user's corpus:

| Rule (CLAUDE.md) | Violations | Sessions |
|---|---|---|
| "Never call Bash in parallel with Write or Edit. State-modify…" | 361 | 35 |
| "Never re-do work that was already committed" | 66 | 22 |
| "do not instantiate directly." (re: dspy.LM) | 62 | 11 |

These rules exist as text in CLAUDE.md. Text rules are *advisory* —
they're loaded into the agent's context at session start, the agent
*should* follow them, but at the rates above it clearly doesn't. There
is no runtime enforcement: nothing fires before the violating tool
call to block it or re-show the rule.

Claude Code already supports `PreToolUse` hooks that fire before any
tool call and can block / prompt / log. The gap: SIO knows which rules
are being violated and at what rate, but **there is no path from "this
rule is being violated 361 times" to "this rule is now a PreToolUse
hook that fires on the violating shape."**

## Proposal

A new `sio promote-rule <rule-id>` CLI verb (and matching
`/sio-promote-rule` skill) that:

1. Reads the violation report row for `<rule-id>`
2. Pulls a representative sample of the actual violating tool calls
   (already in `error_records.tool_input` — 5-10 examples is enough)
3. Uses the LLM to extract a structured detection pattern from the
   rule text + the violating examples (e.g., the "no parallel
   Bash+Write" rule becomes: *"if any tool_call in the current turn
   is Bash and another is Write or Edit, fire."*)
4. Generates a PreToolUse hook script (Python or Node, project
   convention) that implements that detection
5. Writes the hook into `~/.claude/hooks/sio-promoted/<slug>.py`
6. Registers it in `~/.claude/settings.json` under `hooks.PreToolUse`
   with a clear matcher
7. Records the promotion in a new `promoted_hooks` table
   (`rule_id, hook_path, mode (warn|block), promoted_at`) for
   audit + rollback

Default mode is `warn` (hook prints the rule text but doesn't block
the call), so the user sees the friction of the new hook before the
hook starts blocking work. After N sessions of warn-mode with
declining violation count, prompt to switch to `block`.

### Skill surface

`/sio-promote-rule` SKILL.md:
- Trigger phrases: "promote that rule to a hook", "make rule N
  enforceable", "stop the agent from ignoring this rule",
  "hook-ify rule"
- Reads from `sio violations` output to pick the rule
- Confirms the proposed detection logic with the user before writing
  the hook (HITL by default)
- Adds an example invocation that explicitly references the violated
  rule's text so the user can sanity-check

### Telemetry feedback loop

After promotion, the new hook itself emits PostToolUse-style telemetry
into `behavior_invocations` (`behavior_type='instructions_rule'`,
`actual_action='warned'` or `'blocked'`). `sio velocity` then has
direct measurement of hook effectiveness — the closed loop closes for
runtime enforcement just like it does today for offline rule mining.

## Why it matters

- Today: 361 violations on the top rule, no countermeasure available
  via SIO. The text rule "exists" in CLAUDE.md but has zero runtime
  weight.
- After: that rule becomes a hook that warns the agent every time it
  goes to call Bash + Write/Edit in the same turn. Velocity tells us
  within ~5 sessions whether the warn mode is working; user promotes
  to block mode if so.
- Generalises to the whole "rules in CLAUDE.md don't actually enforce
  anything" class of complaints. SIO becomes the bridge between
  rule mining (existing) and rule enforcement (new).

## Out of scope

- Auto-promoting without user confirmation. Default is HITL; an
  `--auto` flag may follow once we have signal that the promotion
  quality is high.
- Rules that aren't structurally enforceable as a PreToolUse pattern
  (e.g., "always be careful with destructive commands"). The skill
  should surface these and explicitly say "this rule is not a good
  candidate for hook promotion — keep as text."
- PostToolUse / Stop / UserPromptSubmit hook generation. Different
  rule classes need different hook events; this PRD ships
  PreToolUse only and tracks the others as follow-up.

## Open questions

1. How does the LLM-extracted detection pattern get tested before
   the hook ships to `~/.claude/`? Probably: dry-run the hook
   against the very violating sessions in
   `error_records.tool_input` and report "would fire on N of M
   violations" so the user can see the detection's coverage and
   false-positive rate before promoting.
2. Hook script language: Python (matches SIO's stack) or Node
   (matches the existing cascade-shield convention)?
3. Rollback path: `sio rollback-rule <rule-id>` removes the hook
   from `settings.json` and unlinks the script file. Should it
   also delete the `promoted_hooks` row or keep it for audit?
4. What about rules added by a different SIO user / a different
   tool / a project's own CLAUDE.md? `sio violations` already
   scans multiple files (`CLAUDE.md`, `rules/tools/sio.md`,
   `~/.claude/rules/tools/sio.md`); promotion should respect the
   source-file scope so a project rule produces a project hook,
   not a user-global one.

## Effort estimate

Medium. Roughly:

- 0.5d: `promoted_hooks` schema + migration; CLI verb scaffold
- 1.0d: LLM signature + DSPy module for "extract detection pattern
  from rule text + 10 violating tool_input examples"
- 0.5d: hook script template (Python) + `~/.claude/settings.json`
  merge logic (lifted from the install-orchestration restore in
  PRD install-orchestration-regression)
- 0.5d: dry-run-against-historical-violations verifier
- 0.5d: `/sio-promote-rule` SKILL.md + interactive flow
- 0.5d: regression tests + a smoke test that promotes the
  "Bash+Write parallel" rule and confirms it fires on a synthetic
  session

Total: ~3.5 days. Strictly blocked by PRD install-orchestration-regression — without hooks
infrastructure restored, there is nothing to promote into.

## References

- `sio violations` output (run today against this user's corpus,
  3 rules being violated 361 / 66 / 62 times)
- PRD install-orchestration-regression (install-orchestration regression — the prerequisite that
  restores the hook system this PRD writes into)
- Claude Code PreToolUse hook docs (covered in `update-config`
  skill — the harness-side mechanism this PRD plugs into)
- The existing `sio-validate` skill is adjacent: it already
  generates `validate-args.js` rules from tool_failure errors.
  PRD violated-rule-to-pretooluse-hook generalises the same shape to *all* CLAUDE.md rules,
  not just tool-arg failures.
