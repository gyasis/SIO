"""Tests for the Goose EXTRACT adapter (GooseAdapter) + factory wiring.

Covers: get_events() yields normalised SessionEvents for one session's
messages in chronological order; manifest_from_handle("goose:<id>") resolves
against the SQLite store (confirming the session actually has rows); and a
short get_live_stream() smoke test (poll for an appended row, thread + timeout
so a regression can't hang the suite).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SESSION_ID = "20260724_1"
OTHER_SESSION_ID = "20260724_2"


def _make_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, working_dir TEXT, "
        "updated_at TIMESTAMP, name TEXT)"
    )
    conn.execute(
        "CREATE TABLE messages (session_id TEXT, role TEXT, content_json TEXT, "
        "created_timestamp INTEGER, message_id TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (SESSION_ID, "/home/x/code/demo", "2026-07-24T00:00:00Z", "demo session"),
    )
    rows = [
        (
            SESSION_ID,
            "user",
            json.dumps([{"type": "text", "text": "hello agent"}]),
            1784900000,
            "m1",
        ),
        (
            SESSION_ID,
            "assistant",
            json.dumps([{"type": "text", "text": "hi there!"}]),
            1784900010,
            "m2",
        ),
        # A sibling session — must never leak into the target session's events.
        (
            OTHER_SESSION_ID,
            "user",
            json.dumps([{"type": "text", "text": "unrelated session"}]),
            1784900020,
            "m3",
        ),
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content_json, created_timestamp, "
        "message_id) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestGooseAdapterGetEvents:
    def test_get_events_yields_only_this_session_in_order(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.goose.adapter import GooseAdapter

        db_path = tmp_path / "sessions.db"
        _make_db(db_path)

        manifest = SessionManifest(
            agent="goose", native_id=SESSION_ID, kind="db", path=str(db_path),
        )
        events = list(GooseAdapter().get_events(manifest))
        assert [e.role for e in events] == ["user", "assistant"]
        assert events[0].content == "hello agent"
        assert events[1].content == "hi there!"
        assert all(e.ts.startswith("2026-") for e in events)
        assert all(e.tool is None for e in events)

    def test_missing_db_yields_nothing(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.goose.adapter import GooseAdapter

        manifest = SessionManifest(
            agent="goose",
            native_id=SESSION_ID,
            kind="db",
            path=str(tmp_path / "no-such.db"),
        )
        assert list(GooseAdapter().get_events(manifest)) == []


class TestFactoryWiring:
    def test_adapter_for_goose_returns_goose_adapter(self):
        from sio.adapters.factory import adapter_for
        from sio.adapters.goose.adapter import GooseAdapter

        adapter = adapter_for("goose")
        assert isinstance(adapter, GooseAdapter)
        assert adapter.agent == "goose"

    def test_manifest_from_handle_resolves_existing_session(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        db_path = tmp_path / "sessions.db"
        _make_db(db_path)
        monkeypatch.setattr(factory, "_GOOSE_DB", db_path)

        manifest = factory.manifest_from_handle(f"goose:{SESSION_ID}")
        assert manifest is not None
        assert manifest.agent == "goose"
        assert manifest.native_id == SESSION_ID
        assert manifest.path == str(db_path)
        assert manifest.kind == "db"

    def test_manifest_from_handle_missing_session_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        db_path = tmp_path / "sessions.db"
        _make_db(db_path)
        monkeypatch.setattr(factory, "_GOOSE_DB", db_path)

        assert factory.manifest_from_handle("goose:no-such-session") is None

    def test_manifest_from_handle_missing_db_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        monkeypatch.setattr(factory, "_GOOSE_DB", tmp_path / "no-such.db")
        assert factory.manifest_from_handle(f"goose:{SESSION_ID}") is None


class TestGetLiveStream:
    def test_live_stream_yields_appended_row(self, tmp_path):
        """Insert a new message after the tailer starts; it must surface.

        Runs the tailer in a background thread with a short poll interval and
        a hard timeout so a regression (e.g. the tailer hanging) fails fast
        instead of hanging the suite.
        """
        from sio.adapters.base import SessionManifest
        from sio.adapters.goose.adapter import GooseAdapter

        db_path = tmp_path / "sessions.db"
        _make_db(db_path)

        manifest = SessionManifest(
            agent="goose", native_id=SESSION_ID, kind="db", path=str(db_path),
        )
        adapter = GooseAdapter()
        seen: list = []

        def _tail():
            for ev in adapter.get_live_stream(manifest, poll_interval=0.05):
                seen.append(ev)
                if seen:
                    return

        t = threading.Thread(target=_tail, daemon=True)
        t.start()
        time.sleep(0.15)  # let the tailer establish its starting watermark

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO messages (session_id, role, content_json, "
            "created_timestamp, message_id) VALUES (?, ?, ?, ?, ?)",
            (
                SESSION_ID,
                "user",
                json.dumps([{"type": "text", "text": "new live message"}]),
                1784900999,
                "m-live",
            ),
        )
        conn.commit()
        conn.close()

        t.join(timeout=5)
        assert not t.is_alive(), "get_live_stream did not surface the new row in time"
        assert len(seen) == 1
        assert seen[0].content == "new live message"
        assert seen[0].role == "user"
