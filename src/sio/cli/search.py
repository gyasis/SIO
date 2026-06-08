"""sio search — unified cross-harness session search.

Absorbed from the former standalone ``session-search`` tool (Phase 0 of the
session-search -> SIO merge, see PRD sio_absorb_session_search). This is a thin
Click veneer that forwards argv to the proven argparse engine in
``sio.search.cli`` so 100% of the per-agent search logic is reused byte-for-byte.
A native-Click flag port is deferred to a later phase.
"""

from __future__ import annotations

import click


@click.command(
    "search",
    add_help_option=False,  # let the underlying argparse render --help
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def search_cmd(args: tuple[str, ...]) -> None:
    """Search coding-agent session history across all harnesses.

    Defaults to the last 7 days (recency-first, FR-001 / Cascade Memory
    Protocol).  Override with ``--recent 0`` or ``--all`` to search full
    history::

        sio search "dbt"                     # last 7 days, newest-first
        sio search "dbt" --recent 14         # last 14 days
        sio search "dbt" --recent 0          # full history
        sio search "dbt" --all               # full history, all sources
        sio search "dbt" --agent all --files # fan-out across all 6 harnesses

    Walk into a hit session with ``--around N`` (FR-003 / US2)::

        sio search "error" --around 3        # ±3 turns around each hit (role-aware)
        sio search "FileNotFoundError" --around 2 --recent 0

    ``--around N`` is DISTINCT from ``--context N`` (ripgrep raw lines) and from
    ``--session <uuid>`` (full transcript dump).  It returns role-aware turns
    (user/assistant/tool) clamped at transcript boundaries.

    On zero results within the default window the tool emits a hint:
    ``0 results in last N days — widen with `--recent 0```.

    Run ``sio search --help`` for the full flag set (--agent, --recent,
    --files, --count, --specstory, --list-agents, --skeleton, --around, ...).
    """
    from sio.search.cli import main as _search_main

    raise SystemExit(_search_main(list(args)))
