"""Stop hook handler — finalizes session metrics and saves high-confidence patterns."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from sio.core.constants import DEFAULT_PLATFORM as _DEFAULT_PLATFORM  # noqa: E402
_DEFAULT_DB_DIR = os.path.expanduser("~/.sio/claude-code")
_ERROR_LOG = os.path.expanduser("~/.sio/hook_errors.log")
_SKILLS_DIR = os.path.expanduser("~/.claude/skills/learned")

_ALLOW = json.dumps({"action": "allow"})


def _log_error(msg: str) -> None:
    """Append an error line to the hook error log file."""
    try:
        os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"[{ts}] Stop: {msg}\n")
    except Exception:
        pass


def _save_pattern_as_skill(pattern: dict) -> str | None:
    """Save a high-confidence pattern as a structured Claude Code skill file.

    Uses the skill_generator module to produce a complete skill with
    trigger conditions, ordered steps, guardrails, and provenance.

    Returns the file path on success, None on failure.
    """
    try:
        from sio.suggestions.skill_generator import (
            generate_skill_from_pattern,
            write_skill_file,
        )
    except ImportError:
        logger.warning("skill_generator not available, skipping skill save")
        return None

    label = pattern.get("label", "unknown-pattern")
    safe_label = (
        label.lower()
        .replace(" ", "-")
        .replace("/", "-")
        .replace(":", "-")[:60]
    )
    slug = f"pattern-{safe_label}"

    # Generate a full skill file from the pattern.
    # No positive examples or flow data available at stop-hook time,
    # so pass empty lists -- the generator handles this gracefully.
    content = generate_skill_from_pattern(
        pattern, positive_examples=[], flow_sequence=None
    )

    return write_skill_file(content, slug, target_dir=_SKILLS_DIR)


def _lightweight_pattern_detection(conn, session_id: str) -> list[dict]:
    """Run lightweight pattern detection on errors from this session.

    Groups errors by their error_text and calculates a confidence score
    based on frequency. Returns patterns with their metadata.
    """
    rows = conn.execute(
        "SELECT error_text, tool_name, COUNT(*) as cnt "
        "FROM error_records "
        "WHERE session_id = ? AND error_text IS NOT NULL "
        "AND error_text != '' "
        "GROUP BY error_text, tool_name "
        "ORDER BY cnt DESC "
        "LIMIT 20",
        (session_id,),
    ).fetchall()

    if not rows:
        return []

    total_errors = sum(r["cnt"] for r in rows)
    patterns = []
    for row in rows:
        count = row["cnt"]
        # Confidence = frequency ratio, boosted for repeated errors
        confidence = min(1.0, count / max(total_errors, 1) + 0.1 * (count - 1))

        # Count distinct sessions with this error for cross-session strength
        session_rows = conn.execute(
            "SELECT COUNT(DISTINCT session_id) as scnt "
            "FROM error_records "
            "WHERE error_text = ?",
            (row["error_text"],),
        ).fetchone()
        session_count = session_rows["scnt"] if session_rows else 1

        patterns.append({
            "label": row["error_text"][:100],
            "tool_name": row["tool_name"],
            "count": count,
            "confidence": round(confidence, 3),
            "example": row["error_text"],
            "session_count": session_count,
        })

    return patterns


def _do_finalize(stdin_json: str, *, conn=None) -> list[str]:
    """Core logic — finalize session and save high-confidence patterns.

    Returns list of saved skill file paths.
    Raises on failure so the caller can implement retry-once logic.
    """
    payload = json.loads(stdin_json)

    from sio.core.db.queries import insert_session_metrics_if_new
    from sio.core.db.schema import init_db

    own_conn = conn is None
    if own_conn:
        db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
        conn = init_db(db_path)

    saved_files: list[str] = []
    try:
        session_id = payload.get("session_id", "unknown")
        transcript_path = payload.get("transcript_path", "")
        now = datetime.now(timezone.utc).isoformat()

        # Gather session stats — tool calls from invocations, errors from error_records
        inv_row = conn.execute(
            "SELECT COUNT(*) as total "
            "FROM behavior_invocations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        tool_call_count = inv_row["total"] if inv_row else 0

        err_row = conn.execute(
            "SELECT COUNT(*) as errors "
            "FROM error_records WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        error_count = err_row["errors"] if err_row else 0

        # Count positive signals
        try:
            pos_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM positive_records "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            positive_count = pos_row["cnt"] if pos_row else 0
        except Exception:
            positive_count = 0

        # Finalize session_metrics with final counts
        record = {
            "session_id": session_id,
            "file_path": transcript_path,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_create_tokens": 0,
            "cache_hit_ratio": None,
            "total_cost_usd": 0,
            "session_duration_seconds": None,
            "message_count": 0,
            "tool_call_count": tool_call_count,
            "error_count": error_count,
            "correction_count": 0,
            "positive_signal_count": positive_count,
            "sidechain_count": 0,
            "stop_reason_distribution": None,
            "model_used": None,
            "mined_at": now,
        }
        insert_session_metrics_if_new(conn, record)

        # Lightweight pattern detection
        patterns = _lightweight_pattern_detection(conn, session_id)

        # Auto-save patterns with confidence > 0.8
        for pat in patterns:
            if pat["confidence"] > 0.8:
                fpath = _save_pattern_as_skill(pat)
                if fpath:
                    saved_files.append(fpath)

        # Mark session as processed
        conn.execute(
            "INSERT OR IGNORE INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (transcript_path, session_id, 0, tool_call_count, now),
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()

    return saved_files


def handle_stop(stdin_json: str, *, conn=None) -> str:
    """Process a Stop hook event.

    Finalizes session_metrics, runs lightweight pattern detection,
    and auto-saves high-confidence patterns to learned skills directory.
    Implements retry-once-then-fail-silent with logging.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.
        conn: Optional database connection (for testing).

    Returns:
        JSON string — no output required for Stop hook, but we
        return empty string for consistency. Exit code is always 0.
    """
    _session_id: str | None = None
    try:
        import json as _json  # noqa: PLC0415
        _session_id = _json.loads(stdin_json).get("session_id")
    except Exception:
        pass

    try:
        _do_finalize(stdin_json, conn=conn)
        try:
            from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415
            record_success("stop", session_id=_session_id)
        except Exception:
            pass
    except Exception as first_err:
        # Retry once
        try:
            _do_finalize(stdin_json, conn=conn)
            try:
                from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415
                record_success("stop", session_id=_session_id)
            except Exception:
                pass
        except Exception as second_err:
            _log_error(f"retry failed: {first_err!r} -> {second_err!r}")
            try:
                from sio.adapters.claude_code.hooks._heartbeat import record_failure  # noqa: PLC0415
                record_failure("stop", second_err)
            except Exception:
                pass

    return _ALLOW


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_stop(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
