# Module 3: From Sessions to Patterns — How Raw Mess Becomes a Signal

### Teaching Arc
- **Metaphor:** A **gold-panning operation by a river**. The river is your daily session transcripts — thousands of messages, most of them noise. The Miner runs them through a sieve that only catches five specific shapes of "rock" (the five error types). Then the Clusterer takes those rocks and sorts them by composition (semantic similarity) — every pile of similar rocks is a *pattern*. One rock = a complaint. Twenty rocks in one pile = a pattern worth writing a rule about.
- **Opening hook:** Your `~/.specstory/` folder has 4,000 markdown files. Your `~/.claude/projects/.../*.jsonl` folder has hundreds more. How does SIO go from "thousands of conversations" to "you keep doing X — let's fix it"?
- **Key insight:** Two stages, both deterministic (no LLM yet): **extract** every event matching one of five regex/text patterns, then **embed each error's text** and group nearby vectors. A pattern is just a cluster of error texts that mean the same thing to a sentence-embedding model.
- **"Why should I care?":** When SIO says "you keep doing X 23 times across 5 sessions" — those numbers come from this stage. If the count looks wrong, this is the stage that produced it.

### Code Snippets (pre-extracted)

**File: `src/sio/mining/error_extractor.py` (lines 1-25)** — the five error shapes:
```python
"""Error extractor — classifies parsed conversation messages into five error
categories and emits ErrorRecord dicts.

Error types detected
--------------------
tool_failure       — assistant message whose ``error`` field is non-null
user_correction    — human message containing correction phrasing
repeated_attempt   — same tool_name called 3+ consecutive times with similar input
undo               — human message containing undo / revert signals
agent_admission    — assistant message where the AI admits a mistake
                    (e.g. "I made a mistake", "I should have", "my apologies")
"""
```

**File: `src/sio/mining/error_extractor.py` (correction patterns, ~lines 40-60)** — what triggers a `user_correction`:
```python
_CORRECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bno,?\s+actually\b", re.IGNORECASE),
    re.compile(r"(?:^|(?<=\s)|\A)no,", re.IGNORECASE),
    # ... "that's wrong", "I meant", "stop", "undo that", etc.
]
```

**File: `src/sio/clustering/pattern_clusterer.py` (header)** — the clusterer's contract:
```python
"""Groups a list of error record dicts into semantic clusters using cosine
similarity on fastembed embeddings.  The public API is a single function:

    cluster_errors(errors, threshold=0.70) -> list[dict]

Pattern dict schema
-------------------
pattern_id   : str   — centroid-hash slug (format: ``<top_term>_<hex10>``)
description  : str   — representative error text (first error's text)
error_count  : int   — number of errors in the cluster
session_count: int   — number of distinct session_ids in the cluster
rank_score   : float — 0.0 initially; downstream ranker sets this
"""
```

**File: `src/sio/clustering/pattern_clusterer.py` (centroid format)** — how a cluster's "center" is stored:
```python
def _pack_centroid(vec: np.ndarray, model_hash: bytes) -> bytes:
    """Pack vec into the R-9 BLOB format.

    Format: [dim: uint32_le (4 bytes)] [model_hash: 8 bytes] [vector: float32[dim]]
    """
    if len(model_hash) != 8:
        raise ValueError(f"model_hash must be exactly 8 bytes, got {len(model_hash)}")
    dim = len(vec)
    header = struct.pack("<I", dim) + model_hash
    floats = vec.astype(np.float32).tobytes()
    return header + floats
```

### Interactive Elements

- [x] **Code↔English translation** — the five error-type list. Right column: a one-line gloss on each error type. Highlight: "the Miner is just **pattern matching** — no AI is reading your transcripts at this stage. It's regex + structural inspection of the JSON. Cheap, fast, deterministic."
- [x] **Data flow animation** — `data-steps='[...]'` JSON on `.flow-animation`. 6 highlighted actors in a row: **JSONL Files** → **specstory_parser / jsonl_parser** → **error_extractor** → **error_records table** → **pattern_clusterer (fastembed)** → **patterns table**. Packet animation: a single tool failure ("`sed -i` wiped .env") travels left-to-right. At `pattern_clusterer`, a second similar error joins the packet and they merge into a cluster blob labeled `sed -i wipe (12 errors, 5 sessions)`.
- [x] **Group chat animation** — Required mandatory element. Actors: **Session Transcript**, **Miner**, **Clusterer**. Sequence:
  1. Session → Miner: "Here's 4,000 messages"
  2. Miner → Miner: "Scanning for tool_failure, user_correction, repeated_attempt, undo, agent_admission..."
  3. Miner → Clusterer: "Got 73 raw errors"
  4. Clusterer → Clusterer: "Embedding each with fastembed... cosine similarity > 0.70 means same cluster..."
  5. Clusterer → DB: "Stored 12 patterns. Top one is 'sed -i wipe' with 12 errors across 5 sessions"
- [x] **Quiz — spot-the-difference (multi-choice)** — Show two error records side-by-side. (A) `sed -i 's/foo/bar/' .env → file emptied`. (B) `sed -i 's/x/y/' .bashrc → file emptied`. Question: "Should the Clusterer group these into one pattern or two?" Correct: ONE — fastembed embeddings of the two strings are nearly identical (>0.70 cosine sim). The pattern is `sed -i wipe`, not `wipe .env specifically`.
- [x] **Quiz — scenario** — "Your `sio patterns` output shows `error_count: 1, session_count: 1`. Should you write a rule for it?" Correct answer: probably not — single occurrence is noise. SIO ranks by `frequency × recency × session spread`; rare events score low and get filtered out before reaching the Suggester.
- [x] **Glossary tooltips** — "regex", "embedding", "cosine similarity", "centroid", "BLOB", "fastembed", "ONNX".

### Aha Callouts
1. **"No LLM has read your code yet."** Mining + clustering use zero AI tokens. fastembed runs ONNX models *locally*. SIO can mine months of history offline. The LLM only enters in the next module.
2. **"The centroid is your savings."** Once a cluster's centroid is stored as a BLOB in SQLite, new errors are matched against it by re-using the saved vector. No re-embedding the whole corpus every time.

### Reference Files to Read
- `references/interactive-elements.md` → Data Flow Animation, Group Chat Animation, Multiple-Choice Quizzes, Scenario Quizzes, Code↔English Translation
- `references/design-system.md` → animation tokens, code block styling
- `references/content-philosophy.md` → all of it
- `references/gotchas.md` → all of it

### Connections
- **Previous module:** "Meet the Actors" — introduced Miner and Clusterer as distinct roles.
- **Next module:** "The DSPy Brain" — now that we have patterns, an LLM writes the actual rule text.
- **Tone/style notes:** Accent = teal. When showing error records, use real-looking text from common AI failures (sed -i wipe, parallel cascade, stale session resume).
