"""Unit tests for the `sio experiment` CLI surface (T026).

Drives the Click commands via CliRunner with SIO_DB_PATH pointed at a
temp DB so nothing touches the real ~/.sio/sio.db.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from click.testing import CliRunner

from sio.cli.main import cli


@pytest.fixture()
def env_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "sio.db")
        monkeypatch.setenv("SIO_DB_PATH", db)
        yield db


def _run(args):
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def test_experiment_start_creates_row(env_db):
    res = _run(["experiment", "start", "exp1", "--note", "hello", "--project", "SIO"])
    assert res.exit_code == 0, res.output
    assert "Experiment started" in res.output
    conn = sqlite3.connect(env_db)
    row = conn.execute(
        "SELECT name, note, project, status, config_hash FROM experiments"
    ).fetchone()
    conn.close()
    assert row[0] == "exp1" and row[1] == "hello" and row[2] == "SIO"
    assert row[3] == "open" and row[4]  # config_hash populated


def test_experiment_start_duplicate_exits_1(env_db):
    _run(["experiment", "start", "dup"])
    res = _run(["experiment", "start", "dup"])
    assert res.exit_code == 1
    assert "already exists" in res.output


def test_experiment_status_named_and_unnamed(env_db):
    _run(["experiment", "start", "a", "--note", "the note"])
    res_all = _run(["experiment", "status"])
    assert res_all.exit_code == 0 and "a" in res_all.output
    res_named = _run(["experiment", "status", "a"])
    assert "the note" in res_named.output and "open" in res_named.output


def test_experiment_status_missing_exits_1(env_db):
    res = _run(["experiment", "status", "ghost"])
    assert res.exit_code == 1 and "No experiment" in res.output


def test_experiment_list_shows_all(env_db):
    _run(["experiment", "start", "a"])
    _run(["experiment", "start", "b"])
    res = _run(["experiment", "list"])
    assert res.exit_code == 0 and "a" in res.output and "b" in res.output


def test_experiment_close_stamps_and_blocks_double(env_db):
    _run(["experiment", "start", "c"])
    res = _run(["experiment", "close", "c"])
    assert res.exit_code == 0 and "Experiment closed" in res.output
    res2 = _run(["experiment", "close", "c"])
    assert res2.exit_code == 1 and "already closed" in res2.output


def test_experiment_close_report_json_is_pipeable(env_db):
    """close --report --format json must emit pure JSON on stdout."""
    _run(["experiment", "start", "j"])
    res = _run(["experiment", "close", "j", "--report", "--format", "json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)  # raises if polluted
    assert data["experiment"]["name"] == "j"
    assert "error_rate" in data


def test_experiment_close_report_html_writes_file(env_db, monkeypatch, tmp_path):
    # Redirect HOME so the html lands in a temp ~/.sio/reports
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["experiment", "start", "h"])
    res = _run(["experiment", "close", "h", "--report", "--format", "html"])
    assert res.exit_code == 0
    out = tmp_path / ".sio" / "reports" / "experiment_h.html"
    assert out.exists()
    assert "<!DOCTYPE html>" in out.read_text()
