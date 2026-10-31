"""`sio mine --agent opencode|paperclip` end to end into a THROWAWAY db."""

from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from tests.unit.adapters.test_opencode_tools_and_stores import _make_store, _tool
from tests.unit.adapters.test_paperclip_adapter import _oc, _write_run
from tests.unit.adapters.test_paperclip_adapter import _tool as _pc_tool


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from sio.adapters.opencode import adapter as oc
    from sio.core.runlog import writer as _w

    (tmp_path / "runs").mkdir()
    monkeypatch.setattr(_w, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(oc, "OPENCODE_DB", tmp_path / "nowhere" / "opencode.db")
    monkeypatch.setenv("SIO_DB_PATH", str(tmp_path / "sio" / "sio.db"))
    (tmp_path / "sio").mkdir()
    monkeypatch.delenv("SIO_OPENCODE_DIRS", raising=False)


def _rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "sio" / "sio.db")
    try:
        return conn.execute(
            "SELECT session_id, error_type, project_tag FROM error_records"
        ).fetchall()
    finally:
        conn.close()


def _mine(*args):
    from sio.cli.main import cli

    r = CliRunner().invoke(cli, ["mine", *args])
    assert r.exit_code == 0, r.output
    return r.output


def test_opencode_dir_option_mines_tool_failures_and_is_idempotent(tmp_path):
    _make_store(tmp_path / "acme", [_tool("git diff x", "fatal: bad revision", 128)])
    out = _mine("--agent", "opencode", "--opencode-dir", str(tmp_path / "acme"))
    assert "Bulk-mined opencode: 1 session" in out
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0][1] == "tool_failure" and rows[0][2] == "demo"
    assert rows[0][0].startswith("opencode:") and rows[0][0].endswith(":ses_same_id")
    assert "1 unchanged" in _mine("--agent", "opencode", "--opencode-dir", str(tmp_path / "acme"))
    assert len(_rows(tmp_path)) == 1


def test_two_stores_same_session_id_do_not_collide(tmp_path):
    for n in ("a", "b"):
        _make_store(tmp_path / n, [_tool("ls", "ok"), _tool("false", "boom", 2)])
    _mine("--agent", "opencode", "--opencode-dir", str(tmp_path / "a"),
          "--opencode-dir", str(tmp_path / "b"))
    assert len({r[0] for r in _rows(tmp_path)}) == 2


def test_env_var_adds_a_store(tmp_path, monkeypatch):
    _make_store(tmp_path / "e", [_tool("false", "boom", 2)])
    monkeypatch.setenv("SIO_OPENCODE_DIRS", str(tmp_path / "e"))
    _mine("--agent", "opencode")
    assert len(_rows(tmp_path)) == 1


def test_paperclip_mine_files_loop_and_leak(tmp_path):
    _write_run(tmp_path / "pc", [_pc_tool("ls q", "x") for _ in range(3)]
               + [_oc("text", {"type": "text", "text": "z<|im_start|>"})])
    out = _mine("--agent", "paperclip", "--paperclip-home", str(tmp_path / "pc"))
    assert "Bulk-mined paperclip: 1 session" in out
    kinds = sorted(r[1] for r in _rows(tmp_path))
    assert kinds == ["repeated_attempt", "tool_failure"]
    assert all(r[0].startswith("paperclip:default:") for r in _rows(tmp_path))
    assert "paperclip:" in _rows(tmp_path)[0][2]
