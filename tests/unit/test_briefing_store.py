"""Unit tests for the off-session briefing store."""

from __future__ import annotations

import time


def _mod(monkeypatch, tmp_path):
    """Import briefing_store with cache/lock paths redirected into tmp."""
    from sio.suggestions import briefing_store as bs

    monkeypatch.setenv("SIO_BRIEFING_STORE", str(tmp_path / "brief.txt"))
    monkeypatch.setattr(bs, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(bs, "_LOCK_FILE", str(tmp_path / "brief.lock"))
    return bs


class TestReadSide:
    def test_missing_store_reads_empty(self, tmp_path, monkeypatch):
        bs = _mod(monkeypatch, tmp_path)
        assert bs.read_store() == ""
        assert bs.store_age() is None
        assert bs.store_is_fresh() is False

    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        bs = _mod(monkeypatch, tmp_path)
        bs._write_atomic("## Recent Violations\n- x")
        assert bs.read_store() == "## Recent Violations\n- x"
        assert bs.store_age() is not None and bs.store_age() < 5
        assert bs.store_is_fresh(ttl=3600) is True

    def test_fresh_respects_ttl(self, tmp_path, monkeypatch):
        bs = _mod(monkeypatch, tmp_path)
        bs._write_atomic("data")
        # Age the store past a tiny TTL.
        past = time.time() - 100
        import os

        os.utime(bs.store_path(), (past, past))
        assert bs.store_is_fresh(ttl=10) is False
        assert bs.store_is_fresh(ttl=1000) is True


class TestRefresh:
    def test_refresh_returns_empty_when_no_db(self, tmp_path, monkeypatch):
        bs = _mod(monkeypatch, tmp_path)
        out = bs.refresh_store(db_path=str(tmp_path / "nope.db"), config={})
        assert out == ""

    def test_refresh_writes_store(self, tmp_path, monkeypatch):
        bs = _mod(monkeypatch, tmp_path)
        db = tmp_path / "sio.db"
        db.write_text("")  # exists so the db-guard passes
        monkeypatch.setattr(bs, "_compute_briefing", lambda db_path, config: "TEST BRIEF")

        out = bs.refresh_store(db_path=str(db), config={})
        assert out == "TEST BRIEF"
        assert bs.read_store() == "TEST BRIEF"

    def test_refresh_releases_lock(self, tmp_path, monkeypatch):
        import os

        bs = _mod(monkeypatch, tmp_path)
        db = tmp_path / "sio.db"
        db.write_text("")
        monkeypatch.setattr(bs, "_compute_briefing", lambda db_path, config: "OK")
        bs.refresh_store(db_path=str(db), config={})
        assert not os.path.exists(bs._LOCK_FILE), "lock must be released after refresh"
