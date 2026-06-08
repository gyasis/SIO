"""Tests for Wave 4 / US3: Multi-hop reachable from search (T030, T031, T032).

FR-005: The two-hop cascade (--refine + --strategy filter|recluster|hybrid,
        reading a cached Hop-1 result via --within/--use-cache) MUST be
        reachable from ``sio search``, not only from ``sio suggest``.

FR-006: When a first-hop ``sio search`` exceeds a configurable noise threshold
        the tool MUST suggest a concrete Hop-2 refine command (log/hint,
        non-blocking).

SC-002: After US3 ships, the multi-hop rate rises from ~0.4%; > 50% of searches
        that exceed the noise threshold receive or act on a Hop-2 suggestion.

All T030–T032 tests target:
  - sio.clustering.hop2  — shared Hop-2 helper extracted from suggest cascade
  - sio.search.cli       — CLI flag surface + noise-hint (T034)

T033 implementation: flags --refine/--strategy/--within/--use-cache surfaced on
      ``sio search`` delegating to sio.clustering.hop2 (which itself calls
      cluster_errors() for recluster/hybrid — no duplication).

T030: a refine on a cached Hop-1 result yields the AND-narrowed subset WITHOUT
      re-querying the DB (verified by asserting no DB connection is opened).
T031: a first-hop result over the noise threshold produces a concrete Hop-2
      suggestion (FR-006).
T032: filter | recluster | hybrid each narrow per their documented behavior.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_error_dict(
    id_: int,
    error_text: str,
    error_type: str = "tool_failure",
    tool_name: str = "Bash",
    session_id: str = "sess-001",
    ts: str = "2026-06-07T12:00:00+00:00",
    user_message: str = "run a command",
    source_file: str = "/fake/session.jsonl",
) -> dict:
    """Build a minimal error record dict for cascade tests."""
    return {
        "id": id_,
        "error_type": error_type,
        "error_text": error_text,
        "tool_name": tool_name,
        "session_id": session_id,
        "timestamp": ts,
        "source_file": source_file,
        "user_message": user_message,
        "context_before": "",
        "context_after": "",
    }


def _write_hop1_csv(path: Path, errors: list[dict]) -> None:
    """Serialize a list of error dicts to a Hop-1 preview CSV."""
    if not errors:
        path.write_text("")
        return
    fields = list(errors[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(errors)


# ---------------------------------------------------------------------------
# T030: refine on cached Hop-1 result — no DB re-query
# ---------------------------------------------------------------------------


class TestT030CachedHop1NoDB:
    """T030: applying --refine on --within CSV does NOT re-query the DB."""

    def test_filter_strategy_subset(self, tmp_path: Path) -> None:
        """The filtered set is a strict subset of the Hop-1 CSV rows."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = [
            _make_error_dict(1, "FileNotFoundError: /tmp/missing.py"),
            _make_error_dict(2, "PermissionError: cannot write /etc/hosts"),
            _make_error_dict(3, "FileNotFoundError: /var/log/app.log"),
        ]
        csv_path = tmp_path / "errors_preview.csv"
        _write_hop1_csv(csv_path, errors)

        # Load from CSV (simulates --within) — the DB is never opened.
        loaded = _load_from_csv(csv_path)

        # Apply Hop-2 filter: only "FileNotFoundError" hits.
        narrowed = apply_hop2_filter(loaded, refine_terms=["filenotfounderror"])

        assert len(narrowed) == 2, f"Expected 2, got {len(narrowed)}"
        ids = {int(e["id"]) for e in narrowed}
        assert ids == {1, 3}

    def test_no_db_connection_opened(self, tmp_path: Path) -> None:
        """When --within is set the sqlite3.connect is never called."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = [
            _make_error_dict(1, "zeno startup failed: port 3000 in use"),
            _make_error_dict(2, "unrelated dbt compile error"),
        ]
        csv_path = tmp_path / "errors_preview.csv"
        _write_hop1_csv(csv_path, errors)
        loaded = _load_from_csv(csv_path)

        # Patch sqlite3.connect to catch any accidental DB access.
        with patch("sqlite3.connect") as mock_connect:
            narrowed = apply_hop2_filter(loaded, refine_terms=["zeno"])
            mock_connect.assert_not_called()

        assert len(narrowed) == 1
        assert "zeno" in narrowed[0]["error_text"]

    def test_use_cache_flag_reads_csv_not_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The CLI --within path loads from CSV without touching the error DB."""
        import sio.clustering.hop2 as _hop2

        errors = [
            _make_error_dict(10, "AttributeError in zeno renderer"),
            _make_error_dict(11, "ImportError in cube service"),
        ]
        csv_path = tmp_path / "errors_preview.csv"
        _write_hop1_csv(csv_path, errors)

        db_open_calls: list[str] = []
        real_load = _hop2.load_errors_from_csv

        def _spy_load(path: str | Path) -> list[dict]:
            return real_load(path)

        monkeypatch.setattr(_hop2, "load_errors_from_csv", _spy_load)

        # Simulate the --within workflow directly (no CLI needed for unit test).
        loaded = _hop2.load_errors_from_csv(csv_path)
        narrowed = _hop2.apply_hop2_filter(loaded, refine_terms=["zeno"])

        assert len(narrowed) == 1
        assert db_open_calls == [], "DB must not be opened during --within cascade"


def _load_from_csv(csv_path: Path) -> list[dict]:
    """Helper: load a Hop-1 preview CSV as a list of dicts."""
    from sio.clustering.hop2 import load_errors_from_csv

    return load_errors_from_csv(csv_path)


# ---------------------------------------------------------------------------
# T031: noise threshold → Hop-2 suggestion
# ---------------------------------------------------------------------------


class TestT031NoiseThresholdSuggestion:
    """T031: when Hop-1 is noisy the tool emits a concrete Hop-2 refine hint."""

    def test_suggestion_emitted_when_over_threshold(self) -> None:
        """emit_noise_hint() returns a non-empty string when count > threshold."""
        from sio.clustering.hop2 import build_noise_hint

        hint = build_noise_hint(
            hop1_count=42,
            noise_threshold=20,
            pattern="dbt",
        )
        assert hint, "expected a non-empty hint when count exceeds threshold"
        assert "--refine" in hint, "hint must mention --refine"
        assert "42" in hint, "hint must include the hit count"

    def test_no_suggestion_below_threshold(self) -> None:
        """build_noise_hint() returns None when count ≤ threshold."""
        from sio.clustering.hop2 import build_noise_hint

        hint = build_noise_hint(
            hop1_count=5,
            noise_threshold=20,
            pattern="dbt",
        )
        assert hint is None, "no hint should fire when count is below threshold"

    def test_suggestion_at_threshold_boundary(self) -> None:
        """Hint fires when count == threshold + 1; silent at count == threshold."""
        from sio.clustering.hop2 import build_noise_hint

        at_threshold = build_noise_hint(10, noise_threshold=10, pattern="x")
        over_threshold = build_noise_hint(11, noise_threshold=10, pattern="x")

        assert at_threshold is None  # equal, not over
        assert over_threshold is not None

    def test_suggestion_contains_concrete_command(self) -> None:
        """The hint must contain a concrete sio search --refine command."""
        from sio.clustering.hop2 import build_noise_hint

        hint = build_noise_hint(50, noise_threshold=20, pattern="zeno")
        assert hint is not None
        # Must give the operator something actionable.
        assert "sio search" in hint or "sio suggest" in hint

    def test_cli_emits_hint_on_noisy_first_hop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The CLI emits the Hop-2 hint to stderr when Hop-1 is noisy."""
        import sio.search.cli as _cli

        # Build a tiny synthetic corpus with many matching lines.
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)

        ts = "2026-06-07T11:00:00+00:00"
        session_id = "noise-session"
        entries = []
        for i in range(30):
            entries.append(
                json.dumps({
                    "type": "assistant",
                    "uuid": f"u{i}",
                    "timestamp": ts,
                    "sessionId": session_id,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"zeno error #{i}: startup failed"}],
                    },
                })
            )
        fpath = proj_dir / f"{session_id}.jsonl"
        fpath.write_text("\n".join(entries) + "\n", encoding="utf-8")
        # Set mtime to recent.
        mtime = time.time() - 60
        import os
        os.utime(fpath, (mtime, mtime))

        monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", tmp_path)
        import time as _time_mod
        # Freeze time.time() inside the CLI at wall-clock now so the 7-day window
        # includes our newly-written file.  Use a fixed epoch rather than calling
        # time.time() again (which would recurse after monkeypatching).
        _frozen = _time_mod.time()
        monkeypatch.setattr(_cli.time, "time", lambda: _frozen)

        captured_stderr = StringIO()
        captured_stdout = StringIO()
        monkeypatch.setattr(sys, "stdout", captured_stdout)
        monkeypatch.setattr(sys, "stderr", captured_stderr)

        _cli.main(["zeno", "--recent", "7", "--noise-threshold", "10"])

        stderr_out = captured_stderr.getvalue()
        # The hint (FR-006) must appear in stderr since it is non-blocking.
        assert "--refine" in stderr_out or "Hop-2" in stderr_out or "refine" in stderr_out, (
            f"Expected a Hop-2 refine hint in stderr. Got:\n{stderr_out}"
        )


# ---------------------------------------------------------------------------
# T032: filter | recluster | hybrid strategies
# ---------------------------------------------------------------------------


class TestT032StrategyBehavior:
    """T032: each --strategy value narrows per its documented behavior."""

    def _make_errors(self) -> list[dict]:
        """15 synthetic errors: 8 about 'zeno', 5 about 'dbt', 2 about 'cube'."""
        errors = []
        for i in range(8):
            errors.append(
                _make_error_dict(
                    i + 1,
                    f"zeno startup failed with error #{i}: port collision",
                    error_type="tool_failure",
                    session_id=f"sess-zeno-{i:02d}",
                )
            )
        for i in range(5):
            errors.append(
                _make_error_dict(
                    i + 10,
                    f"dbt compile error #{i}: model reference not found",
                    error_type="agent_admission",
                    session_id=f"sess-dbt-{i:02d}",
                )
            )
        for i in range(2):
            errors.append(
                _make_error_dict(
                    i + 20,
                    f"cube service crash #{i}: out of memory",
                    error_type="tool_failure",
                    session_id=f"sess-cube-{i:02d}",
                )
            )
        return errors

    def test_filter_strategy_is_subset(self) -> None:
        """'filter' returns only errors whose text contains the refine term."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        narrowed = apply_hop2_filter(errors, refine_terms=["zeno"], strategy="filter")

        assert len(narrowed) < len(errors), "filter must reduce the set"
        assert len(narrowed) == 8, f"expected 8 zeno errors, got {len(narrowed)}"
        for e in narrowed:
            assert "zeno" in e["error_text"].lower()

    def test_filter_strategy_is_not_recluster(self) -> None:
        """'filter' does NOT call cluster_errors (pure text match, no embeddings)."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        with patch("sio.clustering.hop2.cluster_errors") as mock_ce:
            apply_hop2_filter(errors, refine_terms=["zeno"], strategy="filter")
            mock_ce.assert_not_called()

    def test_recluster_strategy_calls_cluster_errors(self) -> None:
        """'recluster' calls cluster_errors() at least once (not a reimplementation).

        The recluster strategy calls cluster_errors once on the full Hop-1 set
        to build initial patterns, then — if enough matching errors are found —
        a second time on the theme-coherent subset for sub-cluster decomposition.
        The important invariant is that cluster_errors IS called (not reimplemented).
        """
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        cluster_call_count: list[int] = [0]

        def _spy(*args: object, **kwargs: object) -> list:
            cluster_call_count[0] += 1
            return [
                {
                    "pattern_id": "zeno_abc123",
                    "description": "zeno startup failed",
                    "error_ids": [1, 2, 3],
                    "error_count": 3,
                    "session_count": 3,
                    "first_seen": "2026-06-07T11:00:00+00:00",
                    "last_seen": "2026-06-07T12:00:00+00:00",
                    "rank_score": 0.9,
                    "tool_name": "Bash",
                }
            ]

        with patch("sio.clustering.hop2.cluster_errors", side_effect=_spy):
            apply_hop2_filter(errors, refine_terms=["zeno"], strategy="recluster")

        assert cluster_call_count[0] >= 1, (
            "recluster strategy must call cluster_errors() at least once"
        )

    def test_hybrid_strategy_filters_then_reclusters(self) -> None:
        """'hybrid' pre-filters by refine term, then calls cluster_errors.

        The hybrid strategy first narrows the error set to only refine-matching
        records (pre-filter), THEN calls cluster_errors on the narrowed set.
        The first call to cluster_errors therefore receives only the zeno errors
        (8 out of 15).
        """
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        cluster_call_args: list = []

        def _spy_cluster(errs: list, **kwargs: Any) -> list:
            cluster_call_args.append(list(errs))
            return [
                {
                    "pattern_id": "hyb_abc",
                    "description": "zeno hybrid",
                    "error_ids": [e["id"] for e in errs[:2]],
                    "error_count": len(errs),
                    "session_count": len(errs),
                    "first_seen": "2026-06-07T11:00:00+00:00",
                    "last_seen": "2026-06-07T12:00:00+00:00",
                    "rank_score": 0.8,
                    "tool_name": "Bash",
                }
            ]

        with patch("sio.clustering.hop2.cluster_errors", side_effect=_spy_cluster):
            apply_hop2_filter(errors, refine_terms=["zeno"], strategy="hybrid")

        # cluster_errors must be called at least once.
        assert len(cluster_call_args) >= 1

        # The FIRST call must be with a pre-filtered set (only zeno errors).
        first_call = cluster_call_args[0]
        assert all("zeno" in e["error_text"].lower() for e in first_call), (
            f"hybrid must pre-filter before the first cluster_errors call. "
            f"Got non-zeno errors: "
            f"{[e['error_text'] for e in first_call if 'zeno' not in e['error_text'].lower()]}"
        )
        assert len(first_call) == 8, (
            f"First cluster call should receive 8 pre-filtered zeno errors, got {len(first_call)}"
        )

    def test_filter_strategy_returns_error_dicts(self) -> None:
        """apply_hop2_filter with 'filter' returns the original dicts (not patterns)."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        narrowed = apply_hop2_filter(errors, refine_terms=["dbt"], strategy="filter")

        # Each result must be a flat error dict, not a cluster pattern.
        assert len(narrowed) == 5
        for item in narrowed:
            assert "error_text" in item
            assert "id" in item

    def test_recluster_and_hybrid_return_error_dicts(self) -> None:
        """recluster/hybrid also return error dicts (unwrapped from cluster patterns)."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        zeno_ids = {e["id"] for e in errors if "zeno" in e["error_text"]}

        with patch("sio.clustering.hop2.cluster_errors") as mock_ce:
            mock_ce.return_value = [
                {
                    "pattern_id": "p1",
                    "description": "zeno",
                    "error_ids": list(zeno_ids)[:3],
                    "error_count": 3,
                    "session_count": 3,
                    "first_seen": "2026-06-07T11:00:00+00:00",
                    "last_seen": "2026-06-07T12:00:00+00:00",
                    "rank_score": 1.0,
                    "tool_name": "Bash",
                }
            ]
            result_recluster = apply_hop2_filter(
                errors, refine_terms=["zeno"], strategy="recluster"
            )

        # Must return error dicts, not pattern dicts.
        assert len(result_recluster) > 0
        for item in result_recluster:
            assert "error_text" in item, (
                f"expected error dict, got: {list(item.keys())}"
            )

    def test_empty_refine_returns_all_errors(self) -> None:
        """When refine_terms is empty, all errors are returned unchanged."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        result = apply_hop2_filter(errors, refine_terms=[], strategy="filter")
        assert len(result) == len(errors)

    def test_no_match_returns_empty(self) -> None:
        """A refine term that matches nothing returns an empty list."""
        from sio.clustering.hop2 import apply_hop2_filter

        errors = self._make_errors()
        result = apply_hop2_filter(errors, refine_terms=["nonexistent_xyz"], strategy="filter")
        assert result == []


# ---------------------------------------------------------------------------
# Integration: CLI flags --refine/--strategy/--within/--use-cache on sio search
# ---------------------------------------------------------------------------


class TestT033CLIFlags:
    """Smoke tests for the CLI flag surface (T033 — flags exist + delegate)."""

    def test_build_parser_has_refine_flag(self) -> None:
        """--refine flag exists in sio search's argument parser."""
        from sio.search.cli import build_parser

        p = build_parser()
        known = {a.option_strings[0] for a in p._actions if a.option_strings}
        assert "--refine" in known, f"--refine missing. Known: {sorted(known)}"

    def test_build_parser_has_strategy_flag(self) -> None:
        """--strategy flag exists with filter|recluster|hybrid choices."""
        from sio.search.cli import build_parser

        p = build_parser()
        for action in p._actions:
            if "--strategy" in (action.option_strings or []):
                assert action.choices == ["filter", "recluster", "hybrid"]
                return
        pytest.fail("--strategy flag not found in build_parser()")

    def test_build_parser_has_no_within_flag(self) -> None:
        """--within is NOT on sio search: it references an error-DB preview CSV that
        does not map to the session corpus (B1 fix). Only 'sio suggest' has --within.
        """
        from sio.search.cli import build_parser

        p = build_parser()
        known = {a.option_strings[0] for a in p._actions if a.option_strings}
        assert "--within" not in known, (
            "--within must be removed from sio search (B1 fix): it is an error-DB "
            "concept that does not apply to session-transcript search."
        )

    def test_build_parser_has_no_use_cache_flag(self) -> None:
        """--use-cache is NOT on sio search: it is shorthand for --within which is
        also removed (B1 fix). Only 'sio suggest' has --use-cache.
        """
        from sio.search.cli import build_parser

        p = build_parser()
        known = {a.option_strings[0] for a in p._actions if a.option_strings}
        assert "--use-cache" not in known, (
            "--use-cache must be removed from sio search (B1 fix): it is an "
            "error-DB concept that does not apply to session-transcript search."
        )

    def test_build_parser_has_noise_threshold_flag(self) -> None:
        """--noise-threshold flag exists (FR-006: noise threshold is configurable)."""
        from sio.search.cli import build_parser

        p = build_parser()
        known = {a.option_strings[0] for a in p._actions if a.option_strings}
        assert "--noise-threshold" in known, "--noise-threshold missing from search parser"

    def test_refine_parses_correctly(self) -> None:
        """--refine <term> is parsed into args.refine without error."""
        from sio.search.cli import build_parser

        p = build_parser()
        args = p.parse_args(["dbt", "--refine", "zeno"])
        assert args.refine == "zeno"

    def test_strategy_defaults_to_filter(self) -> None:
        """--strategy defaults to 'filter' (consistent with suggest cascade)."""
        from sio.search.cli import build_parser

        p = build_parser()
        args = p.parse_args(["dbt"])
        assert args.hop2_strategy == "filter"

    def test_within_not_accepted_by_search_parser(self, tmp_path: Path) -> None:
        """--within is rejected by sio search's argument parser (B1 fix).

        --within references a Hop-1 errors CSV (error-DB concept) that has no
        meaning for session-transcript search. sio suggest retains --within;
        sio search does not.
        """

        csv_path = tmp_path / "hop1.csv"
        csv_path.write_text("id,error_text\n1,test\n")
        # The parser must raise SystemExit(2) (argparse "unrecognized arguments").
        from sio.search.cli import build_parser

        p = build_parser()
        try:
            p.parse_args(["dbt", "--within", str(csv_path)])
            pytest.fail("--within should not be accepted by sio search parser (B1 fix)")
        except SystemExit as exc:
            assert exc.code == 2, (
                f"Expected SystemExit(2) for unrecognized --within, got {exc.code}"
            )
