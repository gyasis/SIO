# Module 2: Meet the Actors

### Teaching Arc
- **Metaphor:** A **newsroom**. The Hook is the wire-service stringer phoning in raw events. The Miner is the desk editor reading the wires for stories. The Clusterer is the section editor grouping related stories into a beat. The Suggester is the columnist writing the op-ed. The Reviewer is the publisher with veto power. The Applier is the press operator who actually prints. Every part of the paper has a different person.
- **Opening hook:** Before we trace the pipeline, who's actually on the team? When you run `sio mine --since '7 days'`, eight separate Python packages light up. Knowing which one to blame when something looks wrong is half the debugging battle.
- **Key insight:** SIO is **not one program** — it's seven small packages, each with one job. Each is a directory under `src/sio/`. Once you can name them, every error message ("failed in sio.clustering.ranker") becomes self-explanatory.
- **"Why should I care?":** When the agent gets a SIO error or a strange suggestion, knowing which actor produced it tells you which file to open. No more guessing.

### Code Snippets (pre-extracted)

**The cast (from `src/sio/` directory layout):**
```
src/sio/
  adapters/claude_code/hooks/   ← The Hook (stringer)
  mining/                       ← The Miner (desk editor)
  clustering/                   ← The Clusterer (section editor)
  suggestions/                  ← The Suggester (columnist)
  review/                       ← The Reviewer (publisher)
  applier/                      ← The Applier (press operator)
  cli/                          ← The Switchboard (the `sio` command itself)
  core/dspy/                    ← The Brain (LLM wiring, optimizers)
```

**File: `src/sio/mining/error_extractor.py` (lines 1-20)** — what the Miner reads for:
```python
"""Error extractor — classifies parsed conversation messages into five error
categories and emits ErrorRecord dicts suitable for insertion into the v2
``error_records`` table.

Error types detected
--------------------
tool_failure       — assistant message whose ``error`` field is non-null
user_correction    — human message containing correction phrasing
repeated_attempt   — same tool_name called 3+ consecutive times with similar input
undo               — human message containing undo / revert signals
agent_admission    — assistant message where the AI admits a mistake
"""
```

**File: `src/sio/clustering/pattern_clusterer.py` (lines 1-12)** — what the Clusterer does:
```python
"""Embedding-based error pattern clusterer.

Groups a list of error record dicts into semantic clusters using cosine
similarity on fastembed embeddings.  The public API is a single function:

    cluster_errors(errors, threshold=0.70) -> list[dict]
"""
```

**File: `src/sio/applier/writer.py` (lines 16-26)** — the Applier's safety perimeter:
```python
_ALLOWED_ROOTS: list[Path] = [
    Path.home() / ".sio",
    Path.home() / ".claude",
]

def _validate_target_path(path: Path, *, extra_roots: tuple[Path, ...] = ()) -> str | None:
    """Return an error message if path is outside allowed roots or cwd."""
    resolved = path.resolve()
    allowed = (*_ALLOWED_ROOTS, Path.cwd(), *extra_roots)
    for root in allowed:
        try:
            resolved.relative_to(root.resolve())
            return None
        except ValueError:
            continue
    return f"Target path {resolved} is outside allowed directories"
```

### Interactive Elements

- [x] **Code↔English translation** — `_validate_target_path`. Right column: "Before SIO writes ANYTHING to disk, it asks: is this path inside `~/.sio/`, `~/.claude/`, or the current working directory? If not, refuse. This is the seatbelt — even a buggy Suggester can't trash `/etc/passwd` because the Applier won't let it. **Always check the actor that touches the disk.**"
- [x] **Quiz — drag-and-drop** — Items: `mining/`, `clustering/`, `applier/`, `suggestions/`, `adapters/claude_code/hooks/`, `core/dspy/`, `review/`. Targets: "captures raw tool calls" → Hook, "scans transcripts for failures" → Miner, "groups similar errors using embeddings" → Clusterer, "drafts the actual rule text using an LLM" → Suggester, "asks the human to approve" → Reviewer, "writes to CLAUDE.md atomically" → Applier, "wires up the language model" → Brain (core/dspy).
- [x] **Quiz — scenario** — "An error message reads `sio.applier.writer.PermissionError: target outside allowed roots`. Where do you look first?" Correct: `src/sio/applier/writer.py` — the path-validation seatbelt rejected the write.
- [x] **Architecture diagram** — Boxes laid out as the newsroom: Hook (left margin), Miner, Clusterer, Suggester, Reviewer, Applier (right margin). Arrows show the one-way flow. Each box has a one-line subtitle ("Reads `~/.sio/<platform>/behavior_invocations.db`" under Miner, etc.).
- [x] **Glossary tooltips** — "package", "embedding", "cosine similarity", "atomic write", "ALLOWED_ROOTS".

### Aha Callouts
1. **"Read the import path — that's the actor."** `sio.clustering.pattern_clusterer` literally means: in the SIO project, the clustering actor, the pattern_clusterer file. Python's namespacing maps 1:1 to responsibilities here.
2. **"The Applier is the only actor with write permission to your config."** Everything upstream is read-only or DB-only. This is intentional — narrow blast radius.

### Reference Files to Read
- `references/interactive-elements.md` → Drag-and-Drop Quizzes, Scenario Quizzes, Architecture Diagrams, Callout Boxes, Code↔English Translation
- `references/design-system.md` → architecture diagram tokens
- `references/content-philosophy.md` → all of it
- `references/gotchas.md` → all of it

### Connections
- **Previous module:** "What SIO Does" — established the closed loop and the hook.
- **Next module:** "From Sessions to Patterns" — zooms into Miner + Clusterer, the two upstream actors.
- **Tone/style notes:** Keep newsroom metaphor consistent. Use the **bold actor name** when referring to each package. Accent color = **teal**.
