"""Characterization tests for sio suggest's Hop-2 cascade logic.

These tests lock the CURRENT (pre-refactor) behavior of the Hop-2 cascade
embedded in ``src/sio/cli/main.py:2207–2331``.  They must pass GREEN against
the unmodified code, and MUST STILL PASS after the refactor that replaces the
inline cascade with calls to ``sio.clustering.hop2``.

Locked behaviors
----------------
1.  ``filter`` + ``hybrid`` (pre-cluster): when ``--refine`` is set and
    strategy is ``filter`` or ``hybrid``, ``errors_to_cluster`` is narrowed to
    only errors whose searchable fields contain at least one refine term BEFORE
    ``cluster_errors()`` is called.  The ``filter`` strategy does NOT call
    ``cluster_errors()`` on the full set.

2.  ``recluster`` + ``hybrid`` (post-cluster): after clustering, patterns whose
    description or underlying errors match the refine terms are selected; their
    errors are re-clustered with the tighter ``recluster_threshold``.  The
    result is PATTERNS (via ``rank_patterns()``) stored in ``ranked``, not raw
    error dicts.  Fallback: when fewer than 2 theme-coherent errors are found,
    ``ranked`` is set to ``matching_patterns`` (pattern-filter fallback).

3.  ``--within`` / ``--use-cache``: the inline CSV loader in suggest strips
    ``context_before`` and ``context_after`` to ``""`` (they are set to empty
    string, not the actual CSV value), and loads all other standard fields.

4.  Presentation strings: the specific console.print() line for the Hop-2
    sub-cluster decomposition path contains the pattern
    "sub-cluster(s) (threshold=<N>, strategy=<S>)".

5.  Empty-refine passthrough: when ``refine_term`` is ``None``, the Hop-2
    block is skipped entirely — ``errors_to_cluster`` is unchanged before
    ``cluster_errors()``.

Design
------
Tests exercise the cascade at the UNIT level by patching:
  - ``sio.cli.main.get_error_records`` → returns controlled error dicts
  - ``sio.cli.main.cluster_errors`` → spy / deterministic stub
  - ``sio.cli.main.rank_patterns`` → identity pass-through
  - ``sio.cli.main.insert_pattern``, ``link_error_to_pattern``,
    ``mark_stale_for_new_cycle``, ``build_dataset``, ``generate_suggestions``
    → no-ops so the function exits after the cascade (preview mode or auto
    short-circuit)
  - ``os.path.exists`` in the suggest entrypoint → True (fake DB present)

The CLI is invoked via ``click.testing.CliRunner`` so the full Click argument
parsing is exercised.
"""

from __future__ import annotations

import csv
import os
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_error(
    id_: int,
    error_text: str,
    error_type: str = "tool_failure",
    tool_name: str = "Bash",
    session_id: str = "sess-001",
    ts: str = "2026-06-07T12:00:00+00:00",
    user_message: str = "run a command",
    source_file: str = "/fake/session.jsonl",
    context_before: str = "",
    context_after: str = "",
) -> dict:
    """Minimal error record dict matching get_error_records() output schema."""
    return {
        "id": id_,
        "error_type": error_type,
        "error_text": error_text,
        "tool_name": tool_name,
        "session_id": session_id,
        "timestamp": ts,
        "source_file": source_file,
        "user_message": user_message,
        "context_before": context_before,
        "context_after": context_after,
    }


def _mixed_errors() -> list[dict]:
    """10 errors: 6 about 'zeno', 4 about 'dbt'."""
    errors = []
    for i in range(6):
        errors.append(
            _make_error(
                i + 1,
                f"zeno startup failed with error #{i}: port collision",
                error_type="tool_failure",
                session_id=f"sess-zeno-{i:02d}",
            )
        )
    for i in range(4):
        errors.append(
            _make_error(
                i + 10,
                f"dbt compile error #{i}: model reference not found",
                error_type="agent_admission",
                session_id=f"sess-dbt-{i:02d}",
            )
        )
    return errors


def _write_hop1_csv(path: Path, errors: list[dict]) -> None:
    """Write error dicts to a Hop-1 preview CSV (same format as --preview produces)."""
    if not errors:
        path.write_text("")
        return
    fields = [
        "id", "error_type", "error_text", "tool_name", "session_id",
        "timestamp", "source_file", "user_message", "context_before", "context_after",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(errors)


# Patch targets — functions are imported LOCALLY inside suggest(), so we must
# patch at their source modules, not at sio.cli.main.
_QUERIES = "sio.core.db.queries"
_CLUSTERER = "sio.clustering.pattern_clusterer"
_RANKER = "sio.clustering.ranker"
_BUILDER = "sio.datasets.builder"
_GENERATOR = "sio.suggestions.generator"


def _make_pattern(
    pattern_id: str,
    description: str,
    error_ids: list[int],
) -> dict:
    return {
        "pattern_id": pattern_id,
        "description": description,
        "error_ids": error_ids,
        "error_count": len(error_ids),
        "session_count": len(error_ids),
        "first_seen": "2026-06-07T11:00:00+00:00",
        "last_seen": "2026-06-07T12:00:00+00:00",
        "rank_score": 0.9,
        "tool_name": "Bash",
        "centroid_embedding": None,
    }


def _run_suggest_preview(
    tmp_path: Path,
    *extra_args: str,
    errors_fixture: list[dict] | None = None,
    cluster_spy: Any = None,
) -> tuple[str, list[list[dict]]]:
    """Run ``sio suggest --preview`` via CliRunner with a fake DB.

    Returns ``(output_text, cluster_calls_args)`` where ``cluster_calls_args``
    is a list of the positional arg lists each ``cluster_errors()`` call received.
    """
    from sio.cli.main import cli

    if errors_fixture is None:
        errors_fixture = _mixed_errors()

    cluster_call_inputs: list[list[dict]] = []

    def _default_spy_cluster(errs: list, **kwargs: Any) -> list:
        cluster_call_inputs.append(list(errs))
        # Return one pattern covering the first two errors.
        ids = [e["id"] for e in errs[:2]] if len(errs) >= 2 else [e["id"] for e in errs]
        return [_make_pattern("p_spy", "spy pattern", ids)]

    if cluster_spy is None:
        actual_spy = _default_spy_cluster
    else:
        # Wrap the caller's spy so we also track calls in cluster_call_inputs.
        _caller_spy = cluster_spy

        def _wrapped_spy(errs: list, **kwargs: Any) -> list:
            cluster_call_inputs.append(list(errs))
            return _caller_spy(errs, **kwargs)

        actual_spy = _wrapped_spy

    fake_db = tmp_path / "sio.db"
    fake_db.write_bytes(b"")  # fake presence

    runner = CliRunner()

    with (
        patch(f"{_QUERIES}.get_error_records", return_value=errors_fixture),
        patch(f"{_CLUSTERER}.cluster_errors", side_effect=actual_spy),
        patch(f"{_RANKER}.rank_patterns", side_effect=lambda ps, **kw: ps),
        patch(f"{_QUERIES}.insert_pattern", return_value=1),
        patch(f"{_QUERIES}.link_error_to_pattern"),
        patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
        patch(f"{_BUILDER}.build_dataset", return_value=None),
        patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
        patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
    ):
        result = runner.invoke(
            cli,
            ["suggest", "--preview"] + list(extra_args),
            catch_exceptions=False,
        )

    return result.output, cluster_call_inputs


# ---------------------------------------------------------------------------
# 1. filter strategy — pre-cluster narrowing, no cluster_errors call
# ---------------------------------------------------------------------------


class TestFilterStrategy:
    """Locks: filter pre-narrows errors_to_cluster BEFORE cluster_errors()."""

    def test_filter_narrows_to_matching_errors_only(self, tmp_path: Path) -> None:
        """cluster_errors receives only 'zeno' errors when --refine=zeno --strategy=filter."""
        errors = _mixed_errors()
        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "filter",
            errors_fixture=errors,
        )

        assert len(cluster_calls) >= 1, "cluster_errors must be called at least once"
        first_call = cluster_calls[0]
        assert len(first_call) == 6, (
            f"filter should pass 6 zeno errors to cluster_errors, got {len(first_call)}"
        )
        for e in first_call:
            assert "zeno" in e["error_text"].lower(), (
                f"Non-zeno error reached cluster_errors with filter strategy: {e['error_text']}"
            )

    def test_filter_does_not_call_cluster_errors_on_full_set(self, tmp_path: Path) -> None:
        """filter strategy never passes all 10 errors to cluster_errors."""
        errors = _mixed_errors()
        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "filter",
            errors_fixture=errors,
        )

        for call_args in cluster_calls:
            assert len(call_args) < len(errors), (
                "filter must pre-narrow before cluster_errors — full set must not be passed"
            )

    def test_filter_zero_results_exits_gracefully(self, tmp_path: Path) -> None:
        """When --refine matches nothing, suggest exits with a 'No errors' message."""
        errors = _mixed_errors()
        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "nonexistent_xyz_term",
            "--strategy", "filter",
            errors_fixture=errors,
        )
        assert "No errors" in output, f"Expected 'No errors' message, got:\n{output}"


# ---------------------------------------------------------------------------
# 2. recluster strategy — post-cluster pattern-level narrowing
# ---------------------------------------------------------------------------


class TestReclusterStrategy:
    """Locks: recluster first calls cluster_errors on the FULL Hop-1 set,
    then re-clusters the theme-coherent subset.  Returns PATTERNS."""

    def test_recluster_calls_cluster_errors_on_full_set_first(self, tmp_path: Path) -> None:
        """cluster_errors receives the full (un-pre-filtered) error set on first call."""
        errors = _mixed_errors()  # 10 errors

        first_call_size: list[int] = []

        def _spy(errs: list, **kwargs: Any) -> list:
            first_call_size.append(len(errs))
            # Build patterns: one for zeno errors (ids 1-6), one for dbt (ids 10-13)
            zeno_ids = [e["id"] for e in errs if "zeno" in e["error_text"]]
            dbt_ids = [e["id"] for e in errs if "dbt" in e["error_text"]]
            patterns = []
            if zeno_ids:
                patterns.append(_make_pattern("p_zeno", "zeno startup failures", zeno_ids))
            if dbt_ids:
                patterns.append(_make_pattern("p_dbt", "dbt compile errors", dbt_ids))
            return patterns

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "recluster",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        assert len(cluster_calls) >= 1, "cluster_errors must be called"
        # The first call to cluster_errors for recluster should receive all 10 errors
        # (no pre-filtering for recluster — only post-cluster pattern selection).
        assert cluster_calls[0] == errors or len(cluster_calls[0]) == len(errors), (
            f"recluster must NOT pre-filter; first cluster_errors call got "
            f"{len(cluster_calls[0])} errors, expected {len(errors)}"
        )

    def test_recluster_fallback_when_too_few_matching_errors(self, tmp_path: Path) -> None:
        """When fewer than 2 theme-coherent errors exist, fallback message is printed."""
        # Errors where only ONE has 'zeno' — triggers the fallback path
        errors = [
            _make_error(1, "zeno startup failed: only one"),
            _make_error(2, "dbt compile error: unrelated"),
            _make_error(3, "another dbt error: still unrelated"),
        ]

        def _spy(errs: list, **kwargs: Any) -> list:
            # Return pattern covering the single zeno error
            zeno_ids = [e["id"] for e in errs if "zeno" in e["error_text"]]
            dbt_ids = [e["id"] for e in errs if "dbt" in e["error_text"]]
            patterns = []
            if zeno_ids:
                patterns.append(_make_pattern("p_z", "zeno pattern", zeno_ids))
            if dbt_ids:
                patterns.append(_make_pattern("p_d", "dbt pattern", dbt_ids))
            return patterns

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "recluster",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        # The fallback message must appear in output.
        assert "recluster fallback" in output.lower() or "pattern-filter" in output.lower(), (
            f"Expected recluster fallback message. Got:\n{output}"
        )
        # cluster_errors should have been called exactly ONCE (first pass only; no second pass
        # because matching_errors < 2).
        assert len(cluster_calls) == 1, (
            f"Fallback path must not call cluster_errors twice; got {len(cluster_calls)} calls"
        )

    def test_recluster_happy_path_calls_cluster_errors_twice(self, tmp_path: Path) -> None:
        """When >= 2 theme-coherent errors exist, cluster_errors is called twice."""
        errors = _mixed_errors()  # 6 zeno + 4 dbt

        call_count: list[int] = [0]

        def _spy(errs: list, **kwargs: Any) -> list:
            call_count[0] += 1
            zeno_ids = [e["id"] for e in errs if "zeno" in (e.get("error_text") or "")]
            dbt_ids = [e["id"] for e in errs if "dbt" in (e.get("error_text") or "")]
            patterns = []
            if zeno_ids:
                patterns.append(_make_pattern("p_zeno", "zeno startup failures", zeno_ids))
            if dbt_ids:
                patterns.append(_make_pattern("p_dbt", "dbt compile errors", dbt_ids))
            return patterns

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "recluster",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        assert call_count[0] == 2, (
            f"recluster happy path must call cluster_errors twice; got {call_count[0]} call(s)"
        )

    def test_recluster_presentation_line(self, tmp_path: Path) -> None:
        """The sub-cluster decomposition console line matches the locked format."""
        errors = _mixed_errors()

        def _spy(errs: list, **kwargs: Any) -> list:
            zeno_ids = [e["id"] for e in errs if "zeno" in (e.get("error_text") or "")]
            dbt_ids = [e["id"] for e in errs if "dbt" in (e.get("error_text") or "")]
            patterns = []
            if zeno_ids:
                patterns.append(_make_pattern("p_zeno", "zeno startup failures", zeno_ids))
            if dbt_ids:
                patterns.append(_make_pattern("p_dbt", "dbt compile errors", dbt_ids))
            return patterns

        output, _ = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "recluster",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        # Locked presentation: "sub-cluster(s) (threshold=<N>, strategy=<S>)"
        assert "sub-cluster" in output.lower(), (
            f"Expected 'sub-cluster' in output. Got:\n{output}"
        )
        assert "threshold=0.85" in output, (
            f"Expected 'threshold=0.85' (default recluster threshold) in output. Got:\n{output}"
        )
        assert "strategy=recluster" in output, (
            f"Expected 'strategy=recluster' in output. Got:\n{output}"
        )


# ---------------------------------------------------------------------------
# 3. hybrid strategy
# ---------------------------------------------------------------------------


class TestHybridStrategy:
    """Locks: hybrid pre-filters errors (like filter), then reclusters survivors."""

    def test_hybrid_pre_filters_before_cluster_errors(self, tmp_path: Path) -> None:
        """cluster_errors receives only refine-matching errors on the first call."""
        errors = _mixed_errors()  # 6 zeno + 4 dbt

        def _spy(errs: list, **kwargs: Any) -> list:
            ids = [e["id"] for e in errs]
            return [_make_pattern("p_hyb", "hybrid pattern", ids[:2])]

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "hybrid",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        assert len(cluster_calls) >= 1, "cluster_errors must be called"
        first_call = cluster_calls[0]
        assert len(first_call) == 6, (
            f"hybrid must pre-filter to 6 zeno errors before cluster_errors; got {len(first_call)}"
        )
        for e in first_call:
            assert "zeno" in e["error_text"].lower()

    def test_hybrid_does_post_cluster_recluster(self, tmp_path: Path) -> None:
        """hybrid triggers the post-cluster recluster branch (calls cluster_errors twice)."""
        errors = _mixed_errors()
        call_count: list[int] = [0]

        def _spy(errs: list, **kwargs: Any) -> list:
            call_count[0] += 1
            ids = [e["id"] for e in errs]
            return [_make_pattern("p_hyb", "zeno pattern", ids)]

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            "--refine", "zeno",
            "--strategy", "hybrid",
            errors_fixture=errors,
            cluster_spy=_spy,
        )

        # hybrid = pre-filter + post-cluster recluster → cluster_errors called twice
        assert call_count[0] == 2, (
            f"hybrid must call cluster_errors twice (pre-filter recluster); got {call_count[0]}"
        )


# ---------------------------------------------------------------------------
# 4. --within / --use-cache: CSV loader behavior in suggest
# ---------------------------------------------------------------------------


class TestWithinCache:
    """Locks: suggest's inline CSV loader behavior."""

    def test_within_skips_db_query(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When --within is set, get_error_records is NOT called."""
        errors = _mixed_errors()[:3]
        csv_path = tmp_path / "hop1.csv"
        _write_hop1_csv(csv_path, errors)

        from sio.cli.main import cli

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        get_records_called: list[bool] = []

        def _spy_get_records(*args: Any, **kwargs: Any) -> list:
            get_records_called.append(True)
            return []

        runner = CliRunner()

        with (
            patch(f"{_QUERIES}.get_error_records", side_effect=_spy_get_records),
            patch(f"{_CLUSTERER}.cluster_errors", return_value=[]),
            patch(f"{_RANKER}.rank_patterns", return_value=[]),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli,
                ["suggest", "--preview", "--within", str(csv_path)],
                catch_exceptions=False,
            )

        assert not get_records_called, (
            "get_error_records must NOT be called when --within is set"
        )

    def test_within_loads_errors_from_csv(self, tmp_path: Path) -> None:
        """Errors from the --within CSV reach cluster_errors (not from DB)."""
        errors = _mixed_errors()[:3]  # 3 errors
        csv_path = tmp_path / "hop1.csv"
        _write_hop1_csv(csv_path, errors)

        from sio.cli.main import cli

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        cluster_inputs: list[list] = []

        def _spy_cluster(errs: list, **kwargs: Any) -> list:
            cluster_inputs.append(list(errs))
            return []

        runner = CliRunner()

        with (
            patch(f"{_QUERIES}.get_error_records", return_value=[]),
            patch(f"{_CLUSTERER}.cluster_errors", side_effect=_spy_cluster),
            patch(f"{_RANKER}.rank_patterns", return_value=[]),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli,
                ["suggest", "--preview", "--within", str(csv_path)],
                catch_exceptions=False,
            )

        assert len(cluster_inputs) >= 1, "cluster_errors must be called with CSV errors"
        # 3 errors from CSV should reach cluster_errors
        assert len(cluster_inputs[0]) == 3, (
            f"Expected 3 errors from CSV in cluster_errors, got {len(cluster_inputs[0])}"
        )

    def test_within_csv_loader_strips_context_fields(
        self, tmp_path: Path
    ) -> None:
        """suggest's inline CSV loader sets context_before/context_after to '' (not CSV value).

        This locks the CURRENT behavior of the inline CSV loader in main.py:2106-2123:
        context_before and context_after are hardcoded to "" regardless of CSV contents.
        """
        errors = [
            _make_error(
                1,
                "zeno startup failed",
                context_before="BEFORE_CONTEXT",
                context_after="AFTER_CONTEXT",
            )
        ]
        csv_path = tmp_path / "hop1.csv"
        _write_hop1_csv(csv_path, errors)

        from sio.cli.main import cli

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        cluster_inputs: list[list] = []

        def _spy_cluster(errs: list, **kwargs: Any) -> list:
            cluster_inputs.append(list(errs))
            return []

        runner = CliRunner()

        with (
            patch(f"{_QUERIES}.get_error_records", return_value=[]),
            patch(f"{_CLUSTERER}.cluster_errors", side_effect=_spy_cluster),
            patch(f"{_RANKER}.rank_patterns", return_value=[]),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli,
                ["suggest", "--preview", "--within", str(csv_path)],
                catch_exceptions=False,
            )

        assert cluster_inputs, f"Expected cluster_errors to be called. Output:\n{result.output}"
        loaded = cluster_inputs[0][0]
        # The inline loader hard-codes context_before/context_after to "" (line 2119-2122).
        assert loaded["context_before"] == "", (
            f"suggest CSV loader must strip context_before to ''; got: {loaded['context_before']!r}"
        )
        assert loaded["context_after"] == "", (
            f"suggest CSV loader must strip context_after to ''; got: {loaded['context_after']!r}"
        )


# ---------------------------------------------------------------------------
# 5. Empty refine passthrough (no-op)
# ---------------------------------------------------------------------------


class TestNoRefineTerm:
    """Locks: when refine_term is None, errors_to_cluster is unchanged pre-cluster."""

    def test_no_refine_passes_all_errors_to_cluster(self, tmp_path: Path) -> None:
        """Without --refine, cluster_errors receives the full unfiltered error set."""
        errors = _mixed_errors()

        output, cluster_calls = _run_suggest_preview(
            tmp_path,
            errors_fixture=errors,
            # No --refine flag.
        )

        assert len(cluster_calls) >= 1, "cluster_errors must be called"
        assert len(cluster_calls[0]) == len(errors), (
            f"Without --refine, all {len(errors)} errors must reach cluster_errors; "
            f"got {len(cluster_calls[0])}"
        )

    def test_no_refine_no_hop2_output(self, tmp_path: Path) -> None:
        """Without --refine, the Hop-2 console lines do NOT appear in output."""
        errors = _mixed_errors()

        output, _ = _run_suggest_preview(tmp_path, errors_fixture=errors)

        assert "Hop-2" not in output and "sub-cluster" not in output.lower(), (
            f"Hop-2 output must not appear when --refine is not set. Got:\n{output}"
        )
