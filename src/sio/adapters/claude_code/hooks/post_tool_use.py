"""PostToolUse hook handler — captures telemetry from Claude Code tool calls."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from sio.core.paths import sio_home as _default_sio_home

logger = logging.getLogger(__name__)

from sio.core.constants import DEFAULT_PLATFORM as _DEFAULT_PLATFORM  # noqa: E402

_DEFAULT_DB_DIR = str(_default_sio_home() / _DEFAULT_PLATFORM)


def _extract_tool_result(payload: dict) -> tuple[str, str | None]:
    """Pull (output, error) out of a PostToolUse payload.

    Claude Code sends `tool_response`, NOT `tool_output`/`error`. Reading the wrong key
    meant auto_label() always saw output="" and error=None, so every one of the 55,348 rows
    captured by 2026-10-27 was labelled activated=1, correct_action=1, correct_outcome=0 —
    a corpus asserting that 99.99% of tool calls produced nothing.

    `tool_response` is a dict for Bash-like tools (stdout/stderr/exit_code/is_error) and a
    bare string for others. `tool_output`/`error` are still honoured so other harnesses and
    the existing contract tests keep working.
    """
    error = payload.get("error")
    resp = payload.get("tool_response")

    if resp is None:
        return payload.get("tool_output", ""), error
    if isinstance(resp, str):
        return resp, error
    if not isinstance(resp, dict):
        return str(resp), error

    out = next((resp[k] for k in ("stdout", "output", "content", "result")
                if resp.get(k) is not None), None)
    if out is None:
        # Unknown shape: keep it as text so "did this produce anything" stays answerable
        # instead of silently collapsing to the empty string.
        out = json.dumps(resp, ensure_ascii=False, default=str)
    elif not isinstance(out, str):
        out = json.dumps(out, ensure_ascii=False, default=str)

    if error is None:
        stderr = resp.get("stderr") or ""
        code = resp.get("exit_code", resp.get("return_code"))
        if resp.get("is_error"):
            error = stderr or "is_error"
        elif isinstance(code, int) and code != 0:
            error = stderr or f"exit_code={code}"
    return out, error


def handle_post_tool_use(stdin_json: str, *, conn=None) -> str:
    """Process a PostToolUse hook event.

    Parses the JSON payload, logs the invocation to the database,
    and always returns {"action": "allow"}.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.
        conn: Optional database connection (for testing). If None,
              creates a new connection to the default DB path.

    Returns:
        JSON string with {"action": "allow"}.
    """
    try:
        payload = json.loads(stdin_json)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"action": "allow"})

    try:
        from sio.adapters.claude_code.hooks._heartbeat import (  # noqa: PLC0415
            record_failure,
            record_success,
        )
        from sio.core.db.schema import init_db  # noqa: PLC0415
        from sio.core.telemetry.logger import log_invocation  # noqa: PLC0415

        own_conn = conn is None
        if own_conn:
            db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
            os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
            conn = init_db(db_path)

        session_id = payload.get("session_id", "unknown")
        tool_name = payload.get("tool_name", "unknown")
        tool_input = json.dumps(payload.get("tool_input", {}))
        tool_output, error = _extract_tool_result(payload)
        user_message = payload.get("user_message", "[UNAVAILABLE]")

        log_invocation(
            conn=conn,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            error=error,
            user_message=user_message,
            platform=_DEFAULT_PLATFORM,
            transcript_path=payload.get("transcript_path"),
        )

        # Passive signal detection (T043)
        _detect_passive_signals(conn, session_id, user_message)

        if own_conn:
            conn.close()

        record_success("post_tool_use", session_id=session_id)
    except Exception as exc:
        logger.exception("PostToolUse hook error — continuing silently")
        try:
            from sio.adapters.claude_code.hooks._heartbeat import record_failure  # noqa: PLC0415

            record_failure("post_tool_use", exc)
        except Exception:
            pass

    return json.dumps({"action": "allow"})


def _detect_passive_signals(conn, session_id, user_message):
    """Run passive signal detection and update previous invocation if needed."""
    try:
        from sio.core.telemetry.passive_signals import (
            detect_correction,
            detect_re_invocation,
            detect_undo,
        )

        signal = None
        if detect_correction(user_message):
            signal = "correction"
        elif detect_undo(
            session_id,
            datetime.now(timezone.utc).isoformat(),
            conn,
        ):
            signal = "undo"
        elif detect_re_invocation(session_id, user_message, conn):
            signal = "re_invocation"

        if signal:
            # Update the PREVIOUS invocation's passive_signal field
            rows = conn.execute(
                "SELECT id FROM behavior_invocations "
                "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 2",
                (session_id,),
            ).fetchall()
            if len(rows) >= 2:
                prev_id = rows[1]["id"]
                conn.execute(
                    "UPDATE behavior_invocations SET passive_signal = ? WHERE id = ?",
                    (signal, prev_id),
                )
                conn.commit()
    except Exception:
        logger.debug("Passive signal detection failed — non-critical")


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_post_tool_use(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
