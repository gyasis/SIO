"""UserPromptSubmit hook handler — detects corrections, undos, and frustration."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_ERROR_LOG = os.path.expanduser("~/.sio/hook_errors.log")
_SESSION_STATE_PATH = os.path.expanduser("~/.sio/session_state.json")

# Search-feedback capture ("you must" narrowing loop) --------------------------
# When the user's prompt is an immediate correction of a just-run `sio search`
# (e.g. "you must narrow that", "too broad", "no, refine to X"), record it as a
# labeled dissatisfaction signal on THAT search's behavior_invocations row —
# populating the otherwise-empty user_note / user_satisfied / correct_action
# columns. This is the substrate the discipline/learning layer can use to learn
# which broad searches should have been narrowed. Best-effort: never blocks the
# hook, never raises out.
_FEEDBACK_WINDOW_SEC = 900  # only label a search corrected within 15 min

# Imperative "narrow it" signals, on TOP of the generic correction/undo signals.
_SEARCH_NARROW_KEYWORDS = (
    "narrow",
    "too broad",
    "too wide",
    "too many",
    "too much",
    "more specific",
    "be specific",
    "refine",
    "filter",
    "you must",
    "you should have",
    "you need to",
    "next time",
    "not multi-hop",
    "multi-hop",
    "multihop",
    "expand",
    "contract",
    "alternation",
)

_ALLOW = json.dumps({"action": "allow"})

# Correction/undo keywords — reused from passive_signals and sentiment_scorer
_CORRECTION_PREFIXES = (
    "no,",
    "no ",
    "actually,",
    "actually ",
    "instead,",
    "instead ",
    "wait,",
    "wait ",
    "stop,",
    "stop ",
    "that's wrong",
    "that is wrong",
    "not what i",
    "don't do",
    "do not do",
    "undo ",
    "revert ",
)

_UNDO_KEYWORDS = (
    "undo",
    "revert",
    "rollback",
    "roll back",
    "go back",
    "restore",
    "put it back",
    "change it back",
)


def _log_error(msg: str) -> None:
    """Append an error line to the hook error log file."""
    try:
        os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"[{ts}] UserPromptSubmit: {msg}\n")
    except Exception:
        pass


def _load_session_state() -> dict:
    """Load the session state JSON file."""
    if os.path.exists(_SESSION_STATE_PATH):
        try:
            with open(_SESSION_STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_session_state(state: dict) -> None:
    """Save the session state JSON file."""
    os.makedirs(os.path.dirname(_SESSION_STATE_PATH), exist_ok=True)
    with open(_SESSION_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _detect_correction_or_undo(message: str) -> str | None:
    """Detect if the message contains a correction or undo request.

    Returns "correction", "undo", or None.
    """
    if not message:
        return None

    lower = message.strip().lower()

    # Check for undo keywords first (more specific)
    for keyword in _UNDO_KEYWORDS:
        if keyword in lower:
            return "undo"

    # Check for correction prefixes
    for prefix in _CORRECTION_PREFIXES:
        if lower.startswith(prefix):
            return "correction"

    return None


def _invocations_db_path() -> str:
    """behavior_invocations.db path (respects SIO_INVOCATIONS_DB_PATH for tests)."""
    override = os.environ.get("SIO_INVOCATIONS_DB_PATH")
    if override:
        return override
    from sio.core.constants import DEFAULT_PLATFORM  # noqa: PLC0415

    return os.path.expanduser(f"~/.sio/{DEFAULT_PLATFORM}/behavior_invocations.db")


def _is_search_command(command: str) -> bool:
    """True if a Bash command is a session-search / sio search invocation."""
    return "session-search" in command or "sio search" in command


def _detect_search_feedback(message: str) -> bool:
    """True if the message reads as feedback that a prior search was too broad.

    Fires on any generic correction/undo OR an explicit "narrow it" keyword.
    The 'must have followed a search' gate (applied by the caller) keeps this
    precise — a bare correction only counts when a search actually preceded it.
    """
    if not message:
        return False
    lower = message.strip().lower()
    if _detect_correction_or_undo(message) is not None:
        return True
    return any(kw in lower for kw in _SEARCH_NARROW_KEYWORDS)


def _capture_search_feedback(
    session_id: str,
    message: str,
    *,
    db_path: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Label the most recent search invocation for *session_id* as corrected.

    Writes user_note / user_satisfied=0 / correct_action=0 on that row when the
    user's prompt is narrowing feedback AND a search ran within the feedback
    window. Returns True if a row was labeled. Best-effort — swallows DB errors.
    """
    if not _detect_search_feedback(message):
        return False

    path = db_path or _invocations_db_path()
    if not os.path.exists(path):
        return False

    now = now or datetime.now(timezone.utc)
    cutoff_iso = (now - timedelta(seconds=_FEEDBACK_WINDOW_SEC)).isoformat()

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        conn.row_factory = sqlite3.Row
        # Most recent search row for this session, within the feedback window.
        rows = conn.execute(
            "SELECT id, tool_input FROM behavior_invocations "
            "WHERE session_id = ? AND tool_name = 'Bash' AND timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT 25",
            (session_id, cutoff_iso),
        ).fetchall()
        target_id: int | None = None
        for row in rows:
            raw = row["tool_input"]
            try:
                cmd = json.loads(raw).get("command", "") if raw else ""
            except (json.JSONDecodeError, TypeError):
                cmd = str(raw or "")
            if _is_search_command(cmd):
                target_id = row["id"]
                break
        if target_id is None:
            return False

        note = f"[search-narrow-feedback] {message.strip()}"[:500]
        conn.execute(
            "UPDATE behavior_invocations "
            "SET user_satisfied = 0, correct_action = 0, user_note = ?, "
            "labeled_by = 'search_feedback_hook', labeled_at = ? "
            "WHERE id = ?",
            (note, now.isoformat(), target_id),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()


def _do_analyze(stdin_json: str, *, state_path: str | None = None) -> None:
    """Core logic — detect corrections and frustration.

    Raises on failure so the caller can implement retry-once logic.
    """
    effective_path = state_path or _SESSION_STATE_PATH

    payload = json.loads(stdin_json)
    session_id = payload.get("session_id", "unknown")
    user_message = payload.get("user_message", "")

    if not user_message.strip():
        return

    # Load session state
    if os.path.exists(effective_path):
        try:
            with open(effective_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            state = {}
    else:
        state = {}

    sessions = state.setdefault("sessions", {})
    sess = sessions.setdefault(
        session_id,
        {
            "correction_count": 0,
            "undo_count": 0,
            "negative_streak": 0,
            "frustration_logged": False,
            "recent_scores": [],
        },
    )

    # Detect correction or undo
    signal = _detect_correction_or_undo(user_message)
    if signal == "correction":
        sess["correction_count"] = sess.get("correction_count", 0) + 1
    elif signal == "undo":
        sess["undo_count"] = sess.get("undo_count", 0) + 1

    # "You must" narrowing loop: if this prompt is narrowing feedback on a
    # search that just ran, label that search row. Best-effort, never fatal.
    try:
        if _capture_search_feedback(session_id, user_message):
            sess["search_feedback_count"] = sess.get("search_feedback_count", 0) + 1
    except Exception:
        pass

    # Lightweight sentiment check for frustration tracking
    from sio.mining.sentiment_scorer import score_sentiment

    score = score_sentiment(user_message)
    recent = sess.get("recent_scores", [])
    recent.append(score)
    # Keep only last 10 scores
    sess["recent_scores"] = recent[-10:]

    # Track negative streak for frustration detection
    if score < 0:
        sess["negative_streak"] = sess.get("negative_streak", 0) + 1
    else:
        sess["negative_streak"] = 0

    # Detect frustration: 3+ consecutive negative messages
    if sess["negative_streak"] >= 3 and not sess.get("frustration_logged"):
        sess["frustration_logged"] = True
        _log_frustration(session_id, sess)

    # Save state
    state["sessions"] = sessions
    parent = os.path.dirname(effective_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(effective_path, "w") as f:
        json.dump(state, f, indent=2)


def _log_frustration(session_id: str, sess: dict) -> None:
    """Log a frustration warning to the hook error log."""
    corrections = sess.get("correction_count", 0)
    undos = sess.get("undo_count", 0)
    streak = sess.get("negative_streak", 0)
    msg = (
        f"Frustration escalation detected for session {session_id}: "
        f"negative_streak={streak}, corrections={corrections}, undos={undos}"
    )
    try:
        os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"[{ts}] UserPromptSubmit FRUSTRATION: {msg}\n")
    except Exception:
        pass


def handle_user_prompt_submit(
    stdin_json: str,
    *,
    state_path: str | None = None,
) -> str:
    """Process a UserPromptSubmit hook event.

    Detects corrections/undos in the user message, increments the
    session correction counter, and logs frustration escalation.
    Must complete in <2000ms. Implements retry-once-then-fail-silent.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.
        state_path: Optional override for session_state.json path (testing).

    Returns:
        JSON string with {"action": "allow"}.
    """
    _session_id: str | None = None
    try:
        _session_id = json.loads(stdin_json).get("session_id")
    except Exception:
        pass

    try:
        _do_analyze(stdin_json, state_path=state_path)
        try:
            from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415

            record_success("user_prompt_submit", session_id=_session_id)
        except Exception:
            pass
    except Exception as first_err:
        # Retry once
        try:
            _do_analyze(stdin_json, state_path=state_path)
            try:
                from sio.adapters.claude_code.hooks._heartbeat import (
                    record_success,  # noqa: PLC0415
                )

                record_success("user_prompt_submit", session_id=_session_id)
            except Exception:
                pass
        except Exception as second_err:
            _log_error(f"retry failed: {first_err!r} -> {second_err!r}")
            try:
                from sio.adapters.claude_code.hooks._heartbeat import (
                    record_failure,  # noqa: PLC0415
                )

                record_failure("user_prompt_submit", second_err)
            except Exception:
                pass

    return _ALLOW


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_user_prompt_submit(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
