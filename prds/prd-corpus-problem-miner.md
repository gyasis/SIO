# PRD — SIO Corpus-of-Problems Miner (mistake→solution→guardrail)

**Status:** ALL 5 STAGES BUILT (2026-06-13) — mine→resolve→cluster→monitor→guardrail end-to-end
**Created:** 2026-06-13
**Owner:** Gyasi (solo)
**Origin:** Live session 2026-06-13 — analyzing one week (763 error rows) of hh-tool friction, then a 6-round Claude×Gemini paired debate to generalize the method.
**Ephemeral marker:** Keep until MVP (Anti-Pattern Autopsy) ships + first `ANTIPATTERNS.md` is validated against the 763-row week; then graduate the design sections into SIO docs.

---

## 1. Context & Problem

SIO mines AI-coding-agent session transcripts into `~/.sio/sio.db` (`error_records`: `error_type ∈ {repeated_attempt, tool_failure, user_correction, agent_admission, undo}`, plus `tool_input/output`, `context_before/after`, `source_file`, `timestamp`, and a nano-generated `summary`).

**The core problem (the developer's words):** *the agent keeps repeating mistakes that already have known fixes — it is ignorant of prior solutions.* Today SIO logs errors but (a) doesn't pair each mistake with the **solution that resolved it**, (b) doesn't surface recurring stuck-points as a **living antipattern list**, and (c) has no way to know **when a fix actually landed** vs when work was merely abandoned.

**The generalization (the key insight):** hh tooling friction is just the *worked example*. The deliverable is a **domain-agnostic method** for mining ANY corpus of problem-sessions (cadastre, PromptChain, any repo) — structure must be **derived from the session, never hardcoded per project**. A hand-written hh-path categorizer is the anti-pattern to avoid.

### Grounding facts from the 763-row week
- `repeated_attempt` (533) are NOT noise — they collapse to ~16 tool-keyed templates but represent **320 distinct commands the agent got stuck retrying**. Agent-stuck = clear signal.
- `tool_failure` (192): ~22% are **harness artifacts** (`file has not been read yet`=34, Edit string-not-found=8) — false positives, flagged `excluded=1`, not deleted.
- Bash failures categorize cleanly by **command-category × project**: `dbt/dbtf`=70, `docker exec→psql`=44, `source .env`=27 — concentrated in cadastre, hh-dev/local-dev, V3 worktrees.
- We already generated nano `summary` for all 763 rows (~$0.16) and wrote it back to `error_records.summary`.

### Hard constraints
- Memory-tight WSL box (47GB, often ~10–20GB free); **local LLMs are CPU-only and TIME OUT at scale** (phi4:14b / gpt-oss:20b hit 600s timeouts on big rows). Prefer **gpt-4.1-nano** (~$0.16/763 rows) or tiny local embeddings.
- Solo dev. **Data integrity: flag, never delete.**
- **Never `gpt-4o`** (cost rule). nano or local only; state cost before paid calls.

---

## 2. The Design — a 5-stage refinery (all project-agnostic)

| Stage | Command | Does | MVP? |
|---|---|---|---|
| 1 | `sio mine` (extend) | tag every error: `project_root`, `command_category`, `source`, `time_block` — generically | ✅ MVP |
| 2 | `sio resolve` (new) | pair **mistake→solution** via silence-triggered nano-judge (fix/bypass/abandon) | v2 |
| 3 | `sio cluster` / **autopsy** (new) | structural-signature cluster → nano-label → emit `ANTIPATTERNS.md` | ✅ MVP |
| 4 | `sio monitor` (new) | command-cycle decay + active-wall-time → "fix landed at block N" | v2 |
| 5 | `sio guardrail` (new) | Negative-RAG PreToolUse hook — Dynamic+Whispered, Promotion Ladder | v2 (see `prd-violated-rule-to-pretooluse-hook.md`) |

### Resolved design decisions (from the 6-round debate)

**Generic derivation (no hh hardcoding):**
- `project_root` = walk `source_file` path up to nearest `.git` / `package.json` / `go.mod` / `pyproject.toml`; fallback = first 3 path segments.
- `command_category` = `binary + subcommand` from `tool_input.command` (e.g. `git checkout -b…` → `git-checkout`, `npm install x` → `npm-install`). Normalizes args away.

**Stage 3 — Structural signature (THE critical fix).** Cluster on the **structural signature, NOT the free-text `summary`** (summary drifts: "File not found: A.js" vs "B.js" won't group). 
`sig = hash(tool_name + error_type + project_root + command_category + cleaned_prefix)` where the cleaner strips volatile tokens: `0x[hex]→HEX`, `\d+→NUM`, quoted-strings→`STR`, absolute paths→relative. The `summary` is used only as **LABEL input**, never the cluster key.
- Threshold `freq ≥ 4` (in a ~763/week corpus, <4 is a fluke). Sort clusters by **total active wall-time**.

**Lite-bypass (deterministic only — full nano-judge deferred):**
- **Zombie Index** = % of cluster rows where the same `sig` recurs within 15 min of its predecessor.
- **Suppression Score** = % rows whose `tool_input` matches `(\|\|\s*true | 2>/dev/null | --no-verify | --force | -f | skip-checks | ignore-scripts | \.skip| xfail)`.
- `Resolution_Type ∈ {CLEAN_FIX, BYPASS, STALLED}`. Bypasses → a separate **"Technical Debt Generators"** section (often higher value than fixes — shows where tooling is so bad the dev gave up).

**Dual denominator** (keep both — they answer different questions):
- **Cycles** (frustration) = `count(*)` per cluster → "I don't know the *command*" (high-cycle/low-time = syntax/env papercut).
- **Active wall-time** (tax) = Σ inter-row gaps with a **12-min cap** (gap >12 min = context switch, timer resets) → "I don't know the *solution*" (low-cycle/high-time = conceptual trap).

**Stage 4 — Temporal fix-detection** (v2): denominator = **command-cycles**, not calendar time. `Fixed` = signature hot (≥K) in last 20 cmds → 0 in next 50 *within same project root*; **commands stop = `Stalled`, not `Fixed`.** Efficacy = **Mean-Turns-To-Success (MTTS)** "Signature Thaw"; auto-archive when MTTS→~1.0 for 10 iterations, re-wake on regression.

**Stage 5 — Guardrail / closing the loop** (v2 — the payoff): **Negative-RAG PreToolUse hook**, **LLM-free at runtime** (nano labels at mine-time → `signatures.db`; hook = indexed SQLite read <5ms, ~20–30ms total). Inject the recorded pro-tip as **"(Internal Project Memory)…" — framed as memory, not a warning** ("Shadow Guardrail").
- **Dynamic + Whispered** (the last attempt died from Static + Loud). A warning must *earn* its interruption: **surprise-gating** (silent if the command's recent success rate is high; only fire in a "Red State"), **frequency floor** (≥3/7d), **bypass 3× priority**, **muzzle** (fires → ignored → succeeds anyway → confidence drops, 24h muzzle), **auto-retire via Signature Thaw**.
- **Promotion Ladder** (composes with existing `sio suggest`, doesn't replace): L1 DB row → L2 just-in-time hook (execution traps) → L3 `CLAUDE.md` rule (architectural universals).

---

## 3. MVP — "Anti-Pattern Autopsy" (build now, on the existing 763 rows)

A read-only batch that treats the 763 rows as a static crime scene. No new mining, no embeddings, no local LLM, no temporal math.

**Pipeline:**
1. Query `error_records` window (`timestamp >= 2026-06-06`, `excluded=0`).
2. Per row: derive `project_root`, `command_category`; build structural `sig`.
3. Group by `sig`; keep `freq ≥ 4`; compute Zombie Index + Suppression Score + cycles + active-wall-time per cluster.
4. Send **top 15 clusters** (freq, wall-time, 3 representative `summary`s + a `tool_input`) to **gpt-4.1-nano** → `{name, why, protip}`.
5. Emit `ANTIPATTERNS.md`: **🏆 Hall of Shame (top time-sinks)**, **🚩 Bypass Gallery (Tech-Debt Generators)**, **📊 Project Friction Heatmap**.

**Fragmentation fallback (v1.1):** if `freq ≥ 4` yields almost nothing (mostly one-off errors), switch to **temporal proximity** — any 30-min window with >10 errors → send the blast-radius to nano ("what was the dev struggling with here?"). Struggle-detection, not pattern-matching.

---

## 4. Risks (from the debate's brutal self-critique)

| Risk | Mitigation |
|---|---|
| **Summary drift** breaks clustering | cluster on **structural sig**, not summary (designed in) |
| **Semantic fragmentation** → empty `ANTIPATTERNS.md` | command-category (not raw command) + temporal fallback |
| Monorepo **project-root ambiguity** | walk to nearest repo marker, not path-prefix |
| **Watch-process zombies** mislabeled | exclude auto-restart noise |
| **Silent failures** (wrong output, no error row) | acknowledged blind spot — SIO only sees error rows |
| **Truncated `error_text`** | use the existing nano `summary` for backlog; "Oreo" head+salient+tail capture for *future* mining |
| **Nag-spam** (killed last attempt) | Stage-5 surprise-gating + muzzle + auto-retire; whispered-as-memory |

---

## 5. Build order
1. ✅ **Stage 3 — Anti-Pattern Autopsy** (cluster/label) on 763 rows → `ANTIPATTERNS.md`. SHIPPED 2026-06-13 (`scripts/autopsy.py`).
2. ✅ **Stage 1 — persisted tags** (`project_tag`, `command_category`, `time_bucket`). SHIPPED 2026-06-13: shared `src/sio/mining/tagging.py`; backfill `scripts/tag_errors.py` (52,365 rows); **wired into the mine pipeline** — `error_extractor._build_record` derives tags, `_ERROR_RECORD_COLS` + schema migration persist them, so every new `sio mine` tags automatically (verified build→insert→readback). autopsy reads the columns.
3. ✅ **Stage 2 — resolve** (`scripts/resolve.py` + `error_resolutions` table + `src/sio/mining/forward_window.py`). SHIPPED 2026-06-13: **classification** — deterministic cross-session recurrence (weight-1.0 disproof) → 35/42 `RECURRING` (chronic, never fixed); judge classifies only ambiguous ones (must NOT override RECURRING/BYPASS). **Fix-extraction SOLVED via the JSONL forward-window** — re-parse the anchor's transcript, read the OK (successful) turns after the error, feed to the judge → **23 real mistake→solution pairs** (exact commands/edits). Judge model via `SIO_RESOLVE_LM`. **Empirical: gpt-4o-mini extracts 23 fixes vs flash 1 — flash too conservative for extraction; resolve should default to gpt-4o-mini** (flash fine for the simpler autopsy labeling). Robust JSON parser handles flash's messier output.
4. ✅ **Stage 4 — monitor** (`scripts/monitor.py` + `signature_lifecycle` table). SHIPPED 2026-06-13 on the FULL history (52,331 rows → 1101 signatures: FIXED=848, DORMANT=143, **ACTIVE=74**, MINOR=36). Denominator = project **events-after** (command-cycle-style), not calendar: a signature that decayed while its project kept working = FIXED (a fix landed); cold-while-project-also-quiet = DORMANT; still failing recently = ACTIVE. Joins `error_resolutions` to show what fix landed. *(7-day window was only the analysis scope; the table has months.)*
5. ✅ **Stage 5 — guardrail hook** (`~/.claude/hooks/sio-guardrail/sio-guardrail.py`). SHIPPED 2026-06-13: Negative-RAG PreToolUse hook (matcher `Bash|Edit|Write`, registered). **LLM-free** (one indexed `~/.sio/sio.db` read, ~60ms), **whisper-only** (exit 0, `permissionDecision: allow` — never blocks), framed as "(Internal project memory)". **Dynamic+Whispered precision:** fires ONLY on an ACTIVE signature (still failing) with a CONCRETE recorded fix, command not in a generic-skip denylist, freq≥4, per-signature 1h muzzle → entire whisper vocabulary is **9 high-value signatures** (e.g. `docker-exec-psql → docker exec -i hh-bi-dev-3380-ccm-pg psql …`). No "slow down" branch (that's the nag-spam mode). Disable: `SIO_GUARDRAIL_DISABLED=1`. settings.json backed up to `settings.json.bak-2026-06-13`.

## 6. Decisions Log
- 2026-06-13: Cluster on structural signature, NOT nano summary (summary = label only). *Resolves the top failure mode.*
- 2026-06-13: Lite-bypass = deterministic signals only for MVP; nano-judge resolve deferred.
- 2026-06-13: Dual denominator kept (cycles=frustration, active-wall-time=tax); 12-min block cap.
- 2026-06-13: Temporal denominator = command-cycles, not calendar time (distinguishes Fixed from Stalled).
- 2026-06-13: Guardrail = Dynamic+Whispered Negative-RAG hook; Promotion Ladder composes with `sio suggest`.
- 2026-06-13: Never delete error rows — `excluded=1` flag (34 harness false-positives already flagged).
- 2026-06-13: Suppression regex must EXCLUDE `2>/dev/null` and bare `-f` (benign idioms in this corpus) — including them flooded bypass detection 21→correct 2. The predicted nag-spam failure, caught live.
- 2026-06-13: Scaffold-skip must include `echo`/`printf`/`:`/`true`/`test` — they're diagnostic header noise; skipping them surfaces the REAL failing command (sed/grep/docker-exec-psql) and makes nano labels cite the actual fix.
- 2026-06-13: Labeling routes through `sio.core.dspy.lm_factory.make_lm`, NOT a raw `openai` client (FR-041 / never construct dspy.LM directly). `SIO_AUTOPSY_LM` overrides the model.
- 2026-06-13: **Stage 1 SHIPPED** — derivation centralized in `src/sio/mining/tagging.py` (single source); `scripts/tag_errors.py` backfills idempotently (NULL-only) and is the post-`sio mine` tagging step; autopsy reads the persisted `project_tag`/`command_category` columns (derive-fallback for untagged rows). Wired into `error_extractor._build_record` → auto-tag on every mine.
- 2026-06-13: **Judge model is tiered + configurable, NOT always nano** — the resolve judge does harder reasoning than the autopsy labeler. `SIO_RESOLVE_LM` (default `gemini/gemini-flash-latest`). Empirical: flash is conservative (0 fixes, honest "unknown"); gpt-4o-mini more aggressive (3 fixes from `context_after`). Both data-limited until the forward-window lands.
- 2026-06-13: **Deterministic recurrence is judge-proof** — a signature recurring across ≥3 sessions is `RECURRING` (FINAL); the judge may extract fix/confidence but must not downgrade it (fixed an override bug that swung RECURRING 35→15).
