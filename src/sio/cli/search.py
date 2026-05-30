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

    All flags pass through to the absorbed engine, e.g.::

        sio search "dbt" --agent all --recent 7 --files

    Run ``sio search --help`` for the full flag set (--agent, --recent,
    --files, --count, --specstory, --list-agents, ...).
    """
    from sio.search.cli import main as _search_main

    raise SystemExit(_search_main(list(args)))
