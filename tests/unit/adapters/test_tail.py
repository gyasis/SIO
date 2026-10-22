"""Regression: live tails must not drop a line the writer is still mid-way through."""

from __future__ import annotations

import json
import threading

from sio.adapters._tail import tail_lines
from sio.adapters.base import SessionManifest
from sio.adapters.pi.adapter import PiAdapter


def _collect(gen, n, out):
    for item in gen:
        out.append(item)
        if len(out) >= n:
            return


def test_half_written_line_is_emitted_once_complete(tmp_path):
    fp = tmp_path / "s.jsonl"
    fp.write_text("")
    line = json.dumps({"k": "v" * 20}) + "\n"
    out: list[str] = []
    t = threading.Thread(
        target=_collect, args=(tail_lines(str(fp), poll_interval=0.02), 1, out), daemon=True
    )
    t.start()
    with fp.open("a") as fh:
        fh.write(line[:10])
        fh.flush()
        t.join(0.2)  # several polls see only the partial line
        assert out == []
        fh.write(line[10:])
    t.join(3)
    assert out == [line.strip()]


def test_truncation_restarts_from_top(tmp_path):
    fp = tmp_path / "s.jsonl"
    fp.write_text("old old old old\n")
    out: list[str] = []
    t = threading.Thread(
        target=_collect, args=(tail_lines(str(fp), poll_interval=0.02), 1, out), daemon=True
    )
    t.start()
    t.join(0.1)
    fp.write_text("new\n")
    t.join(3)
    assert out == ["new"]


def test_pi_live_stream_emits_split_line(tmp_path):
    fp = tmp_path / "s.jsonl"
    fp.write_text("")
    row = json.dumps({
        "type": "message", "timestamp": "t",
        "message": {"role": "user", "content": "hello split"},
    }) + "\n"
    m = SessionManifest(agent="pi", native_id="s", kind="file", path=str(fp), encoding="jsonl")
    out: list = []
    gen = PiAdapter().get_live_stream(m, poll_interval=0.02)
    t = threading.Thread(target=_collect, args=(gen, 1, out), daemon=True)
    t.start()
    with fp.open("a") as fh:
        fh.write(row[:15])
        fh.flush()
        t.join(0.2)
        fh.write(row[15:])
    t.join(3)
    assert [e.content for e in out] == ["hello split"]
