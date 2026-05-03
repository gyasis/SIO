# PRD skill-side-llm-delegation — Skill-side LLM delegation

**Status:** draft
**Created:** 2026-05-02
**Owner:** unassigned

## Problem

When SIO is invoked from inside Claude Code via a slash skill (e.g.
`/sio-suggest`, `/sio-distill`, `/sio-codify-workflow`), the SKILL.md
body is just a thin shell wrapper that runs `sio suggest …` as a
subprocess. The subprocess then opens its own `dspy.LM` via litellm
and makes a paid OpenAI/Anthropic call to do the rule-generation
reasoning.

This duplicates work the user is *already paying for*: the Claude
Code session has its own model and full conversation context, and is
perfectly capable of doing the cluster-summarisation + rule-drafting
that `sio suggest` currently shells out for.

Net effect today: a user running `/sio-suggest` inside Claude Code
pays twice — once for the Claude Code turn, once for the OpenAI call
the subprocess makes — to get the same rule.

## Proposal

For the *generative* skills (anything that turns mined data into
prose), rewrite the SKILL.md so the Claude Code agent does the
reasoning in-context, and demote the `sio` CLI to a pure data-access
tool. Concretely the SKILL.md instructs the agent to:

1. Query `~/.sio/sio.db` (via a new read-only `sio export-cluster
   <pattern_id>` command, or directly with `sqlite3`) to pull the
   error/correction rows + cluster metadata.
2. Reason over them inside the Claude Code turn — cluster summary,
   proposed rule text, confidence rationale.
3. Write the resulting suggestion row back via a new
   `sio suggestion-add --pattern-id … --rule-text …` CLI verb.

Skills in scope (LLM-heavy):

- `sio-suggest`
- `sio-distill`
- `sio-codify-workflow`
- `sio-discover`
- `sio-recall`

Skills out of scope (already pure-data — no LLM call):

- `sio-status`, `sio-budget`, `sio-report`, `sio-apply`,
  `sio-review`, `sio-scan`, `sio-flows`

## Why it matters

- **Cost.** Removes the redundant LLM call for in-harness usage.
- **Provenance.** The rule text now appears in the Claude Code
  transcript, so it's auditable and the user can iterate on it
  conversationally before persisting.
- **Architectural clarity.** The CLI's job becomes "data in / data
  out." The harness's job becomes "reasoning + UX." This is the
  boundary the existing `sio` skills *imply* but don't enforce.

## Out of scope

- Standalone `sio` CLI use (cron, `sio optimize`, scripted runs) —
  still needs `[llm]` configured. This PRD does not touch that path.
- DSPy `sio optimize` / GEPA — those genuinely need a programmatic
  LM. Keep as-is.
- Other harnesses (cursor, codex, gemini-cli stubs) — design the
  delegation contract harness-generically so other adapters can
  implement the same SKILL.md shape, but this PRD only ships the
  Claude Code rewrite.

## Open questions

1. Does the rule text need to be schema-validated (length, format,
   no-emoji rules) before insert? If yes, the validator must run in
   the CLI verb, not in the skill (skills are unenforceable).
2. Where does GEPA/optimize feedback flow when the rule was authored
   by the harness, not by `dspy_generator`? Need to confirm the
   ground-truth corpus accepts harness-authored examples.
3. How does this interact with `sio suggest --auto` (non-interactive
   batch mode)? Probably stays on the CLI/dspy path — but the SKILL
   path should *not* call `--auto`, it should generate one suggestion
   per turn so the user can steer.

## Effort estimate

Small-to-medium. Roughly:

- 1d: design the new CLI verbs (`export-cluster`, `suggestion-add`)
  + their tests.
- 1d: rewrite the 5 generative SKILL.md files.
- 0.5d: update `docs/cookbook.md` and `getting-started.md` to
  reflect the new in-harness flow.
- 0.5d: a smoke test that exercises `/sio-suggest` end-to-end with
  no `OPENAI_API_KEY` set and verifies a suggestion lands in the DB.

## References

- `src/sio/adapters/claude_code/skills/sio-suggest/SKILL.md` —
  current shell-wrapper implementation
- `src/sio/suggestions/dspy_generator.py` — programmatic path that
  stays untouched for CLI use
- `src/sio/core/dspy/lm_factory.py:89-126` — `create_lm` legacy path,
  consumed by the subprocess this PRD obsoletes for in-harness use
