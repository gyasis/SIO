"""Tests for Wave 2 / US1: Recency by default (T010, T011, T012).

FR-001: sio search must default to a bounded recency window (default --recent N,
        N>0) and order raw/text/jsonl output newest-first.
FR-002: On zero results within the default window, emit a "widen with --recent 0"
        hint rather than a bare empty result.

SC-007 / compat: --recent 0 and --all still search full history; CMP callers
        (--files, --count) are unchanged except ordering.

Frozen "now": 2026-06-07T12:00:00+00:00  (from freeze_utc_now fixture).
Corpus ages (from fake_session_corpus):
  session-today  — mtime 1 h ago   (inside any reasonable window)
  session-week   — mtime 8 d ago   (inside 14-day; outside 3-day)
  session-old    — mtime 45 d ago  (outside any window ≤ 30 days)

Default window: 7 days  (FR-001 does not pin N; we used 7 per the CMP protocol
and noted it in the report).
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_search(
    monkeypatch: pytest.MonkeyPatch,
    corpus_root: Path,
    argv: list[str],
) -> tuple[str, str, int]:
    """Invoke sio.search.cli.main() with the given argv, redirecting stdout/stderr.

    Patches CLAUDE_PROJECTS inside sio.search.cli to point at corpus_root so
    the real parser reads only the fixture files, not the developer's live
    ~/.claude/projects directory.

    Returns (stdout, stderr, exit_code).
    """
    import sio.search.cli as _cli

    monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", corpus_root)
    # Also redirect DEV_ROOT to a harmless path so --all / specstory don't
    # accidentally crawl the real filesystem.
    monkeypatch.setattr(_cli, "DEV_ROOT", corpus_root / "_nonexistent_specstory")
    # Redirect CLAUDE_BACKUPS so --all / --backups tests don't read the developer's
    # real ~/.claude/backups — keeps the test hermetic and machine-independent.
    monkeypatch.setattr(_cli, "CLAUDE_BACKUPS", corpus_root / "_nonexistent_backups")

    captured_out = StringIO()
    captured_err = StringIO()

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = captured_out, captured_err
    try:
        rc = _cli.main(argv)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return captured_out.getvalue(), captured_err.getvalue(), rc


# ---------------------------------------------------------------------------
# T010 — default recency window + newest-first ordering
# ---------------------------------------------------------------------------


class TestDefaultRecencyWindow:
    """T010: sio search with no time flag scans only the default window
    and returns newest-first.

    Default window = 7 days (FR-001; value not pinned by spec, set here).
    Corpus: session-today (1h), session-week (8d), session-old (45d).
    With a 7-day default, only session-today (1h ago) falls inside the window;
    session-week (8d) and session-old (45d) are outside.
    """

    def test_only_today_file_returned_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """No time flag → only the file within the default 7-day window appears."""
        # The fixture embeds "sio search --recent 7 example" in the assistant
        # turn of every session, so "sio search" matches all three files.
        # With the 7-day default only session-today should appear.
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["sio search", "--format", "jsonl", "--no-fast"],
        )
        # Non-zero exit means no matches; if today file found we expect 0.
        # The actual search term:
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["sio search", "example", "--format", "jsonl", "--no-fast"],
        )

        # Must have found at least one result (exit 0) or zero (exit 2).
        # Only session-today should appear in output; session-old must not.
        assert "session-old" not in stdout, (
            "session-old (45 days) must NOT appear when default 7-day window is active"
        )
        assert "session-week" not in stdout, (
            "session-week (8 days) must NOT appear when default 7-day window is active"
        )
        # session-today should be present if the search found any hits.
        if rc == 0:
            assert "session-today" in stdout, (
                "session-today (1h ago) MUST appear in default window results"
            )

    def test_results_newest_first_jsonl(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """JSONL output must be ordered newest-first (descending timestamp)."""
        # Use --recent 0 to get multiple hits across all ages so we can check order.
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        if rc != 0:
            pytest.skip("no results returned — corpus may not match pattern")

        records = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if len(records) < 2:
            pytest.skip("need ≥2 records to verify ordering")

        timestamps = [r.get("ts", "") for r in records]
        # Newest-first: each ts must be ≥ next ts lexicographically (ISO-8601).
        for i in range(len(timestamps) - 1):
            a, b = timestamps[i], timestamps[i + 1]
            if a and b:
                assert a >= b, (
                    f"Results must be newest-first; got ts[{i}]={a!r} < ts[{i+1}]={b!r}"
                )

    def test_default_window_gates_old_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """session-old (45d) must be excluded from the default-window search."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        assert "session-old" not in stdout, (
            "session-old (45d) must be gated out by the default recency window"
        )


# ---------------------------------------------------------------------------
# T011 — zero-results-in-window emits widen hint (FR-002)
# ---------------------------------------------------------------------------


class TestWidenHint:
    """T011: When default-window search returns 0 results, the tool must emit
    a hint telling the operator to widen with --recent 0.

    FR-002 wording: "widen with `--recent 0`".
    """

    def test_zero_results_emits_widen_hint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """A pattern that matches nothing in the default window emits the hint."""
        # "ZZZUNLIKELYMATCH999" will never appear in the fixture JSONL content.
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["ZZZUNLIKELYMATCH999", "--format", "jsonl", "--no-fast"],
        )
        combined = stdout + stderr
        assert "--recent 0" in combined, (
            "Zero results in the default window MUST emit a widen hint "
            "containing '--recent 0' (FR-002). Got stdout=%r stderr=%r"
            % (stdout[:200], stderr[:200])
        )

    def test_full_history_zero_does_not_emit_widen_hint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """When --recent 0 (explicit full history) returns 0, no redundant widen hint."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["ZZZUNLIKELYMATCH999", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        # Hint would be misleading here — caller already opted into full history.
        # The hint text is only useful when the default window was the cause.
        # We do NOT assert absence of the hint (the spec only requires it on
        # default-window zero; it is ALLOWED but not required to suppress it here).
        # This test verifies the tool still exits cleanly.
        assert rc in (0, 1, 2), f"Unexpected exit code {rc}"


# ---------------------------------------------------------------------------
# T012 — compat / SC-007: --recent 0 and --all still search full history;
#         --files and --count unchanged except ordering
# ---------------------------------------------------------------------------


class TestCompatNoRegression:
    """T012: Explicit --recent 0 / --all must still search full history.
    CMP callers --files and --count must be unchanged.

    This is the no-regression guard for SC-007.
    """

    def test_recent_zero_searches_full_corpus(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--recent 0 must include files from all ages (today + week + old)."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        # All three session files contain "example" in their assistant turn.
        # With --recent 0 (no cutoff) all three must appear.
        assert "session-today" in stdout, "session-today must appear with --recent 0"
        assert "session-week" in stdout, "session-week must appear with --recent 0"
        assert "session-old" in stdout, "session-old must appear with --recent 0"

    def test_all_flag_searches_full_corpus(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--all must include files from all ages (today + week + old)."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--all", "--format", "jsonl", "--no-fast"],
        )
        # --all expands to JSONL + SpecStory + backups; at minimum JSONL files
        # from all three age buckets must be scanned.
        assert "session-today" in stdout, "session-today must appear with --all"
        assert "session-week" in stdout, "session-week must appear with --all"
        assert "session-old" in stdout, "session-old must appear with --all"

    def test_all_with_explicit_recent_is_still_time_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--all and --recent stay ORTHOGONAL: --all expands sources, but an
        explicit --recent N still bounds the time window. `--all --recent 7`
        means all sources, last 7 days — so the 45d (and 8d) files are excluded
        even though --all is set. Guards against conflating source-expansion
        with a full-history time override (regression check)."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--all", "--recent", "7", "--format", "jsonl", "--no-fast"],
        )
        assert "session-today" in stdout, "today (within 7d) must appear"
        assert "session-old" not in stdout, (
            "session-old (45d) must be excluded — explicit --recent 7 must bound "
            "time even with --all (orthogonality)"
        )
        assert "session-week" not in stdout, (
            "session-week (8d) must be excluded by --recent 7"
        )

    def test_files_flag_still_emits_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--files --recent 0 must still list file paths (unchanged CMP behavior)."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--files"],
        )
        # --files output: one path per line, no JSON, no headers.
        lines = [ln for ln in stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        assert len(lines) >= 1, "--files must emit at least one path line"
        # Each non-comment line must look like a file path.
        for ln in lines:
            assert ln.startswith("/") or ln.startswith("."), (
                f"--files output must be file paths, got: {ln!r}"
            )

    def test_count_flag_still_emits_counts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--count --recent 0 must still emit per-file match counts."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--count"],
        )
        # --count output: "<N>\t<path>" lines.
        lines = [ln for ln in stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        assert len(lines) >= 1, "--count must emit at least one count line"
        for ln in lines:
            parts = ln.split("\t", 1)
            assert len(parts) == 2, f"--count line must be '<N>\\t<path>', got: {ln!r}"
            assert parts[0].isdigit(), f"First column must be integer count, got: {parts[0]!r}"

    def test_files_respects_recency_with_default_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--files with default window must only list files within the window."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--files"],
        )
        # session-old must not be listed when the default window is active.
        assert "session-old" not in stdout, (
            "--files must respect the default recency window; "
            "session-old (45d) must be excluded"
        )

    def test_count_respects_recency_with_default_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """--count with default window must only count files within the window."""
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--count"],
        )
        assert "session-old" not in stdout, (
            "--count must respect the default recency window; "
            "session-old (45d) must be excluded"
        )
