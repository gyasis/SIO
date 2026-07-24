"""Tests for the OpenCode EXTRACT adapter (OpenCodeAdapter) + factory wiring.

Covers: get_events() yields normalised SessionEvents for one session's TEXT
parts in chronological order, skipping reasoning/tool parts;
manifest_from_handle("opencode:<id>") resolves against the SQLite store
(confirming the session actually exists); and a short get_live_stream() smoke
test (poll for an appended part, thread + timeout so it can't hang the suite).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SESSION_ID = "ses_06c3abcdef"
OTHER_SESSION_ID = "ses_deadbeef00"


def _make_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT, "
        "slug TEXT)"
    )
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, "
        "time_created INTEGER)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "data TEXT, time_created INTEGER)"
    )
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?)",
        (SESSION_ID, "/home/x/code/demo", "demo session", "demo-slug"),
    )
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?)",
        (OTHER_SESSION_ID, "/home/x/code/other", "other session", "other-slug"),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("msg_1", SESSION_ID, json.dumps({"role": "user"}), 1784900000000),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("msg_2", SESSION_ID, json.dumps({"role": "assistant"}), 1784900010000),
    )
    # A message belonging to a sibling session — must never leak in.
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("msg_3", OTHER_SESSION_ID, json.dumps({"role": "user"}), 1784900020000),
    )
    parts = [
        (
            "part_1",
            "msg_1",
            SESSION_ID,
            json.dumps({"type": "text", "text": "hello agent"}),
            1784900000500,
        ),
        (
            "part_2",
            "msg_2",
            SESSION_ID,
            json.dumps({"type": "reasoning", "text": "thinking..."}),
            1784900010200,
        ),
        (
            "part_3",
            "msg_2",
            SESSION_ID,
            json.dumps({"type": "text", "text": "hi there!"}),
            1784900010500,
        ),
        (
            "part_4",
            "msg_3",
            OTHER_SESSION_ID,
            json.dumps({"type": "text", "text": "unrelated session part"}),
            1784900020500,
        ),
    ]
    conn.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?)", parts)
    conn.commit()
    conn.close()


class TestOpenCodeAdapterGetEvents:
    def test_get_events_yields_only_text_parts_of_this_session_in_order(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.opencode.adapter import OpenCodeAdapter

        db_path = tmp_path / "opencode.db"
        _make_db(db_path)

        manifest = SessionManifest(
            agent="opencode", native_id=SESSION_ID, kind="db", path=str(db_path),
        )
        events = list(OpenCodeAdapter().get_events(manifest))
        assert [e.role for e in events] == ["user", "assistant"]
        assert events[0].content == "hello agent"
        assert events[1].content == "hi there!"  # reasoning part skipped
        assert all(e.ts.startswith("2026-") for e in events)
        assert all(e.tool is None for e in events)

    def test_missing_db_yields_nothing(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.opencode.adapter import OpenCodeAdapter

        manifest = SessionManifest(
            agent="opencode",
            native_id=SESSION_ID,
            kind="db",
            path=str(tmp_path / "no-such.db"),
        )
        assert list(OpenCodeAdapter().get_events(manifest)) == []


class TestFactoryWiring:
    def test_adapter_for_opencode_returns_opencode_adapter(self):
        from sio.adapters.factory import adapter_for
        from sio.adapters.opencode.adapter import OpenCodeAdapter

        adapter = adapter_for("opencode")
        assert isinstance(adapter, OpenCodeAdapter)
        assert adapter.agent == "opencode"

    def test_manifest_from_handle_resolves_existing_session(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        db_path = tmp_path / "opencode.db"
        _make_db(db_path)
        monkeypatch.setattr(factory, "_OPENCODE_DB", db_path)

        manifest = factory.manifest_from_handle(f"opencode:{SESSION_ID}")
        assert manifest is not None
        assert manifest.agent == "opencode"
        assert manifest.native_id == SESSION_ID
        assert manifest.path == str(db_path)
        assert manifest.kind == "db"

    def test_manifest_from_handle_missing_session_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        db_path = tmp_path / "opencode.db"
        _make_db(db_path)
        monkeypatch.setattr(factory, "_OPENCODE_DB", db_path)

        assert factory.manifest_from_handle("opencode:no-such-session") is None

    def test_manifest_from_handle_missing_db_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        monkeypatch.setattr(factory, "_OPENCODE_DB", tmp_path / "no-such.db")
        assert factory.manifest_from_handle(f"opencode:{SESSION_ID}") is None


class TestGetLiveStream:
    def test_live_stream_yields_appended_part(self, tmp_path):
        """Insert a new text part after the tailer starts; it must surface.

        Runs the tailer in a background thread with a short poll interval and
        a hard timeout so a regression (e.g. the tailer hanging) fails fast
        instead of hanging the suite.
        """
        from sio.adapters.base import SessionManifest
        from sio.adapters.opencode.adapter import OpenCodeAdapter

        db_path = tmp_path / "opencode.db"
        _make_db(db_path)

        manifest = SessionManifest(
            agent="opencode", native_id=SESSION_ID, kind="db", path=str(db_path),
        )
        adapter = OpenCodeAdapter()
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
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_live", SESSION_ID, json.dumps({"role": "user"}), 1784900999000),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (
                "part_live",
                "msg_live",
                SESSION_ID,
                json.dumps({"type": "text", "text": "new live message"}),
                1784900999500,
            ),
        )
        conn.commit()
        conn.close()

        t.join(timeout=5)
        assert not t.is_alive(), "get_live_stream did not surface the new part in time"
        assert len(seen) == 1
        assert seen[0].content == "new live message"
        assert seen[0].role == "user"
