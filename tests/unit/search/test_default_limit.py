"""`sio search` caps output by default and says so when it does.

An agent calling `sio search` with no `--limit` must get a bounded result, and a
capped result must carry a `# Truncated:` line on stderr so a low total is never
read as the real total. `--limit 0` still means unlimited. Both the python
parser path and the ripgrep fast path honour the cap.
"""

from __future__ import annotations

import shutil
import sys
import time
from io import StringIO
from pathlib import Path

import pytest

from tests.unit.search.test_b1_b8_fixes import _parse_jsonl_records, _write_session

NEEDLE = "LIMIT_NEEDLE_XYZ"


def _corpus(tmp_path: Path, n_sessions: int) -> Path:
    proj = tmp_path / "-home-fake-proj"
    proj.mkdir(parents=True)
    now = time.time()
    for i in range(n_sessions):
        _write_session(
            proj,
            f"sess-{i:03d}",
            [{"role": "user", "content": f"{NEEDLE} item {i}"}],
            mtime=now - 60 - i,
        )
    return tmp_path


def _run(monkeypatch: pytest.MonkeyPatch, root: Path, argv: list[str]) -> tuple[str, str, int]:
    import sio.search.cli as cli

    monkeypatch.setattr(cli, "CLAUDE_PROJECTS", root)
    monkeypatch.setattr(cli, "DEV_ROOT", root / "_no_specstory")
    out, err = StringIO(), StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = cli.main(argv)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    finally:
        sys.stdout, sys.stderr = old
    return out.getvalue(), err.getvalue(), rc


BASE = [NEEDLE, "--agent", "claude", "--recent", "0"]


class TestPythonPath:
    def test_default_caps_at_20_and_reports_truncation(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, _ = _run(monkeypatch, root, BASE + ["--no-fast", "--format", "jsonl"])
        assert len(_parse_jsonl_records(out)) == 20
        assert "# Truncated:" in err
        assert "--limit 0" in err

    def test_limit_zero_is_unlimited(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, _ = _run(
            monkeypatch, root, BASE + ["--no-fast", "--format", "jsonl", "--limit", "0"]
        )
        assert len(_parse_jsonl_records(out)) == 30
        assert "# Truncated:" not in err

    def test_under_limit_has_no_truncation_line(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 5)
        out, err, _ = _run(monkeypatch, root, BASE + ["--no-fast", "--format", "jsonl"])
        assert len(_parse_jsonl_records(out)) == 5
        assert "# Truncated:" not in err


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
class TestFastPath:
    def test_files_default_caps_at_20(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, rc = _run(monkeypatch, root, BASE + ["--files"])
        assert rc == 0
        assert len([ln for ln in out.splitlines() if ln.strip()]) == 20
        assert "# Truncated:" in err

    def test_count_respects_explicit_limit(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, _ = _run(monkeypatch, root, BASE + ["--count", "--limit", "5"])
        assert len([ln for ln in out.splitlines() if ln.strip()]) == 5
        assert "# Truncated:" in err

    def test_text_matches_capped(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, _ = _run(
            monkeypatch, root, BASE + ["--format", "text", "--limit", "7", "--context", "0"]
        )
        matches = [ln for ln in out.splitlines() if NEEDLE in ln]
        assert len(matches) == 7
        assert "# Truncated:" in err

    def test_text_long_line_windowed_around_match(self, tmp_path, monkeypatch):
        proj = tmp_path / "-home-fake-proj"
        proj.mkdir(parents=True)
        body = "a" * 30_000 + f" {NEEDLE} " + "b" * 30_000
        _write_session(proj, "long", [{"role": "user", "content": body}])
        out, _, _ = _run(monkeypatch, tmp_path, BASE + ["--format", "text", "--context", "0"])
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 1
        assert NEEDLE in lines[0]
        assert len(lines[0]) < 1_000
        assert "chars omitted" in lines[0]

    def test_text_long_line_kept_whole_with_limit_zero(self, tmp_path, monkeypatch):
        proj = tmp_path / "-home-fake-proj"
        proj.mkdir(parents=True)
        body = "a" * 30_000 + f" {NEEDLE} " + "b" * 30_000
        _write_session(proj, "long", [{"role": "user", "content": body}])
        out, _, _ = _run(
            monkeypatch,
            tmp_path,
            BASE + ["--format", "text", "--context", "0", "--limit", "0"],
        )
        assert len(out) > 60_000

    def test_fast_limit_zero_is_unlimited(self, tmp_path, monkeypatch):
        root = _corpus(tmp_path, 30)
        out, err, _ = _run(monkeypatch, root, BASE + ["--files", "--limit", "0"])
        assert len([ln for ln in out.splitlines() if ln.strip()]) == 30
        assert "# Truncated:" not in err
