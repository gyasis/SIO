"""T040-T042: US4 — Economy guard on the DB load.

Tests verify that ``sio suggest`` replaces the unbounded ``limit=0`` load
with a bounded default and that the preview-cache is size-bounded + stale-
checked.  Spec: FR-007, FR-008, FR-009 from
``specs/005-search-data-remediation/spec.md``.

Defaults chosen (not pinned in spec numerics, justified by FR-007/008/009):
  DEFAULT_SINCE_DAYS = 30   — load only errors from the last 30 days
  DEFAULT_ROW_CAP    = 5000 — hard row limit even inside the window
  CACHE_MAX_ROWS     = 10000 — preview CSV size bound

These are the values the implementation and these tests agree on.
"""

from __future__ import annotations

import csv
import os
import time as _time_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Patch targets — imported locally inside suggest(), so patch at source.
# ---------------------------------------------------------------------------
_QUERIES = "sio.core.db.queries"
_CLUSTERER = "sio.clustering.pattern_clusterer"
_RANKER = "sio.clustering.ranker"
_BUILDER = "sio.datasets.builder"
_GENERATOR = "sio.suggestions.generator"

# Defaults that the implementation MUST use.
DEFAULT_SINCE_DAYS = 30
DEFAULT_ROW_CAP = 5000
CACHE_MAX_ROWS = 10000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_error(
    id_: int,
    ts: str = "2026-06-07T12:00:00+00:00",
    error_text: str = "test error",
    error_type: str = "tool_failure",
) -> dict:
    return {
        "id": id_,
        "error_type": error_type,
        "error_text": error_text,
        "tool_name": "Bash",
        "session_id": f"sess-{id_:04d}",
        "timestamp": ts,
        "source_file": "/fake/session.jsonl",
        "user_message": "run command",
        "context_before": "",
        "context_after": "",
    }


def _make_errors(n: int, ts: str = "2026-06-07T12:00:00+00:00") -> list[dict]:
    """Make N error dicts all with the same timestamp."""
    return [_make_error(i + 1, ts=ts) for i in range(n)]


def _write_preview_csv(
    path: Path,
    errors: list[dict],
    age_seconds: float = 0,
) -> None:
    """Write a preview CSV and set its mtime to simulate age."""
    fields = [
        "id", "error_type", "error_text", "tool_name", "session_id",
        "timestamp", "source_file", "user_message",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for e in errors:
            writer.writerow({k: e.get(k, "") for k in fields})
    if age_seconds:
        mtime = _time_module.time() - age_seconds
        os.utime(path, (mtime, mtime))


def _run_suggest(
    tmp_path: Path,
    *extra_args: str,
    errors_from_db: list[dict] | None = None,
    fake_db: Path | None = None,
) -> tuple[str, list[dict] | None]:
    """Invoke ``sio suggest --preview`` via CliRunner.

    Returns ``(output_text, get_error_records_call_kwargs)`` where
    ``get_error_records_call_kwargs`` is the kwargs dict from the first call
    to the mocked ``get_error_records``, or ``None`` if not called (e.g.
    --use-cache path).
    """
    from sio.cli.main import cli

    if errors_from_db is None:
        errors_from_db = _make_errors(5)

    if fake_db is None:
        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

    call_record: list[dict] = []

    def _spy_get_error_records(conn: Any, **kwargs: Any) -> list[dict]:
        call_record.append(dict(kwargs))
        return errors_from_db

    runner = CliRunner()
    with (
        patch(f"{_QUERIES}.get_error_records", side_effect=_spy_get_error_records),
        patch(f"{_CLUSTERER}.cluster_errors", return_value=[]),
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

    return result.output, (call_record[0] if call_record else None)


# ---------------------------------------------------------------------------
# T040: Default load is bounded + bound is logged
# ---------------------------------------------------------------------------


class TestDefaultBoundedLoad:
    """T040: No time flag → bounded row set, bound logged to output."""

    def test_default_load_passes_since_to_get_error_records(
        self, tmp_path: Path
    ) -> None:
        """get_error_records must be called with a 'since' kwarg on default run.

        FR-007: ``sio suggest`` MUST replace the unbounded ``limit=0`` error
        load with a bounded default (row cap and/or ``--since`` window).
        """
        _, kwargs = _run_suggest(tmp_path)
        assert kwargs is not None, "get_error_records was not called"
        # Must pass a non-empty 'since' string (date window bound)
        assert "since" in kwargs, (
            "get_error_records must be called with 'since' kwarg on default run"
        )
        assert kwargs["since"] is not None and kwargs["since"] != "", (
            "Default 'since' must not be None/empty — that would be unbounded"
        )

    def test_default_load_passes_limit_greater_than_zero(
        self, tmp_path: Path
    ) -> None:
        """get_error_records must receive limit > 0 (never limit=0) by default.

        FR-007: the unbounded ``limit=0`` sentinel MUST be replaced.
        """
        _, kwargs = _run_suggest(tmp_path)
        assert kwargs is not None, "get_error_records was not called"
        limit = kwargs.get("limit")
        assert limit is not None, (
            "get_error_records must receive a 'limit' kwarg on default run"
        )
        assert isinstance(limit, int) and limit > 0, (
            f"Default limit must be > 0 (got {limit!r}) — limit=0 means unbounded"
        )

    def test_default_load_limit_does_not_exceed_cap(
        self, tmp_path: Path
    ) -> None:
        """Default limit must be ≤ DEFAULT_ROW_CAP (5000).

        Ensures the default is conservative enough to protect the 951 MB DB.
        """
        _, kwargs = _run_suggest(tmp_path)
        assert kwargs is not None, "get_error_records was not called"
        limit = kwargs.get("limit", 0)
        assert limit <= DEFAULT_ROW_CAP, (
            f"Default limit {limit} exceeds cap {DEFAULT_ROW_CAP}"
        )

    def test_default_since_window_is_at_most_30_days(
        self, tmp_path: Path
    ) -> None:
        """Default since-window must not exceed 30 days.

        The DB is 951 MB; a conservative default protects against runaway loads.
        """
        from datetime import datetime, timezone

        _, kwargs = _run_suggest(tmp_path)
        assert kwargs is not None, "get_error_records was not called"
        since_str = kwargs.get("since", "")
        assert since_str, "Default 'since' must be non-empty"

        try:
            since_dt = datetime.fromisoformat(since_str)
        except ValueError:
            pytest.fail(f"'since' is not a valid ISO datetime: {since_str!r}")

        now = datetime.now(timezone.utc)
        age_days = (now - since_dt).days
        assert age_days <= DEFAULT_SINCE_DAYS, (
            f"Default since-window is {age_days} days — exceeds {DEFAULT_SINCE_DAYS}-day cap"
        )

    def test_bound_is_logged_in_output(self, tmp_path: Path) -> None:
        """The applied bound (window or cap) must be printed to output.

        FR-007: MUST log the bound applied.  We look for a message containing
        the since-date or row cap so the operator can see what was applied.
        """
        output, _ = _run_suggest(tmp_path)
        lower = output.lower()
        # The bound log must mention at least one of: "bound", "since",
        # "window", "limit", "last", "days", "rows", or a date-like pattern.
        signals = [
            "bound", "since", "window", "last", "days", "rows",
            "default", "loading",
        ]
        assert any(sig in lower for sig in signals), (
            f"Output must mention the applied load bound. "
            f"Signals checked: {signals}.\nActual output:\n{output}"
        )


# ---------------------------------------------------------------------------
# T041: --since / cap override widens the load and surfaces it
# ---------------------------------------------------------------------------


class TestSinceOverrideWidensLoad:
    """T041: --since <date> and explicit cap override widen load + surface it (AS-2)."""

    def test_since_flag_passes_date_to_get_error_records(
        self, tmp_path: Path
    ) -> None:
        """--since 2024-01-01 must be forwarded as the 'since' kwarg.

        FR-008: ``sio suggest`` MUST accept ``--since`` to widen the load.
        """
        _, kwargs = _run_suggest(tmp_path, "--since", "2024-01-01")
        assert kwargs is not None, "get_error_records was not called"
        since = kwargs.get("since", "")
        assert "2024-01-01" in since, (
            f"--since 2024-01-01 should forward that date, got: {since!r}"
        )

    def test_since_flag_widens_relative_to_default(
        self, tmp_path: Path
    ) -> None:
        """--since 90d must produce an older cutoff than the default.

        The since-date from --since must be earlier (older) than the default,
        meaning a wider load window.
        """
        from datetime import datetime

        _, default_kwargs = _run_suggest(tmp_path)
        _, wide_kwargs = _run_suggest(tmp_path, "--since", "90d")

        assert default_kwargs is not None, "Default: get_error_records not called"
        assert wide_kwargs is not None, "--since 90d: get_error_records not called"

        default_since = default_kwargs.get("since", "")
        wide_since = wide_kwargs.get("since", "")

        assert default_since, "Default since must be non-empty"
        assert wide_since, "--since 90d must produce a non-empty since value"

        try:
            default_dt = datetime.fromisoformat(default_since)
            wide_dt = datetime.fromisoformat(wide_since)
        except ValueError as exc:
            pytest.fail(f"Could not parse since dates: {exc}")

        assert wide_dt < default_dt, (
            f"--since 90d ({wide_dt}) should be earlier than default "
            f"({default_dt}), meaning a wider window"
        )

    def test_since_override_is_surfaced_in_output(
        self, tmp_path: Path
    ) -> None:
        """The wider load is surfaced (FR-008: surfacing the wider read)."""
        output, _ = _run_suggest(tmp_path, "--since", "90d")
        lower = output.lower()
        signals = ["since", "90", "days", "window", "wider", "bound", "loading"]
        assert any(sig in lower for sig in signals), (
            f"--since 90d should be surfaced in output. Signals: {signals}.\n"
            f"Actual output:\n{output}"
        )

    def test_max_rows_flag_raises_limit_above_default(
        self, tmp_path: Path
    ) -> None:
        """--max-rows N passes a higher limit than the default cap.

        FR-008: ``sio suggest`` MUST accept an explicit cap override.
        Tests that --max-rows > DEFAULT_ROW_CAP is forwarded.
        """
        higher_cap = DEFAULT_ROW_CAP + 1000
        _, kwargs = _run_suggest(tmp_path, "--max-rows", str(higher_cap))
        assert kwargs is not None, "get_error_records was not called"
        limit = kwargs.get("limit", 0)
        assert limit >= higher_cap, (
            f"--max-rows {higher_cap} must produce limit ≥ {higher_cap}, got {limit}"
        )

    def test_max_rows_zero_means_no_cap(self, tmp_path: Path) -> None:
        """--max-rows 0 removes the row cap (opt-in to full history by row count).

        FR-008: explicit override must be honored.
        """
        _, kwargs = _run_suggest(tmp_path, "--max-rows", "0")
        assert kwargs is not None, "get_error_records was not called"
        limit = kwargs.get("limit")
        # limit=0 is the "no cap" sentinel in get_error_records — acceptable here
        # because the user explicitly opted in.
        assert limit == 0 or limit is None, (
            f"--max-rows 0 should produce limit=0 (no cap), got {limit!r}"
        )


# ---------------------------------------------------------------------------
# T042: Preview cache is size-bounded + stale-checked
# ---------------------------------------------------------------------------


class TestPreviewCacheBound:
    """T042: FR-009 — cache is size-bounded and stale-checked against --cache-ttl."""

    def test_fresh_cache_used_when_within_ttl(
        self, tmp_path: Path, tmp_previews_dir: Path
    ) -> None:
        """A fresh cache (age < TTL) is accepted and used.

        FR-009: the cache must not be silently stale — TTL gate must accept
        fresh files.
        """
        errors_csv = tmp_previews_dir / "errors_preview.csv"
        _write_preview_csv(errors_csv, _make_errors(3), age_seconds=10)

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        runner = CliRunner()
        with (
            patch(f"{_CLUSTERER}.cluster_errors", return_value=[]),
            patch(f"{_RANKER}.rank_patterns", side_effect=lambda ps, **kw: ps),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli_fn(),
                ["suggest", "--preview", "--use-cache", "--cache-ttl", "24"],
                catch_exceptions=False,
            )

        # Must not warn about stale cache
        assert "stale" not in result.output.lower() or "not stale" in result.output.lower(), (
            "Fresh cache (age=10s, ttl=24h) should not trigger a stale warning"
        )
        # Should acknowledge using the cache
        lower = result.output.lower()
        assert any(sig in lower for sig in ["cache", "hop-1", "loaded", "errors"]), (
            f"Output should mention cache usage. Output:\n{result.output}"
        )

    def test_stale_cache_triggers_warning(
        self, tmp_path: Path, tmp_previews_dir: Path
    ) -> None:
        """A cache older than TTL triggers a staleness warning.

        FR-009: stale-checked against --cache-ttl.
        """
        errors_csv = tmp_previews_dir / "errors_preview.csv"
        _write_preview_csv(errors_csv, _make_errors(3), age_seconds=25 * 3600)

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        runner = CliRunner()
        with (
            patch(f"{_CLUSTERER}.cluster_errors", return_value=[]),
            patch(f"{_RANKER}.rank_patterns", side_effect=lambda ps, **kw: ps),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli_fn(),
                ["suggest", "--preview", "--use-cache", "--cache-ttl", "24"],
                catch_exceptions=False,
            )

        # Must warn about staleness
        lower = result.output.lower()
        assert "stale" in lower or "old" in lower or "ttl" in lower, (
            f"Stale cache (25h old, ttl=24h) must trigger a warning. "
            f"Output:\n{result.output}"
        )

    def test_cache_truncated_when_exceeds_size_bound(
        self, tmp_path: Path, tmp_previews_dir: Path
    ) -> None:
        """When the cache has more rows than CACHE_MAX_ROWS, it is truncated.

        FR-009: the preview cache MUST be size-bounded.

        Strategy: spy on ``cluster_errors`` — the function that receives
        ``errors_to_cluster`` *after* the size-bound truncation.  The number
        of errors passed to clustering must be ≤ CACHE_MAX_ROWS.
        Additionally, the output must mention the truncation.
        """
        over_limit = CACHE_MAX_ROWS + 500
        errors_csv = tmp_previews_dir / "errors_preview.csv"
        _write_preview_csv(errors_csv, _make_errors(over_limit), age_seconds=10)

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        # Spy on cluster_errors to capture how many errors reach clustering
        cluster_input_sizes: list[int] = []

        def _spy_cluster(errs: list, **kwargs: Any) -> list:
            cluster_input_sizes.append(len(errs))
            return []

        runner = CliRunner()
        with (
            patch(f"{_CLUSTERER}.cluster_errors", side_effect=_spy_cluster),
            patch(f"{_RANKER}.rank_patterns", side_effect=lambda ps, **kw: ps),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            result = runner.invoke(
                cli_fn(),
                ["suggest", "--preview", "--use-cache"],
                catch_exceptions=False,
            )

        # The output must mention truncation (bound surfaced to operator)
        lower = result.output.lower()
        assert any(sig in lower for sig in ["truncat", "cap", "bound", "size"]), (
            f"Output must mention size-bound truncation when cache exceeds limit. "
            f"Output:\n{result.output}"
        )

        # cluster_errors must receive ≤ CACHE_MAX_ROWS errors
        if cluster_input_sizes:
            assert cluster_input_sizes[0] <= CACHE_MAX_ROWS, (
                f"cluster_errors received {cluster_input_sizes[0]} errors — "
                f"must be ≤ {CACHE_MAX_ROWS} after cache truncation"
            )

    def test_preview_write_respects_row_cap(
        self, tmp_path: Path, tmp_previews_dir: Path
    ) -> None:
        """When --preview writes errors_preview.csv, it caps at CACHE_MAX_ROWS.

        FR-009: size-bounded also applies to the write path, not just the read path.
        """
        over_limit_errors = _make_errors(CACHE_MAX_ROWS + 200)

        fake_db = tmp_path / "sio.db"
        fake_db.write_bytes(b"")

        runner = CliRunner()
        with (
            patch(f"{_QUERIES}.get_error_records", return_value=over_limit_errors),
            patch(
                f"{_CLUSTERER}.cluster_errors",
                return_value=[
                    {
                        "pattern_id": "p1",
                        "description": "test pattern",
                        "error_ids": [e["id"] for e in over_limit_errors[:3]],
                        "error_count": 3,
                        "session_count": 3,
                        "first_seen": "2026-06-07T00:00:00+00:00",
                        "last_seen": "2026-06-07T12:00:00+00:00",
                        "rank_score": 0.9,
                        "tool_name": "Bash",
                        "centroid_embedding": None,
                    }
                ],
            ),
            patch(f"{_RANKER}.rank_patterns", side_effect=lambda ps, **kw: ps),
            patch(f"{_QUERIES}.insert_pattern", return_value=1),
            patch(f"{_QUERIES}.link_error_to_pattern"),
            patch(f"{_QUERIES}.mark_stale_for_new_cycle"),
            patch(f"{_BUILDER}.build_dataset", return_value=None),
            patch(f"{_GENERATOR}.generate_suggestions", return_value=[]),
            patch.dict(os.environ, {"SIO_DB_PATH": str(fake_db)}),
        ):
            runner.invoke(
                cli_fn(),
                ["suggest", "--preview"],
                catch_exceptions=False,
            )

        errors_csv = tmp_previews_dir / "errors_preview.csv"
        if errors_csv.exists():
            with errors_csv.open(newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                rows = list(reader)
            # rows[0] is header; data rows = rows[1:]
            data_rows = len(rows) - 1
            assert data_rows <= CACHE_MAX_ROWS, (
                f"errors_preview.csv must be capped at ≤ {CACHE_MAX_ROWS} rows; "
                f"got {data_rows} data rows"
            )


def cli_fn():
    """Lazy import of cli to avoid import-time side-effects in patch contexts."""
    from sio.cli.main import cli

    return cli
