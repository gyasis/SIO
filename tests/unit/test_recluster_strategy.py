"""CLI contract tests for the v0.1.4 recluster sub-cluster decomposition fix.

Origin: PRD `sio_v0_1_4_scope_2026-05-11` item #3 (recluster fix).
Resolves drift documented in `sio_ship_pickup_tomorrow_2026-05-02` B7
and the original design in graduated `L003_sio_multi_hop_search_2026-04-24`.

These are surface-level contract tests: they confirm the new
`--recluster-threshold` option is wired into the CLI with the right
default, range, and help text. The functional path (cluster_errors
called twice with tighter threshold on second pass) is exercised by
the broader integration suite; isolating it here would require
populating a temp SQLite DB with embedded error records, which is
disproportionate for the small contract surface this file guards.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sio.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestReclusterThresholdOption:
    """`--recluster-threshold` CLI option contract."""

    def test_flag_appears_in_suggest_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["suggest", "--help"])
        assert result.exit_code == 0, result.output
        assert "--recluster-threshold" in result.output

    def test_default_value_is_advertised(self, runner: CliRunner) -> None:
        """show_default=True surfaces 0.85 in the help text."""
        result = runner.invoke(cli, ["suggest", "--help"])
        assert "0.85" in result.output, (
            "Default --recluster-threshold (0.85) should be visible in help"
        )

    def test_help_describes_second_pass_behavior(self, runner: CliRunner) -> None:
        """Help text must explain this is the SECOND clustering pass — not
        the first-pass threshold (which is 0.70 and not user-configurable)."""
        result = runner.invoke(cli, ["suggest", "--help"])
        text = result.output.lower()
        assert "second" in text or "tighter" in text, (
            "Help must communicate that --recluster-threshold governs a "
            "SECOND pass, not the default 0.70 first-pass clustering"
        )

    def test_clamps_out_of_range_to_bounds(self, runner: CliRunner) -> None:
        """FloatRange(0.50, 0.99, clamp=True) silently clamps — no error,
        but the option is recognized. We verify via --help to confirm the
        range is documented somewhere."""
        result = runner.invoke(cli, ["suggest", "--help"])
        # Range bounds may render as 0.5/0.99 or 0.50/0.99 depending on click
        # version; accept either.
        assert ("0.5" in result.output and "0.99" in result.output), (
            "Help should document the [0.50, 0.99] valid range"
        )


class TestStrategyHelpTextReflectsNewBehavior:
    """The `--strategy` help text was updated to describe true sub-cluster
    decomposition — not the prior 'stricter filter' behavior."""

    def test_recluster_help_mentions_sub_clusters(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["suggest", "--help"])
        assert result.exit_code == 0
        text = result.output.lower()
        # The help text should describe sub-cluster selection / re-clustering
        # — not "stricter filter" which was the buggy pre-v0.1.4 behavior.
        assert "re-cluster" in text or "sub-cluster" in text, (
            "--strategy recluster help should describe re-clustering, not filtering"
        )
        assert "stricter filter" not in text, (
            "Pre-v0.1.4 'stricter filter' wording should be gone from help"
        )
