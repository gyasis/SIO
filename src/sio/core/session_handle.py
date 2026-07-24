"""Canonical cross-agent session handle — the "Session URI".

Format: ``agent:native_id`` (e.g. ``claude:<uuid>``, ``goose:<name>``,
``opencode:<dbhash>:<rowid>``, ``aider:<repohash>:<isodate>``).

Background: SIO rows historically store a bare Claude JSONL UUID (no colon).
The cross-agent merge (PRD sio_absorb_session_search, Phase A) generalises this
to ``agent:native_id``. To let ``--session`` work BEFORE and AFTER the
non-destructive colon-backfill migration, the match helper here matches BOTH
the bare legacy id and the canonical form.
"""

from __future__ import annotations

from pathlib import Path

KNOWN_AGENTS: tuple[str, ...] = (
    "claude",
    "codex",
    "goose",
    "opencode",
    "gemini",
    "aider",
    "promptchain",
    "kimi",
)

# Legacy default: a colon-less handle (or one whose prefix is not a known agent)
# is treated as a Claude session id, matching the historical bare-UUID rows.
LEGACY_AGENT = "claude"


def parse_handle(handle: str) -> tuple[str, str]:
    """Parse a session handle into ``(agent, native_id)``.

    A handle whose prefix before the first ``:`` is a known agent is split on
    that colon. Anything else (no colon, or an unknown prefix) is treated as a
    legacy bare Claude id.

    >>> parse_handle("claude:abc-123")
    ('claude', 'abc-123')
    >>> parse_handle("opencode:deadbeef:42")
    ('opencode', 'deadbeef:42')
    >>> parse_handle("abc-123")
    ('claude', 'abc-123')
    """
    h = handle.strip()
    if ":" in h:
        prefix, native = h.split(":", 1)
        if prefix in KNOWN_AGENTS:
            return prefix, native
    return LEGACY_AGENT, h


def to_canonical(handle: str) -> str:
    """Normalise any handle to canonical ``agent:native_id`` form."""
    agent, native = parse_handle(handle)
    return f"{agent}:{native}"


def ensure_canonical(session_id: str | None, agent: str = LEGACY_AGENT) -> str | None:
    """Namespace a bare session id as ``agent:id`` for writing.

    Idempotent: empty or already-namespaced ids are returned unchanged. Applied
    at the DB write path so new rows match the backfilled canonical form.

    >>> ensure_canonical("abc-123")
    'claude:abc-123'
    >>> ensure_canonical("claude:abc-123")
    'claude:abc-123'
    >>> ensure_canonical(None) is None
    True
    """
    if not session_id or ":" in session_id:
        return session_id
    return f"{agent}:{session_id}"


def looks_like_path(raw: str) -> bool:
    """True if ``raw`` looks like a session file path rather than a handle."""
    return "/" in raw or raw.endswith((".jsonl", ".md"))


def from_path(path_str: str) -> str:
    """Map a session FILE path (as emitted by ``sio search --files``) to a
    canonical handle.

    The file stem is the agent-native session id; the agent is inferred from the
    path location (defaults to claude, covering JSONL, SpecStory md, backups).

    >>> from_path("/home/u/.claude/projects/-x/abc-123.jsonl")
    'claude:abc-123'
    >>> from_path("/home/u/.local/share/goose/sessions/mysession.jsonl")
    'goose:mysession'
    """
    p = Path(path_str)
    native = p.stem
    s = str(p)
    if "/.codex/" in s:
        agent = "codex"
    elif "/goose/" in s:
        agent = "goose"
    elif "/opencode" in s:
        agent = "opencode"
    elif "/.gemini/" in s:
        agent = "gemini"
    elif ".aider" in s:
        agent = "aider"
    elif "/.kimi-code/" in s:
        agent = "kimi"
        # wire.jsonl always stems to "wire" — use the session_<uuid> dir name
        # instead so the handle round-trips through manifest_from_handle().
        if p.name == "wire.jsonl" and len(p.parents) >= 3:
            native = p.parents[2].name
    else:
        agent = LEGACY_AGENT
    return f"{agent}:{native}"


def coerce_session_input(raw: str) -> str:
    """Normalise a raw ``--session`` value (CLI arg or stdin line) to a handle.

    Accepts a handle (``claude:uuid`` or bare id), a session file path (as from
    ``sio search --files``), or a ``sio search --count`` line (``"N\\tpath"`` —
    the path is taken). Returns a string ready for :func:`session_match_clause`.

    >>> coerce_session_input("claude:abc")
    'claude:abc'
    >>> coerce_session_input("/home/u/.claude/projects/-x/abc-123.jsonl")
    'claude:abc-123'
    """
    raw = raw.strip()
    if "\t" in raw:  # `sio search --count` emits "N\tpath"
        raw = raw.split("\t")[-1].strip()
    if looks_like_path(raw):
        return from_path(raw)
    return raw


def session_match_clause(
    handle: str, column: str = "session_id"
) -> tuple[str, list[str]]:
    """Build a SQL clause + params matching ``handle`` in BOTH forms.

    Matches the bare native id (legacy rows written before the colon-backfill
    migration) OR the canonical ``agent:native_id`` (rows written after it), so
    ``--session`` is correct throughout the transition.

    Returns ``(clause, params)`` ready to append to a parameterised query, e.g.::

        clause, p = session_match_clause(handle)
        where_clauses.append(clause)
        params.extend(p)

    Only ``claude`` ever had bare legacy rows (it was the sole ingested agent
    before the merge), so the bare-id arm is added ONLY for claude handles.
    Non-claude agents are canonical-only — otherwise ``goose:<uuid>`` would
    wrongly match a legacy bare claude row that happens to share that id.
    """
    agent, native = parse_handle(handle)
    canonical = f"{agent}:{native}"
    if agent == LEGACY_AGENT:
        return f"({column} = ? OR {column} = ?)", [native, canonical]
    return f"({column} = ?)", [canonical]
