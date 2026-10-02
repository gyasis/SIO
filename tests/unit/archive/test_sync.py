"""sio archive sync: copies session stores, never deletes, versions rewrites."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from sio.archive import sync as arch


@pytest.fixture()
def env(tmp_path: Path):
    home = tmp_path / "home"
    root = tmp_path / "archive"
    store = home / "store"
    store.mkdir(parents=True)
    sources = [("agent/store", "store"), ("agent/missing", "nope")]
    return home, root, store, sources


def _run(env):
    home, root, _, sources = env
    return arch.sync(home=home, root=root, sources=sources)


def _counts(status, name="agent/store"):
    return next(s for s in status["sources"] if s["name"] == name)["counts"]


def test_new_file_is_copied_byte_for_byte(env):
    _, root, store, _ = env
    (store / "a" / "s1.jsonl").parent.mkdir()
    (store / "a" / "s1.jsonl").write_text('{"n":1}\n')
    status = _run(env)
    assert (root / "agent/store/a/s1.jsonl").read_text() == '{"n":1}\n'
    assert _counts(status) == {"new": 1}
    assert status["ok"] is True


def test_second_run_is_unchanged(env):
    _, _, store, _ = env
    (store / "s.jsonl").write_text("x\n")
    _run(env)
    assert _counts(_run(env)) == {"unchanged": 1}


def test_appended_file_updates_archive(env):
    _, root, store, _ = env
    f = store / "s.jsonl"
    f.write_text("line1\n")
    _run(env)
    with f.open("a") as fh:
        fh.write("line2\n")
    status = _run(env)
    assert _counts(status) == {"appended": 1}
    assert (root / "agent/store/s.jsonl").read_text() == "line1\nline2\n"
    assert not list((root / "agent/store").glob("s.jsonl.~*"))


def test_shrunk_file_keeps_old_copy_as_version(env):
    _, root, store, _ = env
    f = store / "s.jsonl"
    f.write_text("a long original transcript\n")
    _run(env)
    f.write_text("short\n")
    status = _run(env)
    assert _counts(status) == {"rewritten": 1}
    assert (root / "agent/store/s.jsonl").read_text() == "short\n"
    versions = list((root / "agent/store").glob("s.jsonl.~*"))
    assert len(versions) == 1
    assert versions[0].read_text() == "a long original transcript\n"


def test_same_size_rewrite_is_detected(env):
    _, root, store, _ = env
    f = store / "s.jsonl"
    f.write_text("aaaa\n")
    _run(env)
    f.write_text("bbbb\n")
    assert _counts(_run(env)) == {"rewritten": 1}
    assert (root / "agent/store/s.jsonl").read_text() == "bbbb\n"


def test_deleted_source_keeps_archive_and_is_marked_gone(env):
    _, root, store, _ = env
    f = store / "s.jsonl"
    f.write_text("keep me\n")
    _run(env)
    f.unlink()
    status = _run(env)
    assert (root / "agent/store/s.jsonl").read_text() == "keep me\n"
    assert _counts(status) == {"source_gone": 1}
    gone = sqlite3.connect(root / "_archive.db").execute(
        "SELECT source_gone_at FROM files WHERE rel='s.jsonl'"
    ).fetchone()[0]
    assert gone is not None


def test_reappearing_file_clears_gone_mark(env):
    _, root, store, _ = env
    f = store / "s.jsonl"
    f.write_text("x\n")
    _run(env)
    f.unlink()
    _run(env)
    f.write_text("x\n")
    _run(env)
    gone = sqlite3.connect(root / "_archive.db").execute(
        "SELECT source_gone_at FROM files WHERE rel='s.jsonl'"
    ).fetchone()[0]
    assert gone is None


def test_absent_source_is_reported_not_error(env):
    status = _run(env)
    missing = next(s for s in status["sources"] if s["name"] == "agent/missing")
    assert missing["state"] == "absent"
    assert status["ok"] is True


def test_single_file_source(tmp_path: Path):
    home, root = tmp_path / "home", tmp_path / "archive"
    home.mkdir()
    (home / "history.jsonl").write_text("p1\n")
    arch.sync(home=home, root=root, sources=[("claude/history.jsonl", "history.jsonl")])
    assert (root / "claude/history.jsonl").read_text() == "p1\n"


def test_sqlite_is_backed_up_including_wal_and_sidecars_skipped(env):
    _, root, store, _ = env
    db = store / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE m (t TEXT)")
    conn.execute("INSERT INTO m VALUES ('hello')")
    conn.commit()  # row lives in the -wal file; connection stays open
    try:
        assert (store / "sessions.db-wal").exists()
        status = _run(env)
    finally:
        conn.close()
    archived = sqlite3.connect(root / "agent/store/sessions.db")
    assert archived.execute("SELECT t FROM m").fetchall() == [("hello",)]
    assert _counts(status) == {"sqlite": 1}
    assert not (root / "agent/store/sessions.db-wal").exists()


def test_unreadable_file_fails_loud(env):
    _, _, store, _ = env
    f = store / "s.jsonl"
    f.write_text("x\n")
    os.chmod(f, 0)
    try:
        if os.access(f, os.R_OK):
            pytest.skip("running as root; permissions not enforced")
        status = _run(env)
    finally:
        os.chmod(f, 0o644)
    src = next(s for s in status["sources"] if s["name"] == "agent/store")
    assert src["state"] == "error" and src["error_count"] == 1
    assert status["ok"] is False


def test_status_reads_manifest(env):
    _, root, store, _ = env
    (store / "s.jsonl").write_text("x\n")
    _run(env)
    info = arch.status(root)
    assert info["manifest"] == [
        {"agent": "agent/store", "files": 1, "source_gone": 0, "versions": 0}
    ]
