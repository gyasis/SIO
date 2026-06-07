# Feature Specification: SIO Search & Data-Sourcing Remediation

**Feature Branch**: `005-search-data-remediation`
**Created**: 2026-06-07
**Status**: Draft
**Input**: User question — "use sio to analyze itself and its commands… how is it doing in the search and the data side? Is it doing targeted data sourcing and targeted multi-hop searches? Does it invoke recency, or when it finds hits does it walk back/forward to the specific sessions to grab context?"

## Overview

This feature was born from a meta-audit: the operator asked SIO to evaluate *its own* search and data-sourcing behavior. We answered it two ways at once — a **capability audit** (read the search/data code and verify what it actually implements, file:line) and a **behavioral mining pass** (count how the disciplines are actually used across the last 21 days of session history). The two halves told a single, coherent story.

**The story.** SIO *has* the right disciplines designed in — recency, targeted sourcing, a real two-hop cascade, and session context-grabbing — but every one of them is either **opt-in (the tool won't do it unless the agent types the flag)** or **siloed in a command the agent rarely uses**, and three of the four carry a genuine implementation gap. The result is a self-reinforcing under-use loop: the agent reaches for plain `sio search` / `session-search` (which *cannot* multi-hop and can only dump-or-nothing for context), while the commands that *can* do the smart thing (`sio suggest`, `sio recall`) have their own holes (no recency window on `suggest`, ±1-turn-only context, a stubbed `recall --polish`). The capability is half-built; the behavior is light; and the two starve each other.

**The evidence (grounding for this spec lives in `research.md`):**

Capability audit (read-only, 34 files inspected, file:line cited):

| Dimension | Verdict | Anchor |
| --- | --- | --- |
| Targeted data sourcing / conservation | **PARTIAL** | `sio suggest` loads errors with `limit=0` (no row cap) on a 951 MB DB; no token budget, no dedup-before-load (`main.py:2139`, `queries.py:310`) |
| Targeted multi-hop search | **IMPLEMENTED but siloed** | Real 2-hop cascade exists *only* in `sio suggest` (`main.py:2208–2331`); `sio search` / `sio recall` are single-pass |
| Recency | **PARTIAL** | `sio search` defaults to `--recent 0` (no cutoff) and returns filesystem order, not time-sorted, unless `--skeleton`; `sio suggest` has **no `--since` at all** (`search/cli.py:888`, `main.py:1851–2001`) |
| Session walk-back / forward | **PARTIAL** | Context is hard-coded **±1 message** at mine time (`error_extractor.py:253`) and **±1 step** in recall (`recall.py:88`); `--session` is an all-or-nothing full dump; `recall --polish` emits a prompt string instead of calling Gemini (`main.py:1345`) |

Behavioral mining (last 21 days, 437 sessions, 808 real search invocations):

| Discipline | Signal | Rate | Read |
| --- | --- | --- | --- |
| Recency-first | `--recent` | ~50% | Used when typed; not reflexive. `--all` still appears ~7% |
| Targeted sourcing | `--files`-first | ~32% | A real habit |
| Multi-hop / refine | `--refine`/`--within`/`--strategy` | **~0.4% (3 / 808)** | Built but behaviorally dead |
| Session walk-back | `--context` + `--session` | **~3%** | Searches end at the match |

**The thesis of this feature**: close the four implementation gaps **and** flip the disciplines from opt-in to default, so the healthy behavior happens without the agent having to remember a flag. Then make SIO *measure its own search discipline* so the gap can never silently re-open. Per the SIO constitution's ECONOMY-FIRST principle (memory.md), the data-conservation guard (US4) is non-negotiable, not a nicety.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recency happens by default, not by flag (Priority: P1)

As the operator, when I run `sio search "<term>"` without thinking about flags, I want the freshest matches first and old sessions gated out by default, so that I never accidentally resume 6-week-old work as if it were current. Today `sio search` defaults to `--recent 0` (scans all files, no time window) and emits matches in filesystem/ripgrep order — recency only happens when I explicitly type `--recent N`, and the agent does so only ~50% of the time.

**Why this priority**: Highest leverage for the smallest change. The recency-first gate is already a hard rule in `memory-search.md`; making it the tool default makes the rule physically true instead of advisory, and lifts the recency rate toward 100% with zero behavior change required.

**Independent Test**: Run `sio search "<term>"` with no flags on a corpus spanning months. Verify (a) only files within the default window are scanned, and (b) results are ordered newest-first. Override with `--recent 0` to opt back into full-history. Verifiable without any other story.

**Acceptance Scenarios**:

1. **Given** a corpus with sessions from today and from 60 days ago, **When** the operator runs `sio search "<term>"` with no time flag, **Then** only sessions within the default window are returned and the newest match is first.
2. **Given** the default window returns zero hits, **When** the search completes, **Then** the tool emits a "0 results in last N days — widen with `--recent 0`" hint rather than silently returning empty.
3. **Given** an operator who genuinely wants full history, **When** they pass `--recent 0` (or `--all`), **Then** the full corpus is searched and the override is honored.
4. **Given** raw `jsonl`/`text` output mode, **When** results are returned, **Then** they carry a stable newest-first ordering (not filesystem order).

---

### User Story 2 - Walk into the hit session and read around it (Priority: P1)

As the operator, when a search returns a promising hit, I want to open *that* session at *that* point and read a few turns before and after, so that I get the context of how the moment unfolded instead of either one stray line or a 200 KB full-transcript dump. Today the only context options are the ripgrep `-C` raw-line flag, a hard-coded ±1-message window baked in at mine time, or `--session <uuid>` which dumps the entire transcript with no offset.

**Why this priority**: This is the "walk back/forward to the specific session to grab context" behavior the operator explicitly asked about — and it's the weakest leg in the code (no offset/N-turn API exists at all) and the rarest in practice (~3%).

**Independent Test**: Search for a term, take a hit's session+offset, request a ±N-turn window, and verify exactly the surrounding N turns (not one line, not the whole file) are returned with role/turn markers. Verifiable independently of Story 1.

**Acceptance Scenarios**:

1. **Given** a search hit with a known session and turn offset, **When** the operator requests a context window of N turns, **Then** the N turns before and after the hit are returned with turn boundaries and roles intact.
2. **Given** a hit near the start or end of a session, **When** the window would run past the transcript edge, **Then** the window clamps gracefully (no crash, no negative index).
3. **Given** an operator who wants the whole session, **When** they request the full dump, **Then** the existing full-transcript behavior is still available.
4. **Given** a struggle→fix moment in a session, **When** the operator walks forward from the struggle hit, **Then** the fix turns are reachable in the same call without re-searching.

---

### User Story 3 - Multi-hop is reachable from the search the agent actually uses (Priority: P2)

As the operator, when a first-pass search returns a noisy pile of hits, I want to narrow with a second hop (refine + re-cluster) **from `sio search` itself**, so I don't have to know that the only cascade lives inside `sio suggest`. Today the real two-hop machinery (`--grep`→`--refine`→`--strategy filter|recluster|hybrid`, `--within`/`--use-cache`) exists exclusively in the `suggest` pipeline; `sio search` and `sio recall` are single-pass, which is why multi-hop is used in ~0.4% of searches.

**Why this priority**: The capability is already built and correct — this is a reachability/ergonomics fix, not new search science. Surfacing it (or auto-suggesting Hop-2 when Hop-1 is noisy) is what converts a dead 0.4% into routine use.

**Independent Test**: Run a deliberately broad `sio search`, then narrow with a refine term in a second hop fed from the first hop's cached result (no re-query of the DB). Verify the narrowed set is a subset and the DB was not reloaded. Verifiable independently.

**Acceptance Scenarios**:

1. **Given** a broad first-hop result, **When** the operator applies a refine term, **Then** the result is the AND-narrowed subset and the operation reused the cached Hop-1 result rather than re-scanning.
2. **Given** a first-hop result exceeding a noise threshold (e.g. > N hits across > M files), **When** the search completes, **Then** the tool suggests a concrete Hop-2 refine command.
3. **Given** the three strategies, **When** the operator selects `filter` vs `recluster` vs `hybrid`, **Then** each produces its documented narrowing behavior.

---

### User Story 4 - Search/suggest cannot blow the compute budget (Priority: P2)

As the operator, when SIO loads from the 951 MB error DB, I want a bounded, time-windowed read so a single `sio suggest` can't pull the entire historical error table into memory. Today `sio suggest` calls `get_error_records(conn, limit=0, …)` (`main.py:2139`) — the `limit=0` sentinel means *no cap* — and the command exposes no `--since`, so every run loads all of history before filtering.

**Why this priority**: Direct application of the constitution's ECONOMY-FIRST principle (the 2026-04-20 $5K embedding incident). A bounded default protects against the same class of unbounded-load cost; it is mandatory, not optional.

**Independent Test**: Run `sio suggest` against a large DB and assert the rows loaded are bounded by a default window/cap, and that `--since` / a higher cap are honored when explicitly requested. Verifiable independently.

**Acceptance Scenarios**:

1. **Given** a DB with far more rows than the default cap, **When** `sio suggest` runs with no time flag, **Then** only rows within the default window/cap are loaded and the bound is logged.
2. **Given** an operator who needs full history, **When** they pass `--since 0` (or an explicit cap), **Then** the wider load is honored and the cost is surfaced.
3. **Given** the preview cache, **When** `--use-cache` reads it, **Then** the cache is size-bounded and stale-checked against `--cache-ttl`.

---

### User Story 5 - `recall --polish` does what it says (Priority: P3)

As the operator, when I run `sio recall "<task>" --polish`, I want an actual Gemini-polished runbook, not a prompt string printed to stdout. Today `--polish` emits the prompt it *would* send (`main.py:1345–1357`) and defers the real call to a skill, so the CLI flag silently no-ops.

**Why this priority**: Correctness of an advertised flag. Low blast radius but it's a stub masquerading as a feature (placeholder-code class).

**Independent Test**: Run `recall --polish` and verify either a real polished runbook is produced via the configured LLM (with the cost surfaced per the cost-control gate) or the flag is removed and documented as skill-only. Verifiable independently.

**Acceptance Scenarios**:

1. **Given** `--polish`, **When** recall runs, **Then** a polished runbook is returned via the configured model **and** the estimated cost is stated before the call.
2. **Given** no LLM configured, **When** `--polish` is requested, **Then** the tool fails loudly with a clear message (not a silent prompt-string dump).

---

### User Story 6 - SIO measures its own search discipline (Priority: P3)

As the operator, I want SIO to report how often its own searches are recency-gated, multi-hop, files-first, and context-walked, so the gap this feature closes can never silently re-open. Today nothing tracks search-discipline usage; the 808-invocation analysis in `research.md` had to be hand-run.

**Why this priority**: Closes the loop. A standing metric turns a one-off audit into a regression guard and feeds `sio velocity` / `sio briefing`.

**Independent Test**: After the feature lands, run the new discipline report and verify it emits per-discipline rates over a window from real invocations. Verifiable independently.

**Acceptance Scenarios**:

1. **Given** a window of session history, **When** the operator runs the search-discipline report, **Then** it emits recency-rate, multi-hop-rate, files-first-rate, and context-walk-rate over that window.
2. **Given** a discipline rate falls below a target after this feature ships, **When** `sio briefing` runs, **Then** it flags the regression.

### Edge Cases

- **Default window hides genuinely-old target work.** US1 must keep `--recent 0`/`--all` as a first-class, documented override and emit the widen-hint on zero results (AS-2) so recency-first never becomes recency-only.
- **Context window past transcript edges** (US2 AS-2) — clamp, never crash.
- **Preview cache staleness** (US4 AS-3) — `--use-cache` must honor `--cache-ttl` and warn on stale, not silently serve old data.
- **Cross-harness fan-out** (`--agent all`) interacts with the default recency window — the window must apply per-harness consistently.
- **`--polish` with cost gate** (US5 AS-1) — the cost-control rule requires model + estimate + confirmation before any paid call; the fix must not bypass it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `sio search` MUST default to a bounded recency window (default `--recent N`, N > 0) and order raw/text/jsonl output newest-first; `--recent 0` and `--all` MUST remain explicit full-history overrides. *(US1)*
- **FR-002**: On zero results within the default window, `sio search` MUST emit a "widen with `--recent 0`" hint rather than a bare empty result. *(US1)*
- **FR-003**: `sio search` MUST expose a context-window option that, given a session + hit offset, returns ±N **turns** (role-aware), distinct from ripgrep `-C` raw lines and from the full `--session` dump. *(US2)*
- **FR-004**: The context window MUST clamp at transcript boundaries and support walking forward from a struggle hit into the subsequent fix turns within a single call. *(US2)*
- **FR-005**: The two-hop cascade (`--refine` + `--strategy filter|recluster|hybrid`, reading a cached Hop-1 result via `--within`/`--use-cache`) MUST be reachable from `sio search`, not only from `sio suggest`. *(US3)*
- **FR-006**: When a first-hop `sio search` exceeds a configurable noise threshold, the tool MUST suggest a concrete Hop-2 refine command (log/`additionalContext`, non-blocking). *(US3)*
- **FR-007**: `sio suggest` MUST replace the unbounded `limit=0` error load with a bounded default (row cap and/or `--since` window) and MUST log the bound applied. *(US4)*
- **FR-008**: `sio suggest` MUST accept `--since` (and an explicit cap override) to widen the load deliberately, surfacing the wider read. *(US4)*
- **FR-009**: The preview cache (`~/.sio/previews/*.csv`) MUST be size-bounded and stale-checked against `--cache-ttl`. *(US4)*
- **FR-010**: `sio recall --polish` MUST either perform a real model-backed polish (with cost stated per the cost-control gate, model ≤ approved tier) or be removed and documented as skill-only; it MUST NOT silently emit a prompt string. *(US5)*
- **FR-011**: SIO MUST provide a search-discipline report (recency-rate, multi-hop-rate, files-first-rate, context-walk-rate) over a window, sourced from real invocation telemetry. *(US6)*
- **FR-012**: `sio briefing` MUST flag when a search-discipline rate regresses below target after this feature ships. *(US6)*
- **FR-013**: No change in this feature may bypass the cost-control confirmation gate for paid LLM calls (applies to FR-010). *(cross-cut)*
- **FR-014**: All new defaults MUST preserve existing callers — the hardwired Cascade-Memory-Protocol callers (`--files`, `--count`, `--context`, `--clean`, `--all`, `--specstory`, `--backups`) MUST behave unchanged except for the newest-first ordering and the recency default (both overridable). *(compat)*

### Key Entities *(include if feature involves data)*

- **Search invocation** — a single `sio search` / `session-search` run; carries the flags used. The unit of behavioral measurement (FR-011).
- **Hit** — a single match within a session: (session id/path, turn offset, matched line). Input to the context window (FR-003).
- **Context window** — N turns around a hit, role-aware; the US2 artifact.
- **Hop-1 result / preview cache** — the bounded error/match set persisted to `~/.sio/previews/*.csv`, reused by Hop-2 (FR-005, FR-009).
- **Discipline metric** — per-discipline usage rate over a window (FR-011), consumed by briefing/velocity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After US1 ships, the **recency rate** of `sio search` invocations rises from the audited ~50% toward ≥ 95% (because recency is the default), measured by the FR-011 report over a 14-day window.
- **SC-002**: After US3 ships, the **multi-hop rate** rises from the audited ~0.4% (3 / 808) to a materially higher floor on noisy searches (target: > 50% of searches that exceed the noise threshold receive or act on a Hop-2 suggestion).
- **SC-003**: After US2 ships, the **context-walk rate** rises from the audited ~3%, and a context window returns *exactly* ±N turns (verified: not 1 line, not the full transcript).
- **SC-004**: After US4 ships, a default `sio suggest` run loads a **bounded** row set (no `limit=0` full-table load); the applied bound is present in the run log.
- **SC-005**: `sio recall --polish` produces a real polished runbook (or the flag is gone); no code path prints a prompt string in lieu of the advertised behavior.
- **SC-006**: The search-discipline report exists and is wired into `sio briefing`; a deliberately-induced regression is flagged.
- **SC-007**: Zero regression for existing Cascade-Memory-Protocol callers — the `/memory-search`, `/done-before`, and recency-first protocol flows return equivalent results (modulo newest-first ordering) on a fixture corpus.

## Assumptions

- The 951 MB `~/.sio/sio.db` and the per-platform `behavior_invocations.db` remain the authoritative stores; this feature changes *how much* and *in what order* is read, not the schema's home.
- `sio search` is the absorbed `session-search` (cross-harness); the Cascade-Memory-Protocol rules in `memory-search.md` are the behavioral contract the defaults must satisfy.
- The multi-hop cascade in `sio suggest` is correct as-built (the audit verdict was IMPLEMENTED); US3 is reachability, not a rewrite.
- "Turn" boundaries are derivable from the JSONL record structure (role + message) for the context window.

## Dependencies

- Cost-control gate (`cost-control.md`) for FR-010/FR-013.
- ECONOMY-FIRST principle (`memory.md`) for US4.
- Recency-first gate + Cascade Memory Protocol (`memory-search.md`) — the contract US1 makes physical.
- DSPy clustering (`cluster_errors()`) already used by the `suggest` cascade — reused by US3.

## Out of Scope

- Re-architecting the storage layer (no new DB, no migration of the 951 MB store).
- Semantic/vector search over sessions (this feature is recency + targeted + cascade + context-walk over the existing keyword/cluster engines).
- Changing the DSPy optimizer surface (covered by spec 003/004).
- Cross-harness *capture* changes — only *search* over already-captured history.

## Changelog

- 2026-06-07 — Draft created from the self-audit (capability + behavioral). Raw evidence in `research.md`.
