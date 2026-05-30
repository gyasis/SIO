---
name: sio-router
description: "SIO meta-router for prompt-engineering targets. Detects the target_surface from agent intent (claude_md_rule / skill_update / hook_config / mcp_config / settings_config / agent_profile / project_config) and dispatches to the appropriate trained SIO skill, falling back to /sio-rule-generator for un-trained surfaces."
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-rule-generator]
  hooks: []
  optional: [prd]
metadata:
  source: "manual"
  created: "2026-05-16"
  pairs_with: "SIO optimizer pipeline (mine → curate → amplify → GEPA → render)"
---

# SIO Router — pick the right generator

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-rule-generator` — primary dispatch target for `claude_md_rule` surface and fallback for all untrained surfaces
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)
- **Optional:** `/prd` — can be used to persist planning when a multi-surface engineering session spans many changes; demoted to optional because the router itself has no hard dependency on PRD state

You are dispatching a prompt-engineering request to the appropriate SIO-trained
sub-skill. The user has shown you (or you have inferred from context) a need to
create or modify Claude Code configuration. Your job is to:

1. **Classify** the request into ONE target_surface
2. **Invoke** the matching specialized SIO skill if one exists
3. **Fall back** to the rule generator with an analogical preamble when no
   specialized skill is trained yet

---

## Target surface classification

Pick exactly one based on what the user is asking for:

| Surface | When to pick | Specialized skill (when trained) |
|---|---|---|
| **`claude_md_rule`** | User wants a prevention rule, a "do not X" directive, an "if Y then Z" gate, anything that goes in `CLAUDE.md` or a project rules file | `/sio-rule-generator` ✅ (active, score 0.87) |
| **`skill_update`** | User wants to create or edit a Claude Code skill — a file in the skills directory with YAML frontmatter that's user-invocable. Multi-step workflow, action-oriented description. | `/sio-skill-generator` (not yet trained — fall back) |
| **`hook_config`** | User wants a PreToolUse / PostToolUse / SessionStart / etc. hook — typically a shell script plus a `hooks` block in `settings.json`. | `/sio-hook-generator` (not yet trained — fall back) |
| **`mcp_config`** | User wants to add/edit an MCP server entry in `settings.json`'s `mcpServers` block. | `/sio-mcp-generator` (not yet trained — fall back) |
| **`settings_config`** | User wants to change non-MCP settings (model, theme, env vars, permissions). | `/sio-settings-generator` (not yet trained — fall back) |
| **`agent_profile`** | User wants to create or edit an agent definition under the agents directory. | `/sio-agent-generator` (not yet trained — fall back) |
| **`project_config`** | User wants to modify project-scoped config (e.g. `pyproject.toml`, build configs, `package.json`). | `/sio-project-config-generator` (not yet trained — fall back) |

If you can't pick exactly one, ask the user to disambiguate before proceeding.

---

## Dispatch protocol

### Step 1 — Pick the surface

State your classification explicitly in 1 line:
> "Classifying as `<target_surface>` because <one-sentence reason>."

### Step 2 — Try the specialized skill

If a `Specialized skill` exists for the chosen surface, INVOKE it with the three SIO inputs:

- `pattern_description` — a 1-2 sentence summary of what the user needs
- `example_errors` — concrete error messages, error logs, or current-state
  symptoms the user has shown you (verbatim, in backticks)
- `project_context` — the directory, repo, or platform context (if relevant)

The specialized skill is responsible for producing the output in the right
shape for that surface.

### Step 3 — Fallback path (Path B)

If no specialized skill exists yet:

1. Invoke `/sio-rule-generator`
2. **Wrap the call with an analogical preamble** like this:

> "The target surface is `<target_surface>`, NOT `claude_md_rule`. Apply the
> following principles from the rule generator to the target surface form:
> ground in literals, gating + prohibitive + remediation structure, exact
> token preservation. Output as <target_surface_format>, NOT as
> rule_title/rule_body/rule_rationale."

The fallback gives the user something usable while a per-surface module
accumulates training data. Each fallback output is also a candidate training
example for the eventual specialized module — flag promising ones for
`sio promote-to-gold --target-surface <surface>`.

---

## Examples

### Example A — clear rule case

User: *"I keep accidentally running `sed -i` on config files. Add a rule."*

Classification: `claude_md_rule`
Dispatch: `/sio-rule-generator` directly. No fallback needed.
Output: rule_title + rule_body + rule_rationale.

### Example B — skill creation, no trained skill yet

User: *"Make me a skill that runs a daily summary report."*

Classification: `skill_update`
Specialized skill exists? **No.**
Dispatch: `/sio-rule-generator` with the analogical preamble. Output should
be a Claude Code skill file (YAML frontmatter + body), NOT a rule.

### Example C — ambiguous

User: *"Help me with my Claude setup."*

Action: **Ask first.** Don't classify. Say:
> "Help me narrow it down — do you want a CLAUDE.md rule, a new skill, a hook,
> an MCP server entry, or something else?"

---

## When to retrain

When the fallback path has produced 20+ examples for a particular
`<target_surface>` (visible via `sio promote-to-gold --all-eligible
--target-surface <surface>` + `sio analyze same-error --target-surface
<surface>`), recommend the user run:

```bash
sio multi-train --surfaces <surface> --task-mode cheap --reflection-mode work
sio render --all-active
```

The new specialized skill replaces the fallback automatically.

---

## Provenance

This router is **handwritten** (not optimized by SIO). It's pure dispatch
logic — no LLM evaluation needed. Specialized sub-skills below this router
ARE optimized; their evolved instructions carry the SIO-trained signal.

- Pairs with: SIO optimizer pipeline + `sio render --all-active`
