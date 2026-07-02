"""Briefing store — the pre-computed session-start briefing, decoupled from sessions.

The session-start briefing (violations / declining rules / budget / pending /
stats / search-discipline) is expensive to compute because it scans the SIO DBs,
which have grown to hundreds of MB.  Computing it *on every session start* used
to block the interactive session for minutes.

This module is the fix: the briefing is materialised **off-session** (by the
scheduler / a systemd user timer / the passive-analysis pipeline) into a small
**store** file.  Every coding-agent adapter's session-start hook then just
*reads* the store — an instant file read, zero compute, for any agent (Claude
Code, Codex, Goose, …), not just one.

Public surface
--------------
    read_store()                 -> str      fast, never raises
    store_age()                  -> float | None
    store_is_fresh(ttl)          -> bool
    refresh_store(...)           -> str      compute + write (off-session path)

Env knobs
---------
    SIO_BRIEFING_TTL             freshness window in seconds (default 21600 = 6h)
    SIO_BRIEFING_BUILD_TIMEOUT   hard cap on a refresh, seconds (default 120)
    SIO_BRIEFING_STORE           override the store path (tests / alt layouts)
"""

from __future__ import annotations

import os
import time

_DEFAULT_TTL = 6 * 3600
# Off-session ceiling: this runs off-hours / on idle, so the cap only guards
# against a genuinely *hung* (infinite) compute, not normal runtime.  Phase-B
# rollup deltas make the real compute milliseconds, so this becomes moot.
_DEFAULT_BUILD_TIMEOUT = 15 * 60

_CACHE_DIR = os.path.expanduser("~/.sio/cache")
_DEFAULT_STORE = os.path.join(_CACHE_DIR, "session_briefing.txt")
_LOCK_FILE = os.path.join(_CACHE_DIR, "session_briefing.lock")


def store_path() -> str:
    """Path to the store file (honours ``SIO_BRIEFING_STORE`` override)."""
    return os.environ.get("SIO_BRIEFING_STORE", _DEFAULT_STORE)


def ttl_seconds() -> int:
    try:
        return max(0, int(os.environ.get("SIO_BRIEFING_TTL", _DEFAULT_TTL)))
    except ValueError:
        return _DEFAULT_TTL


def build_timeout_seconds() -> int:
    try:
        return max(1, int(os.environ.get("SIO_BRIEFING_BUILD_TIMEOUT", _DEFAULT_BUILD_TIMEOUT)))
    except ValueError:
        return _DEFAULT_BUILD_TIMEOUT


# --------------------------------------------------------------------------- #
# Read side — used by every adapter's session-start hook. Must never raise.
# --------------------------------------------------------------------------- #
def read_store() -> str:
    """Return the materialised briefing text, or "" if the store is absent/empty."""
    try:
        with open(store_path()) as f:
            return f.read().strip()
    except OSError:
        return ""


def store_age() -> float | None:
    """Seconds since the store was last written, or None if it does not exist."""
    try:
        return time.time() - os.path.getmtime(store_path())
    except OSError:
        return None


def store_is_fresh(ttl: int | None = None) -> bool:
    """True when the store exists and is younger than *ttl* (default env TTL)."""
    age = store_age()
    if age is None:
        return False
    return age < (ttl if ttl is not None else ttl_seconds())


# --------------------------------------------------------------------------- #
# Write side — the off-session path (scheduler / timer / pipeline / CLI).
# --------------------------------------------------------------------------- #
def _write_atomic(text: str) -> None:
    os.makedirs(os.path.dirname(store_path()), exist_ok=True)
    tmp = f"{store_path()}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, store_path())


def _touch() -> None:
    """Bump the store mtime (creating an empty store if needed) so a failed
    refresh backs off for a full TTL instead of retrying every trigger."""
    try:
        os.makedirs(os.path.dirname(store_path()), exist_ok=True)
        if os.path.exists(store_path()):
            os.utime(store_path(), None)
        else:
            _write_atomic("")
    except OSError:
        pass


def _refresh_in_flight() -> bool:
    try:
        age = time.time() - os.path.getmtime(_LOCK_FILE)
    except OSError:
        return False
    return age < (build_timeout_seconds() + 30)


def _compute_briefing(db_path: str, config) -> str:
    import sqlite3

    from sio.suggestions.consultant import build_session_briefing

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        text = build_session_briefing(conn, config=config)
    finally:
        conn.close()
    return text.strip() if text else ""


def refresh_store(
    *,
    db_path: str | None = None,
    config=None,
    timeout: int | None = None,
) -> str:
    """Recompute the briefing and write it to the store (the off-session path).

    Guarded by a lock (no piled-up refreshes) and a hard SIGALRM timeout so a
    pathological scan can never run unbounded.  Returns the briefing text that
    was written ("" if the DB is missing or the compute produced nothing).

    Intended to be called by the scheduler / systemd timer / passive-analysis
    pipeline — NOT from a session's hot path.
    """
    import signal

    if db_path is None:
        db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        return ""

    os.makedirs(_CACHE_DIR, exist_ok=True)

    # Acquire the lock; bail if a refresh is already running, else steal a stale one.
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        # Mutual exclusion: if a refresh is already running, never start a second
        # heavy compute — just return whatever is in the store.
        if _refresh_in_flight():
            return read_store()
        try:
            os.unlink(_LOCK_FILE)
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except OSError:
            return read_store()

    if config is None:
        from sio.core.config import load_config

        config = load_config()

    cap = timeout if timeout is not None else build_timeout_seconds()

    def _on_timeout(signum, frame):  # noqa: ANN001, ARG001
        raise TimeoutError("briefing refresh exceeded timeout")

    alarm_set = False
    try:
        try:
            signal.signal(signal.SIGALRM, _on_timeout)
            signal.alarm(cap)
            alarm_set = True
        except (ValueError, AttributeError):
            pass  # not the main thread / unsupported platform -> no hard cap

        text = _compute_briefing(db_path, config)
        _write_atomic(text)
        return text
    except TimeoutError:
        _log_error(f"briefing refresh timed out after {cap}s; keeping prior store")
        _touch()
        return read_store()
    except Exception as err:  # noqa: BLE001
        _log_error(f"briefing refresh failed: {err!r}")
        _touch()
        return read_store()
    finally:
        if alarm_set:
            try:
                signal.alarm(0)
            except (ValueError, AttributeError):
                pass
        try:
            os.unlink(_LOCK_FILE)
        except OSError:
            pass


def _log_error(msg: str) -> None:
    try:
        log = os.path.expanduser("~/.sio/hook_errors.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        from datetime import datetime, timezone

        with open(log, "a") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] briefing_store: {msg}\n")
    except Exception:
        pass
