# Module 5: Closing the Loop — Apply Safely or Not At All

### Teaching Arc
- **Metaphor:** A **surgical timeout**. Before any operation, the team pauses, reads the patient's chart aloud, confirms the procedure, confirms the site, and only then makes the first cut. A backup copy of the patient's pre-op state lives in a folder, kept for ten generations of patients. If something goes wrong, the surgeon can rewind to before the cut. **The Applier never writes without a backup, never writes outside permitted directories, and never writes a rule the human didn't approve.**
- **Opening hook:** A DSPy module just generated `"Never use sed -i — use the Edit tool."` That string is sitting in the `suggestions` table. Nothing has touched your `CLAUDE.md` yet. What's the path from "suggestion in a database" to "rule in your config file" — and how does SIO make sure the path is reversible?
- **Key insight:** Three gates close the loop: (1) **human approval** via `sio suggest-review`, (2) **path validation** rejecting anything outside `~/.sio/`, `~/.claude/`, or `cwd`, (3) **atomic write + timestamped backup**, with the last 10 backups retained. Rollback is `sio apply --rollback <id>` and works because the pre-write diff is stored in the DB.
- **"Why should I care?":** This is the part of SIO where things can break your environment. Understanding the three gates means you can fearlessly approve suggestions — the system can't write outside its sandbox, can't lose your old config, and can't apply anything you didn't approve.

### Code Snippets (pre-extracted)

**File: `src/sio/applier/writer.py` (lines 15-40)** — the path-allowlist seatbelt:
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
    return (
        f"Target path {resolved} is outside allowed directories: "
        f"{', '.join(str(r) for r in allowed)}"
    )
```

**File: `src/sio/applier/writer.py` (apply_change flow, lines ~195-220)** — the approval gate:
```python
def apply_change(db, suggestion_id, config=None, force=False) -> dict:
    row = db.execute(
        "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()

    if row is None:
        return {"success": False, "reason": "Suggestion not found"}

    suggestion = dict(row)

    if suggestion["status"] not in ("approved", "auto_approved"):
        return {
            "success": False,
            "reason": f"Suggestion is not approved (status: {suggestion['status']})",
        }
    # ... budget check, similarity merge, atomic write with backup
```

**File: `src/sio/applier/budget.py` (lines 1-30)** — the instruction-file budget:
```python
"""sio.applier.budget -- instruction budget management for config files.

Public API
----------
    count_meaningful_lines(file_path) -> int
    check_budget(file_path, new_rule_lines, config) -> BudgetResult
    trigger_consolidation(file_path, config) -> bool
"""

class BudgetResult(NamedTuple):
    status: str  # 'ok' | 'consolidate' | 'blocked'
    current_lines: int
    cap: int
    message: str
```

**File: `src/sio/applier/rollback.py` (lines 1-15)** — the undo button:
```python
"""sio.applier.rollback — revert applied changes.

Public API
----------
    rollback_change(db, change_id) -> dict
"""
```

### Interactive Elements

- [x] **Code↔English translation** — `_validate_target_path`. Right column: "This is the **kill switch**. `resolve()` turns any sneaky path (`../../etc/passwd`) into an absolute path. Then we check it's inside ~/.sio/, ~/.claude/, or your current working directory. If not, return an error string. The Applier WILL NOT write if this returns anything but None. **No matter how a suggestion's `target_file` got into the database, this seatbelt catches it before disk.**"
- [x] **Code↔English translation** — `apply_change` approval-status check. Right column: "Notice line 5: if the suggestion's status is not `approved` or `auto_approved`, the function bails. A suggestion in `pending` state cannot be applied even if you pass its ID directly. The human-in-the-loop gate is enforced in code, not just in the CLI."
- [x] **Data flow animation** — Required mandatory element. `data-steps='[...]'` JSON. 7 actors in sequence: **Suggestion (pending)** → **`sio suggest-review`** → **You (approve / reject / defer)** → **Suggestion (approved)** → **`sio apply <id>`** → **3 gates: (path validation, budget check, backup write)** → **CLAUDE.md (updated)** **+ backup file in ~/.sio/backups/**. Animate one packet from start to finish; pause at each gate with a tooltip explaining what it checks.
- [x] **Quiz — spot-the-bug** — Show this snippet: `path = Path(suggestion["target_file"]); path.write_text(new_content)`. Question: "What's missing?" Correct: path validation AND backup. Wrong-but-tempting: "exception handling" (real code does this too but isn't the safety-critical omission).
- [x] **Quiz — scenario** — "You ran `sio apply 142`. The output says `BLOCKED: instruction file at capacity`. What does SIO want you to do?" Correct: Run `sio dedupe` to find consolidation opportunities — the budget check refuses to add more rules until existing ones are merged. Reframe: SIO **prevents** unbounded growth of your CLAUDE.md.
- [x] **Quiz — multi-choice** — "Where are pre-apply backups stored?" (A) `/tmp` (B) Git stash (C) `~/.sio/backups/` with last-10 retention ✅ (D) Nowhere — they're discarded.
- [x] **Glossary tooltips** — "atomic write", "approval gate", "path traversal", "rollback", "diff", "budget cap", "consolidate", "auto_approved", "human-in-the-loop".

### Aha Callouts
1. **"Three gates, all enforced in code."** Human approval (status field), path validation (allowlist), atomic backup. Each gate has its own file. Even if one is wrong, the others still defend the system.
2. **"`sio apply` is reversible."** Every applied change writes a row to `applied_changes` with `diff_before` and `diff_after`. `sio rollback <change_id>` restores the file. You can experiment without fear.

### Reference Files to Read
- `references/interactive-elements.md` → Data Flow Animation, Multi-Choice Quizzes, Scenario Quizzes, Spot-the-Bug Quizzes, Code↔English Translation, Callout Boxes
- `references/design-system.md` → animation tokens
- `references/content-philosophy.md` → all of it
- `references/gotchas.md` → all of it

### Connections
- **Previous module:** "The DSPy Brain" — a rule has been generated and is sitting in the suggestions table.
- **Next module:** none — this is the closing module. End with a single concrete CTA: "Run `sio mine --since '24 hours'` against your own session history to see what patterns SIO finds." Don't preach. End on agency.
- **Tone/style notes:** Accent = teal. This module is about **trust**: the user is being asked to let SIO write to their config. Every code snippet should reinforce: "look how many checks happen before disk is touched."
