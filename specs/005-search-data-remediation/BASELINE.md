# Behavioral Baseline — SIO Search Discipline
**Captured**: 2026-06-07
**Source**: `specs/005-search-data-remediation/research.md` §B
**Purpose**: Pre-implementation before-numbers for SC-001..003 in `spec.md`.

---

## Measured Rates (21-day window, N = 808 invocations)

| Discipline (SC ref) | Signal | Count | Rate |
|---|---|---|---|
| **SC-001** Recency-first | `--recent` flag present | 402 | ~50% |
| **SC-002** Multi-hop / refine | `--refine`/`--within`/`--use-cache`/`--strategy` | 3 | **~0.4%** |
| **SC-003** Context walk-back | `--context` | 24 | ~3% |
| SC-003 Session pin | `--session` | 23 | ~3% |
| Broad-net risk | `--all` | 59 | ~7% |
| Targeted sourcing | `--files` | 258 | ~32% |
| Cross-harness | `--agent` | 29 | ~3.6% |
| Count mode | `--count` | 43 | ~5% |

**Corpus**: `~/.claude/projects/**/*.jsonl`, 437 recent session files, 21-day window.

---

## Reproduce Block (verbatim from research.md §B)

```bash
cd ~/.claude/projects
FILES=$(find . -name '*.jsonl' -mtime -21 2>/dev/null)
INV=$(echo "$FILES" | xargs rg --no-filename '"command":"[^@]*(session-search|sio search)' 2>/dev/null)
printf '%s\n' "$INV" | grep -c .                                  # N (808)
printf '%s\n' "$INV" | rg -c -- '--recent'                        # recency
printf '%s\n' "$INV" | rg -c -- '--all'                           # broad-net
printf '%s\n' "$INV" | rg -c -- '--files'                         # targeted
printf '%s\n' "$INV" | rg -c -- '--refine|--within|--use-cache|--strategy'  # multi-hop
printf '%s\n' "$INV" | rg -c -- '--context'                       # walk-back
printf '%s\n' "$INV" | rg -c -- '--session'                       # session pin
```

**Caveat**: counts are full-line matches; minor inflation possible from tool_result echoes.
Ratios are the signal, not absolute counts. Naïve `rg -c '--recent'` across all 437 files
returns ~14,998 (rule text echoed in system-reminders); the `"command":`-scoped count is the
real-invocation figure.

---

## Target deltas (post-rollout, T072)

| SC | Before | Target |
|---|---|---|
| SC-001 recency-first | ~50% | ≥ 85% (default flipped) |
| SC-002 multi-hop | ~0.4% | ≥ 5% (surfaced from `suggest` to `search`) |
| SC-003 context walk-back | ~3% | ≥ 15% (real `±N turns` API) |

---

## Post-rollout (T072) — 2026-06-07

### Measurement methodology

The §B reproduce block from `research.md` is designed for the live
`~/.claude/projects/**/*.jsonl` corpus (808 real invocations, 21-day window).
Running it against the `fake_session_corpus` fixture yields N=8 (deterministic
but not statistically meaningful). Per the T072 task description, we document
**both**:

1. **Fixture-level validation**: the discipline-counting machinery works correctly
   against the fixture (see below).
2. **Live-corpus projection**: the expected post-rollout delta based on the
   US1–US6 implementation.

### Fixture reproduce (deterministic, N=8 synthetic invocations)

Fixture layout (matching conftest.py `fake_session_corpus` + synthetic calls):

| Session | Age | Calls embedded |
|---|---|---|
| session-today | 1h | `--recent 7 --files` ×2, `--recent 7` ×1 |
| session-week | 8d | `--refine` ×1, `--within` ×1, `--recent 14` ×1 |
| session-old | 45d | plain `sio search` ×2 (no flags) |

Counts over N=8:

| SC ref | Discipline | Before (BASELINE, N=808 live) | Fixture post (N=8) | Expected post live |
|---|---|---|---|---|
| SC-001 | recency-first (`--recent`) | ~50% (402/808) | 50% (4/8) | ≥ 85% |
| SC-002 | multi-hop (`--refine`/`--within`/…) | ~0.4% (3/808) | 25% (2/8) | ≥ 5% |
| SC-003 | context walk-back (`--context`) | ~3% (24/808) | 0% (0/8) | ≥ 15% |

**Fixture interpretation**: the fixture was not designed to saturate
context-walk calls (there is no `--around` or `--context` in the synthetic
calls). Recency and multi-hop machinery counts correctly. The 0% context rate
on the fixture is expected — the fixture predates any `--around N` live usage
and only tests the counting mechanism.

### Expected post-rollout SC-001..003 deltas (live corpus projection)

| SC | Before (BASELINE) | Target | Expected driver |
|---|---|---|---|
| SC-001 recency-first | ~50% | ≥ 85% | US1 makes `--recent 7` the *default* — every plain `sio search` now implicitly counts as recency-gated; new sessions don't need the explicit flag to hit the target |
| SC-002 multi-hop | ~0.4% | ≥ 5% | US3 surfaces `--refine`/`--strategy`/`--within`/`--use-cache` on `sio search` directly (were `sio suggest`-only); noise-threshold hint (FR-006) prompts operators to use Hop-2 |
| SC-003 context walk-back | ~3% | ≥ 15% | US2 introduces a real `--around N` API; old `--context N` (raw lines) is now complemented by a turn-aware option that is far more useful, expected to drive adoption |

### Reproduce note for live re-measurement

Once new session files accumulate post-rollout, re-run the §B reproduce block
(from `research.md`) against `~/.claude/projects` to get actual post-rollout
numbers. The fixture-level validation above confirms the counting is correct;
the live re-run is the definitive before/after comparison for SC-001..003.

The T070 integration test (`tests/unit/search/test_sc007_no_regression.py`)
provides the machine-readable no-regression gate against the fixture.
