"""PreCompact hook handler — captures session metrics snapshot before compaction."""

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

_ALLOW = json.dumps({"action": "allow"})


def _log_error(msg: str) -> None:
    """Append an error line to the hook error log file."""
    try:
        os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"[{ts}] PreCompact: {msg}\n")
    except Exception:
        pass  # Last-resort silence


def _do_snapshot(stdin_json: str, *, conn=None) -> None:
    """Core logic — snapshot session_metrics and recent positive signals.

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

    try:
        session_id = payload.get("session_id", "unknown")
        transcript_path = payload.get("transcript_path", "")
        now = datetime.now(timezone.utc).isoformat()

        # Count tool calls from behavior_invocations
        inv_row = conn.execute(
            "SELECT COUNT(*) as total "
            "FROM behavior_invocations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        tool_call_count = inv_row["total"] if inv_row else 0

        # Count errors from error_records
        err_row = conn.execute(
            "SELECT COUNT(*) as errors "
            "FROM error_records WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        error_count = err_row["errors"] if err_row else 0

        # Count positive signals already captured for this session
        try:
            pos_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM positive_records "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            positive_count = pos_row["cnt"] if pos_row else 0
        except Exception:
            positive_count = 0

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
    finally:
        if own_conn:
            conn.close()


def handle_pre_compact(stdin_json: str, *, conn=None) -> str:
    """Process a PreCompact hook event.

    Captures a session_metrics snapshot and recent positive signals.
    Implements retry-once-then-fail-silent with logging.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.
        conn: Optional database connection (for testing).

    Returns:
        JSON string with {"action": "allow"}.
    """
    _session_id: str | None = None
    try:
        import json as _json  # noqa: PLC0415
        _session_id = _json.loads(stdin_json).get("session_id")
    except Exception:
        pass

    try:
        _do_snapshot(stdin_json, conn=conn)
        try:
            from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415
            record_success("pre_compact", session_id=_session_id)
        except Exception:
            pass
    except Exception as first_err:
        # Retry once
        try:
            _do_snapshot(stdin_json, conn=conn)
            try:
                from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415
                record_success("pre_compact", session_id=_session_id)
            except Exception:
                pass
        except Exception as second_err:
            _log_error(f"retry failed: {first_err!r} -> {second_err!r}")
            try:
                from sio.adapters.claude_code.hooks._heartbeat import record_failure  # noqa: PLC0415
                record_failure("pre_compact", second_err)
            except Exception:
                pass

    return _ALLOW


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_pre_compact(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
