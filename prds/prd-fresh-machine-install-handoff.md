# PRD fresh-machine-install-handoff — Fresh-machine SIO install test report + session handoff

**Status:** report (not a proposal — captures state for handoff)
**Created:** 2026-05-03
**Author:** Claude Code agent (gyasis@gmail.com session)
**Machine:** secondary install, host `gyasis-Blade-15-Base-Model-Early-2020-RZ09-0328`

## Why this PRD exists

This file captures the full state of a fresh-machine SIO install +
end-to-end verification run done on 2026-05-03, so a future agent
(on this machine or another) can pick up without rebuilding context.
Read this before re-running anything covered below.

## What we tested

End-to-end SIO pipeline on a machine with no prior `~/.sio/`,
targeting the disaster-recovery work (April 2026) as the test data:

1. `pip install -e .` from `/home/gyasis/Documents/code/SIO` (editable)
2. `sio init` (Claude Code harness)
3. Mine 38 historical jsonl sessions covering 2026-04-04 → 2026-05-02
4. Run the suggestion pipeline against the recovery slice
5. Run the specialised parsers (`sio discover`, `sio violations`,
   `sio velocity`, `sio briefing`)
6. Verify which SIO skills had ever been invoked

## Final state of this machine

### Versions + locations

| Item | Value |
|---|---|
| SIO version | `0.1.3` (commit `b439209` + my fixes on top) |
| Install mode | editable: `pip install -e /home/gyasis/Documents/code/SIO` |
| Repo branch | `main`, fast-forwarded to `origin/main` |
| `sio` binary | `/home/gyasis/miniconda3/bin/sio` |
| Python | 3.13 |

### LLM configuration (live + persistent)

| Surface | Value |
|---|---|
| `~/.sio/config.toml` `[llm]` | `openai/gpt-4o-mini`, `max_tokens=16000` |
| `~/.sio/config.toml` `[llm.sub]` | `openai/gpt-4o-mini` |
| `OPENAI_API_KEY` | `sk-proj-eo***ZsMA` (modern project key, **verified working**) |
| Source for OPENAI_API_KEY | `~/.config/environment.d/sio-openai.conf` (mode 600) + live `systemctl --user set-environment` |
| Previous bad key (sk-tFTjb...LRV6) | replaced — was invalid, returning HTTP 401 |
| `OLLAMA_HOST` | `http://192.168.0.159:11434` — set in env.d but not currently primary |
| `~/.config/environment.d/sio-ollama.conf` | mode 600 |
| Ollama models discovered (24 total) | best for SIO: `qwen3-coder:30b` ctx=262144, `gpt-oss:20b` ctx=131072, `deepseek-r1:70b` ctx=131072 |
| `sio config test` last result | ✓ OpenAI: "2 + 2 equals 4", 4.5s |

### Skills installed (~/.claude/)

- 20 bootstrap files: 19 SKILL.md + 1 `rules/tools/sio.md`
- `sio init --status`: `installed=20, missing=0, drifted=0`
- Manifest: `~/.claude/.sio-managed.json`
- Backup of replaced rules (from routing-fix re-init):
  `~/.sio/backups/20260503T102442Z/rules/tools/sio.md`

### Database state

| Table | Rows | Notes |
|---|---|---|
| `error_records` | 417 | from 38 sessions mined since 2026-04-04 |
| `patterns` | 25+ | recovery-slice clustering produced 25 |
| `datasets` | 2 | only 2 patterns met `min_examples` threshold |
| `suggestions` (pending) | 2 | both DSPy-generated; details below |
| `behavior_invocations` | **0** | per-platform AND canonical — see PRD install-orchestration-regression |
| `applied_changes` | 0 | nothing applied yet |
| `schema_version` | n/a | table never created — see PRD install-orchestration-regression |

### Pending suggestions ready for review

| ID | Conf | Target | Description |
|---|---|---|---|
| 1 | 78% | CLAUDE.md | "Limit consecutive Bash calls with similar input to avoid redundancy" — addresses the dominant 270-error / 33-session Bash 3× retry pattern |
| 2 | 42% | CLAUDE.md | "Clarify tool permissions and data retention when using Bash" — weaker, generic |

Run `sio suggest-review` to act on these, or `sio approve 1 --note "…"`
to fast-track #1.

## Commits landed during this session

| Commit | What |
|---|---|
| `c87f557` | `fix(schema): add cycle_id to datasets + suggestions DDLs` (via PR #1, merged) |
| `1a633e3` | merge of PR #1 |
| `0e97eb4` | `docs(skills): wire all 19 skills into the master sio router + canonical rule` |
| `5569b45` | `docs(prds): seed prds/ backlog with two draft PRDs` (001 + 002) |
| `fdc2a8c` | `docs(prds): add 004 + 005` |
| (this PR) | adds 006 |

PR #1 is merged + branch deleted. No open PRs.

## Findings worth knowing

### What worked

- `sio mine --since "2026-04-04" --source jsonl` cleanly captured 36
  newly-mined sessions (2 already processed) and 417 errors
- `sio config test` confirmed OpenAI path end-to-end after key swap
- `sio suggest --grep "recover,opensearch,reconstruct,extract" --auto`
  ran all 4 steps cleanly *after* the schema fix landed
- `sio discover` / `sio violations` / `sio velocity` / `sio briefing`
  all produce useful output

### Friction surfaced (the actually-interesting part)

- **The `cycle_id` schema bug.** v0.1.3 ships `sio suggest` broken on
  every fresh install. PR #1 fixed it. This bug exists *because* the
  install pipeline regression in PRD install-orchestration-regression means
  `_apply_004_migration_if_needed` no longer runs.
- **No hooks installed.** `~/.claude/settings.json` doesn't exist
  after `sio init`. Hooks were dropped in commit `bc39869` (the
  harness refactor) — see PRD install-orchestration-regression for the full delta.
- **Routing rot.** Master `sio` skill + canonical `tools/sio.md`
  routed to 9 of 19 skills — the 9 added in v0.1.3 were invisible to
  the agent's decision tree. Fixed in commit `0e97eb4`.
- **`sio distill` leaks credentials.** A 109KB playbook from session
  `38e5c002` captured the literal `cat > ~/.aws/credentials << 'EOF'`
  block, AWS access key + secret in cleartext. The file was shredded
  immediately; nothing left disk. No redaction layer for
  here-doc credentials. Worth a future PRD; not yet written.
- **Context-grounding gap (already known).** Both `sio suggest` and
  `sio flows` produce abstract output (tool-class shapes, generic
  rule text) with zero file paths, session IDs, or project context.
  Even `sio discover --repo` returns the same 3 candidates as
  un-scoped. Worth a future PRD; not yet written.
- **Recovery-friction patterns drowned out.** 64 errors in the
  recovery slice clustered into 25 patterns, of which 23 were
  singletons that didn't meet the dataset threshold. The two
  surviving patterns are generic (Bash retries + permission UX) —
  nothing recovery-specific got promoted to a suggestion.
- **SpecStory not auto-discovered.** `sio mine --source specstory`
  expects a canonical path but this user's specstory files live
  under `llmlouge/RECOVERY/specstory/` and `hybridrag/docs/…`.
  None in `~/.specstory/` (which doesn't exist).

## Backlog produced this session

| PRD | Title | Severity / Status |
|---|---|---|
| [001](prd-skill-side-llm-delegation.md) | Skill-side LLM delegation | draft |
| [002](prd-local-lm-backend.md) | Local LM backend (Ollama) | draft |
| 003 | (not written — would be "context-grounded outputs" — gap surfaced today, deferred) | — |
| [004](prd-install-orchestration-regression.md) | Install-orchestration regression after harness refactor | 🔴 high |
| [005](prd-violated-rule-to-pretooluse-hook.md) | Promote violated rule → PreToolUse hook | draft (blocked by 004) |

## What an incoming agent should do next

In rough priority:

1. **Implement PRD install-orchestration-regression** (restore install-orchestration). Without it
   `sio velocity` is permanently zeroed and the closed loop stays
   open. Highest-leverage single piece of work.
2. **Add the cycle_id regression test** in `tests/unit/` so PR #1
   doesn't regress again. Cheap (one file, ~30 lines) and was offered
   as a follow-up in the PR description.
3. **Apply pending suggestion #1** (`sio approve 1 && sio apply 1`)
   if the 78%-conf Bash-retry rule looks reasonable — should
   measurably shrink the 270-error pattern over the next few sessions.
4. **Write the deferred PRDs** — context-grounding outputs (would
   be 003) and `sio distill` credential redaction. Both surfaced
   today, neither captured yet.
5. **Implement PRD violated-rule-to-pretooluse-hook** *after* 004 lands. Rule-to-hook promotion
   needs hooks to exist first.

## Quick-rehydrate checklist for a new session on this machine

```bash
# Verify SIO + LLM still wired
sio --version              # → 0.1.3
sio config test            # → "2 + 2 = 4" via OpenAI
sio status                 # canonical DB state, watch behavior_invocations=0

# Re-fetch routing if main moved
git -C /home/gyasis/Documents/code/SIO pull --ff-only
pip install -e /home/gyasis/Documents/code/SIO
sio init --status          # 20 installed / 0 missing / 0 drifted

# Look at pending suggestions before acting
sio suggest-review

# Re-mine if more sessions accumulated
sio mine --since "7 days"
```

## Provenance

- This session: `~/.claude/projects/-home-gyasis-Documents-code-SIO/52b676ef-afda-4c86-ba3b-f01cb495a304.jsonl`
- Recovery test data: `~/.claude/projects/-home-gyasis-Documents-code/38e5c002…jsonl` (450 recovery hits) + 21 others
- Master CLAUDE.md (recovery context): `/home/gyasis/Documents/code/CLAUDE.md`
- SIO project CLAUDE.md: `/home/gyasis/Documents/code/SIO/CLAUDE.md`
