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

KNOWN_AGENTS: tuple[str, ...] = (
    "claude",
    "codex",
    "goose",
    "opencode",
    "gemini",
    "aider",
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
