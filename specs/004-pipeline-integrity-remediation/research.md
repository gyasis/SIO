# Phase 0 Research — SIO Pipeline Integrity & Training-Data Remediation

**Branch**: `004-pipeline-integrity-remediation`
**Date**: 2026-04-20
**Purpose**: Resolve every unknown / design choice flagged in plan.md and spec.md before Phase 1 design.

---

## R-1. Per-Platform DB vs. Consolidation (Constitution V reconciliation)

**Context**: PRD §7 Open Q1 asks whether `behavior_invocations` should stay in per-platform DBs (`~/.sio/<platform>/behavior_invocations.db`) or consolidate into `~/.sio/sio.db`. Spec Assumption picks "consolidate (simplest)". Constitution Principle V (Shared Core, Separate Data) says "Each platform adapter MUST maintain its own separate `behavior_invocations.db`. Cross-platform data MUST NOT be mixed in a single database."

**Decision**: **Per-platform writes preserved; read-side sync into `~/.sio/sio.db`.**
- Hook writers continue to write to `~/.sio/<platform>/behavior_invocations.db`.
- A new module `src/sio/core/db/sync.py` exposes a `sync_behavior_invocations()` function that, on every `sio` CLI invocation (lazy) or on a short cron cadence (explicit), uses SQLite `ATTACH DATABASE` and `INSERT OR IGNORE INTO main.behavior_invocations SELECT * FROM <platform>.behavior_invocations` to mirror into `~/.sio/sio.db`.
- The `platform` column in `sio.db.behavior_invocations` is the discriminator; each row is tagged at mirror time from its source DB.
- Readers (optimizer, gold_standards, feedback, labeler, DSPy metric evaluator) continue to query `~/.sio/sio.db` as before.
- One-time backfill (FR-002) uses the same ATTACH pattern to copy 38,091 legacy rows.

**Rationale**:
1. Honors Constitution Principle V (supreme per §Governance): platform isolation preserved for future Cursor/Codex/Aider adapters (spec Out-of-Scope today, but V exists for when they arrive).
2. Honors FR-001 ("single canonical data store that the training, optimization, and suggestion pipelines read from") by giving readers a single `~/.sio/sio.db` target.
3. Hook writes stay low-latency (writing to local per-platform DB is independent of `sio.db` lock contention).
4. Sync is idempotent (INSERT OR IGNORE on PK `(platform, session_id, timestamp, tool)` or equivalent) — safe to re-run.

**Alternatives considered**:
- *Consolidate writes directly* (original spec assumption): violates Principle V; rejected.
- *Union-view via ATTACH at read time only* (no materialized mirror): makes every read depend on the attached DB's presence + busy state; brittle under concurrent hook writes; rejected.
- *Trigger-based real-time mirror*: SQLite triggers across databases require ATTACH at trigger-definition time, which is fragile across sessions; rejected in favor of explicit sync.

**Spec impact**: Spec Assumption updated in plan (tech-context bullet). FR-001 wording still holds — readers see a single canonical store, just reached via sync rather than direct write.

**Action items for Phase 1**:
- `contracts/storage-sync.md` documents the sync call contract, idempotency key, and cadence.
- Data model: `sio.db.behavior_invocations` keeps its existing schema; add composite UNIQUE `(platform, session_id, timestamp, tool_name)` if not already present.

---

## R-2. DSPy GEPA — Reflection LM Choice & Default Wiring

**Context**: FR-037 requires GEPA as the default optimizer with a separate, stronger `reflection_lm`. Spec Assumption says the choice is overridable via the LM factory (FR-041).

**Decision**: Default wiring via `sio.dspy_config.get_reflection_lm()`:
- **Task LM** (cheap, executes module forward): `openai/gpt-4o-mini` or equivalent via the LM factory's `get_task_lm()`.
- **Reflection LM** (strong, critiques prompt candidates): `openai/gpt-5` (per the DSPy tutorial precedent) OR `anthropic/claude-opus-4-7` if the operator has Anthropic credentials configured. Temperature 1.0, `max_tokens=32000`.
- The operator overrides via `SIO_TASK_LM` / `SIO_REFLECTION_LM` env vars consumed by the factory.
- `num_threads=8`, `reflection_minibatch_size=3`, `max_full_evals=2`, `track_stats=True` as sane defaults (per DSPy tutorial `gepa_trusted_monitor`).

**Rationale**: GEPA's quality lift comes from having the reflection LM be materially stronger than the task LM. A weaker reflection LM delivers worse prompts than MIPROv2. Defaulting to a known-strong reflection model while making it overridable gives operators the quality lift without hard-coding an API key.

**Alternatives considered**:
- *Single LM for both*: defeats GEPA's design; rejected.
- *Always use the operator's strongest model*: may exhaust budget; overridable but not defaulted-to by guess.

**Spec impact**: Covered by FR-037 + Assumptions. No spec change needed.

**Action items for Phase 1**:
- `contracts/optimizer-selection.md` specifies `--optimizer gepa|mipro|bootstrap` CLI flag and the env-var overrides.
- `src/sio/core/dspy/lm_factory.py` test-covered per FR-041 / SC-022.

---

## R-3. Autoresearch Scheduling — systemd-user vs. Claude Code CronCreate

**Context**: FR-006 requires the autoresearch loop to fire on a schedule without an interactive session. PRD §7 Open Q2 flags the choice between host-level systemd-user and Claude Code's `CronCreate` MCP trigger.

**Decision**: **Claude Code `CronCreate` (primary), systemd-user (documented fallback)**.
- Use `CronCreate` with a daily cadence (e.g., `0 4 * * *` — 4 AM local) for the default install.
- Ship a `scripts/install_autoresearch_systemd.sh` that writes a user systemd unit for operators who want OS-level independence from Claude Code.
- Either scheduler invokes `scripts/autoresearch_cron.py`, which is a thin wrapper over `sio autoresearch --run-once`.

**Rationale**:
- Lower-friction install for the current single-operator use case.
- Keeps the scheduling inside the Claude Code ecosystem (aligns with Principle I: Platform-Native First).
- systemd-user remains available for operators whose workflow diverges from Claude Code sessions.

**Alternatives considered**:
- *Foreground-only (status quo)*: violates FR-006; rejected.
- *Long-running daemon*: adds complexity beyond YAGNI (Principle VII); rejected.

**Spec impact**: Assumption already notes "in-ecosystem scheduler preferred" — consistent.

**Action items for Phase 1**:
- `contracts/cli-commands.md` documents `sio autoresearch --run-once` and `sio autoresearch --install-schedule {cron|systemd}`.

---

## R-4. Atomic File Write on WSL2 (FR-004)

**Context**: Spec FR-004 requires atomic write + backup. CLAUDE.md documents a confirmed `.env` wipe on WSL2 caused by `sed -i`'s temp-file-rename race with Windows Defender / VS Code file watchers. Any naïve temp-rename could reproduce this.

**Decision**: **Read-verify-backup-write pattern** in `src/sio/core/applier/writer.py`:

```python
def atomic_write(target: Path, new_content: str) -> Path:
    # 1. Read current content (also creates an in-memory "pre-state")
    prev = target.read_text(encoding="utf-8") if target.exists() else None

    # 2. Pre-write backup with timestamp
    if prev is not None:
        backup_dir = BACKUP_ROOT / target.relative_to(BACKUP_ANCHOR)
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir.with_suffix(f".{_ts()}.bak")
        backup_path.write_text(prev, encoding="utf-8")
        os.fsync(os.open(backup_path, os.O_RDONLY))

    # 3. Write to same-dir tmp, fsync, rename
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(new_content, encoding="utf-8")
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, target)  # atomic on POSIX + NTFS

    # 4. Post-write verification: re-read, compare length
    after = target.read_text(encoding="utf-8")
    if len(after) < len(new_content) * 0.9:
        # File watcher race corrupted output — restore from backup
        if prev is not None:
            target.write_text(prev, encoding="utf-8")
        raise WriteIntegrityError(f"{target}: post-write size regression")

    # 5. Retention: prune backups beyond last 10 per file
    _prune_backups(backup_dir.parent, keep=10)
    return backup_path
```

**Rationale**:
- Same-dir tmp ensures `os.replace` stays atomic (cross-filesystem rename is not atomic).
- Post-write size check catches the documented WSL2 Defender race before releasing to caller.
- Explicit `os.fsync` avoids SQLite/filesystem lag masking a lost write.
- CLAUDE.md's "NEVER sed -i" rule is enforced by using only `write_text` + `os.replace`.

**Alternatives considered**:
- *Python stdlib `tempfile.NamedTemporaryFile(dir=...)` + `shutil.move`*: `shutil.move` is not atomic on cross-device; overhead without benefit; rejected.
- *Just `os.replace` without size verification*: does not detect file-watcher races; rejected.

**Spec impact**: FR-004 satisfied. Post-write verification becomes an integration-test requirement (`tests/integration/test_apply_safety.py`).

---

## R-5. Stable Pattern-ID Slug Algorithm (FR-014)

**Context**: Current `clustering/pattern_clusterer.py:163-184` derives slugs from the first few member errors, so row-insertion order changes slugs. Must be deterministic; ground-truth foreign keys depend on stability.

**Decision**: **Centroid-hash slug** — deterministic given the set of errors:
1. Compute cluster centroid (normalized mean of member fastembed vectors).
2. Round centroid to 4 decimal places to absorb floating-point jitter.
3. SHA-256 hash the rounded centroid byte-representation.
4. Take first 10 hex chars as the slug suffix; prefix with a human-readable tag derived from the cluster's top-1 error-type term (e.g., `tool_failure_a8c21e9f71`).
5. Ground-truth rows keyed on old slugs are remapped once using Jaccard overlap of member error sets (≥ 0.5 overlap → same pattern, transfer FK).

**Rationale**:
- Centroid is a set-function (order-independent) on the member errors.
- Rounding absorbs numerical noise from floating-point non-associativity in sum reductions.
- SHA-256 prefix is collision-resistant for the projected scale (<10k patterns).
- Jaccard remap gives a one-time migration path without forcing the operator to re-label.

**Alternatives considered**:
- *UUID4 slugs*: trivially stable per pattern but forfeits the ability to re-derive if DB is rebuilt; rejected.
- *Error-list hash (sorted)*: stable but breaks when one error is added; rejected.
- *Manual slug field*: requires human curation per pattern; violates YAGNI.

**Action items for Phase 1**:
- Data model: document `patterns.pattern_id` format and the migration step.
- Unit test: `tests/unit/clustering/test_deterministic_slugs.py` covers re-ordering invariance.

---

## R-6. Byte-Offset-Resume Dedup (FR-010)

**Context**: `mining/pipeline.py:237-247` currently hashes each file in full on every mine. JSONL session files grow continuously; hash-changes on every append.

**Decision**: **Per-file offset state in `processed_sessions`**:
- Add columns `last_offset INTEGER NOT NULL DEFAULT 0`, `last_mtime REAL`, `last_mined_at TEXT` to `processed_sessions`.
- Mining algorithm: `open(path, 'rb'); f.seek(last_offset); for line in f: parse(line); f.tell() → new_offset`.
- Update `last_offset` and `last_mtime` after a successful batch commit.
- If `mtime < last_mtime - tolerance` → file was truncated/rotated → reset `last_offset=0`.

**Rationale**:
- Linear cost in *new* bytes, not total file size.
- Detects truncation/rotation via mtime sanity check.
- Paired with FR-009 streaming parse, satisfies SC-007 (500 MB RSS on 100 MB file).

**Alternatives considered**:
- *Line-count dedup*: cheap but loses position on line-insertion (shouldn't happen with append-only JSONL but fragile);
- *Content-hash of tail block*: cheap-ish but adds state to verify; rejected in favor of offset+mtime.

**Action items for Phase 1**:
- Data model: document `processed_sessions` schema delta.
- Unit test: `tests/unit/mining/test_byte_offset.py` covers append + truncation scenarios.

---

## R-7. Timezone Normalization (FR-030)

**Context**: Mixed input formats — SpecStory `Z` suffix, JSONL wire format, localized filenames, WSL2 `TZ=America/New_York`. Spec edge case "Timezone drift" and FR-030 mandate tz-aware storage.

**Decision**: **Normalize-on-write to UTC ISO-8601 with explicit offset**:
- New helper `src/sio/core/util/time.py::to_utc_iso(s: str) -> str`:
  - If string ends with `Z` → parse as UTC, emit as `YYYY-MM-DDTHH:MM:SS+00:00`.
  - If string has a numeric offset → parse, convert to UTC, emit with `+00:00`.
  - If naive (no tz info) → **assume local** via `datetime.fromisoformat(s).astimezone(tz=None).astimezone(UTC)` AND log a `tz-naive-input` counter (so we can audit sources).
  - Return canonical string.
- All existing `datetime.fromisoformat(...)` sites (`clustering/ranker.py:79-80`, `clustering/grader.py:88-89`, `mining/pipeline.py:432`) call `to_utc_iso()` on input before compare.
- DB schema: all timestamp columns documented as "UTC ISO-8601 with +00:00"; not enforced via CHECK constraint (SQLite) but enforced via test fixture.

**Rationale**:
- "Assume local" is less wrong than "assume UTC" for naive strings produced on the operator's machine. The audit counter lets us track and eventually reject naive inputs.
- All comparisons happen in UTC so `TZ=America/New_York` no longer drifts grade thresholds.

**Alternatives considered**:
- *Store naive + convert on read*: requires every reader to know the source tz; fragile; rejected.
- *Store Unix epoch ints*: simpler math but loses human-readability for DB inspection; rejected for a CLI debugging tool where readability matters.

**Action items for Phase 1**:
- `src/sio/core/util/time.py` + unit test `tests/unit/util/test_time.py` with `TZ=America/New_York` simulation.

---

## R-8. SQLite Concurrency — busy_timeout + WAL

**Context**: Current `busy_timeout=1000ms` collides with concurrent hook + mine writes. FR-012 mandates "long enough to absorb expected concurrent mine + hook contention".

**Decision**: **busy_timeout = 30_000 ms (30 s), WAL mode enforced at connect time**:
- `core/db/schema.py` applies `PRAGMA busy_timeout=30000; PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA wal_autocheckpoint=1000;` on every new connection.
- Every `sqlite3.connect(...)` wraps through a factory that applies these PRAGMAs.

**Rationale**:
- WAL allows concurrent reads with one writer without blocking.
- 30 s covers a worst-case mine transaction completing while a hook fires.
- `synchronous=NORMAL` with WAL is crash-safe on typical Linux (per SQLite docs).

**Alternatives considered**:
- *busy_timeout=0, retry loop*: reinvents what busy_timeout already does; rejected.
- *busy_timeout=120_000*: unnecessarily masks genuine deadlocks; rejected.

**Action items for Phase 1**:
- Centralize the connection factory at `src/sio/core/db/connect.py` (NEW).

---

## R-9. Centroid Embedding Persistence (FR-032 / H11)

**Context**: `patterns.centroid_embedding` BLOB column exists but is hardcoded to `None` at `cli/main.py:1409`. Every `sio suggest` run recomputes ONNX embeddings over 45k errors. FR-032 + SC-011 require reuse for unchanged patterns.

**Decision**: **Store fastembed ONNX vector as BLOB with dimension + model-name headers**:
- On cluster creation/update: compute centroid (normalized mean of member vectors), pack as `(dim: uint32_le, model_hash: 8 bytes, vector: float32[dim])` → SQLite BLOB.
- On next `sio suggest`: load BLOB, verify `model_hash` matches current fastembed model, verify `dim` matches expected, skip recompute if valid.
- If model_hash mismatches (e.g., fastembed upgraded) → invalidate all centroids in one pass, log, recompute.
- `model_hash` = `sha256(onnx_model_filename + model_version)[:8]`.

**Rationale**:
- Model-versioned header protects against silent quality regression from fastembed upgrades.
- BLOB avoids serialization overhead of TEXT-encoded JSON vectors.
- Matches `dspy.save/load` pattern of artifact-with-metadata.

**Alternatives considered**:
- *JSON vector storage*: 3-4× space overhead; parse cost; rejected.
- *Always recompute*: current bug; rejected.

**Action items for Phase 1**:
- Data model: `patterns.centroid_embedding` format documented.
- Unit test: `tests/unit/clustering/test_centroid_reuse.py` covers hit, miss, model-upgrade invalidation.

---

## R-10. Schema Version Marker (FR-017)

**Context**: `core/db/schema.py:383-494` uses `IF NOT EXISTS` ALTER pattern with no version table. FR-017 requires a marker and refusal-to-start on partial migration.

**Decision**: **Lightweight version table**:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER PRIMARY KEY,
    applied_at   TEXT NOT NULL,                  -- UTC ISO-8601
    status       TEXT NOT NULL DEFAULT 'applied', -- 'applying' | 'applied' | 'failed'
    description  TEXT
);
```

- Each migration writes `(N, now(), 'applying', ...)` at the start and updates to `'applied'` on success; leaves `'applying'` on crash.
- On startup, `sio` checks `MAX(version)` and refuses to start if any row has `status='applying'` (crashed mid-migration); operator runs `sio db repair` to roll forward or abort.
- The IF-NOT-EXISTS ALTER pattern is retained (Spec Out-of-Scope: "Full migration framework") — the version table is a safety marker, not a framework.

**Rationale**:
- Detects partial-migration state without importing Alembic.
- Minimal schema delta.

**Action items for Phase 1**:
- Data model: schema_version columns documented.
- Unit test: partial-migration detection + `sio db repair` behavior.

---

## R-11. `dspy.Assert` API in 3.1.3 (FR-038)

**Context**: FR-038 requires runtime `dspy.Assert` / `dspy.Suggest`. Spec Assumption flagged this as "reconfirm at implementation time" because DSPy 3.x is migrating assertion logic into adapters.

**Decision**: **Use `dspy.Assert(predicate, msg, backoff_factor=1)` inside `forward()`** — confirmed present in 3.1.3 via context7 reference (`research/dspy-3.x-reference.md` §8):
- Place asserts after any structured output parse; message describes the violated constraint.
- On assertion failure, DSPy re-runs the predictor with the assertion message as additional context (automatic backtracking, capped by `backoff_factor` to prevent infinite loops).
- `dspy.Suggest(...)` is the non-fatal variant — logs and prefers backtracking but does not raise.

**Rationale**:
- Native framework path, no re-invention.
- Already in `research/dspy-3.x-reference.md` as canonical reference; plan cites that file.

**Action items for Phase 1**:
- `src/sio/core/dspy/assertions.py` exposes typed helpers `assert_valid_rule_format(pred)`, `assert_no_phi(pred)`, etc.
- Unit test: `tests/unit/dspy/test_assertions.py` verifies backtrack count is logged.

---

## R-12. Native Function Calling Adapters (FR-040)

**Context**: FR-040 requires `ChatAdapter(use_native_function_calling=True)` when the provider supports it. Spec Assumption says DSPy-managed orchestration is reserved for providers that lack native support.

**Decision**: **Adapter-selection helper in LM factory**:

```python
def get_adapter_for(lm: dspy.LM) -> dspy.Adapter:
    model_id = lm.model  # e.g., "openai/gpt-4o-mini"
    provider = model_id.split("/", 1)[0]
    if provider in ("openai", "anthropic", "azure"):
        return dspy.ChatAdapter(use_native_function_calling=True)
    if provider == "ollama":
        return dspy.JSONAdapter(use_native_function_calling=False)  # Ollama's function-calling is flaky
    return dspy.ChatAdapter(use_native_function_calling=False)      # safe fallback
```

- Factory emits the adapter alongside the LM; caller does `dspy.configure(lm=lm, adapter=adapter)`.
- Override via `SIO_FORCE_ADAPTER={chat|json}` + `SIO_FORCE_NATIVE_FC={0|1}` env vars.

**Rationale**:
- Provider-aware default captures FR-040 intent.
- Ollama's inconsistent tool-calling (as of DSPy 3.1.3) is handled explicitly.

**Action items for Phase 1**:
- `contracts/dspy-module-api.md` documents adapter auto-selection.

---

## R-13. Subagent Linkage (FR-011)

**Context**: `mining/pipeline.py:302` treats subagent JSONLs as standalone sessions, bypassing the sidechain filter. FR-011 requires linkage to parent and exclusion from top-level error mining unless explicitly requested.

**Decision**: **Path-based parent detection + FK column**:
- File path pattern: `.../subagents/<parent_session_id>/<subagent_id>.jsonl` is the convention; alternative pattern `.../<parent_session_id>__subagent_<subagent_id>.jsonl` also recognized.
- On mine, extract `parent_session_id` via regex; if matched, insert rows with `is_subagent=1`, `parent_session_id=<parent>`.
- `error_records` and `flow_events` gain a `parent_session_id TEXT NULL` column (nullable; non-null for subagents).
- Default `sio mine` query excludes `is_subagent=1` from top-level aggregates; `sio mine --include-subagents` opts in.

**Rationale**:
- Preserves provenance for later analysis (who spawned whom).
- Keeps top-level counts honest (no double-counting).

**Action items for Phase 1**:
- Data model: `parent_session_id` column added; `processed_sessions` gains `is_subagent` flag.
- Unit test: `tests/unit/mining/test_subagent_link.py` covers both path patterns.

---

## R-14. Path-Traversal Validation (FR-019)

**Context**: `applier/writer.py:32` allowlist grants blanket write access via `Path.cwd()`. FR-019 forbids this.

**Decision**: **Explicit allowlist rooted at `$HOME/.claude/` plus operator-configured extras**:

```python
ALLOWLIST_ROOTS = [
    Path.home() / ".claude",                    # always
    *[Path(p) for p in os.environ.get("SIO_APPLY_EXTRA_ROOTS", "").split(":") if p],
]

def _validate_target_path(target: Path) -> None:
    resolved = target.expanduser().resolve(strict=False)
    for root in ALLOWLIST_ROOTS:
        if resolved.is_relative_to(root.resolve()):
            return
    raise UnauthorizedApplyTarget(f"{resolved} is outside {ALLOWLIST_ROOTS}")
```

- `resolve()` collapses `..` so path traversal is caught.
- `is_relative_to()` is Python 3.9+.
- `Path.cwd()` is NOT in the allowlist (per FR-019).

**Rationale**: Simple, auditable, covers traversal via symlink by using `resolve()`.

**Action items for Phase 1**:
- Unit test: `tests/unit/applier/test_allowlist.py` with `../../etc/hosts`, symlink traversal, valid paths.

---

## R-15. Testing Strategy — TDD Task Ordering (Principle IV)

**Context**: Constitution IV is NON-NEGOTIABLE — tests before implementation.

**Decision**: `/speckit.tasks` will produce task pairs `(write_test, implement)` where each implementation task is BLOCKED by its test task. Concretely:
- Per User Story, write `tests/integration/test_<story_slug>.py` first (fails).
- Per FR, write `tests/unit/<module>/test_<fr_slug>.py` first (fails).
- Only after user approves test design (Constitution IV step 2) does the implementation task become runnable.
- Crash-injection test for FR-004 uses `pytest` + `os.kill(os.getpid(), signal.SIGKILL)` in a subprocess with a forked writer.

**Rationale**: Directly from Constitution IV.

**Action items for Phase 1**:
- `quickstart.md` documents the test scaffolding (pytest config, conftest fixtures for `/tmp/sio-test.db` cloning, DSPy LM mocks, fastembed stubs).

---

## Consolidated Decisions Table

| # | Topic | Decision | Primary Source |
|---|---|---|---|
| R-1 | Per-platform DB vs consolidate | Per-platform writes, read-side sync into sio.db | Constitution V reconciliation |
| R-2 | GEPA reflection LM | `openai/gpt-5` default, overridable via env | DSPy 3.x reference |
| R-3 | Autoresearch schedule | Claude Code `CronCreate` primary, systemd-user fallback | Principle I, PRD §7 Q2 |
| R-4 | Atomic file write | Read-verify-backup-write + size check | CLAUDE.md WSL2 rule |
| R-5 | Stable pattern slugs | Centroid-hash + Jaccard remap | Adversarial finding H5 |
| R-6 | Byte-offset resume | `processed_sessions.last_offset + mtime` | PRD C6 / FR-010 |
| R-7 | Timezone normalization | UTC ISO-8601 + `+00:00` on write | PRD H12 / FR-030 |
| R-8 | SQLite concurrency | 30s busy_timeout + WAL + NORMAL sync | PRD H4 |
| R-9 | Centroid persistence | BLOB with dim + model_hash header | PRD H11 / FR-032 |
| R-10 | Schema version | Marker table + refuse-to-start on `applying` | FR-017 |
| R-11 | dspy.Assert API | `dspy.Assert(pred, msg)` + backtrack logging | DSPy 3.x reference §8 |
| R-12 | Native function calling | Provider-aware adapter factory | FR-040 |
| R-13 | Subagent linkage | Path-regex parent_session_id + `is_subagent` | PRD C7 / FR-011 |
| R-14 | Path-traversal validation | Resolve + is_relative_to against fixed allowlist | FR-019 |
| R-15 | TDD task ordering | Test-first pairs enforced by `/speckit.tasks` | Constitution IV |

**All NEEDS CLARIFICATION items resolved.** Ready for Phase 1.
