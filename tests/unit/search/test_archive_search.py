"""sio search reads sessions Claude Code deleted, from SIO's archive, exactly once."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import sio.search.cli as _cli


def _session(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"type": "user", "timestamp": "2026-06-01T00:00:00Z",
             "message": {"role": "user", "content": text}}
    path.write_text(json.dumps(entry) + "\n")


@pytest.fixture()
def corpus(tmp_path: Path, monkeypatch):
    live = tmp_path / "projects"
    arch = Path(os.environ["SIO_ARCHIVE_DIR"]) / "claude" / "projects"
    # still live AND archived -> must be reported once, from the live copy
    _session(live / "-proj" / "kept.jsonl", "ARCHNEEDLE kept")
    _session(arch / "-proj" / "kept.jsonl", "ARCHNEEDLE kept")
    # deleted by the harness -> only the archive has it
    _session(arch / "-proj" / "deleted.jsonl", "ARCHNEEDLE deleted")
    monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", live)
    return live, arch


def test_python_path_finds_deleted_session_once(corpus):
    hits = list(_cli.search_claude("ARCHNEEDLE", False, None))
    by_session = {h.session_id: h.metadata["source_kind"] for h in hits}
    assert by_session == {"kept": "jsonl", "deleted": "archive"}
    assert len(hits) == 2


def test_fast_path_lists_deleted_session_once(corpus, capsys):
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    rc = _cli.main(["ARCHNEEDLE", "--files", "--recent", "0"])
    out = capsys.readouterr().out
    files = [ln for ln in out.splitlines() if ln.endswith(".jsonl")]
    assert rc == 0
    assert sorted(Path(f).name for f in files) == ["deleted.jsonl", "kept.jsonl"]
    live, arch = corpus
    assert str(live / "-proj" / "kept.jsonl") in files
    assert str(arch / "-proj" / "deleted.jsonl") in files
