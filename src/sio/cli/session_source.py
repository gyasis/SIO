"""session_source — find and load ONE session from any harness for the analysis commands.

``distill``, ``recall`` and ``collect-recall`` analyse a single session. This
module gives them one entry point that works for every harness:

* :func:`resolve_session` turns a path, a handle (``omp:<id>``) or nothing
  (meaning "latest") into a target, searching every harness store.
* :func:`load_session_messages` returns the message dicts the distiller expects.
  Claude transcripts go through ``parse_jsonl``; every other harness goes
  through its adapter, and :func:`events_to_paired_messages` joins each tool
  call to its result so the shape matches the Claude parser's.

A session that yields nothing raises :class:`SessionLoadError` with the reason.
It is never reported as an empty session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from sio.core.session_handle import coerce_session_input, from_path, looks_like_path, parse_handle

# Harnesses whose sessions are one file each: agent -> (store attr in sio.cli.live, glob).
_FILE_STORES: dict[str, tuple[str, str]] = {
    "claude": ("CLAUDE_PROJECTS", "*.jsonl"),
    "codex": ("CODEX_SESSIONS", "rollout-*.jsonl"),
    "gemini": ("GEMINI_TMP", "session-*.jsonl"),
    "kimi": ("KIMI_SESSIONS", "wire.jsonl"),
    "pi": ("PI_SESSIONS", "*.jsonl"),
    "omp": ("OMP_SESSIONS", "*.jsonl"),
    "dsh": ("DSH_SESSIONS", "session*.jsonl*"),
}
# Harnesses whose sessions live in a SQLite store: agent -> loader name in sio.cli.live.
_DB_STORES: dict[str, str] = {
    "goose": "_goose_sessions_from_db",
    "opencode": "_opencode_sessions_from_db",
}
LATEST_AGENTS = tuple(_FILE_STORES) + tuple(_DB_STORES)


class SessionLoadError(Exception):
    """A session could not be found or read. The message says why."""


def _latest_candidates(agent: str | None) -> Iterable[tuple[float, str, str]]:
    """Yield ``(mtime, target, match_text)`` for every session in the selected stores."""
    from sio.cli import live

    agents = [agent] if agent else list(LATEST_AGENTS)
    for name in agents:
        if name in _FILE_STORES:
            attr, glob = _FILE_STORES[name]
            root: Path = getattr(live, attr)
            if not root.exists():
                continue
            for fp in root.rglob(glob):
                try:
                    yield fp.stat().st_mtime, str(fp), str(fp)
                except OSError:
                    continue
        elif name in _DB_STORES:
            for row in getattr(live, _DB_STORES[name])(0.0):
                handle = f"{row['agent']}:{row['native_id']}"
                yield row["mtime"], handle, f"{handle} {row.get('cwd') or ''}"


def resolve_session(
    target: str | None, agent: str | None = None, project: str | None = None
) -> str:
    """Return the path or handle of the session to analyse.

    ``target`` given: returned as is. Otherwise the newest session across every
    harness store (or only ``agent``'s store), optionally filtered to paths or
    working directories containing ``project``.
    """
    if target:
        return target
    if agent and agent not in LATEST_AGENTS:
        raise SessionLoadError(
            f"--latest cannot list {agent} sessions; pass a session handle such as "
            f"'{agent}:<id>' instead. Listable harnesses: {', '.join(LATEST_AGENTS)}."
        )
    best: tuple[float, str] | None = None
    for mtime, cand, text in _latest_candidates(agent):
        if project and project.lower() not in text.lower():
            continue
        if best is None or mtime > best[0]:
            best = (mtime, cand)
    if best is None:
        scope = f"{agent} " if agent else ""
        extra = f" matching '{project}'" if project else ""
        raise SessionLoadError(f"No {scope}sessions found{extra}.")
    return best[1]


def events_to_paired_messages(events: Iterable) -> list[dict]:
    """Adapter events -> message dicts with each tool call joined to its result.

    Adapters emit a tool call (``assistant`` event with ``tool`` set, arguments
    as content) and its result (``tool`` event) separately. The Claude parser
    emits ONE assistant message per call carrying ``tool_input``,
    ``tool_output`` and ``error``; the distiller reads that shape. Results are
    matched to the oldest open call of the same tool name, else the oldest open
    call. Adapters that already carry the whole call in ``raw`` (OpenCode,
    Paperclip) produce one message directly.
    """
    out: list[dict] = []
    pending: dict[str, list[int]] = {}

    def _base(ev, role: str) -> dict:
        return {
            "role": role,
            "content": ev.content or "",
            "tool_name": None,
            "tool_input": None,
            "tool_output": None,
            "error": None,
            "timestamp": ev.ts,
        }

    for ev in events:
        raw = ev.raw if isinstance(ev.raw, dict) else {}
        if ev.tool and ("tool_input" in raw or "tool_output" in raw):
            msg = _base(ev, "assistant")
            msg.update(
                tool_name=ev.tool,
                tool_input=raw.get("tool_input"),
                tool_output=raw.get("tool_output"),
                error=getattr(ev, "error", None) or None,
            )
            out.append(msg)
        elif ev.role == "assistant" and ev.tool:
            msg = _base(ev, "assistant")
            msg.update(content="", tool_name=ev.tool, tool_input=ev.content or "")
            out.append(msg)
            pending.setdefault(ev.tool, []).append(len(out) - 1)
        elif ev.role == "tool":
            queue = pending.get(ev.tool) if ev.tool else None
            if not queue:
                open_calls = [q for q in pending.values() if q]
                queue = min(open_calls, key=lambda q: q[0]) if open_calls else None
            if queue:
                call = out[queue.pop(0)]
                call["tool_output"] = ev.content or ""
                call["error"] = getattr(ev, "error", None) or None
            else:
                out.append(_base(ev, "tool"))
        else:
            out.append(_base(ev, ev.role))
    return out


def _unparsed_reason(path: Path) -> str:
    """Explain why a file yielded no Claude messages: line count and record types seen."""
    lines = 0
    types: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                lines += 1
                if len(types) < 5:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = rec.get("type") if isinstance(rec, dict) else None
                    if t and t not in types:
                        types.append(str(t))
    except OSError as exc:
        return f"{path}: cannot read ({exc})"
    if lines == 0:
        return f"{path}: file is empty."
    seen = f" Record types seen: {', '.join(types)}." if types else ""
    return (
        f"{path}: 0 messages parsed from {lines} non-blank lines. It is not a Claude Code "
        f"transcript, and its location matches no SIO harness store "
        f"({', '.join(LATEST_AGENTS)}). Pass a handle such as 'omp:<id>' if it belongs "
        f"to a supported harness.{seen}"
    )


def _manifest_for(target: str):
    """Resolve a path or handle to ``(SessionManifest, handle)``."""
    from sio.adapters.base import SessionManifest
    from sio.adapters.factory import manifest_from_handle

    if looks_like_path(target):
        path = Path(target).expanduser()
        if not path.is_file():
            raise SessionLoadError(f"File not found: {target}")
        handle = from_path(str(path))
        agent, native = parse_handle(handle)
        if agent == "claude":
            return SessionManifest(
                agent="claude", native_id=native, kind="file", path=str(path), encoding="jsonl"
            ), handle
        manifest = manifest_from_handle(handle)
        if manifest is None and agent in _FILE_STORES:
            # A copy outside the harness store: read the file the user named.
            manifest = SessionManifest(
                agent=agent, native_id=native, kind="file", path=str(path), encoding="jsonl"
            )
    else:
        handle = coerce_session_input(target)
        try:
            manifest = manifest_from_handle(handle)
        except NotImplementedError as exc:
            raise SessionLoadError(str(exc)) from exc
    if manifest is None:
        raise SessionLoadError(f"Session not found: {handle}")
    return manifest, handle


def load_session_messages(target: str) -> tuple[list[dict], str]:
    """Load one session as distiller-ready message dicts.

    Returns ``(messages, label)`` where ``label`` names the session for output.
    Raises :class:`SessionLoadError` when the session is missing or yields no
    messages, so an unreadable session is never mistaken for an empty one.
    """
    from sio.adapters.factory import adapter_for
    from sio.mining.jsonl_parser import parse_jsonl

    manifest, handle = _manifest_for(target)
    if manifest.agent == "claude":
        path = Path(manifest.path)
        messages = parse_jsonl(path)
        if not messages:
            raise SessionLoadError(_unparsed_reason(path))
        return messages, path.name

    label = f"{manifest.agent}:{manifest.native_id}"
    events = list(adapter_for(manifest.agent).get_events(manifest))
    if not events:
        where = manifest.path or f"the {manifest.agent} store"
        raise SessionLoadError(
            f"{label}: the {manifest.agent} adapter read 0 events from {where}. "
            f"The session id may be wrong, or the store format may have changed."
        )
    return events_to_paired_messages(events), label
