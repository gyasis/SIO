"""wuphf: a KNOWN_AGENT with NO local transcript store.

wuphf (the twicedata office platform) writes its mined rows directly into
the SIO database via an external bridge -- it has no on-disk session
directory the way claude/codex/goose/... do. So:

  * it IS a KNOWN_AGENT (drives the `agent` column, `errors_wuphf` view,
    and `sio errors --agent wuphf` -- covered in tests/unit/db/test_agent_isolation.py)
  * it must NOT be assumed to have a directory to walk by anything that
    enumerates agents to read transcripts off disk.

This file covers the second half: the enumeration surfaces stay safe with
wuphf present in KNOWN_AGENTS, because none of them derive their probe list
from KNOWN_AGENTS in the first place -- they enumerate PARSERS (session
search) or hardcoded per-agent paths (sio live discovery), so wuphf is
skipped by construction rather than special-cased.
"""

from __future__ import annotations

from sio.core.session_handle import KNOWN_AGENTS


def test_wuphf_is_a_known_agent():
    assert "wuphf" in KNOWN_AGENTS


def test_wuphf_has_no_session_search_parser():
    """It must never be offered as a session-search --agent choice: there is
    nothing on disk for it to search."""
    from sio.search.cli import PARSERS

    assert "wuphf" not in PARSERS


def test_session_search_agent_all_does_not_crash_with_wuphf_present():
    """`--agent all` fans out over PARSERS, not KNOWN_AGENTS -- wuphf being a
    KNOWN_AGENT must not make this loop try (and fail) to probe it."""
    from sio.search.cli import main

    rc = main(["zzz-pattern-unlikely-to-match-anything-zzz", "--agent", "all", "--files"])
    # 0 = matched something, 1/2 = no matches (various "widen your search" exit
    # codes) -- any of these is fine; only an uncaught exception is a crash.
    assert rc in (0, 1, 2)


def test_session_search_rejects_wuphf_as_an_agent_choice():
    """argparse's --agent choices come from PARSERS.keys() + 'all', so an
    explicit --agent wuphf is refused up front (exit 2), never a KeyError
    reaching PARSERS[args.agent]."""
    import pytest

    from sio.search.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["pattern", "--agent", "wuphf"])
    assert exc_info.value.code == 2


def test_mine_bulk_agent_choice_excludes_wuphf():
    """`sio mine`'s bulk --agent option is an explicit, hand-maintained list
    (not derived from KNOWN_AGENTS) -- wuphf must stay off it, since bulk
    mining assumes a session-search parser exists for the agent."""
    from click.testing import CliRunner

    from sio.cli.main import cli

    result = CliRunner().invoke(cli, ["mine", "--agent", "wuphf", "--since", "1 day"])
    assert result.exit_code != 0
    assert "wuphf" in (result.output or "") or result.exception is not None


def test_live_discover_sessions_does_not_crash_with_wuphf_present():
    """sio live's discovery enumerates hardcoded per-agent session
    directories, not KNOWN_AGENTS -- it must keep working (and simply never
    surface a wuphf row, since wuphf never writes a transcript file)."""
    from sio.cli.live import discover_sessions

    rows = discover_sessions(minutes=1)
    assert isinstance(rows, list)
    assert all(r.get("agent") != "wuphf" for r in rows)


def test_adapter_factory_has_no_file_probe_for_wuphf():
    """Direct, explicit resolution for wuphf is expected to say "no such
    thing", not silently probe a nonexistent directory -- confirming the
    'no local transcript store' contract stays explicit rather than being
    accidentally satisfied by a stray directory match."""
    import pytest

    from sio.adapters.factory import adapter_for, manifest_from_handle

    with pytest.raises(NotImplementedError):
        adapter_for("wuphf")
    with pytest.raises(NotImplementedError):
        manifest_from_handle("wuphf:some-office-task-id")
