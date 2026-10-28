"""Telemetry logger — writes invocation records to the behavior database."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from sio.core.db.queries import insert_invocation
from sio.core.session_handle import ensure_canonical
from sio.core.telemetry.auto_labeler import auto_label
from sio.core.telemetry.secret_scrubber import scrub

logger = logging.getLogger(__name__)


def _behavior_type_for(tool_name: str) -> str:
    """Map a tool call to a legal `behavior_type`.

    Every row was hardcoded "skill", so 46,322 Bash calls on razer were filed as skills and
    any analysis grouping by behavior_type saw exactly one group. `'tool'` became a legal
    CHECK value in schema 007 (`migrate_007_behavior_type_tool`), so a plain tool call now
    has somewhere correct to go:

    - `mcp__*`  -> `mcp_tool`
    - `Skill`   -> `skill`, the only tool that actually invokes one
    - anything else (Bash, Read, Edit, Write, Agent, WebSearch, ...) -> `tool`

    A DB that has not run the 007 migration rejects `'tool'` on the CHECK and this function
    would swallow the IntegrityError, so the migration runs from `init_db` -- which every
    hook calls -- as well as from `bootstrap.ensure_db`.
    """
    if tool_name and tool_name.startswith("mcp__"):
        return "mcp_tool"
    if tool_name == "Skill":
        return "skill"
    return "tool"


def log_invocation(
    conn: sqlite3.Connection,
    session_id: str,
    tool_name: str,
    tool_input: str,
    tool_output: str | None,
    error: str | None,
    user_message: str,
    platform: str,
    transcript_path: str | None = None,
) -> int:
    """Log a single tool invocation to the behavior database.

    Scrubs secrets from user_message, applies auto-labeling, and handles
    deduplication and error resilience.

    Returns:
        Row id on success, -1 on error.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        scrubbed_message = scrub(user_message) if user_message else user_message
        # Canonicalize once: insert_invocation stores the canonical
        # <agent>:<id> form, so the dedup lookup below MUST query the same
        # canonical value or it can never match its own prior writes.
        canonical_session_id = ensure_canonical(session_id)

        # Deduplication: check for existing record within 1s window
        existing = conn.execute(
            "SELECT id FROM behavior_invocations "
            "WHERE session_id = ? AND actual_action = ? "
            "AND ABS(JULIANDAY(timestamp) - JULIANDAY(?)) < (1.0 / 86400.0)",
            (canonical_session_id, tool_name, now),
        ).fetchone()
        if existing:
            return existing[0]

        # Auto-label agent-inferred fields
        labels = auto_label(tool_name, tool_input, tool_output, error)

        # Audit Round 2 N-R2D.2: tool_name / tool_input / conversation_pointer
        # MUST be populated. The UNIQUE ix_bi_identity index depends on
        # tool_name being non-NULL for INSERT OR IGNORE dedup to work across
        # sync and re-ingest. Previously only actual_action carried tool_name.
        record = {
            "session_id": canonical_session_id,
            "timestamp": now,
            "platform": platform,
            "user_message": scrubbed_message,
            "behavior_type": _behavior_type_for(tool_name),
            "actual_action": tool_name,
            "expected_action": None,
            "activated": labels["activated"],
            "correct_action": labels["correct_action"],
            "correct_outcome": labels["correct_outcome"],
            "user_satisfied": None,
            "user_note": None,
            "passive_signal": None,
            "history_file": transcript_path or None,
            "line_start": None,
            "line_end": None,
            "token_count": None,
            "latency_ms": None,
            "labeled_by": None,
            "labeled_at": None,
            "tool_name": tool_name,
            "tool_input": tool_input,
            # The evidence pointer: without it no row can be walked back to the
            # transcript that produced it. Was hardcoded None despite the audit note above.
            "conversation_pointer": transcript_path or None,
        }

        return insert_invocation(conn, record)

    except Exception:
        logger.exception("Failed to log invocation")
        return -1
