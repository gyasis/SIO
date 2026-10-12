"""Paperclip adapter -- one SIO session per Paperclip heartbeat run.

Layout (instance-generic, nothing company-specific)::

    <home>/instances/<instance>/config.json
    <home>/instances/<instance>/data/run-logs/<companyId>/<agentId>/<runId>.ndjson

``<home>`` is ``$SIO_PAPERCLIP_HOME`` / ``--paperclip-home`` and defaults to
``~/.paperclip``. Each ndjson line is ``{"ts","stream","chunk","seq"}`` where
``chunk`` is raw adapter stdout/stderr -- a chunk can end mid-line, so each
stream is re-assembled into lines before parsing. Recognised line formats:

* OpenCode ``--format json`` events (``text`` / ``tool_use``): parsed with the
  SAME helpers as the OpenCode adapter (:mod:`sio.adapters.opencode.parsing`);
* Claude Code ``stream-json`` (``assistant`` / ``user`` / ``result``);
* ACPX (``acpx.text_delta`` / ``acpx.tool_call`` / ``acpx.error``);
* ``[paperclip] ...`` harness lines and any unrecognised non-JSON line become
  ``system`` text events. Nothing here raises on malformed input.

When the instance's HTTP API answers (2 s timeout; one failure marks the
instance down for the rest of the run) the manifest ``meta`` is enriched with
company / agent names, run status + error and the issue identifier + title.
Without it, names fall back to ids.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sio.adapters.base import SessionEvent, SessionManifest
from sio.adapters.opencode.parsing import (
    SessionSignals,
    call_event,
    clip,
    iso_ms,
    part_events,
    result_event,
)

API_TIMEOUT_S = 2.0
_NOISE_TYPES = frozenset(
    {
        "system", "tool_progress", "rate_limit_event", "acpx.status", "acpx.session",
        "acpx.result", "step_start", "step_finish", "reasoning",
    }
)
_RUN_OK = frozenset({"succeeded", "completed", "running", "queued"})


def paperclip_home(override: str | os.PathLike | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("SIO_PAPERCLIP_HOME")
    return Path(env).expanduser() if env else Path.home() / ".paperclip"


def list_instances(home: Path, only: str | None = None) -> list[Path]:
    root = home / "instances"
    if not root.is_dir():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and (only is None or p.name == only)]


class PaperclipApi:
    """Best-effort, cached, read-only client for one instance's HTTP API."""

    def __init__(self, instance_dir: Path, timeout: float = API_TIMEOUT_S) -> None:
        self.timeout = timeout
        self.down = False
        self._cache: dict[str, Any] = {}
        self.base = self._base_url(instance_dir)
        if not self.base:
            self.down = True

    @staticmethod
    def _base_url(instance_dir: Path) -> str | None:
        try:
            server = json.loads((instance_dir / "config.json").read_text()).get("server") or {}
            port = int(server["port"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        host = str(server.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    def get(self, path: str) -> Any:
        """GET ``path`` as JSON; None on any failure (and mark the server down)."""
        if self.down:
            return None
        if path in self._cache:
            return self._cache[path]
        result: Any = None
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError:
            result = None  # server is up; this object just does not exist
        except (urllib.error.URLError, OSError, ValueError):
            self.down = True
        self._cache[path] = result
        return result

    @staticmethod
    def _names(data: Any) -> dict[str, str]:
        if not isinstance(data, list):
            return {}
        return {x["id"]: x.get("name", "") for x in data if isinstance(x, dict) and "id" in x}

    def company_names(self) -> dict[str, str]:
        return self._names(self.get("/api/companies"))

    def agent_names(self, company_id: str) -> dict[str, str]:
        return self._names(self.get(f"/api/companies/{company_id}/agents"))


def _enrich(meta: dict[str, Any], api: PaperclipApi) -> None:
    """Add API-derived labels to ``meta`` (no-op when the server is down)."""
    if api.down:
        return
    companies = api.company_names()
    if api.down:  # the probe itself just failed
        return
    meta["company_name"] = companies.get(meta["company_id"], "")
    meta["agent_name"] = api.agent_names(meta["company_id"]).get(meta["agent_id"], "")
    run = api.get(f"/api/heartbeat-runs/{meta['run_id']}")
    if not isinstance(run, dict):
        return
    meta["run_status"] = run.get("status") or ""
    meta["run_error"] = run.get("error") or run.get("errorCode") or ""
    meta["liveness"] = run.get("livenessState") or ""
    meta["started_at"] = run.get("startedAt") or ""
    meta["finished_at"] = run.get("finishedAt") or ""
    ctx = run.get("contextSnapshot") or {}
    issue_id = ctx.get("issueId") if isinstance(ctx, dict) else None
    if issue_id:
        issue = api.get(f"/api/issues/{issue_id}")
        if isinstance(issue, dict):
            meta["issue"] = issue.get("identifier") or ""
            meta["issue_title"] = issue.get("title") or ""
            meta["issue_status"] = issue.get("status") or ""


def label_for(meta: dict[str, Any]) -> str:
    """Short human label: ``company/agent/ISSUE`` (ids when names are unknown)."""
    parts = [
        meta.get("company_name") or str(meta.get("company_id", ""))[:8],
        meta.get("agent_name") or str(meta.get("agent_id", ""))[:8],
    ]
    if meta.get("issue"):
        parts.append(meta["issue"])
    return "/".join(p for p in parts if p)


def list_runs(
    home: Path,
    instance: str | None = None,
    cutoff_epoch: float | None = None,
    enrich: bool = True,
) -> list[SessionManifest]:
    """Manifests for every run log under ``home`` (newest activity >= cutoff)."""
    out: list[SessionManifest] = []
    for inst in list_instances(home, instance):
        api = PaperclipApi(inst)
        for f in sorted((inst / "data" / "run-logs").glob("*/*/*.ndjson")):
            try:
                if cutoff_epoch is not None and f.stat().st_mtime < cutoff_epoch:
                    continue
            except OSError:
                continue
            out.append(manifest_for(inst, f, api, enrich))
    return out


def manifest_for(
    inst: Path, f: Path, api: PaperclipApi | None = None, enrich: bool = True
) -> SessionManifest:
    """Manifest for one run-log file (``api`` is shared to reuse its cache)."""
    meta: dict[str, Any] = {
        "instance": inst.name,
        "company_id": f.parent.parent.name,
        "agent_id": f.parent.name,
        "run_id": f.stem,
    }
    if enrich:
        _enrich(meta, api or PaperclipApi(inst))
    meta["label"] = label_for(meta)
    return SessionManifest(
        agent="paperclip",
        native_id=f"{inst.name}:{f.stem}",
        kind="file",
        path=str(f),
        encoding="ndjson",
        meta=meta,
    )


def find_run(home: Path, native_id: str) -> SessionManifest | None:
    """Resolve ``<instance>:<runId>`` (or a bare / partial run id) to a manifest."""
    inst_name, _, run = native_id.partition(":")
    if not run:
        inst_name, run = "", native_id
    for inst in list_instances(home, inst_name or None):
        hits = sorted((inst / "data" / "run-logs").glob(f"*/*/{run}*.ndjson"))
        if hits:
            return manifest_for(inst, hits[-1])
    return None


def _lines(path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield ``(ts, stream, line)`` re-assembled from chunks that may split lines."""
    buf: dict[str, str] = {}
    start: dict[str, str] = {}
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for raw in fh:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            stream = str(row.get("stream") or "stdout")
            ts = str(row.get("ts") or "")
            if not buf.get(stream):
                start[stream] = ts
            buf[stream] = buf.get(stream, "") + str(row.get("chunk") or "")
            while "\n" in buf[stream]:
                line, buf[stream] = buf[stream].split("\n", 1)
                yield start[stream], stream, line
                start[stream] = ts
    for stream, rest in buf.items():
        if rest.strip():
            yield start[stream], stream, rest


def _blocks_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    return ""


class _Parser:
    """Turns assembled run-log lines into events; holds cross-line state."""

    def __init__(self) -> None:
        self.signals = SessionSignals()
        self.pending: dict[str, tuple[str, str]] = {}  # tool_use_id -> (tool, summary)
        self.delta: list[str] = []
        self.delta_ts = ""

    def flush_delta(self) -> Iterator[SessionEvent]:
        if self.delta:
            text = "".join(self.delta)
            self.delta = []
            yield from part_events({"type": "text", "text": text}, self.delta_ts, "assistant")

    def line(self, ts: str, stream: str, line: str) -> Iterator[SessionEvent]:
        text = line.strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            yield from self.flush_delta()
            yield SessionEvent(
                ts=ts, role="system", content=clip(text, 400, 0), raw={"stream": stream}
            )
            return
        etype = str(obj.get("type") or "")
        if etype == "acpx.text_delta":
            if not self.delta:
                self.delta_ts = ts
            self.delta.append(str(obj.get("text") or ""))
            return
        yield from self.flush_delta()
        ev_ts = iso_ms(obj.get("timestamp")) or ts
        part = obj.get("part")
        if isinstance(part, dict):  # OpenCode --format json
            yield from part_events(part, ev_ts, "assistant", self.signals)
        elif etype == "error":
            err = obj.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or err.get("name") or json.dumps(err))
            else:
                msg = str(err or "error")
            yield SessionEvent(ts=ev_ts, role="system", content=clip(msg), error=clip(msg))
        elif etype == "assistant":
            yield from self._claude_assistant(obj, ts)
        elif etype == "user":
            yield from self._claude_user(obj, ts)
        elif etype == "result":
            if obj.get("is_error"):
                msg = clip(str(obj.get("result") or obj.get("subtype") or "result error"))
                yield SessionEvent(ts=ts, role="system", content=msg, error=msg)
        elif etype == "acpx.tool_call":
            yield from self._acpx_tool(obj, ts)
        elif etype == "acpx.error":
            msg = clip(str(obj.get("message") or obj.get("summary") or "acpx error"))
            yield SessionEvent(ts=ts, role="system", content=msg, error=msg)
        elif etype not in _NOISE_TYPES:
            yield SessionEvent(
                ts=ts, role="system", content=clip(text, 300, 0), raw={"type": etype}
            )

    def _claude_assistant(self, obj: dict[str, Any], ts: str) -> Iterator[SessionEvent]:
        for b in (obj.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                block = {"type": "text", "text": b.get("text", "")}
                yield from part_events(block, ts, "assistant")
            elif b.get("type") == "tool_use":
                tool = str(b.get("name") or "tool")
                ev = call_event(tool, b.get("input"), ts, self.signals)
                if b.get("id"):
                    self.pending[str(b["id"])] = (tool, ev.content)
                yield ev

    def _claude_user(self, obj: dict[str, Any], ts: str) -> Iterator[SessionEvent]:
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tool, summary = self.pending.get(str(b.get("tool_use_id")), ("tool", ""))
                yield result_event(
                    tool, summary, _blocks_text(b.get("content")), ts,
                    is_error=bool(b.get("is_error")),
                )

    def _acpx_tool(self, obj: dict[str, Any], ts: str) -> Iterator[SessionEvent]:
        status = obj.get("status")
        tool = str(obj.get("name") or "tool")
        cid = str(obj.get("toolCallId") or "")
        if status == "failed":
            body = str(obj.get("text") or "tool call failed")
            yield result_event(tool, clip(tool, 160, 0), body, ts, is_error=True)
        elif status in (None, "pending", "in_progress", "completed"):
            # Only the first sighting of a call is an assistant event; the
            # updates repeat the same toolCallId.
            if cid and cid in self.pending:
                return
            if cid:
                self.pending[cid] = (tool, clip(tool, 160, 0))
            # exec-* ids are shell commands whose "name" IS the command line.
            is_shell = cid.startswith("exec-")
            yield call_event(
                "bash" if is_shell else tool, {"command": tool}, ts, self.signals
            )


class PaperclipAdapter:
    """EXTRACT events from one Paperclip run log."""

    agent = "paperclip"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        path = Path(manifest.path)
        if not path.exists():
            return
        meta = manifest.meta or {}
        first_ts = ""
        header = _header_event(meta)
        parser = _Parser()
        for ts, stream, line in _lines(path):
            if header is not None:
                header.ts = ts
                first_ts = ts
                yield header
                header = None
            yield from parser.line(ts, stream, line)
        yield from parser.flush_delta()
        status = str(meta.get("run_status") or "")
        if status and status not in _RUN_OK:
            msg = f"Paperclip run {status}: {meta.get('run_error') or 'no error text'}"
            yield SessionEvent(
                ts=first_ts or _mtime_iso(path), role="system", content=msg, error=msg
            )

    def get_live_stream(self, manifest: SessionManifest, **_: Any) -> Iterator[SessionEvent]:
        raise NotImplementedError("paperclip runs are mined after the fact, not tailed")


def _header_event(meta: dict[str, Any]) -> SessionEvent | None:
    if not meta:
        return None
    bits = [f"{k}={meta[k]}" for k in (
        "instance", "company_name", "agent_name", "run_status", "run_error",
        "liveness", "issue", "issue_title", "issue_status",
    ) if meta.get(k)]
    content = "[paperclip run] " + (" ".join(bits) or str(meta.get("run_id", "")))
    return SessionEvent(ts="", role="system", content=content, raw=dict(meta))


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    except OSError:
        return ""
