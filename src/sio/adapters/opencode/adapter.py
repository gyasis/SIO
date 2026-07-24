"""OpenCode CLI session adapter — reads ~/.local/share/opencode/opencode.db.

OpenCode stores one row per turn in ``message`` (id, session_id, data JSON
with a top-level ``role``, time_created epoch MILLISECONDS) and the turn's
rendered pieces in ``part`` (data JSON; only ``{"type":"text",...}`` parts are
searchable content — reasoning/step-start/step-finish/tool parts are noise).
This adapter EXTRACTs one session's text parts (chronological) into normalised
:class:`SessionEvent` objects, and tails NEW parts by POLLING (SQLite has no
file-append signal to watch).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sio.adapters.base import SessionEvent, SessionManifest

OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

_GET_EVENTS_QUERY = (
    "SELECT p.id AS part_id, p.data AS part_data, p.time_created AS ts, "
    "m.data AS message_data "
    "FROM message m JOIN part p ON p.message_id = m.id "
    "WHERE m.session_id = ? "
    "ORDER BY p.time_created ASC"
)

_LIVE_QUERY = (
    "SELECT p.id AS part_id, p.data AS part_data, p.time_created AS ts, "
    "m.data AS message_data "
    "FROM message m JOIN part p ON p.message_id = m.id "
    "WHERE m.session_id = ? AND p.time_created > ? "
    "ORDER BY p.time_created ASC"
)


def _iso(epoch_ms: float | int | None) -> str:
    """Convert opencode's ``time_created`` (epoch MILLISECONDS) to ISO-8601 UTC."""
    if epoch_ms is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _part_text(data_json: str | None) -> str:
    """Return the ``text`` of an opencode ``part.data`` block, else "".

    Only ``{"type":"text",...}`` parts carry searchable content.
    """
    if not data_json:
        return ""
    try:
        obj = json.loads(data_json)
    except json.JSONDecodeError:
        return ""
    if isinstance(obj, dict) and obj.get("type") == "text":
        return str(obj.get("text", "") or "")
    return ""


def _message_role(data_json: str | None) -> str:
    """Return the ``role`` of an opencode ``message.data`` blob, else "unknown"."""
    if not data_json:
        return "unknown"
    try:
        obj = json.loads(data_json)
    except json.JSONDecodeError:
        return "unknown"
    if isinstance(obj, dict):
        return str(obj.get("role") or "unknown")
    return "unknown"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=0", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _max_part_ts(db_path: Path, session_id: str) -> int:
    """Highest ``part.time_created`` already on disk for ``session_id``, or 0."""
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError):
        return 0
    try:
        row = conn.execute(
            "SELECT MAX(p.time_created) AS m FROM message m "
            "JOIN part p ON p.message_id = m.id WHERE m.session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


class OpenCodeAdapter:
    """EXTRACT events from one OpenCode session's text parts (SQLite)."""

    agent = "opencode"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every text part of ``manifest.native_id``, oldest first."""
        db_path = Path(manifest.path) if manifest.path else OPENCODE_DB
        if not db_path.exists():
            return
        try:
            conn = _connect(db_path)
        except (sqlite3.Error, OSError):
            return
        try:
            for row in conn.execute(_GET_EVENTS_QUERY, (manifest.native_id,)):
                text = _part_text(row["part_data"])
                if not text:
                    continue
                yield SessionEvent(
                    ts=_iso(row["ts"]),
                    role=_message_role(row["message_data"]),
                    content=text,
                    tool=None,
                    raw={"time_created": row["ts"]},
                )
        except sqlite3.Error:
            return
        finally:
            conn.close()

    def get_live_stream(
        self,
        manifest: SessionManifest,
        *,
        poll_interval: float = 1.0,
        from_start: bool = False,
    ) -> Iterator[SessionEvent]:
        """POLL the ``part`` table for new text parts of this session.

        Starts at the current max ``time_created`` unless ``from_start``. Runs
        until the caller stops iterating; never raises out of the loop.
        """
        db_path = Path(manifest.path) if manifest.path else OPENCODE_DB
        last_ts = 0 if from_start else _max_part_ts(db_path, manifest.native_id)
        while True:
            try:
                conn = _connect(db_path)
            except (sqlite3.Error, OSError):
                time.sleep(poll_interval)
                continue
            try:
                for row in conn.execute(_LIVE_QUERY, (manifest.native_id, last_ts)):
                    ts = row["ts"] or 0
                    if ts > last_ts:
                        last_ts = ts
                    text = _part_text(row["part_data"])
                    if not text:
                        continue
                    yield SessionEvent(
                        ts=_iso(ts),
                        role=_message_role(row["message_data"]),
                        content=text,
                        tool=None,
                        raw={"time_created": ts},
                    )
            except sqlite3.Error:
                pass
            finally:
                conn.close()
            time.sleep(poll_interval)
