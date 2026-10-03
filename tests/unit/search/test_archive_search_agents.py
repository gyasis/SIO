"""sio search reads every archived agent's sessions, including ones the harness deleted.

Each test builds a live store under a fake $HOME, runs the real ``sio archive sync``
into the per-test archive, deletes part of the live store, then searches. A session
that still exists live must be reported once (from the live copy); a deleted one
must be reported once, from the archive, tagged ``archived``.

Fixtures are SYNTHETIC — never copies of real transcripts.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import sio.search.cli as _cli
from sio.archive import sync as arch

NEEDLE = "ARCHNEEDLE"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(_cli, "HOME", h)
    monkeypatch.setattr(_cli, "GOOSE_DB", h / ".local/share/goose/sessions/sessions.db")
    monkeypatch.setattr(_cli, "OPENCODE_DB", h / ".local/share/opencode/opencode.db")
    return h


def _write_lines(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _hits(agent: str) -> list:
    return list(_cli.search_parser(agent)(NEEDLE, False, None))


def _by_source(hits: list) -> dict[str, bool]:
    return {Path(h.source_path).name: bool(h.metadata.get("archived")) for h in hits}


def _pi(path: Path, text: str) -> None:
    _write_lines(path, [{"type": "message", "id": "e1", "timestamp": "2026-06-01T00:00:00Z",
                         "message": {"role": "user",
                                     "content": [{"type": "text", "text": text}]}}])


def test_pi_deleted_session_found_once(home):
    d = home / ".pi/agent/sessions/--proj--"
    _pi(d / "kept.jsonl", f"{NEEDLE} kept")
    _pi(d / "gone.jsonl", f"{NEEDLE} gone")
    arch.sync(home=home)
    (d / "gone.jsonl").unlink()
    hits = _hits("pi")
    assert len(hits) == 2
    assert _by_source(hits) == {"kept.jsonl": False, "gone.jsonl": True}


def test_kimi_deleted_session_found_once(home):
    root = home / ".kimi-code/sessions/wd_x"
    for name in ("session_kept", "session_gone"):
        _write_lines(root / name / "agents/main/wire.jsonl",
                     [{"type": "turn.prompt", "time": 1780000000000,
                       "input": [{"type": "text", "text": f"{NEEDLE} {name}"}]}])
    arch.sync(home=home)
    (root / "session_gone/agents/main/wire.jsonl").unlink()
    hits = _hits("kimi")
    assert sorted((h.session_id, bool(h.metadata.get("archived"))) for h in hits) == [
        ("session_gone", True), ("session_kept", False)]


def test_gemini_and_promptchain_deleted_sessions(home):
    chats = home / ".gemini/tmp/hash/chats"
    chats.mkdir(parents=True)
    for name in ("session-kept.json", "session-gone.json"):
        (chats / name).write_text(json.dumps(
            {"sessionId": name, "messages": [{"type": "user", "content": f"{NEEDLE}",
                                             "timestamp": "2026-06-01T00:00:00Z"}]}))
    pc = home / ".promptchain/sessions"
    for uuid in ("u-kept", "u-gone"):
        _write_lines(pc / uuid / "messages.jsonl",
                     [{"role": "user", "content": f"{NEEDLE} {uuid}", "timestamp": "1780000000"}])
    arch.sync(home=home)
    (chats / "session-gone.json").unlink()
    (pc / "u-gone/messages.jsonl").unlink()

    gem = _hits("gemini")
    assert _by_source(gem) == {"session-kept.json": False, "session-gone.json": True}
    assert len(gem) == 2
    pch = _hits("promptchain")
    assert sorted((h.session_id, bool(h.metadata.get("archived"))) for h in pch) == [
        ("u-gone", True), ("u-kept", False)]


def test_codex_shared_history_not_duplicated(home):
    codex = home / ".codex"
    _write_lines(codex / "history.jsonl",
                 [{"session_id": "s1", "ts": 1780000000, "text": f"{NEEDLE} typed"}])
    (codex / "sessions").mkdir()
    (codex / "sessions/rollout-gone.json").write_text(json.dumps({"note": NEEDLE}))
    arch.sync(home=home)
    (codex / "sessions/rollout-gone.json").unlink()
    hits = _hits("codex")
    # history.jsonl exists in both places -> once, live; the deleted rollout -> archive
    assert _by_source(hits) == {"history.jsonl": False, "rollout-gone.json": True}
    assert len(hits) == 2


def test_goose_row_deleted_from_live_db_found_in_archive(home):
    db = home / ".local/share/goose/sessions/sessions.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content_json TEXT, "
                 "created_timestamp INTEGER, message_id TEXT)")
    for i, sid in enumerate(("kept", "gone")):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                     (sid, "user", json.dumps([{"type": "text", "text": f"{NEEDLE} {sid}"}]),
                      1780000000 + i, f"m{i}"))
    conn.commit()
    arch.sync(home=home)
    conn.execute("DELETE FROM messages WHERE session_id='gone'")
    conn.commit()
    conn.close()
    hits = _hits("goose")
    assert sorted((h.session_id, bool(h.metadata.get("archived"))) for h in hits) == [
        ("gone", True), ("kept", False)]


def test_no_archive_yet_is_harmless(home):
    _pi(home / ".pi/agent/sessions/--p--/s.jsonl", NEEDLE)
    hits = _hits("pi")
    assert len(hits) == 1 and not hits[0].metadata.get("archived")


def test_path_mapping_round_trips(home):
    live = home / ".pi/agent/sessions/--p--/s.jsonl"
    archived = arch.archive_path_for(live, home=home)
    assert archived == arch.archive_root() / "pi/sessions/--p--/s.jsonl"
    assert arch.live_path_for(archived, home=home) == live
    assert arch.archive_path_for(home / "not-a-store/x", home=home) is None
