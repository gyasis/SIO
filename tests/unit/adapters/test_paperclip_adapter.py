"""Paperclip adapter: run-log ndjson -> events, enrichment, API-down fallback.

Each fixture is a tiny SYNTHETIC run log under ``tmp_path``. A patch over
``PaperclipApi.get`` stands in for the API, so no test touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

from sio.adapters.paperclip import adapter as pc

COMPANY = "c0000000-0000-0000-0000-000000000001"
AGENT = "a0000000-0000-0000-0000-000000000002"
RUN = "r0000000-0000-0000-0000-000000000003"


def _oc(kind: str, part: dict, ts_ms: int = 1784900000000) -> str:
    return json.dumps({"type": kind, "timestamp": ts_ms, "sessionID": "ses_x", "part": part}) + "\n"


def _tool(cmd: str, out: str, exit_code: int = 0) -> str:
    return _oc("tool_use", {"type": "tool", "tool": "bash", "state": {
        "status": "completed", "input": {"command": cmd}, "output": out,
        "metadata": {"exit": exit_code}}})


def _write_run(home: Path, lines: list[str], instance="default", split_at: int | None = None,
               server_port: int | None = 1) -> Path:
    inst = home / "instances" / instance
    d = inst / "data" / "run-logs" / COMPANY / AGENT
    d.mkdir(parents=True, exist_ok=True)
    if server_port is not None:
        (inst / "config.json").write_text(json.dumps({"server": {"host": "127.0.0.1",
                                                                  "port": server_port}}))
    text = "".join(lines)
    chunks = [text] if split_at is None else [text[:split_at], text[split_at:]]
    f = d / f"{RUN}.ndjson"
    with f.open("w") as fh:
        for i, c in enumerate(chunks, 1):
            fh.write(json.dumps({"ts": f"2026-10-12T10:00:0{i}.000Z", "stream": "stdout",
                                 "chunk": c, "seq": i}) + "\n")
    return f


def _events(home: Path, enrich=False):
    (m,) = pc.list_runs(home, enrich=enrich)
    return m, list(pc.PaperclipAdapter().get_events(m))


def test_run_becomes_one_session_with_text_and_tool_events(tmp_path):
    _write_run(tmp_path, [
        "[paperclip] starting\n",
        _oc("text", {"type": "text", "text": "plan"}),
        _tool("ls", "a\n"),
        _oc("step_finish", {"type": "step-finish"}),
    ])
    m, evs = _events(tmp_path)
    assert m.agent == "paperclip" and m.native_id == f"default:{RUN}"
    roles = [(e.role, e.tool) for e in evs if not e.content.startswith("[paperclip run]")]
    assert roles == [("system", None), ("assistant", None), ("assistant", "bash"), ("tool", "bash")]


def test_chunk_split_mid_line_is_reassembled(tmp_path):
    line = _tool("git status", "fatal: not a git repository", 128)
    _write_run(tmp_path, [line], split_at=40)
    _, evs = _events(tmp_path)
    errs = [e.error for e in evs if e.error]
    assert errs and "fatal: not a git repository" in errs[0]


def test_loop_and_template_leak_signals(tmp_path):
    _write_run(tmp_path, [_tool("ls -F x", "no") for _ in range(3)]
               + [_oc("text", {"type": "text", "text": "oops<channel|>"})])
    _, evs = _events(tmp_path)
    assert sum(1 for e in evs if e.error_type == "repeated_attempt") == 1
    assert any(e.error and "<channel|>" in e.error for e in evs)


def test_unknown_formats_never_crash_and_emit_text(tmp_path):
    _write_run(tmp_path, ["not json at all\n", '{"type":"mystery","x":1}\n', "{broken\n",
                          '{"type":"rate_limit_event"}\n'])
    _, evs = _events(tmp_path)
    contents = [e.content for e in evs]
    assert "not json at all" in contents and "{broken" in contents
    assert any("mystery" in c for c in contents)
    assert not any("rate_limit" in c for c in contents)


def test_claude_stream_json_tool_error(tmp_path):
    _write_run(tmp_path, [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "false"}}]}}) + "\n",
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": "Exit code 1"}]}}) + "\n",
    ])
    _, evs = _events(tmp_path)
    assert [e.tool for e in evs if e.tool] == ["Bash", "Bash"]
    assert any(e.error == "Exit code 1" for e in evs)


def test_acpx_deltas_aggregate_and_error_surfaces(tmp_path):
    _write_run(tmp_path, [
        '{"type":"acpx.text_delta","text":"Hel"}\n', '{"type":"acpx.text_delta","text":"lo"}\n',
        '{"type":"acpx.error","message":"limit hit"}\n',
    ])
    _, evs = _events(tmp_path)
    assert "Hello" in [e.content for e in evs]
    assert any(e.error == "limit hit" for e in evs)


def test_api_down_falls_back_to_ids(tmp_path):
    _write_run(tmp_path, [_oc("text", {"type": "text", "text": "x"})], server_port=1)
    (m,) = pc.list_runs(tmp_path, enrich=True)  # port 1: connection refused
    assert m.meta["label"] == f"{COMPANY[:8]}/{AGENT[:8]}"
    assert "company_name" not in m.meta


def test_api_down_is_probed_once_per_instance(tmp_path, monkeypatch):
    f = _write_run(tmp_path, [_oc("text", {"type": "text", "text": "x"})], server_port=1)
    (f.parent / "second.ndjson").write_text(f.read_text())
    calls = []
    real = pc.urllib.request.urlopen

    def spy(*a, **k):
        calls.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(pc.urllib.request, "urlopen", spy)
    pc.list_runs(tmp_path, enrich=True)
    assert len(calls) == 1


def test_api_enrichment_and_failed_run_event(tmp_path, monkeypatch):
    _write_run(tmp_path, [_oc("text", {"type": "text", "text": "x"})])
    run_path = f"/api/heartbeat-runs/{RUN}"
    answers = {
        "/api/companies": [{"id": COMPANY, "name": "Acme"}],
        f"/api/companies/{COMPANY}/agents": [{"id": AGENT, "name": "Engineer"}],
        run_path: {"status": "failed", "error": "adapter exploded", "livenessState": "stuck",
                   "contextSnapshot": {"issueId": "i1"}},
        "/api/issues/i1": {"identifier": "ACM-47", "title": "Audit", "status": "blocked"},
    }
    monkeypatch.setattr(pc.PaperclipApi, "get", lambda self, p: answers.get(p))
    m, evs = _events(tmp_path, enrich=True)
    assert m.meta["label"] == "Acme/Engineer/ACM-47"
    assert m.meta["issue_title"] == "Audit" and m.meta["run_status"] == "failed"
    assert "ACM-47" in evs[0].content and "Engineer" in evs[0].content
    assert any(e.error == "Paperclip run failed: adapter exploded" for e in evs)


def test_instance_filter_and_home_override(tmp_path, monkeypatch):
    _write_run(tmp_path, [_oc("text", {"type": "text", "text": "x"})], instance="alpha")
    assert len(pc.list_runs(tmp_path, instance="alpha", enrich=False)) == 1
    assert pc.list_runs(tmp_path, instance="beta", enrich=False) == []
    monkeypatch.setenv("SIO_PAPERCLIP_HOME", str(tmp_path))
    assert pc.paperclip_home() == tmp_path
    assert pc.paperclip_home("/x") == Path("/x")


def test_find_run_by_partial_id_via_factory(tmp_path, monkeypatch):
    from sio.adapters.factory import adapter_for, manifest_from_handle

    _write_run(tmp_path, [_oc("text", {"type": "text", "text": "x"})])
    monkeypatch.setenv("SIO_PAPERCLIP_HOME", str(tmp_path))
    assert isinstance(adapter_for("paperclip"), pc.PaperclipAdapter)
    m = manifest_from_handle(f"paperclip:default:{RUN[:8]}")
    assert m is not None and m.meta["run_id"] == RUN
