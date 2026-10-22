"""Tests for omp (oh-my-pi, a pi fork) support: adapter, factory, search, live.

The fixture is SYNTHETIC with omp's shape: a padded ``title`` record on line 1,
the ``session`` header (string ``version``) on line 2, ``custom`` /
``model_usage`` / ``title_change`` bookkeeping, a tool call and its result.
"""

from __future__ import annotations

import json
from pathlib import Path

UUID = "0199a0b1-c2d3-7e4f-8a5b-6c7d8e9f0a1b"
STEM = f"2026-10-22T05-12-21-700Z_{UUID}"
TS = "2026-10-22T05:12:22.000Z"


def _make_fixture(home: Path) -> Path:
    d = home / ".omp" / "agent" / "sessions" / "-code-demo"
    d.mkdir(parents=True)
    fp = d / f"{STEM}.jsonl"
    rows = [
        {"type": "title", "v": "1", "title": "Demo", "source": "auto",
         "updatedAt": TS, "pad": "x" * 40},
        {"type": "session", "version": "3", "id": UUID, "timestamp": TS,
         "cwd": "/home/someone/code/demo"},
        {"type": "custom", "id": "e1", "parentId": None, "timestamp": TS,
         "customType": "system-prompt-digest", "data": {"note": "widget widget"}},
        {"type": "model_usage", "id": "e2", "parentId": "e1", "timestamp": TS},
        {"type": "title_change", "id": "e3", "parentId": "e2", "timestamp": TS,
         "title": "widget"},
        {"type": "message", "id": "e4", "parentId": "e3", "timestamp": TS,
         "message": {"role": "user", "content": [{"type": "text", "text": "find the widget"}]}},
        {"type": "message", "id": "e5", "parentId": "e4", "timestamp": TS,
         "message": {"role": "assistant", "content": [
             {"type": "toolCall", "id": "c1", "name": "read",
              "arguments": {"path": "widget.txt"}}]}},
        {"type": "message", "id": "e6", "parentId": "e5", "timestamp": TS,
         "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "read",
                     "content": [{"type": "text", "text": "widget contents"}]}},
    ]
    fp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return fp


def test_adapter_parses_omp_shape_and_drops_bookkeeping(tmp_path):
    from sio.adapters.base import SessionManifest
    from sio.adapters.omp.adapter import OmpAdapter

    fp = _make_fixture(tmp_path)
    m = SessionManifest(agent="omp", native_id=fp.stem, kind="file", path=str(fp),
                        encoding="jsonl")
    evs = list(OmpAdapter().get_events(m))
    # session header, user, toolCall, toolResult -- title/custom/usage dropped
    assert [e.role for e in evs] == ["system", "user", "assistant", "tool"]
    assert evs[2].tool == "read" and "widget.txt" in evs[2].content
    assert evs[3].tool == "read" and evs[3].error is None
    assert OmpAdapter.agent == "omp"


def test_factory_adapter_and_manifest(tmp_path, monkeypatch):
    from sio.adapters import factory
    from sio.adapters.omp.adapter import OmpAdapter

    fp = _make_fixture(tmp_path)
    monkeypatch.setattr(factory, "_OMP_SESSIONS", tmp_path / ".omp" / "agent" / "sessions")
    assert isinstance(factory.adapter_for("omp"), OmpAdapter)
    for native in (STEM, UUID[:8]):
        m = factory.manifest_from_handle(f"omp:{native}")
        assert m is not None and m.agent == "omp" and m.path == str(fp)
    assert factory.manifest_from_handle("omp:nope") is None


def test_from_path_maps_to_omp_handle(tmp_path):
    from sio.core.session_handle import KNOWN_AGENTS, from_path

    assert "omp" in KNOWN_AGENTS
    assert from_path(str(_make_fixture(tmp_path))) == f"omp:{STEM}"


def test_search_omp_registered_and_matches(tmp_path, monkeypatch):
    import sio.search.cli as _cli

    assert "omp" in _cli.PARSERS
    fp = _make_fixture(tmp_path)
    monkeypatch.setattr(_cli, "HOME", tmp_path)
    recs = list(_cli.search_omp("widget", cs=False, cutoff=None))
    assert recs and all(r.agent == "omp" for r in recs)
    assert all(r.session_id == STEM and r.source_path == str(fp) for r in recs)
    assert all(r.metadata["source_kind"] == "omp" for r in recs)
    # custom / title_change mention "widget" but must never surface
    assert all(r.metadata["entry_type"] in ("message",) for r in recs)


def test_live_probe_finds_cwd_past_title_line(tmp_path):
    from sio.cli.live import _pi_session

    row = _pi_session(_make_fixture(tmp_path), "omp")
    assert row["agent"] == "omp" and row["cwd"] == "/home/someone/code/demo"
    assert row["native_id"] == STEM
