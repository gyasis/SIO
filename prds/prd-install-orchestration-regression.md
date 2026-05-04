# PRD install-orchestration-regression — Install-orchestration regression after harness refactor

**Status:** ✅ implemented (2026-05-04)
**Created:** 2026-05-03
**Owner:** gyasis (with Claude Opus 4.7)
**Severity:** ~~🔴 high — blocks SIO's own closed loop~~ — resolved

## Implementation summary

Landed across 5 commits on `main` (2026-05-03 → 2026-05-04):

| Commit | Phase | Closes |
|---|---|---|
| `8defd49` | 1 — `pre_install`/`post_install` lifecycle on `HarnessAdapter` Protocol (no-op defaults) | (enables the rest) |
| `b25e851` | 2 — `_register_hooks` ported into `ClaudeCodeAdapter.post_install` | gap: hooks not registered |
| `462d549` | 3 — `sio.core.db.bootstrap.ensure_canonical_db_ready()` (canonical) + `pre_install` per-platform DB init | gaps: canonical schema, schema_version baseline, 004 migration, per-platform DB |
| `08b22da` | 4 — `platform_config` row recorded in `post_install` after hooks land | gap: platform_config empty |
| `e4b6f1c` | 5 — 12 regression tests in `tests/unit/test_install_orchestration.py` covering every gap + the PR #1 `cycle_id` regression | n/a — regression coverage |

End-to-end verification on a fresh-install machine (2026-05-03) confirmed:

- `~/.claude/settings.json` exists with all 5 SIO hook events registered
- `~/.sio/claude-code/behavior_invocations.db` created with full schema
- `~/.sio/sio.db` has `schema_version` row `1|applied|baseline` (was `n/a`)
- `platform_config` table populated with `claude-code|1|1|1`
- `sio status` reports `schema_version │ 1 (applied)` and `behavior_invocations 0 ↔ per-platform 0 ✓ in sync`
- All 41 tests in scope pass (29 existing harness + 12 new orchestration regression)

The closed loop is wired. Hooks will start capturing telemetry on
the next Claude Code session restart, which is when `sio velocity`
will start having data to measure.

The 4 stale `TestInstallerHooks` tests in `tests/unit/test_hooks.py`
that imported the deleted `installer.py` were removed in the same
landing — they tested the same intent the new tests now cover.

---

## Original problem statement (archived for reference)

Commit `bc39869 feat(harnesses): sio init bootstrap + adapter pattern
(claude-code + 3 stubs)` introduced a new harness-adapter pattern in
`src/sio/harnesses/` and replaced the monolithic
`src/sio/adapters/claude_code/installer.py`. The new pattern is
well-designed for what it does (idempotent file staging with manifest
tracking, drift detection, multi-harness support).

But the new pattern's `HarnessAdapter` Protocol is `detect / install /
uninstall / status` — and `install()` is contractually limited to
copying bootstrap files (skills, rules) into the harness config dir.
The old `installer.py` did **nine** things; the new code ports **three**.

The other six install-orchestration concerns were silently dropped:

| Old `installer.py` step | What it did | Status now |
|---|---|---|
| `_install_config(sio_base)` | Seed `~/.sio/config.toml` | ✅ in `bootstrap.py` |
| `_install_skills(claude_dir)` | Copy SKILL.md files | ✅ in `claude_code.py` |
| `init_db(per_platform_db)` | Create `~/.sio/<platform>/behavior_invocations.db` with full schema | ❌ never runs — per-platform DB only created lazily by hook writes, but hooks don't exist either |
| `_ensure_canonical_schema(canonical_conn)` | Bootstrap base tables in `~/.sio/sio.db` | ❌ works by accident — created on first CLI call that hits `init_db()` |
| `ensure_schema_version(conn)` | Seed `schema_version` baseline row | ❌ `sio status` reports `schema_version: n/a (n/a)` on every fresh install |
| `_apply_004_migration_if_needed(...)` | Run the 004 schema migration | ❌ — exactly the kind of gap that produced the `cycle_id` bug fixed in PR #1 |
| `_run_split_brain_backfill()` | Mirror per-platform → canonical DB | ❌ — even if hooks ran, telemetry would never reach `sio.db` |
| `_register_hooks(settings_path)` | Write `~/.claude/settings.json` with PostToolUse / PreCompact / Stop / UserPromptSubmit / SessionStart hook registrations | ❌ **the regression that broke the closed loop** — `~/.claude/settings.json` doesn't even exist after `sio init` on a fresh machine |
| `INSERT INTO platform_config` | Record install date, hook status, capability tier | ❌ table empty after `sio init` |

## Why it matters

Verified on a fresh-machine test today:

- `sio status` → `behavior_invocations (claude-code) │ 0  ↔  per-platform: 0` and `schema_version: n/a (n/a)`
- jsonl-grep across 38 historical sessions → **0 SIO skill invocations** captured by hooks (because no hooks)
- `sio velocity` → every error type shows `Δ -, Rule Applied: none` — not because rules don't work, but because **velocity has no telemetry to measure**
- `sio violations` → finds rules being broken 361 / 66 / 62 times across 35 / 22 / 11 sessions, but the agent has zero runtime enforcement of those rules because no PreToolUse hook is wired up
- The PR-1 `cycle_id` schema bug only existed because `_apply_004_migration_if_needed` was no longer being called

The closed-loop premise of SIO ("we measure whether applied rules
actually reduce errors") **does not work today** on a fresh install.

## Proposal

Extend the `HarnessAdapter` Protocol with two lifecycle methods so
orchestration concerns can be expressed per-harness without polluting
`bootstrap.py` (which should stay pure file-staging):

```python
class HarnessAdapter(Protocol):
    name: str
    def detect(self) -> bool: ...
    def pre_install(self, *, dry_run: bool) -> InstallReport: ...   # NEW
    def install(self, *, dry_run: bool, force: bool) -> InstallReport: ...
    def post_install(self, *, dry_run: bool) -> InstallReport: ...  # NEW
    def uninstall(self, *, dry_run: bool) -> InstallReport: ...
    def status(self) -> StatusReport: ...
```

`pre_install` and `post_install` default to no-op so existing stub
adapters (cursor, windsurf, opencode) don't need updating until
they're real adapters.

For Claude Code (`claude_code.py`), implement:

- `pre_install`: call `init_db(per_platform_db)`, `init_db(canonical_db)`,
  `ensure_schema_version`, `apply_pending_migrations`,
  `_run_split_brain_backfill`. These are universal-ish but they need to
  know the per-platform path, which is harness-specific.
- `post_install`: call `_register_hooks(settings_path)` and
  `INSERT INTO platform_config`.

Separately, add a **harness-agnostic** schema bootstrap inside `bootstrap.py`
(or a new `sio.core.db.bootstrap` module) that runs once before any
adapter — covering the canonical DB. This way the canonical DB is always
ready even if no harness adapter is selected.

### Test plan

- A regression test for *each* of the six gaps that asserts the post-
  condition is true after a fresh `sio init`:
  - `~/.claude/settings.json` exists and contains 5 SIO hook registrations
  - `behavior_invocations` table exists in per-platform DB
  - `schema_version` table has version=1 row with status='applied'
  - `platform_config` table has a row for `claude-code` with
    `hooks_installed=1, skills_installed=1`
  - `cycle_id` columns exist in `datasets` and `suggestions` (already
    landed via PR #1)
  - PreCompact hook fires on a synthetic session and writes to
    `behavior_invocations`

## Out of scope

- Redesigning the harness adapter pattern itself — it's the right shape.
- Cursor / Windsurf / OpenCode adapters — they remain stubs; this PRD
  only restores the orchestration that exists upstream of any specific
  harness, plus the Claude Code-specific wiring that was lost.
- Per-tool argument validation hooks (cascade-shield) — that's the
  `sio-validate` skill's territory, separate from the SIO PostToolUse
  telemetry hooks this PRD restores.

## Open questions

1. Should `pre_install` failures (e.g., schema migration error) abort
   the install or be reported and continue? Current `install()` returns
   per-file errors without aborting; pre_install should probably do the
   same but with louder reporting.
2. Should there be a `sio doctor --fix` that detects "post-install
   orchestration was never run on this DB" (e.g., `schema_version` is
   `n/a`) and offers to back-fill? Useful for users who installed
   v0.1.2/v0.1.3 before this fix lands.
3. Hook registration writes to `~/.claude/settings.json`, which the user
   may have other entries in. The old `installer.py` had a careful
   merge-not-overwrite path with backup-on-corrupt-JSON. The port should
   preserve that behavior verbatim — it's load-bearing for users with
   existing hook setups.

## Effort estimate

Medium. Roughly:

- 0.5d: extend `HarnessAdapter` protocol + InstallReport + base no-op
  defaults; thread pre_install/post_install through `sio init` CLI
- 0.5d: port `_register_hooks` from old `installer.py` into
  `claude_code.py::post_install`, preserving merge-not-overwrite + bare-
  to-wrapped legacy hook migration
- 0.5d: port `ensure_schema_version` + migration application into a
  harness-agnostic `bootstrap.py::ensure_canonical_db_ready()`
- 0.5d: per-platform DB init + platform_config insert in
  `claude_code.py::pre_install` / `post_install`
- 0.5d: regression tests covering each of the six gaps
- 0.5d: `sio doctor --fix` back-fill verb for users on v0.1.2/v0.1.3

Total: ~3 days. Should ship as v0.1.4 or v0.2.0 depending on whether
the protocol extension is considered breaking for downstream stub
adapters (it shouldn't be — defaults are no-op).

## References

- Commit `bc39869` — the harness refactor that introduced the
  regression (file-staging done right, orchestration not ported)
- PR #1 (merged 2026-05-03) — `cycle_id` schema fix, a downstream
  symptom of `_apply_004_migration_if_needed` not running
- PRD violated-rule-to-pretooluse-hook — depends on this PRD (rule-to-hook promotion needs hooks
  to exist first)
- `src/sio/adapters/claude_code/installer.py` — the old monolith,
  deleted from disk; full source is preserved in git history at
  `bc39869~1` if needed for line-by-line porting
