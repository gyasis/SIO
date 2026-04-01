# SIO Competitive Enhancement Plan: Import Best Features from 10+ Tools

**Status**: Planning  
**Created**: 2026-04-01  
**SIO Codebase**: `/home/gyasisutton/dev/projects/SIO/`  
**Total current LOC**: ~15,386 lines across 80+ Python files  

---

## 1. Master Competitive Feature Table

### Legend
- **HAS**: SIO already implements this (possibly at lower quality)
- **NEED**: SIO lacks this entirely
- **UPGRADE**: SIO has a version but the competitor does it better
- **P0**: Must-have, blocks other work or huge ROI
- **P1**: High value, implement in near-term
- **P2**: Nice-to-have, implement when bandwidth allows

---

### 1.1 claude-reflect (BayramAnnakov) — 871 stars

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Real-time correction detection via UserPromptSubmit hook | NEED | P0 | SIO only has PostToolUse hook; claude-reflect intercepts BEFORE tool execution. SIO's `passive_signals.py` detects corrections but only retroactively via PostToolUse. |
| 2 | Positive feedback capture ("yes exactly", "perfect") | UPGRADE | P0 | SIO's `flow_extractor.py` has `_POSITIVE_KEYWORDS` and `is_success_signal()` but only for flow tagging, not for generating positive reinforcement rules. |
| 3 | Confidence scoring with temporal decay (0.60-0.95 bands) | UPGRADE | P1 | SIO's `confidence.py` scores on error_count + dataset_coverage + rank_score but has NO temporal decay. Old patterns never lose confidence. |
| 4 | `--scan-history` retroactive mining | HAS | -- | SIO's `run_mine()` in `pipeline.py` already does retroactive mining with `--since` flag. Equivalent functionality. |
| 5 | `--dedupe` semantic deduplication | NEED | P1 | SIO clusters errors via DBSCAN in `pattern_clusterer.py` but has no deduplication of *applied rules* in CLAUDE.md. Rules can accumulate duplicates. |
| 6 | Delta-based skill updates | NEED | P1 | SIO's `writer.py` is append-only (line 75-83). Never merges or updates existing rules. Always appends new text. |

**Gaps in claude-reflect that SIO already covers**:
1. No cross-session pattern clustering (SIO has DBSCAN + FastEmbed)
2. No arena/regression testing of applied rules
3. No DSPy-based LLM optimization of suggestions

---

### 1.2 Claude Diary (rlancemartin)

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | PreCompact hook — captures context before compression | NEED | P0 | SIO has no PreCompact hook. This is the single best moment to snapshot what worked before context is lost. |
| 2 | Pattern grading heuristic (2x = emerging, 3+ = strong) | NEED | P1 | SIO ranks by `rank_score` but has no promotion mechanic from emerging to strong. |
| 3 | processed.log deduplication | NEED | P1 | SIO re-mines the same files on every `sio mine` invocation. No tracking of already-processed sessions. |
| 4 | Temporal episodic structure (YYYY-MM-DD-session-N.md) | HAS | -- | SIO uses SpecStory filenames which embed timestamps. |
| 5 | CLAUDE.md violation detection | NEED | P2 | SIO generates rules but never checks if Claude is violating existing rules. |

**Gaps in Claude Diary that SIO already covers**:
1. No error classification (SIO has 5 error types)
2. No suggestion generation or review workflow
3. No embedding-based clustering

---

### 1.3 GuideMode (119 metrics)

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Learning velocity tracking | NEED | P0 | How fast does Claude adapt? SIO tracks errors but not improvement rate over time. |
| 2 | Token efficiency metrics (cache_read / total_input) | NEED | P0 | JSONL files contain `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` — SIO ignores ALL of these. |
| 3 | Plan mode tracking (plan vs execution ratio) | NEED | P1 | SpecStory headers contain plan mode info. Not parsed. |
| 4 | Git efficiency (commits/session, message quality) | NEED | P2 | Extractable from session data but not tracked. |
| 5 | Context utilization (tokens used / window size) | NEED | P1 | Available in JSONL but not parsed. |
| 6 | Multi-platform benchmarking | NEED | P2 | SIO is Claude Code only currently. |

**Gaps in GuideMode that SIO already covers**:
1. No error pattern clustering or rule generation
2. No review/approval workflow
3. No arena testing

---

### 1.4 AutoResearch Loop (Karpathy-inspired)

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Binary assertion evaluation (pass/fail gates) | NEED | P0 | SIO's arena does gold standard replay but lacks deterministic pass/fail assertions on rule effectiveness. |
| 2 | Unattended loop (30-50 cycles) | NEED | P0 | SIO has no autonomous loop. Everything requires human invocation. |
| 3 | Git-backed rollback with experiment branches | NEED | P1 | SIO's `rollback.py` undoes DB changes but does not create git branches for experiments. |
| 4 | MAD statistics for anomaly detection | NEED | P2 | No statistical anomaly detection in SIO. |
| 5 | autoresearch.jsonl append-only transaction log | NEED | P1 | SIO logs to SQLite but has no append-only experiment log. |

**Gaps in AutoResearch that SIO already covers**:
1. No error mining or pattern detection
2. No suggestion generation
3. No embedding-based similarity

---

### 1.5 /insights (Anthropic built-in)

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Haiku-based facet extraction per session | NEED | P1 | SIO does not generate qualitative session summaries. `session_distiller.py` extracts steps but no facets. |
| 2 | Smart filtering (excludes sub-agents, short sessions) | NEED | P1 | SIO processes all files; no filtering of sub-agent or trivially short sessions. |
| 3 | Interactive HTML report with copy-paste suggestions | NEED | P2 | SIO is CLI-only. No HTML output. |
| 4 | Session caching (facets cached in usage-data/facets/) | NEED | P1 | Related to processed.log — SIO has no caching of derived data. |
| 5 | Cross-session trend analysis (30-day window) | UPGRADE | P1 | SIO has time-filtered mining but no trend visualization or analysis. |

**Gaps in /insights that SIO already covers**:
1. No rule generation or application
2. No pattern clustering
3. No DSPy optimization

---

### 1.6 continuous-learning skill

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Stop hook (session END capture) | NEED | P0 | SIO has PostToolUse but no session-end hook. Critical for capturing final session state. |
| 2 | 5 pattern categories | HAS | -- | SIO has 5 error types (tool_failure, user_correction, repeated_attempt, undo, agent_admission). |
| 3 | Auto-save to ~/.claude/skills/learned/ | NEED | P1 | SIO generates suggestions but does not auto-promote to skills. |

**Gaps in continuous-learning that SIO already covers**:
1. No clustering or ranking
2. No review workflow
3. No arena testing

---

### 1.7 Bootstrap Seed (ChristopherA)

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Promotion mechanics (learning -> rule after 2+ uses) | NEED | P1 | SIO has no automatic promotion from pattern to applied rule. |
| 2 | Consolidation triggers (entries > 30 -> auto-consolidate) | NEED | P1 | SIO's writer is append-only with no consolidation. |
| 3 | Anti-proliferation guardrails (<100 lines CLAUDE.md cap) | NEED | P0 | SIO can append indefinitely. No line budget enforcement. |
| 4 | Quad Pattern format (rule + process + requirements + reference) | NEED | P2 | SIO rules are free-form markdown, not structured. |

**Gaps in Bootstrap Seed that SIO already covers**:
1. No automated mining
2. No error classification
3. No DSPy optimization

---

### 1.8 Addy Osmani's Patterns

| # | Feature | SIO Status | Priority | Notes |
|---|---------|-----------|----------|-------|
| 1 | Ralph Wiggum task-atomic loop | NEED | P2 | SIO does not decompose tasks. |
| 2 | Four memory channels (git + progress.txt + prd.json + AGENTS.md) | NEED | P2 | SIO writes to CLAUDE.md and rules/ only. |
| 3 | Planner-Worker-Judge concurrency | NEED | P2 | SIO is single-threaded pipeline. |

---

## 2. SIO Data Extraction Gap Analysis

Current extraction rate: ~35-40% of available JSONL/SpecStory data.

| Missing Field | JSONL Location | Priority | Estimated Value |
|--------------|---------------|----------|-----------------|
| Token usage (input_tokens, output_tokens, cache metrics) | `message.usage` object | P0 | Enables token efficiency tracking, cost analysis, cache optimization |
| Inter-message latency | Diff between consecutive `timestamp` fields | P0 | Enables responsiveness tracking, bottleneck detection |
| Sub-agent success rates | `isSidechain` flag on messages | P1 | Enables sub-agent filtering and quality tracking |
| Plan mode detection | SpecStory headers, message metadata | P1 | Enables plan vs execution ratio |
| Context window utilization | `usage.input_tokens` / model context limit | P1 | Enables context pressure tracking |
| Tool approval/rejection | User response patterns after tool_use | P1 | Enables approval rate metrics |
| User sentiment trajectory | Sequence of positive/negative signals | P1 | Enables frustration detection |
| Stop reason | `stop_reason` field ("end_turn" vs "max_tokens") | P1 | Enables context exhaustion detection |
| Inference geography / service tier | Message metadata | P2 | Performance benchmarking |
| Cost per session | `cost_usd` field | P0 | Enables cost-effectiveness tracking |

---

## 3. Implementation Waves

### Wave 1: Data Foundation (Parser Enhancements)
**Goal**: Extract the missing 60% of session data from JSONL/SpecStory files.  
**Dependency**: None. This unblocks everything else.

#### W1.1: Enhanced JSONL Parser — Token & Cost Extraction
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/jsonl_parser.py`  
**What to add**: Extract `usage` object from each assistant message line. The JSONL wire format includes:
```
{"type":"assistant","message":{"role":"assistant","content":[...],"usage":{"input_tokens":N,"output_tokens":N,"cache_creation_input_tokens":N,"cache_read_input_tokens":N}},"costUsd":0.05,...}
```
**Changes**:
- In `_parse_real_assistant()` (line 156): Extract `raw.get("costUsd")` and `message.get("usage", {})` fields
- Add to returned record dict: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `cost_usd`, `stop_reason`
- In `_parse_real_user()` (line 84): Extract `raw.get("isSidechain", False)` for sub-agent detection
- **Estimated LOC**: +40 lines to jsonl_parser.py
- **New fields in record dict**: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_create_tokens`, `cost_usd`, `stop_reason`, `is_sidechain`

#### W1.2: New Database Table — session_metrics
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py`  
**What to add**: New `session_metrics` table to store per-session aggregated metrics.
```sql
CREATE TABLE IF NOT EXISTS session_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    source_file TEXT NOT NULL,
    total_messages INTEGER NOT NULL DEFAULT 0,
    total_tool_calls INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    total_cache_create_tokens INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    cache_hit_ratio REAL DEFAULT 0.0,
    context_utilization REAL DEFAULT 0.0,
    duration_seconds REAL DEFAULT 0.0,
    error_count INTEGER DEFAULT 0,
    correction_count INTEGER DEFAULT 0,
    positive_signal_count INTEGER DEFAULT 0,
    stop_reason_end_turn INTEGER DEFAULT 0,
    stop_reason_max_tokens INTEGER DEFAULT 0,
    sidechain_count INTEGER DEFAULT 0,
    plan_mode_ratio REAL DEFAULT 0.0,
    first_message_at TEXT,
    last_message_at TEXT,
    mined_at TEXT NOT NULL
);
```
- **Estimated LOC**: +45 lines (DDL + indexes + init_db call)
- **Dependencies**: None

#### W1.3: New Database Table — processed_sessions
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/core/db/schema.py`  
**What to add**: Track which files have been mined to avoid re-processing (from Claude Diary's `processed.log` pattern).
```sql
CREATE TABLE IF NOT EXISTS processed_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    error_count INTEGER DEFAULT 0,
    metrics_extracted INTEGER DEFAULT 0
);
```
- **Estimated LOC**: +20 lines
- **Dependencies**: None

#### W1.4: Inter-message Latency Computation
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/jsonl_parser.py`  
**What to add**: After parsing all records, compute `latency_ms` between consecutive messages by diffing timestamps. Add as a post-processing step in `parse_jsonl()`.
- **Estimated LOC**: +25 lines
- **Dependencies**: W1.1

#### W1.5: Pipeline Integration — Metrics Aggregation
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py`  
**What to add**: New function `aggregate_session_metrics(parsed_messages) -> dict` that computes all metrics from parsed message list and inserts into `session_metrics` table. Add dedup check against `processed_sessions` table in `run_mine()`.
- **Estimated LOC**: +80 lines
- **Dependencies**: W1.1, W1.2, W1.3

---

### Wave 2: Detection Upgrade (New Signal Types)
**Goal**: Detect positive signals, approval patterns, sentiment, and sub-agent quality.  
**Dependency**: Wave 1.

#### W2.1: Positive Signal Extractor
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/mining/positive_extractor.py`  
**What to add**: New module parallel to `error_extractor.py` but for positive signals. Detects:
- User confirmations: "yes exactly", "perfect", "that's right", "looks good", "ship it", "lgtm"
- User gratitude: "thanks", "great work", "awesome"
- Implicit approval: short responses (<5 words) with no negative keywords after tool execution
- Session success: session ends with positive signal and no pending errors

**Pattern lists** (modeled on `error_extractor.py`'s `_CORRECTION_PATTERNS`):
```python
_CONFIRMATION_PATTERNS = [
    re.compile(r"\byes,?\s+exactly\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+(right|correct|perfect)\b", re.IGNORECASE),
    re.compile(r"\blooks?\s+good\b", re.IGNORECASE),
    re.compile(r"\blgtm\b", re.IGNORECASE),
    re.compile(r"\bship\s+it\b", re.IGNORECASE),
    re.compile(r"\bperfect\b", re.IGNORECASE),
    re.compile(r"\bexactly\s+what\s+i\s+(?:wanted|needed)\b", re.IGNORECASE),
]
```

**Public API**: `extract_positive_signals(parsed_messages, source_file, source_type) -> list[dict]`

**Return schema** (PositiveRecord):
```python
{
    "session_id": str,
    "timestamp": str,
    "source_file": str,
    "signal_type": str,  # "confirmation", "gratitude", "implicit_approval", "session_success"
    "signal_text": str,
    "context_before": str,  # what the agent did that earned the positive signal
    "tool_name": str | None,  # tool that was used successfully
    "mined_at": str,
}
```
- **Estimated LOC**: ~150 lines
- **Dependencies**: Wave 1 (timestamp/metrics available)
- **New DB table**: `positive_records` (parallel to `error_records`)

#### W2.2: Tool Approval/Rejection Detector
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/mining/approval_detector.py`  
**What to add**: Detect when users approve vs reject tool calls. In JSONL, after a `tool_use` block, the user either provides a `tool_result` (approval) or sends a text message interrupting (rejection/redirect).
- Track approval rate per tool
- Track rejection patterns (which tools get rejected most)
- **Estimated LOC**: ~80 lines
- **Dependencies**: W1.1

#### W2.3: Sentiment Trajectory Scorer
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/mining/sentiment_scorer.py`  
**What to add**: Score user sentiment per message on a -1.0 to +1.0 scale using keyword matching (no LLM needed). Track trajectory over session. Detect frustration escalation (3+ consecutive negative scores).
- Uses both `_CORRECTION_PATTERNS` from error_extractor and `_CONFIRMATION_PATTERNS` from positive_extractor
- Adds escalation words: "frustrated", "annoying", "waste of time", "just do X"
- **Estimated LOC**: ~100 lines
- **Dependencies**: W2.1

#### W2.4: Sub-agent Filter
**File**: Modify `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py`  
**What to add**: When `isSidechain` is true on a message, either filter it out entirely or tag it so metrics are computed separately. From /insights pattern: exclude sub-agents from main metrics, track them separately.
- Add `--exclude-sidechains` flag to mine command
- **Estimated LOC**: +30 lines to pipeline.py
- **Dependencies**: W1.1

#### W2.5: Smart Session Filtering
**File**: Modify `/home/gyasisutton/dev/projects/SIO/src/sio/mining/pipeline.py`  
**What to add**: From /insights — skip sessions with <5 messages or <2 tool calls. These are trivially short and add noise.
- Add `min_messages` and `min_tool_calls` config params
- **Estimated LOC**: +20 lines
- **Dependencies**: W1.5

---

### Wave 3: Intelligence Layer (Metrics, Grading, Velocity)
**Goal**: Transform raw data into actionable intelligence.  
**Dependency**: Wave 1, Wave 2.

#### W3.1: Learning Velocity Tracker
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/metrics/velocity.py`  
**What to add**: Compute how fast Claude adapts to corrections over time. For each error type, track:
- `error_rate_per_session` over rolling 7-day windows
- `correction_decay_rate`: after a rule is applied, how quickly do related errors decrease?
- `adaptation_speed`: sessions until error type drops below threshold after rule applied
- **Estimated LOC**: ~120 lines
- **Dependencies**: W1.2 (session_metrics table)
- **New DB table**: `velocity_snapshots` (session_id, error_type, rate, window_start, window_end)

#### W3.2: Confidence Scoring with Temporal Decay
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/suggestions/confidence.py`  
**What to change**: Add temporal decay factor. Currently `score_confidence()` uses only error_count + dataset_coverage + rank_score. Add:
```python
# Temporal decay: patterns not seen in 14 days lose 20% confidence per week
days_since_last = (now - last_seen).days
if days_since_last > 14:
    weeks_stale = (days_since_last - 14) / 7
    decay_factor = max(0.3, 1.0 - 0.20 * weeks_stale)
else:
    decay_factor = 1.0
raw = raw * decay_factor
```
- Change `score_confidence()` signature to accept `last_seen: str | None`
- Add decay bands: Fresh (0-14 days, 1.0x), Cooling (15-28 days, 0.8x-0.6x), Stale (29+ days, 0.6x-0.3x floor)
- **Estimated LOC**: +25 lines (modify existing function)
- **Dependencies**: None (patterns table already has `last_seen`)

#### W3.3: Pattern Grading & Promotion
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/clustering/grader.py`  
**What to add**: Implement promotion mechanics from Bootstrap Seed + Claude Diary:
- `emerging`: pattern seen 2x across 2+ sessions
- `strong`: pattern seen 3+ times across 3+ sessions  
- `established`: pattern seen 5+ times, has been consistent for 7+ days
- `declining`: pattern was strong but has temporal decay below 0.5
- Auto-promote `strong` patterns to suggestion generation
- **Estimated LOC**: ~80 lines
- **Dependencies**: W3.2

#### W3.4: Instruction Budget Awareness (Anti-Proliferation)
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/applier/budget.py`  
**What to add**: From Bootstrap Seed — hard cap enforcement:
- Before applying any suggestion, count current lines in target file
- CLAUDE.md: hard cap at 100 lines (configurable)
- Rules files: soft cap at 50 lines each
- When budget exceeded: trigger consolidation (merge similar rules) instead of appending
- Block application if consolidation still exceeds budget
- **Estimated LOC**: ~100 lines
- **Dependencies**: None

#### W3.5: CLAUDE.md Violation Detection
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/mining/violation_detector.py`  
**What to add**: From Claude Diary — cross-reference mined session errors against existing CLAUDE.md rules. If the agent violated a rule that already exists, flag it as a "violation" rather than a "new pattern". This is higher priority than generating new rules.
- Parse existing CLAUDE.md rules into structured list
- For each error, check if it matches an existing rule's prevention criteria
- **Estimated LOC**: ~120 lines
- **Dependencies**: W2.1

#### W3.6: Semantic Deduplication of Applied Rules
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/applier/deduplicator.py`  
**What to add**: From claude-reflect's `--dedupe` — scan all rule files for semantically similar entries using FastEmbed embeddings. Propose consolidated versions when similarity > 0.85.
- Reuse `_get_backend()` from `pattern_clusterer.py` for embeddings
- Parse markdown rules into chunks, embed each, find near-duplicates
- Generate consolidated rule text
- **Estimated LOC**: ~120 lines
- **Dependencies**: None (uses existing FastEmbed)

---

### Wave 4: Automation (New Hooks + Auto-Mining)
**Goal**: Capture data at PreCompact, Stop, and UserPromptSubmit lifecycle moments.  
**Dependency**: Wave 1, Wave 2.

#### W4.1: PreCompact Hook Handler
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/pre_compact.py`  
**What to add**: From Claude Diary — capture the full context state BEFORE compression. This is the richest data capture point because all context is still available.
- Save a snapshot of: current session metrics, active patterns, tool call history
- Trigger incremental mining of the current session
- Detect "what just worked" by looking at recent positive signals
- **Estimated LOC**: ~80 lines
- **Hook registration**: Add to `.claude/settings.json` under `hooks.PreCompact`

#### W4.2: Stop Hook Handler (Session End)
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/stop.py`  
**What to add**: From continuous-learning skill — capture at session END:
- Finalize session_metrics entry
- Run lightweight pattern detection on the completed session
- Auto-save learned patterns to `~/.claude/skills/learned/` if confidence > 0.8
- Update processed_sessions table
- **Estimated LOC**: ~100 lines
- **Hook registration**: Add to `.claude/settings.json` under `hooks.Stop`

#### W4.3: UserPromptSubmit Hook Handler
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/hooks/user_prompt_submit.py`  
**What to add**: From claude-reflect — intercept BEFORE processing to detect corrections in real-time:
- Run `detect_correction()` and `detect_undo()` on the incoming message
- If correction detected, increment session correction counter
- If frustration escalation detected (W2.3), log warning
- Always return `{"action": "allow"}` — never block
- **Estimated LOC**: ~60 lines
- **Hook registration**: Add to `.claude/settings.json` under `hooks.UserPromptSubmit`

#### W4.4: Hook Installer Update
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/adapters/claude_code/installer.py`  
**What to modify**: Register all three new hooks in the installer so `sio install` sets them up:
- PreCompact -> `pre_compact.py`
- Stop -> `stop.py`  
- UserPromptSubmit -> `user_prompt_submit.py`
- **Estimated LOC**: +40 lines
- **Dependencies**: W4.1, W4.2, W4.3

#### W4.5: Delta-Based Rule Writer
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/applier/writer.py`  
**What to modify**: From claude-reflect — replace pure append with delta-based updates:
- Before appending, parse existing rules in target file
- Check if new rule overlaps with existing rule (using FastEmbed similarity)
- If overlap > 0.80: MERGE (update existing rule text) instead of append
- If overlap < 0.80: APPEND as before
- Track delta in `applied_changes` table
- **Estimated LOC**: +60 lines (modify `apply_change()` function)
- **Dependencies**: W3.6 (deduplicator provides similarity check)

---

### Wave 5: Arena Evolution (AutoResearch Loop)
**Goal**: Automated, unattended rule optimization with deterministic validation.  
**Dependency**: Wave 1-3.

#### W5.1: Binary Assertion Framework
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/arena/assertions.py`  
**What to add**: From AutoResearch — deterministic pass/fail gates for rule testing:
```python
@dataclass
class Assertion:
    name: str
    condition: Callable[[dict], bool]  # Takes session_metrics, returns bool
    description: str

class AssertionResult:
    assertion: Assertion
    passed: bool
    actual_value: Any
    expected: str
```
- Built-in assertions:
  - `error_rate_decreased`: error count in pattern category dropped after rule applied
  - `no_new_regressions`: no new error types introduced
  - `confidence_above_threshold`: suggestion confidence > 0.6
  - `budget_within_limits`: target file stays under line cap
  - `no_collisions`: new rule doesn't collide with existing rules
- Custom assertion support via config
- **Estimated LOC**: ~100 lines
- **Dependencies**: W3.1 (velocity tracker)

#### W5.2: Experiment Branch Manager
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/arena/experiment.py`  
**What to add**: From AutoResearch — git-backed experiment branches:
- Create `experiment/<suggestion-id>-<timestamp>` branch before applying rule
- Apply rule on experiment branch
- Run assertions against next N sessions
- If assertions pass: merge to main, delete branch
- If assertions fail: delete branch, mark suggestion as "failed_experiment"
- **Estimated LOC**: ~120 lines
- **Dependencies**: W5.1

#### W5.3: AutoResearch Loop Engine
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/arena/autoresearch.py`  
**What to add**: The crown jewel — unattended optimization loop:

**Loop cycle** (runs every N minutes, configurable):
1. **Mine**: Run `run_mine()` on new sessions since last cycle
2. **Cluster**: Run `cluster_errors()` + `rank_patterns()`
3. **Grade**: Run `grade_patterns()` — promote emerging to strong
4. **Generate**: For strong patterns, run `generate_suggestions()`
5. **Assert**: For each suggestion, run binary assertions
6. **Experiment**: For passing suggestions, create experiment branch and apply
7. **Validate**: After M sessions, check if experiment improved metrics
8. **Promote/Rollback**: Merge successful experiments, delete failed ones
9. **Log**: Append cycle results to `autoresearch.jsonl`

**Safety guardrails**:
- Max 3 active experiments at once
- Max 1 rule application per cycle
- Budget enforcement (W3.4) on every apply
- Human-reviewable log at `~/.sio/autoresearch.jsonl`
- Emergency stop: `sio autoresearch --stop`

**Integration with existing arena**:
- Uses `regression.py`'s `run_arena()` as the validation step
- Uses `drift_detector.py` to check if applied rules cause drift
- Uses `collision.py` to prevent rule collisions
- Uses `gold_standards.py` for regression testing

**Estimated LOC**: ~250 lines
**Dependencies**: W5.1, W5.2, W3.1, W3.3, W3.4

#### W5.4: MAD Statistics for Anomaly Detection
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/arena/anomaly.py`  
**What to add**: From AutoResearch — Median Absolute Deviation for detecting unusual session metrics:
```python
def detect_anomalies(values: list[float], threshold: float = 3.0) -> list[int]:
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad == 0:
        return []
    modified_z = 0.6745 * (values - median) / mad
    return [i for i, z in enumerate(modified_z) if abs(z) > threshold]
```
- Apply to: error_rate, token_usage, session_duration, cost_per_session
- Flag anomalous sessions for manual review
- **Estimated LOC**: ~60 lines
- **Dependencies**: W1.2

#### W5.5: Experiment Transaction Log
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/core/arena/txlog.py`  
**What to add**: Append-only JSONL log at `~/.sio/autoresearch.jsonl`:
```json
{"cycle": 1, "timestamp": "...", "phase": "mine", "files_scanned": 5, "errors_found": 12}
{"cycle": 1, "timestamp": "...", "phase": "assert", "suggestion_id": 7, "assertions": {"error_rate_decreased": true, "no_regressions": true}, "verdict": "pass"}
{"cycle": 1, "timestamp": "...", "phase": "experiment", "branch": "experiment/7-20260401", "action": "created"}
```
- **Estimated LOC**: ~40 lines
- **Dependencies**: None

---

### Wave 6: UX & Reporting
**Goal**: HTML reports, dashboards, copy-paste suggestions.  
**Dependency**: Wave 1-3.

#### W6.1: HTML Report Generator
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/reports/html_report.py`  
**What to add**: From /insights — interactive HTML report:
- Session metrics dashboard (token usage, cost, cache efficiency)
- Error trend chart (30-day rolling)
- Pattern table with confidence scores and temporal decay visualization
- Copy-paste suggestion cards (click to copy proposed CLAUDE.md rule)
- Learning velocity graph
- Uses embedded CSS/JS (no external dependencies), single HTML file output
- **Estimated LOC**: ~200 lines
- **Dependencies**: W1.2, W3.1

#### W6.2: Facet Extraction
**File**: NEW — `/home/gyasisutton/dev/projects/SIO/src/sio/mining/facet_extractor.py`  
**What to add**: From /insights — generate qualitative session facets using the sub-LLM:
- Categories: "tool_mastery", "error_prone_area", "user_satisfaction", "session_complexity"
- Cache facets in `~/.sio/facets/` directory (keyed by session file hash)
- Fall back to keyword-based extraction when no LLM available
- **Estimated LOC**: ~120 lines
- **Dependencies**: W1.5 (processed_sessions for caching)

#### W6.3: CLI Enhancements
**File**: `/home/gyasisutton/dev/projects/SIO/src/sio/cli/main.py`  
**What to add**: New commands:
- `sio metrics` — show session_metrics dashboard in terminal (Rich tables)
- `sio velocity` — show learning velocity trends
- `sio report --html` — generate HTML report
- `sio dedupe` — find and consolidate duplicate rules
- `sio violations` — show CLAUDE.md violation report
- `sio autoresearch start|stop|status` — manage auto-research loop
- `sio budget` — show instruction budget usage per file
- **Estimated LOC**: ~200 lines (7 new commands, ~30 lines each)
- **Dependencies**: Various Wave 1-5 modules

---

## 4. AutoResearch Loop Integration Plan (Detailed)

This is the most architecturally significant addition. Here is the detailed integration with SIO's existing arena system.

### 4.1 Architecture

```
                     ┌─────────────────────────────┐
                     │   AutoResearch Loop Engine   │
                     │   (autoresearch.py)          │
                     └─────────┬───────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
    ┌───────────────┐  ┌──────────────┐  ┌──────────────┐
    │ Mine + Cluster │  │ Grade + Gen  │  │ Experiment   │
    │ pipeline.py    │  │ grader.py    │  │ experiment.py│
    │ clusterer.py   │  │ generator.py │  │              │
    │ ranker.py      │  │ dspy_gen.py  │  │              │
    └───────┬───────┘  └──────┬───────┘  └──────┬───────┘
            │                 │                  │
            ▼                 ▼                  ▼
    ┌───────────────┐  ┌──────────────┐  ┌──────────────┐
    │ session_metrics│  │ assertions.py│  │ Git branches │
    │ (new table)   │  │ (new)        │  │ experiment/* │
    └───────────────┘  └──────┬───────┘  └──────┬───────┘
                              │                  │
                              ▼                  ▼
                      ┌──────────────┐  ┌──────────────┐
                      │ Arena        │  │ txlog.py     │
                      │ regression.py│  │ (append-only)│
                      │ drift_det.py │  │              │
                      │ collision.py │  │              │
                      │ gold_std.py  │  │              │
                      └──────────────┘  └──────────────┘
```

### 4.2 Loop State Machine

```
IDLE ──(cron/manual trigger)──> MINING ──> CLUSTERING ──> GRADING
  ▲                                                         │
  │                                                         ▼
  │                                                    GENERATING
  │                                                         │
  │                                                         ▼
  │                                                    ASSERTING
  │                                                      │     │
  │                                          (fail)──────┘     │──(pass)
  │                                            │               ▼
  │                                         SKIPPED     EXPERIMENTING
  │                                            │           │      │
  │                                            │    (N sessions)  │
  │                                            │           ▼      │
  │                                            │      VALIDATING  │
  │                                            │       │     │    │
  │                                            │  (pass)│    │(fail)
  │                                            │       ▼    ▼    │
  │                                            │   PROMOTED ROLLED_BACK
  │                                            │       │      │
  └────────────────────────────────────────────┴───────┴──────┘
                         (next cycle)
```

### 4.3 Integration Points with Existing Arena

**`regression.py` (existing)**: Currently runs gold standard replay + drift check + collision check. In AutoResearch:
- Called during VALIDATING phase
- If `run_arena()` returns `passed: False`, the experiment is rolled back
- Enhancement: Add `session_metrics` comparison to `run_arena()` — not just gold standard replay, but actual metric improvement verification

**`drift_detector.py` (existing)**: Currently uses SequenceMatcher as proxy.
- Enhancement for AutoResearch: Use actual FastEmbed cosine distance instead of SequenceMatcher
- Called during ASSERTING phase to ensure new rule doesn't semantically drift from pattern intent

**`collision.py` (existing)**: Currently checks skill description overlaps.
- Enhancement: Also check proposed rules against ALL existing rules in target file
- Called during ASSERTING phase to prevent rule conflicts

**`gold_standards.py` (existing)**: Currently replays saved test cases against new prompts.
- Enhancement for AutoResearch: Auto-generate gold standards from successful sessions
- Each experiment that passes validation should produce a new gold standard

### 4.4 Configuration

Add to `~/.sio/config.toml`:
```toml
[autoresearch]
enabled = false
cycle_interval_minutes = 30
max_active_experiments = 3
max_rules_per_cycle = 1
min_sessions_before_validate = 5
assertion_threshold = 0.6
budget_cap_claude_md = 100
budget_cap_rules_file = 50
auto_promote = false  # require human approval for final merge
```

### 4.5 Safety Mechanisms

1. **Budget enforcement**: Every rule application checks line count (W3.4)
2. **Collision detection**: Every rule is checked against all existing rules (collision.py)
3. **Experiment isolation**: Rules applied on git branches, not main
4. **Max concurrent**: At most 3 experiments running simultaneously
5. **Human gate**: `auto_promote = false` by default — human must approve final merge
6. **Emergency stop**: `sio autoresearch --stop` kills loop and rolls back all active experiments
7. **Transaction log**: Every action logged to append-only JSONL for auditability
8. **Temporal validation**: Experiments must survive N sessions (configurable) before promotion

---

## 5. Dependency Chain & Sequencing

```
Wave 1 (Data Foundation) ──────────────────────────────────> DONE
  │
  ├── W1.1 JSONL Parser Enhancement (no deps)
  ├── W1.2 session_metrics table (no deps)
  ├── W1.3 processed_sessions table (no deps)
  ├── W1.4 Latency computation (depends on W1.1)
  └── W1.5 Pipeline integration (depends on W1.1, W1.2, W1.3)

Wave 2 (Detection Upgrade) ────────────────────────────────> DONE
  │  (depends on Wave 1)
  ├── W2.1 Positive signal extractor (depends on W1.1)
  ├── W2.2 Approval/rejection detector (depends on W1.1)
  ├── W2.3 Sentiment trajectory scorer (depends on W2.1)
  ├── W2.4 Sub-agent filter (depends on W1.1)
  └── W2.5 Smart session filtering (depends on W1.5)

Wave 3 (Intelligence Layer) ───────────────────────────────> DONE
  │  (depends on Wave 1, Wave 2)
  ├── W3.1 Learning velocity tracker (depends on W1.2)
  ├── W3.2 Temporal decay confidence (no deps — uses existing patterns table)
  ├── W3.3 Pattern grading & promotion (depends on W3.2)
  ├── W3.4 Instruction budget awareness (no deps)
  ├── W3.5 CLAUDE.md violation detection (depends on W2.1)
  └── W3.6 Semantic deduplication (no deps — uses existing FastEmbed)

Wave 4 (Automation) ───────────────────────────────────────> DONE
  │  (depends on Wave 1, Wave 2)
  ├── W4.1 PreCompact hook (depends on W1.5, W2.1)
  ├── W4.2 Stop hook (depends on W1.2, W1.3)
  ├── W4.3 UserPromptSubmit hook (depends on W2.3)
  ├── W4.4 Hook installer update (depends on W4.1, W4.2, W4.3)
  └── W4.5 Delta-based writer (depends on W3.6)

Wave 5 (Arena Evolution) ─────────────────────────────────> DONE
  │  (depends on Wave 1, 2, 3)
  ├── W5.1 Binary assertion framework (depends on W3.1)
  ├── W5.2 Experiment branch manager (depends on W5.1)
  ├── W5.3 AutoResearch loop engine (depends on W5.1, W5.2, W3.1, W3.3, W3.4)
  ├── W5.4 MAD anomaly detection (depends on W1.2)
  └── W5.5 Experiment transaction log (no deps)

Wave 6 (UX & Reporting) ──────────────────────────────────> DONE
  │  (depends on Wave 1, 2, 3)
  ├── W6.1 HTML report generator (depends on W1.2, W3.1)
  ├── W6.2 Facet extraction (depends on W1.5)
  └── W6.3 CLI enhancements (depends on various)
```

---

## 6. Estimated Total Effort

| Wave | New Files | Modified Files | Estimated LOC | Priority Items |
|------|-----------|---------------|---------------|----------------|
| 1: Data Foundation | 0 | 3 | ~210 | P0: Token extraction, session metrics, processed tracking |
| 2: Detection Upgrade | 3 | 1 | ~360 | P0: Positive signals, approval patterns, sentiment |
| 3: Intelligence Layer | 4 | 1 | ~565 | P0: Velocity, temporal decay, budget caps, violation detection |
| 4: Automation | 3 | 2 | ~340 | P0: PreCompact, Stop, UserPromptSubmit hooks |
| 5: Arena Evolution | 5 | 0 | ~570 | P0: Assertions, experiments, AutoResearch loop |
| 6: UX & Reporting | 2 | 1 | ~520 | P1: HTML reports, facets, CLI commands |
| **TOTAL** | **17** | **8** | **~2,565** | |

This represents a ~17% increase in codebase size (2,565 / 15,386) while adding capabilities from all 10 competitive tools.

---

## 7. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Hook latency (all 3 new hooks) | Keep hook handlers <100ms; defer heavy work to background queues |
| AutoResearch loop runaway | Max 3 experiments, budget caps, emergency stop, human gate by default |
| Database growth from session_metrics | Retention policy (existing `retention.py`), VACUUM on schedule |
| FastEmbed model loading in hooks | Use module-level singleton (existing pattern in `pattern_clusterer.py`) |
| Backward compatibility of JSONL parser | New fields are optional; old records get None values |
| Rule consolidation changing behavior | Delta-based writer always creates applied_changes record for rollback |

---

## 8. Files Summary

### New Files (17)
1. `src/sio/mining/positive_extractor.py` — Positive signal detection
2. `src/sio/mining/approval_detector.py` — Tool approval/rejection tracking
3. `src/sio/mining/sentiment_scorer.py` — User sentiment trajectory
4. `src/sio/mining/violation_detector.py` — CLAUDE.md rule violation detection
5. `src/sio/mining/facet_extractor.py` — Qualitative session facets
6. `src/sio/core/metrics/velocity.py` — Learning velocity computation
7. `src/sio/clustering/grader.py` — Pattern grading and promotion
8. `src/sio/applier/budget.py` — Instruction budget enforcement
9. `src/sio/applier/deduplicator.py` — Semantic rule deduplication
10. `src/sio/core/arena/assertions.py` — Binary assertion framework
11. `src/sio/core/arena/experiment.py` — Git-backed experiment branches
12. `src/sio/core/arena/autoresearch.py` — Unattended optimization loop
13. `src/sio/core/arena/anomaly.py` — MAD anomaly detection
14. `src/sio/core/arena/txlog.py` — Append-only experiment transaction log
15. `src/sio/adapters/claude_code/hooks/pre_compact.py` — PreCompact hook
16. `src/sio/adapters/claude_code/hooks/stop.py` — Stop hook
17. `src/sio/adapters/claude_code/hooks/user_prompt_submit.py` — UserPromptSubmit hook

### Modified Files (8)
1. `src/sio/mining/jsonl_parser.py` — Token/cost/sidechain extraction
2. `src/sio/core/db/schema.py` — session_metrics + processed_sessions tables
3. `src/sio/mining/pipeline.py` — Metrics aggregation, dedup, filtering
4. `src/sio/suggestions/confidence.py` — Temporal decay
5. `src/sio/applier/writer.py` — Delta-based updates
6. `src/sio/adapters/claude_code/installer.py` — New hook registration
7. `src/sio/cli/main.py` — New CLI commands
8. `src/sio/reports/html_report.py` — HTML report generation
