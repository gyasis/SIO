"""Goose CLI session adapter — reads ~/.local/share/goose/sessions/sessions.db.

Goose (`goose session`) stores its transcripts in a single SQLite database
(WAL mode) rather than per-session files. This adapter EXTRACTs one session's
messages (chronological) into normalised :class:`SessionEvent` objects, and
tails NEW rows for that session by POLLING the ``messages`` table (SQLite has
no file-append signal to watch, unlike the JSONL-backed harnesses).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sio.adapters.base import SessionEvent, SessionManifest

GOOSE_DB = Path.home() / ".local" / "share" / "goose" / "sessions" / "sessions.db"


def _iso(epoch: float | int | None) -> str:
    """Convert goose's ``created_timestamp`` (epoch SECONDS) to ISO-8601 UTC."""
    if epoch is None:
        return ""
    try:
        value = float(epoch)
        if value > 1e12:  # tolerate a stray epoch-ms value
            value = value / 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _content_text(content_json: str | None) -> str:
    """Join the ``text`` of a goose ``content_json`` block array into one string."""
    if not content_json:
        return ""
    try:
        blocks = json.loads(content_json)
    except json.JSONDecodeError:
        return str(content_json)
    if isinstance(blocks, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in blocks
        )
    return str(blocks)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=0", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _max_timestamp(db_path: Path, session_id: str) -> float:
    """Highest ``created_timestamp`` already on disk for ``session_id``, or 0."""
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError):
        return 0.0
    try:
        row = conn.execute(
            "SELECT MAX(created_timestamp) AS m FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return float(row["m"]) if row and row["m"] is not None else 0.0
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()


class GooseAdapter:
    """EXTRACT events from one Goose session's messages (SQLite)."""

    agent = "goose"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every text-bearing message of ``manifest.native_id``, oldest first."""
        db_path = Path(manifest.path) if manifest.path else GOOSE_DB
        if not db_path.exists():
            return
        try:
            conn = _connect(db_path)
        except (sqlite3.Error, OSError):
            return
        try:
            cursor = conn.execute(
                "SELECT role, content_json, created_timestamp FROM messages "
                "WHERE session_id = ? ORDER BY created_timestamp ASC",
                (manifest.native_id,),
            )
            for row in cursor:
                text = _content_text(row["content_json"])
                if not text:
                    continue
                yield SessionEvent(
                    ts=_iso(row["created_timestamp"]),
                    role=row["role"] or "unknown",
                    content=text,
                    tool=None,
                    raw={"created_timestamp": row["created_timestamp"]},
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
        """POLL the ``messages`` table for new rows of this session.

        Starts at the current max ``created_timestamp`` unless ``from_start``.
        Runs until the caller stops iterating (e.g. KeyboardInterrupt). Never
        raises out of the loop — a transient DB error just delays the next poll.
        """
        db_path = Path(manifest.path) if manifest.path else GOOSE_DB
        last_ts = 0.0 if from_start else _max_timestamp(db_path, manifest.native_id)
        while True:
            try:
                conn = _connect(db_path)
            except (sqlite3.Error, OSError):
                time.sleep(poll_interval)
                continue
            try:
                cursor = conn.execute(
                    "SELECT role, content_json, created_timestamp FROM messages "
                    "WHERE session_id = ? AND created_timestamp > ? "
                    "ORDER BY created_timestamp ASC",
                    (manifest.native_id, last_ts),
                )
                for row in cursor:
                    ts = row["created_timestamp"] or 0
                    if ts > last_ts:
                        last_ts = ts
                    text = _content_text(row["content_json"])
                    if not text:
                        continue
                    yield SessionEvent(
                        ts=_iso(ts),
                        role=row["role"] or "unknown",
                        content=text,
                        tool=None,
                        raw={"created_timestamp": ts},
                    )
            except sqlite3.Error:
                pass
            finally:
                conn.close()
            time.sleep(poll_interval)
