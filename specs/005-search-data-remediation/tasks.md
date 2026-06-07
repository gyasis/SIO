# Tasks: SIO Search & Data-Sourcing Remediation

**Branch**: `005-search-data-remediation`
**Input**: `specs/005-search-data-remediation/{spec,research,plan}.md`
**Tests**: REQUIRED (Constitution IV "Test-First" is NON-NEGOTIABLE). Every implementation task is paired with a preceding test-write task.
**Orchestration**: dev-kid waves, `wave_size: 10` (see `dev-kid.yml`). `[P]` = parallelizable (file-disjoint, no incomplete deps).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup & Foundational

- [ ] T001 Confirm toolchain + test harness loads (`pytest -q` green on current tree); record baseline.
- [ ] T002 [P] Build shared fixtures in `tests/conftest.py`: `fake_session_corpus` (JSONL files spanning today, 8d, 45d), `fake_error_db` (rows across a date range), `freeze_utc_now`, `tmp_previews_dir`.
- [ ] T003 [P] Capture the **behavioral baseline** from `research.md` §B into `specs/005-search-data-remediation/BASELINE.md` (recency ~50%, multi-hop ~0.4%, walk-back ~3%) so SC-001..003 have a before-number.

**Checkpoint**: harness green, fixtures + baseline ready.

---

## Phase 2: US1 — Recency by default (P1)  [Wave A]

- [ ] T010 [US1] Test: `sio search "<term>"` with no time flag scans only files within the default window and returns newest-first (assert order + window) — `tests/unit/search/test_recency_default.py`.
- [ ] T011 [US1] Test: zero-results-in-window emits the "widen with `--recent 0`" hint (FR-002).
- [ ] T012 [US1] Test (compat, SC-007): `--recent 0` and `--all` still search full history; CMP callers (`--files`/`--count`) unchanged except ordering.
- [ ] T013 [US1] Implement default recency window + newest-first ordering for raw/text/jsonl output in `src/sio/search/cli.py` (default `--recent N`, N>0; keep `0`/`--all` overrides). (FR-001)
- [ ] T014 [US1] Implement zero-result widen-hint in `src/sio/search/cli.py`. (FR-002)
- [ ] T015 [US1] Wire/adjust flags + help text in `src/sio/cli/search.py`; document the new default.

**Checkpoint (US1 independently testable)**: default search is recency-gated + newest-first; overrides + hint verified.

---

## Phase 3: US2 — Walk into the hit session (P1)  [Wave B]

- [ ] T020 [US2] Test: given session + hit offset, a ±N-turn context window returns exactly N turns each side, role-aware (not 1 line, not full dump) — `tests/unit/search/test_context_window.py`.
- [ ] T021 [US2] Test: window clamps at transcript start/end (no negative index, no overrun) (AS-2).
- [ ] T022 [US2] Test: forward-walk from a struggle hit reaches the subsequent fix turns in one call (AS-4) — `tests/unit/search/test_context_window.py`.
- [ ] T023 [US2] Implement turn-boundary parsing + `±N turns around (session, offset)` API in `src/sio/search/cli.py` (FR-003), distinct from rg `-C` and from full `--session` dump.
- [ ] T024 [US2] Implement boundary clamping (FR-004) + forward-walk in `src/sio/mining/recall.py`/search as appropriate.
- [ ] T025 [US2] Expose the context-window flag in `src/sio/cli/search.py` (e.g. `--around N` or `--context-turns N`); keep full `--session` dump available.

**Checkpoint (US2)**: a hit can be expanded to ±N turns; full dump preserved.

---

## Phase 4: US3 — Multi-hop reachable from search (P2)  [Wave B, parallel with US2 — disjoint files where possible]

- [ ] T030 [US3] Test: a refine on a cached Hop-1 result yields the AND-narrowed subset **without re-querying** the DB — `tests/unit/search/test_cascade_reach.py`.
- [ ] T031 [US3] Test: first-hop result over the noise threshold produces a concrete Hop-2 suggestion (FR-006).
- [ ] T032 [US3] Test: `filter|recluster|hybrid` each narrow per their documented behavior (reuse `suggest` cascade semantics).
- [ ] T033 [US3] Surface `--refine`/`--strategy`/`--within`/`--use-cache` on `sio search`, delegating to the existing audited cascade (`main.py:2208–2331` logic / `cluster_errors()`), not a reimplementation. (FR-005)
- [ ] T034 [US3] Implement noise-threshold Hop-2 suggestion (non-blocking log/hint) in `src/sio/search/cli.py`. (FR-006)

**Checkpoint (US3)**: multi-hop usable from the command the agent actually runs.

---

## Phase 5: US4 — Economy guard on the DB load (P2)  [Wave C]

- [ ] T040 [US4] Test: `sio suggest` with no time flag loads a **bounded** row set (assert ≤ default cap / within default window) and logs the bound — `tests/unit/suggest/test_bounded_load.py`.
- [ ] T041 [US4] Test: `--since` / explicit cap override widens the load and surfaces it (AS-2).
- [ ] T042 [US4] Test: preview cache is size-bounded + stale-checked against `--cache-ttl` (AS-3).
- [ ] T043 [US4] Replace `get_error_records(conn, limit=0, …)` at `main.py:2139` with a bounded default (row cap and/or `--since` window) + bound logging. (FR-007)
- [ ] T044 [US4] Add `--since` (+ explicit cap override) to `sio suggest` in `src/sio/cli/main.py`. (FR-008)
- [ ] T045 [US4] Enforce preview-cache size bound + TTL check in the `--use-cache` path. (FR-009)

**Checkpoint (US4)**: no `limit=0` full-table load; bound is visible.

---

## Phase 6: US5 — `recall --polish` is real or gone (P3)  [Wave C, parallel]

- [ ] T050 [US5] Test: `recall --polish` returns a real polished runbook via the configured model **and** states cost before the call; with no LLM configured it fails loudly (not a prompt-string dump) — `tests/unit/recall/test_polish.py`.
- [ ] T051 [US5] Implement the real polish call (cost-gated per cost-control.md) OR remove `--polish` and document skill-only; eliminate the prompt-string emit at `main.py:1345–1357`. (FR-010/FR-013)

**Checkpoint (US5)**: no advertised-but-stubbed flag remains.

---

## Phase 7: US6 — SIO measures its own search discipline (P3)  [Wave D]

- [ ] T060 [US6] Test: discipline report emits recency-rate, multi-hop-rate, files-first-rate, context-walk-rate over a window from invocation telemetry — `tests/unit/reporting/test_search_discipline.py`.
- [ ] T061 [US6] Test: a sub-target rate is flagged in `sio briefing` (AS-2).
- [ ] T062 [US6] Implement `src/sio/reporting/search_discipline.py` (window → per-discipline rates) + a `sio` subcommand to surface it. (FR-011)
- [ ] T063 [US6] Wire the regression flag into `sio briefing` in `src/sio/cli/main.py`. (FR-012)

**Checkpoint (US6)**: the gap is now self-measured.

---

## Phase 8: Polish & Verification

- [ ] T070 Integration (SC-007): replay the `/memory-search` + recency-first protocol flows against the fixture corpus; assert equivalent results modulo newest-first ordering.
- [ ] T071 Update `CHANGELOG.md` + `sio search`/`sio suggest`/`sio recall` help text + any rule docs referencing the old defaults.
- [ ] T072 Re-run the `research.md` §B reproduce block after rollout (or on the fixture) to confirm SC-001..003 deltas vs `BASELINE.md`.
- [ ] T073 Adversarial pass: spawn `adversarial-bug-hunter` (targeted: default-recency regressions on CMP callers + boundary clamping) per the user's standing two-hunter rule.

---

## Dependency / Wave Map (for dev-kid `orchestrate`)

- **Wave A**: US1 (T010–T015) — foundational default change, gates the no-regression contract.
- **Wave B**: US2 (T020–T025) ∥ US3 (T030–T034) — file-disjoint where possible.
- **Wave C**: US4 (T040–T045) ∥ US5 (T050–T051).
- **Wave D**: US6 (T060–T063), then Phase 8 polish (T070–T073).

Priorities: P1 (US1, US2) first and independently shippable; P2 (US3, US4) next; P3 (US5, US6) last. Each user story is independently testable per its checkpoint.
