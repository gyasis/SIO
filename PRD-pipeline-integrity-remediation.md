# PRD — SIO Pipeline Integrity & Training-Data Remediation

**Status:** Draft
**Author:** Gyasi Sutton (with adversarial bug-hunter agent analysis)
**Date:** 2026-04-15
**Owner:** SIO core
**Target branch:** `feat/pipeline-integrity-remediation`
**Speckit constitution required:** Yes — this PRD will feed `/speckit-workflow` for true dev

---

## 1. Executive Summary

SIO's mining pipeline is alive (`error_records`=45,536, `flow_events`=66,567, `patterns`=48), but **the entire downstream training/optimization pipeline is starving or broken**. A targeted self-audit + dual adversarial bug-hunter sweep found:

- **DSPy training has zero per-message labels available.** The Claude Code hook IS firing — it writes 38,091 rows to `~/.sio/claude-code/behavior_invocations.db`, but every DSPy reader queries `~/.sio/sio.db` where the table is empty. **Split-brain SQLite layout with no bridge anywhere in code.** This single defect starves: `gold_standards` (0), `optimization_runs` (0), `optimized_modules` (3 stale), and the entire `sio optimize` path.
- **`sio suggest` destroys audit history** every invocation by `DELETE FROM applied_changes/suggestions/datasets/pattern_errors/patterns`. Explains why `applied_changes`=0 despite the user having applied rules.
- **`apply_change` is non-atomic** with no backup of the target file. WSL2 + Defender makes file corruption a real risk on user-owned `CLAUDE.md`.
- **Autoresearch loop has never been scheduled** — no cron, no systemd, no daemon. `autoresearch_txlog`=0 forever.
- **`sio mine` flow path bypasses dedup**, re-ingesting every JSONL each run (1.5K-1.8K dupes/day to `flow_events`).
- **JSONL parser reads full file into RAM** (OOM risk on 100MB+ session files).
- **Subagent JSONLs mined as standalone sessions** with no parent linkage, evading the sidechain filter.
- 16 additional HIGH/MEDIUM/LOW findings spanning concurrency, schema migration, dead-grade transitions, fabricated rule merges, and broken metrics.

This PRD prioritizes a 3-phase remediation plan that restores the data flow first, then hardens the destructive paths, then fixes the long tail of correctness bugs.

---

## 2. Goals

| # | Goal | Success Metric |
|---|---|---|
| G1 | Unblock DSPy training data flow | `behavior_invocations` in `~/.sio/sio.db` grows daily; `gold_standards` count > 0 within 7 days |
| G2 | Protect audit history | `sio suggest` no longer deletes `applied_changes`; rollback works on previously-applied rules |
| G3 | Make `apply_change` safe | All writes are atomic + backed up to `~/.sio/backups/` |
| G4 | Activate the autoresearch loop | `autoresearch_txlog` receives rows; `applied_changes` shows automated promotions |
| G5 | Fix `sio mine` correctness | Flow dedup keyed on `(file_path, session_id, flow_hash)`; `processed_sessions` honored everywhere |
| G6 | Eliminate silent failures | All hooks write a heartbeat file; `sio status` surfaces last-error and consecutive-failure-count |
| G7 | Bound memory and concurrency | JSONL streaming parser; `busy_timeout` ≥ 30s; missing indexes added |
| G8 | Remove dead/order-dependent code paths | Stable pattern slugs; `declining` grade reachable; correct n-gram range |

---

## 3. Non-Goals

- New SIO features (no new commands, no new tables beyond what's required for the fixes)
- DSPy metric redesign beyond replacing the trivially-broken `recall_metric`
- Full schema migration framework (we'll add `schema_version` but keep `IF NOT EXISTS` ALTER pattern for now)
- UI/CLI redesign — only `sio status` gets new health output
- Multi-platform support (`claude-code` only)

---

## 4. Background — Evidence

### 4.1 Database split-brain (the headline bug)

```bash
$ sqlite3 ~/.sio/claude-code/behavior_invocations.db "SELECT COUNT(*), MAX(timestamp) FROM behavior_invocations;"
38091|2026-04-15T09:12:54Z

$ sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM behavior_invocations;"
0
```

**Writers** all use `~/.sio/claude-code/behavior_invocations.db`:
- `src/sio/adapters/claude_code/hooks/post_tool_use.py:14,42`
- `src/sio/adapters/claude_code/hooks/stop.py:14,129`
- `src/sio/adapters/claude_code/hooks/pre_compact.py:14,43`
- `src/sio/adapters/claude_code/installer.py:79,95`
- `src/sio/cli/main.py:66,108,169,244,275`

**Readers** all assume `~/.sio/sio.db`:
- `src/sio/core/arena/gold_standards.py:20`
- `src/sio/core/dspy/optimizer.py:71-73` (`get_labeled_for_optimizer`)
- `src/sio/core/feedback/batch_review.py`
- `src/sio/core/feedback/labeler.py`
- Every `db_path = ~/.sio/sio.db` branch in `cli/main.py`

There is no `ATTACH`, no `attach_database`, no path bridge anywhere in `src/sio` (verified by grep).

### 4.2 Per-table health snapshot

| Table | Rows | Last Activity | Diagnosis |
|---|---|---|---|
| `error_records` | 45,536 | 2026-04-09 | Mining alive, 6d stale |
| `flow_events` | 66,567 | 2026-04-15 | Mining alive but **dedup-broken** (re-ingests daily) |
| `positive_records` | 675 | 2026-04-09 | Alive |
| `patterns` | 48 | 2026-04-09 (one batch) | Wiped+rebuilt every `sio suggest` |
| `pattern_errors` | 252 | — | Will be wiped on next suggest |
| `processed_sessions` | 1,121 | — | Honored by `error` mining only |
| `session_metrics` | 977 | — | Alive |
| `datasets` | 26 | 2026-04-09 (one batch) | Wiped+rebuilt; thin |
| `ground_truth` | 26 | 2026-04-14 (1 row) | 92% rejection rate at approval gate |
| `recall_examples` | **1** | 2026-03-25 | Manual-only; never auto-populated |
| `optimized_modules` | **3** | 2026-03-25 | Dead since one batch in March |
| `optimization_runs` | **0** | never | Broken — no labeled examples to optimize on |
| `behavior_invocations` | **0** | never | **Split-brain — 38,091 rows in wrong DB** |
| `gold_standards` | **0** | never | `promote_to_gold` never called |
| `applied_changes` | **0** | never | Wiped every suggest run |
| `autoresearch_txlog` | **0** | never | Loop never started |
| `platform_config` | **0** | never | Installer never writes it |

### 4.3 Code citations for each major defect

- **C1** Split-brain: `adapters/claude_code/hooks/post_tool_use.py:14`, `stop.py:14`, `pre_compact.py:14`, `installer.py:79,95`, `cli/main.py:11,66,108,169,244,275` vs `arena/gold_standards.py:20`, `dspy/optimizer.py:71-73`
- **C2** Destructive suggest: `cli/main.py:1389-1395` and `:1425-1428`
- **C3** Non-atomic write: `applier/writer.py:321` (`target_path.write_text(diff_after)`)
- **C4** Autoresearch never scheduled: `cli/main.py:3442-3465` is foreground-blocking only; `crontab -l` empty; no systemd unit
- **C5** Recall manual-only: `cli/main.py:2638-2653`
- **C6** Full-file JSONL read: `mining/jsonl_parser.py:417` (`text = file_path.read_text(encoding="utf-8")`)
- **C7** Subagent files mined as top-level: `mining/pipeline.py:302` (`for file_path in directory.rglob("*")`)
- **H1** Flow mine bypasses dedup: `mining/flow_pipeline.py:53-144`
- **H2** `promote_to_gold` orphan: `core/arena/gold_standards.py:11` (zero callers)
- **H3** File-hash dedup on growing JSONLs: `mining/pipeline.py:237-247`
- **H4** `busy_timeout=1000`: `core/db/schema.py:429`
- **H5** Order-dependent clustering: `clustering/pattern_clusterer.py:163-184`
- **H6** `fromisoformat("")` crash: `clustering/ranker.py:75`
- **H7** Path-traversal-adjacent allowlist: `applier/writer.py:32`
- **H8** Hooks `except Exception: pass`: `adapters/claude_code/hooks/post_tool_use.py:68-69`, `stop.py`, `_detect_passive_signals` line 109
- **H9** Trivial `recall_metric`: `training/recall_trainer.py:277-285`
- **M1** No `schema_version`: `core/db/schema.py:383-494`
- **M2** Min-message threshold rejects high-signal sessions: `mining/pipeline.py:147-148`
- **M3** Missing indexes: `error_records(user_message, error_text)`, `flow_events(was_successful, flow_hash)`
- **M4** Dead `declining` grade: `clustering/grader.py:80`
- **M5** N-gram off-by-one: `mining/flow_extractor.py:116`
- **M6** Rule merge fabrication: `applier/writer.py:140-157`
- **M7** `sio purge` wrong DB: `cli/main.py:242-245`
- **M8** 92% suggestion rejection rate: data-only finding, signal for `suggestions/dspy_generator.py` quality work
- **L1** Extension allowlist drops `.rs/.go/.java/.cpp/.ipynb`: `mining/flow_extractor.py:42`
- **L2** `tool_failure` deduped away: `_dedup_by_error_type_priority`
- **L3** Loose success-signal heuristic: `mining/flow_extractor.py:170`
- **L4** Silent missing-dir skip: `mining/pipeline.py:300`
- **L5** No max-size guard in `_file_hash`
- **H10** `behavior_invocations.platform` filter inconsistency: writers hard-code `_DEFAULT_PLATFORM='claude-code'` but readers in `dspy/optimizer.py:73-75` and elsewhere filter by `platform=`. Once C1 is fixed, any drift in platform string causes silent zero-row queries. (Hunter 1, H5)
- **H11** `patterns.centroid_embedding` BLOB column is hardcoded `None` at `cli/main.py:1409` (`# skip blob for now`). Schema reserves the column and `queries.py` has upsert logic, but every `sio suggest` run wipes patterns and re-runs fastembed ONNX over all 45K errors from scratch. Multi-minute pass that should be incremental. (Hunter 2, H3)
- **H12** Timezone drift — naive `datetime.fromisoformat(...)` calls in `clustering/ranker.py:79-80`, `clustering/grader.py:88-89`, `mining/pipeline.py:432` silently treat naive timestamps as UTC. WSL TZ=America/New_York + SpecStory filename Z-suffix + JSONL wire-format mix produces 4-5h shifts in recency scoring and the 7-day "established" grade threshold. (Hunter 2, H7)
- **L6** `installer.py:95` recreates `~/.sio/claude-code/behavior_invocations.db` on every `sio install`, guaranteeing the split-brain reappears after any reinstall. Once C1 is fixed, the installer code path must also be updated, otherwise the next `sio install` resurrects the bug. (Hunter 1, L2)

---

## 5. Phased Plan

### Phase 1 — Restore Data Flow (CRITICAL, week 1)

**Objective:** Make DSPy training data exist where readers expect it. Stop destroying audit history.

| Task | Files | Acceptance |
|---|---|---|
| 1.1 Repoint all hook writers to `~/.sio/sio.db` | `adapters/claude_code/hooks/{post_tool_use,stop,pre_compact}.py`, `adapters/claude_code/installer.py`, `cli/main.py` (~6 sites) | New `behavior_invocations` rows land in `sio.db` after one tool call |
| 1.2 Backfill 38,091 legacy rows | One-shot script `scripts/migrate_split_brain.py` doing `ATTACH … AS legacy; INSERT INTO behavior_invocations SELECT * FROM legacy.behavior_invocations;` with `INSERT OR IGNORE` | `sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM behavior_invocations" >= 38091` |
| 1.3 Make `sio suggest` non-destructive | `cli/main.py:1389-1428` — replace `DELETE FROM` with upsert / mark-stale. Never touch `applied_changes` | Re-running `sio suggest` preserves all `applied_changes` rows |
| 1.4 Atomic write + backup in `apply_change` | `applier/writer.py:321` — write to `<target>.tmp`, fsync, rename; pre-write copy to `~/.sio/backups/<file>.<ts>` | Crash mid-write leaves `<target>` intact; backup file exists |
| 1.5 Auto-promotion: `behavior_invocations` → `gold_standards` | New trigger in `post_tool_use.py` or `stop.py`: when `user_satisfied=1 AND correct_outcome=1`, call `promote_to_gold` | `gold_standards` count > 0 after 7 days |
| 1.6 Schedule `autoresearch` | New systemd-user unit OR Claude Code `CronCreate` trigger; document cadence in README | `autoresearch_txlog` accumulates rows |

**Phase 1 exit criteria:**
- `sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM behavior_invocations" > 38091`
- `sio suggest` run twice in a row preserves `applied_changes`
- `applier/writer.py` has unit test for atomic write + backup
- `autoresearch_txlog` has at least 5 rows after 24h of scheduled runs

### Phase 2 — Mining Correctness (HIGH, week 2)

**Objective:** Make `sio mine` idempotent, bounded, and accurate.

| Task | Files | Acceptance |
|---|---|---|
| 2.1 Add `processed_sessions` dedup to flow mining | `mining/flow_pipeline.py:53-144` | Re-running `sio flows --mine-first` doesn't duplicate `flow_events` |
| 2.2 Stream JSONL parsing | `mining/jsonl_parser.py:417` — replace `read_text` + `splitlines` with `for line in open(path)` | 100MB session file mines without RAM spike |
| 2.3 Byte-offset-resume dedup | `mining/pipeline.py:237-247` — store last byte offset per file, resume from there on next mine | Growing JSONL only re-parses appended bytes |
| 2.4 Subagent file linkage | `mining/pipeline.py:302` — detect `subagents/` path component, attach `parent_session_id`, mark `is_subagent=True`, exclude from top-level error mining unless explicitly requested | Subagent rows have FK to parent; no double-counting |
| 2.5 Bump `busy_timeout` | `core/db/schema.py:429` → 30000 | Concurrent mine + hook writes don't raise `database is locked` |
| 2.6 Fix `fromisoformat("")` crash | `clustering/ranker.py:75` — guard empty timestamps, fall back to `mined_at` | `sio suggest` runs on mixed JSONL+SpecStory corpus without ValueError |
| 2.7 Stable pattern_id slugs | `clustering/pattern_clusterer.py:163-184` — sort errors deterministically before clustering; derive slug from cluster centroid hash | Same corpus → same `pattern_id` slugs across runs |
| 2.8 Add missing indexes | `core/db/schema.py` — `error_records(user_message, error_text)`, `flow_events(was_successful, flow_hash)`, `behavior_invocations(platform, timestamp)` | `EXPLAIN QUERY PLAN` shows index usage on insert-time dedup and flow promotion queries |

**Phase 2 exit criteria:**
- `sio mine` and `sio flows --mine-first` are idempotent (running twice = same row counts)
- One full mine pass on 907 JSONL files completes with peak RSS < 500MB
- No more `flow_events` daily dupe ingestion

### Phase 3 — Hardening & Long Tail (MEDIUM/LOW, weeks 3-4)

**Objective:** Eliminate silent failures, fix metrics, retire dead code.

| Task | Files | Acceptance |
|---|---|---|
| 3.1 Hook heartbeat file | All hooks write `~/.sio/hook_health.json` `{last_success, last_error, consecutive_failures, hook_name}`; `sio status` reads and displays | `sio status` shows hook health; tests cover stale-heartbeat detection |
| 3.2 Schema version table | `core/db/schema.py` — add `schema_version` table; gate migrations on version; refuse to start if migration partially failed | Old DB upgrades cleanly; partial-migration test passes |
| 3.3 Replace `recall_metric` | `training/recall_trainer.py:277-285` — embedding-based similarity OR exact-match for routing/flow tasks | Metric distinguishes correct from hallucinated outputs in unit tests |
| 3.4 Path safety in `_validate_target_path` | `applier/writer.py:32` — restrict to `~/.claude/` and explicit allowlist; reject `Path.cwd()` blanket allow | Cannot overwrite a file outside the explicit allowlist |
| 3.5 Restore lower-priority error data | `_dedup_by_error_type_priority` — keep `tool_failure` rows alongside `user_correction` (don't dedupe across types) | Tool error text preserved in `error_records` |
| 3.6 Stricter success heuristic | `mining/flow_extractor.py:170` — require explicit positive signal, not "absence of negative" | Flow success rate corrected on test corpus |
| 3.7 Fix n-gram range | `mining/flow_extractor.py:116` — `range(n_range[0], n_range[1] + 1)` | 5-grams produced when `(2,5)` requested |
| 3.8 Reachable `declining` grade | `clustering/grader.py:80` — compute decay against `MAX(error_records.timestamp)` per pattern_id (not just current insertion time) | At least one pattern transitions to `declining` on a corpus with stale errors |
| 3.9 Rule merge guardrails | `applier/writer.py:140-157` — require explicit `--merge` flag OR user prompt before similarity-based merge | No fabricated hybrid rules without explicit consent |
| 3.10 Fix `sio purge` target | `cli/main.py:242-245` — purge `~/.sio/sio.db` (`error_records`, `flow_events` by `mined_at`); add separate `--behavior-only` flag | Main DB shrinks after `sio purge --days 30` |
| 3.11 Extension allowlist | `mining/flow_extractor.py:42` — add `.rs/.go/.java/.cpp/.ipynb` | Flow extractor handles polyglot codebases |
| 3.12 Loud missing-dir warnings | `mining/pipeline.py:300` — log warning when expected dir doesn't exist | "0 sessions found" runs print explicit reason |
| 3.13 Max-size guard in `_file_hash` | Skip files > 1GB with warning | Pathological files don't OOM hash |
| 3.14 Suggestion quality investigation | `suggestions/dspy_generator.py` — instrument why 92% rejection rate; tune prompt + metric | Approval rate > 30% on next batch |
| 3.15 Centralize `_DEFAULT_PLATFORM` constant + audit all `platform=` filter sites (H10) | All writers + readers — single shared constant in `core/db/queries.py` | Zero string-duplicated platform filters; integration test confirms read after write returns rows |
| 3.16 Revive `centroid_embedding` for incremental clustering (H11) | `cli/main.py:1409`, `clustering/pattern_clusterer.py`, `core/db/queries.py` upsert path — store ONNX vectors per pattern; reuse on next suggest | Re-running `sio suggest` skips ONNX pass for unchanged patterns; runtime drops from minutes to seconds on no-new-error case |
| 3.17 Timezone correctness (H12) | All `datetime.fromisoformat` sites — make tz-aware, normalize on write to UTC, store as ISO 8601 with explicit `+00:00` | Unit test passes with TZ=America/New_York simulating naive timestamp inputs |
| 3.18 Update `installer.py` to never recreate split-brain DB (L6) | `adapters/claude_code/installer.py:79,95` — point to `~/.sio/sio.db`, ensure `sio install` is idempotent and won't resurrect old layout | Re-running `sio install` after C1 fix does not create `~/.sio/claude-code/behavior_invocations.db` |

**Phase 3 exit criteria:**
- All `except Exception: pass` replaced with logged + heartbeat-tracked error states
- `sio status` shows comprehensive health (mine cadence, hook heartbeat, last optimization, DB size)
- Suggestion approval rate > 30%

---

### Phase 4 — Coverage closure (every remaining adversarial finding) — required, not optional

This phase exists to guarantee **zero deferrals**. Every finding from the adversarial agents is in scope and must close before this PRD is marked done.

| Task | Files | Acceptance |
|---|---|---|
| 4.1 Apply fix for H10 (platform string) | see 3.15 | covered |
| 4.2 Apply fix for H11 (centroid_embedding) | see 3.16 | covered |
| 4.3 Apply fix for H12 (timezone) | see 3.17 | covered |
| 4.4 Apply fix for L6 (installer) | see 3.18 | covered |
| 4.5 Cross-check — adversarial agent re-run | re-spawn both `adversarial-bug-hunter` agents on the post-fix repo | Both reports return zero CRITICAL / HIGH findings; any new MEDIUM/LOW findings open a follow-up PRD |

**Phase 4 exit criteria:**
- Adversarial re-run returns clean
- Every finding from the original audit (sections 4.3) has a closed task with file:line citation in the changelog
- No "deferred" markers anywhere in this PRD

---

## 6. Risk & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Phase 1.1 hook repointing breaks existing telemetry collection | Medium | High | One-shot migration script run BEFORE hook redeploy; verify row count parity |
| Phase 1.3 changing `sio suggest` semantics breaks downstream queries that assume "patterns table = current cycle only" | Medium | Medium | Audit all `SELECT … FROM patterns` callers; add `WHERE active=1` filter; backfill `active` column in migration |
| Phase 2.4 subagent linkage changes error_record counts, invalidates existing pattern centroids | Low | Medium | Run on a copy of the DB first; compare top-50 patterns before/after |
| Phase 2.7 stable slug change breaks existing `ground_truth.pattern_id` text-FK | High | Medium | One-shot remap script: `UPDATE ground_truth SET pattern_id = new_slug WHERE pattern_id = old_slug` keyed by error overlap |
| Autoresearch (Phase 1.6) running unattended and applying bad rules | Medium | High | Require human approval gate (`status='pending_approval'`) before any `apply`; `arena_passed=1` required for auto-promote |
| Atomic write + backup (1.4) fills disk over time | Low | Low | Backup retention: keep last 10 per file, prune older |

---

## 6.1 Scope Statement (NON-NEGOTIABLE)

**Per direct user direction (2026-04-15): ALL adversarial-agent findings in scope. No deferrals. No "must-ship slice." No "we'll do the top six."**

The PRD covers **34 tasks across 4 phases**:
- Phase 1 (CRITICAL, 6 tasks)
- Phase 2 (HIGH, 8 tasks)
- Phase 3 (MEDIUM/LOW, 18 tasks — including 3.15-3.18 added for H10/H11/H12/L6)
- Phase 4 (Coverage closure, 5 tasks — includes adversarial re-run)

Section 9 "Out-of-Scope Follow-ons" lists items that were never adversarial findings (new features, multi-platform, web UI). Those remain out of scope. **Everything that came out of the bug hunters is in.**

---

## 7. Open Questions

1. **Should `behavior_invocations` stay in a separate per-platform DB OR consolidate to `sio.db`?** PRD currently assumes consolidate (simplest). Alternative: keep per-platform writers, add a `sync` step that mirrors into `sio.db` for DSPy reads.
2. **Autoresearch scheduling — systemd-user vs Claude Code CronCreate?** CronCreate keeps everything in the Claude ecosystem. systemd-user is independent of any Claude session. Recommendation: CronCreate (lower friction).
3. **Backfill cutoff for legacy 38,091 rows** — full backfill or only last 30 days? Full is simplest; trimming may avoid noise from very old labels.
4. **`sio suggest` non-destructive design** — soft-delete (status='stale') vs version table vs in-place upsert. Versioning gives best provenance but most schema work.
5. **Subagent linkage** — store `parent_session_id` in `error_records` directly, or normalize into a `session_relationships` table?

---

## 8. Acceptance — Definition of Done

- All 7 CRITICAL findings resolved; verified by row-count and idempotency tests
- All 12 HIGH findings (H1-H12) resolved; verified by unit + integration tests
- All MEDIUM findings (M1-M8) resolved; no deferrals
- All LOW findings (L1-L6) resolved; no deferrals
- Adversarial re-run (Phase 4.5) returns zero CRITICAL / HIGH findings
- `sio status` shows green health across: hooks, mine cadence, training tables, last optimization, DB size
- DSPy `optimization_runs` table receives at least one row from a real auto-triggered optimization
- Documentation updated: README pipeline diagram reflects new data flow; CLAUDE.md rule for SIO-DB paths added (`/home/gyasisutton/.claude/rules/tools/sio.md`)
- All hook writers + DSPy readers reference the same DB path constant (no string duplication)

---

## 9. Out-of-Scope Follow-ons (capture as separate tickets)

- New DSPy modules / signatures
- Multi-platform support (Cursor, Codex, Aider)
- Web UI for suggestion review
- LLM-judge for `recall_metric` instead of embedding similarity
- Pattern centroid_embedding revival as resumable clustering primitive
- Distributed mining (currently single-machine SQLite)

---

## 10. Speckit Constitution Notes

When `/speckit-workflow` runs against this PRD:
- **Spec phase**: produce one spec per Phase task (~28 specs)
- **Plan phase**: critical Phase 1 tasks must produce migration scripts AND rollback paths
- **Implement phase**: all Phase 1 changes need integration tests against a copy of the user's `~/.sio/sio.db`
- **Constitution gates**: any change to hook DB paths or `applier/writer.py` requires human review (no auto-merge)
- **Test environment**: use `/tmp/sio-test.db` clones; never run destructive tests against the live `~/.sio/sio.db`
