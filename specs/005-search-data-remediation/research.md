# Research & Evidence: SIO Search & Data-Sourcing Self-Audit

**Date**: 2026-06-07
**Method**: Two independent passes — (A) read-only capability audit of the search/data code (34 files, file:line cited), and (B) behavioral mining of the last 21 days of session history.

---

## A. Capability audit (what the code actually does)

| Dimension | Verdict | Key file:line | How it works |
| --- | --- | --- | --- |
| **1. Targeted data sourcing / conservation** | **PARTIAL** | `search/cli.py:84–91` (`_file_within`), `queries.py:278–314` (`get_error_records`), `main.py:2138` (`limit=0`) | `sio search` has `--recent N` (mtime cutoff), `--limit N` (per-agent cap), `--files`/`--count` (cheap enumeration). `get_error_records` supports `--since`/`--project`/`--type` SQL predicates + `ORDER BY timestamp DESC LIMIT ?`. BUT `sio suggest` calls it with `limit=0` (= no cap, `queries.py:310`) → loads the whole error table on a 951 MB DB. No token budget, no dedup-before-load, no economy gate. |
| **2. Targeted multi-hop search** | **IMPLEMENTED (suggest only)** | `main.py:1917–1980` (flags), `main.py:2208–2331` (Hop-2 exec) | `sio suggest` = real 2-hop cascade: Hop-1 (`--grep`/`--type`/`--project`/`--within`/`--use-cache`) → Hop-2 (`--refine`, `--strategy filter|recluster|hybrid`, `--recluster-threshold`) incl. true re-clustering via `cluster_errors()`. `--within`/`--use-cache` feed a saved CSV back to skip the DB round-trip. `sio search` and `sio recall` are single-pass — no `--refine`. |
| **3. Recency** | **PARTIAL** | `search/cli.py:84–91,464–474,888,1041`; `queries.py:309` (`ORDER BY timestamp DESC`); `main.py:875–876` (mine requires `--since`); `main.py:2138` (suggest: no `--since`); `main.py:1307–1317` (recall picks freshest mtime); `suggestions/confidence.py:32–79` (temporal decay) | `sio search` defaults `--recent 0` (no cutoff) and sorts nothing — ripgrep returns filesystem order; only `--skeleton` sorts by `ts` DESC (`search/cli.py:774`). `get_error_records` orders DESC + respects `since`. **`sio suggest` has no `--since` option** → loads entire history. `sio mine` *requires* `--since`. `recall` sorts JSONL by `st_mtime` DESC, picks freshest by default. `confidence.py` has a fresh/cooling/stale decay multiplier — but that's *scoring*, not data-*filtering*. |
| **4. Session walk-back / forward** | **PARTIAL** | `error_extractor.py:253–254` (±1 msg); `recall.py:84–92` (±1 step); `search/cli.py:826–853` (`expand_sessions`); `search/cli.py:530` (`-C` rg flag) | Mining saves `context_before`/`context_after` as the single message at `idx±1` (hard-coded, one turn). Recall expands matched steps by ±1 step index. `sio search --session <uuid>` dumps the full transcript in order. The rg fast-path honors `-C N` raw lines. **No walk-forward-N-turns, no "±K turns around a hit" API, no offset-based navigation beyond full dump.** `recall --polish` emits a Gemini *prompt string* to stdout rather than calling Gemini (`main.py:1345–1357`). |

### Gaps distilled
1. **G1 (US4)** — `sio suggest` `get_error_records(conn, limit=0, …)` (`main.py:2139`) = unbounded load on 951 MB DB; no `--since`; preview cache uncapped (`main.py:2120–2121` strips context fields but no size cap).
2. **G2 (US3)** — multi-hop cascade siloed to `suggest`; absent from `search`/`recall`.
3. **G3 (US1)** — `sio search` default `--recent 0` + filesystem-order output; `suggest` no time window at all.
4. **G4 (US2)** — context window is ±1 only, no offset/N-turn API; `--session` is all-or-nothing; `recall --polish` stubbed.

---

## B. Behavioral mining (how the disciplines are actually used)

**Window**: 21 days. **Corpus**: `~/.claude/projects/**/*.jsonl`, 437 recent session files.
**Unit**: full invocation lines (JSONL records carrying a `"command"` that runs `session-search`/`sio search`). **N = 808.**

| Discipline | Signal counted | Count | Rate of 808 |
| --- | --- | --- | --- |
| Recency-first | `--recent` | 402 | ~50% |
| Broad-net (recency-gate risk) | `--all` | 59 | ~7% |
| Targeted sourcing | `--files` | 258 | ~32% |
| **Multi-hop / refine** | `--refine`/`--within`/`--use-cache`/`--strategy` | **3** | **~0.4%** |
| Context (walk-back) | `--context` | 24 | ~3% |
| Session pin (walk-back) | `--session` | 23 | ~3% |
| Cross-harness fan-out | `--agent` | 29 | ~3.6% |
| Count mode | `--count` | 43 | ~5% |

### Reproduce
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
**Caveat**: counts are full-line matches; minor inflation possible from tool_result echoes. Ratios are the signal, not absolute counts. (Naïve `rg -c '--recent'` across all 437 files returned 14,998 — almost entirely the `memory-search.md` rule text echoed into every transcript's system-reminder; the `"command":`-scoped count above is the real-invocation figure.)

---

## C. The synthesis (why capability × behavior reinforce)

The disciplines are **opt-in or siloed**, so the agent's path of least resistance (plain `sio search`) skips them:
- Recency is a flag, not a default → used ~50%.
- Multi-hop lives only in `suggest` → used ~0.4% in `search`.
- Context-walk has no good API (±1 or full dump) → used ~3%.

Flipping defaults (US1), surfacing the cascade (US3), and adding a real context window (US2) converts each into the easy path. US4 enforces the economy guard the constitution already mandates; US6 makes the discipline measurable so the gap can't silently re-open.
