"""OpenCode CLI session adapter — reads ~/.local/share/opencode/opencode.db.

OpenCode writes one row per turn into ``message``: an id, a session_id, and a
data JSON blob. That blob carries a top-level ``role``, plus ``time_created``
in epoch MILLISECONDS. The turn's rendered pieces go into ``part``, also as data JSON.
Only ``{"type":"text",...}`` parts hold searchable prose. Reasoning,
step-start and step-finish parts are noise.

This adapter extracts one session's text parts in order and returns normalised
:class:`SessionEvent` objects. It tails new parts by polling, because SQLite
raises no file-append signal to watch.

Tool parts carry the bash command and its output, so this adapter keeps them
as events too. That is where an error shows up. The part-to-event rules live
in :mod:`sio.adapters.opencode.parsing`, which the Paperclip adapter also
calls.

The store location takes configuration. Pass ``--opencode-dir`` (repeatable)
to ``sio mine --agent opencode``, or set ``SIO_OPENCODE_DIRS``, separated by
``os.pathsep``. A session id from any store but the default one takes the
prefix ``<8-hex store hash>:``, so two stores never collide.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from sio.adapters.base import SessionEvent, SessionManifest
from sio.adapters.opencode.parsing import SessionSignals, iso_ms, part_events, store_hash

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


_iso = iso_ms


def _part_obj(data_json: str | None) -> dict:
    """Decode ``part.data`` to a dict ({} when missing or malformed)."""
    if not data_json:
        return {}
    try:
        obj = json.loads(data_json)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _part_text(data_json: str | None) -> str:
    """Return the ``text`` of an opencode ``part.data`` block, else "".

    Serves callers that want searchable prose only.
    :func:`sio.adapters.opencode.parsing.part_events` covers tool parts.
    """
    obj = _part_obj(data_json)
    if obj.get("type") == "text":
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


_PREFIX_RE = re.compile(r"^([0-9a-f]{8}):(.+)$")


def resolve_db(path: str | Path) -> Path:
    """Map a user-supplied path to an ``opencode.db`` file.

    Accepts the db file itself, the data dir that holds it, or an XDG data
    root that holds ``opencode/opencode.db``.
    """
    p = Path(path).expanduser()
    if p.is_dir():
        for cand in (p / "opencode.db", p / "opencode" / "opencode.db"):
            if cand.exists():
                return cand
        return p / "opencode.db"
    return p


def configured_dbs(cli_dirs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """The OpenCode databases to mine.

    Explicit ``cli_dirs`` REPLACE the default; otherwise it is the default
    store plus every ``SIO_OPENCODE_DIRS`` entry. De-duplicated, order kept.
    """
    if cli_dirs:
        raw = [str(d) for d in cli_dirs]
    else:
        raw = [str(OPENCODE_DB)] + [
            d for d in os.environ.get("SIO_OPENCODE_DIRS", "").split(os.pathsep) if d.strip()
        ]
    seen: set[Path] = set()
    out: list[Path] = []
    for r in raw:
        db = resolve_db(r)
        key = db.resolve() if db.exists() else db
        if key not in seen:
            seen.add(key)
            out.append(db)
    return out


def _is_default(db_path: Path) -> bool:
    try:
        return db_path.resolve() == OPENCODE_DB.resolve()
    except OSError:
        return db_path == OPENCODE_DB


def native_id_for(db_path: Path, session_id: str) -> str:
    """Session id as SIO stores it: bare for the default store, else hash-prefixed."""
    return session_id if _is_default(db_path) else f"{store_hash(db_path)}:{session_id}"


def split_native_id(native_id: str) -> str:
    """The opencode-side session id inside an SIO native id."""
    m = _PREFIX_RE.match(native_id)
    return m.group(2) if m else native_id


def list_sessions(db_path: Path, cutoff_epoch: float | None = None) -> list[SessionManifest]:
    """Manifests for every session in ``db_path`` (newest activity >= cutoff)."""
    if not db_path.exists():
        return []
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError):
        return []
    try:
        rows = conn.execute(
            "SELECT s.id AS id, s.title AS title, s.directory AS directory, "
            "MAX(m.time_created) AS last_ms FROM session s "
            "LEFT JOIN message m ON m.session_id = s.id GROUP BY s.id"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        last_ms = r["last_ms"]
        if cutoff_epoch is not None and last_ms is not None and last_ms / 1000.0 < cutoff_epoch:
            continue
        out.append(
            SessionManifest(
                agent="opencode",
                native_id=native_id_for(db_path, r["id"]),
                kind="db",
                path=str(db_path),
                encoding="sqlite",
                meta={
                    "title": r["title"] or "",
                    "directory": r["directory"] or "",
                    "last_activity": iso_ms(last_ms),
                    "store": str(db_path),
                },
            )
        )
    return out


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
        """Yield every text and tool part of the session, oldest first."""
        db_path = Path(manifest.path) if manifest.path else OPENCODE_DB
        if not db_path.exists():
            return
        try:
            conn = _connect(db_path)
        except (sqlite3.Error, OSError):
            return
        signals = SessionSignals()
        try:
            for row in conn.execute(
                _GET_EVENTS_QUERY, (split_native_id(manifest.native_id),)
            ):
                yield from part_events(
                    _part_obj(row["part_data"]),
                    _iso(row["ts"]),
                    _message_role(row["message_data"]),
                    signals,
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
        """POLL the ``part`` table for new text/tool parts of this session.

        Starts at the current max ``time_created`` unless ``from_start``. Runs
        until the caller stops iterating; never raises out of the loop.
        """
        db_path = Path(manifest.path) if manifest.path else OPENCODE_DB
        sid = split_native_id(manifest.native_id)
        last_ts = 0 if from_start else _max_part_ts(db_path, sid)
        signals = SessionSignals()
        while True:
            try:
                conn = _connect(db_path)
            except (sqlite3.Error, OSError):
                time.sleep(poll_interval)
                continue
            try:
                for row in conn.execute(_LIVE_QUERY, (sid, last_ts)):
                    ts = row["ts"] or 0
                    if ts > last_ts:
                        last_ts = ts
                    yield from part_events(
                        _part_obj(row["part_data"]),
                        _iso(ts),
                        _message_role(row["message_data"]),
                        signals,
                    )
            except sqlite3.Error:
                pass
            finally:
                conn.close()
            time.sleep(poll_interval)
