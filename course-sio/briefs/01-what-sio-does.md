# Module 1: What SIO Does — Catching Your AI in the Act

### Teaching Arc
- **Metaphor:** A **flight data recorder** for your AI agent. After every flight (session), it pulls the black box, finds the moments your copilot stalled or veered off, and writes a new line in the flight manual so the same maneuver doesn't kill you twice.
- **Opening hook:** Your AI just told you `sed -i` was safe. It silently wiped your `.env` file. Last month it did the same thing on a different machine. Why does it keep forgetting?
- **Key insight:** SIO is a **closed feedback loop**: a tiny hook records every tool call your agent makes → SIO mines the failures → clusters them into patterns → writes a rule into your `CLAUDE.md` so the agent reads the rule next session and doesn't repeat the mistake. The loop only closes because *you* approve the rule before it's written.
- **"Why should I care?":** Stop typing the same correction every week. The rules you'd manually write get drafted for you, ranked by how often the pattern recurs.

### Code Snippets (pre-extracted)

**File: `src/sio/adapters/claude_code/hooks/post_tool_use.py` (lines 1-60)** — the hook that captures every tool call:
```python
"""PostToolUse hook handler — captures telemetry from Claude Code tool calls."""

from __future__ import annotations
import json, logging, os, sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from sio.core.constants import DEFAULT_PLATFORM as _DEFAULT_PLATFORM
_DEFAULT_DB_DIR = os.path.expanduser(f"~/.sio/{_DEFAULT_PLATFORM}")


def handle_post_tool_use(stdin_json: str, *, conn=None) -> str:
    """Process a PostToolUse hook event.

    Parses the JSON payload, logs the invocation to the database,
    and always returns {"action": "allow"}.
    """
    try:
        payload = json.loads(stdin_json)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"action": "allow"})

    own_conn = conn is None
    if own_conn:
        db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
        conn = init_db(db_path)

    session_id = payload.get("session_id", "unknown")
    tool_name  = payload.get("tool_name", "unknown")
    tool_input = json.dumps(payload.get("tool_input", {}))
    tool_output = payload.get("tool_output", "")
    error = payload.get("error")
    user_message = payload.get("user_message", "[UNAVAILABLE]")

    log_invocation(conn=conn, session_id=session_id, tool_name=tool_name,
                   tool_input=tool_input, tool_output=tool_output,
                   error=error, user_message=user_message,
                   platform=_DEFAULT_PLATFORM)

    return json.dumps({"action": "allow"})
```

**File: `README.md` (the pipeline flow)** — the stages the rest of the course will explore:
```
Sessions → Mine → Cluster → Rank → Suggest → Review → Apply
                                                  ↑
                                          (you approve here)
```

### Interactive Elements

- [x] **Code↔English translation** — `handle_post_tool_use`. Right column: "Every time your AI calls a tool, this little function copies the call details (which tool, what input, did it fail) into a SQLite file at `~/.sio/claude-code/behavior_invocations.db`. It NEVER blocks the AI — `return {"action": "allow"}` is unconditional. If the hook itself errors, it swallows the exception silently. SIO must never be the reason your agent stops working."
- [x] **Quiz** — 2 questions:
  - Q1 (scenario): "Your hook crashes mid-call. What happens to your AI agent?" Options: (A) Agent halts (B) Agent continues — hook is silent on failure ✅ (C) Tool call is retried (D) Claude switches models.
  - Q2 (tracing): "Where does this tool-call data eventually become a CLAUDE.md rule?" Show pipeline; correct answer = "After patterns are clustered and a suggestion is approved." Wrong answers force learner to trace the diagram.
- [x] **Group chat animation** — 4 actors: **You**, **Claude Agent**, **Hook (post_tool_use.py)**, **SIO DB (~/.sio/...)**. Sequence:
  1. You → Claude: "Run `sed -i 's/foo/bar/' .env`"
  2. Claude → Tool: *runs `sed -i`*
  3. Tool → Hook: payload with `error: ".env emptied"`
  4. Hook → SIO DB: `log_invocation(...)`
  5. Hook → Claude: `{"action": "allow"}`
  6. You → Claude: "no, you wiped my .env!" *(another packet for the DB)*
- [x] **Glossary tooltips** — "hook", "tool call", "SQLite", "telemetry", "CLAUDE.md", "feedback loop".

### Aha Callouts
1. **"SIO is a passive observer, not a guard."** Hooks never block. They only watch and write. The agent stays fast; learning happens offline.
2. **"Your CLAUDE.md is the agent's long-term memory."** Every session, the agent re-reads it. A rule written today is in effect tomorrow.

### Reference Files to Read
- `references/interactive-elements.md` → Group Chat Animation, Multiple-Choice Quizzes, Code↔English Translation, Callout Boxes, Glossary Tooltips
- `references/design-system.md` → typography + color tokens
- `references/content-philosophy.md` → all of it
- `references/gotchas.md` → all of it

### Connections
- **Previous module:** none — this is the opening.
- **Next module:** "Meet the Actors" — names every package in `src/sio/` and what role each plays.
- **Tone/style notes:** Accent color = **teal** (#2A7B9B). Use this color sparingly for emphasis. Actor naming: **Hook**, **Miner**, **Clusterer**, **Suggester**, **Reviewer**, **Applier** — capitalize first letter, use throughout the course.
