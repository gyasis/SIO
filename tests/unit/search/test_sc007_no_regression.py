"""SC-007 Integration tests — no regression for CMP callers (T070).

SC-007: Zero regression for existing Cascade-Memory-Protocol callers — the
``/memory-search``, ``/done-before``, and recency-first protocol flows return
equivalent results (modulo newest-first ordering) on the fixture corpus.

Concretely this file asserts:

1. ``--recent 0`` (full history) returns the **same SET** of session-file hits
   as a pre-feature full-corpus scan would (order aside).
2. The **default window** is a **strict recency-gated subset** of that full set,
   ordered newest-first.
3. CMP callers ``--files`` and ``--count`` are unchanged:
   - ``--files --recent 0`` yields one path per matched file (same files as
     direct corpus scan).
   - ``--count --recent 0`` yields per-file integer counts that are ≥ 1 for
     matched files.
4. Newest-first ordering: in a multi-file result set the ``ts`` field on each
   record is non-increasing (ISO-8601 lexicographic).
5. The default window is a strict subset of full-history: every file returned
   by the default search is also returned by ``--recent 0``.

Corpus (from ``fake_session_corpus`` fixture):
  session-today  — mtime 1 h ago   → inside 7-day default window
  session-week   — mtime 8 days ago → outside 7-day default (inside 30-day)
  session-old    — mtime 45 days ago → outside any reasonable default window

The ``example`` search term appears in the assistant turn of every session
(embedded by the fixture as "sio search --recent 7 example").
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
    """Invoke sio.search.cli.main() against corpus_root; return (stdout, stderr, rc)."""
    import sio.search.cli as _cli

    monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", corpus_root)
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


def _parse_jsonl_records(stdout: str) -> list[dict]:
    """Extract valid JSON objects from a JSONL stdout blob."""
    records = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _hit_files(records: list[dict]) -> set[str]:
    """Return the set of source paths referenced by the record list.

    The search CLI JSONL format uses ``source_path`` as the primary path key;
    the helpers also check legacy aliases for forward-compatibility.
    """
    files = set()
    for r in records:
        # Primary key emitted by sio.search.cli Python-parser path (US1+).
        f = (
            r.get("source_path")
            or r.get("file")
            or r.get("source_file")
            or r.get("path")
            or ""
        )
        if f:
            files.add(f)
    return files


def _file_basenames(records: list[dict]) -> set[str]:
    """Return the set of basename stems (session-today, session-week, …) from records.

    Resolves the ``source_path`` key emitted by the CLI and falls back to
    legacy aliases so the helper remains forward-compatible.
    """
    from pathlib import Path as _Path

    basenames = set()
    for r in records:
        f = (
            r.get("source_path")
            or r.get("file")
            or r.get("source_file")
            or r.get("path")
            or ""
        )
        if f:
            stem = _Path(f).stem  # e.g. "session-today"
            basenames.add(stem)
    return basenames


# ---------------------------------------------------------------------------
# SC-007-A: full-history equivalence — ``--recent 0`` vs. raw corpus
# ---------------------------------------------------------------------------

class TestSC007FullHistoryEquivalence:
    """SC-007-A: ``--recent 0`` returns the same match SET as a pre-feature
    full-corpus scan (order aside).

    The no-regression contract for CMP callers: adding the default recency
    window must NOT silently drop matches when the caller explicitly opts into
    full history via ``--recent 0``.
    """

    def test_recent_zero_finds_all_three_sessions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """``--recent 0`` must surface hits from today, week, AND old sessions.

        This is the direct no-regression assertion: the full-history override
        must reach the entire fixture corpus, matching the behavior of the
        pre-recency-default era where every search was implicitly --recent 0.
        """
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        records = _parse_jsonl_records(stdout)
        basenames = _file_basenames(records)

        assert "session-today" in basenames, (
            "session-today (1h ago) must be found with --recent 0 "
            f"(basenames found: {basenames})"
        )
        assert "session-week" in basenames, (
            "session-week (8d ago) must be found with --recent 0 — "
            "this is the CMP no-regression check "
            f"(basenames found: {basenames})"
        )
        assert "session-old" in basenames, (
            "session-old (45d ago) must be found with --recent 0 — "
            "full-history override must reach the whole corpus "
            f"(basenames found: {basenames})"
        )

    def test_recent_zero_set_equals_pre_feature_full_scan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """The session-file SET from ``--recent 0`` is the same 3-session full corpus.

        Simulates "what the pre-feature scan returned": the fixture has exactly
        3 session files.  --recent 0 must find all 3; no file may be silently
        excluded by any ordering or caching side-effect.
        """
        stdout, stderr, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        records = _parse_jsonl_records(stdout)
        basenames = _file_basenames(records)

        # The fixture corpus has exactly these 3 sessions.
        expected = {"session-today", "session-week", "session-old"}
        missing = expected - basenames
        assert not missing, (
            f"--recent 0 is missing corpus sessions: {missing}. "
            "These were reachable before the recency-default feature; "
            "they must still be reachable with explicit --recent 0."
        )


# ---------------------------------------------------------------------------
# SC-007-B: default window is a strict subset of full history
# ---------------------------------------------------------------------------

class TestSC007DefaultIsSubsetOfFullHistory:
    """SC-007-B: every file returned by the default search is also returned by
    ``--recent 0`` (subset relationship guarantees no phantom matches).
    """

    def test_default_window_subset_of_full_history(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """Default results ⊆ full-history results (modulo newest-first ordering)."""
        # Full history baseline.
        full_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        full_basenames = _file_basenames(_parse_jsonl_records(full_stdout))

        # Default window (no --recent flag).
        default_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        default_basenames = _file_basenames(_parse_jsonl_records(default_stdout))

        # Every file in the default result must also be in the full-history result.
        phantom = default_basenames - full_basenames
        assert not phantom, (
            f"Default window returned phantom matches not in full history: {phantom}. "
            "The default recency filter must be a strict subset of --recent 0."
        )

    def test_default_window_excludes_old_sessions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """The 45-day-old session is excluded from the default window (recency gate).

        This is the core behavioral change: CMP callers that did NOT pass
        --recent N before now get a recency-gated result.  The gate must
        exclude session-old (45d), reducing noise without losing history.
        """
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        assert "session-old" not in stdout, (
            "session-old (45d) must be gated out by the default recency window. "
            "CMP callers now get recency-first behavior by default; use --recent 0 "
            "to restore the full-history scan."
        )

    def test_default_window_is_strict_subset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """Default session set is a STRICT subset of --recent 0 (not equal).

        The default 7-day window excludes session-week (8d) and session-old (45d),
        so the two sets are not equal — confirming the gate is actually active.
        """
        full_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        full_set = _file_basenames(_parse_jsonl_records(full_stdout))

        default_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        default_set = _file_basenames(_parse_jsonl_records(default_stdout))

        assert default_set < full_set, (
            f"Default window set {default_set} must be a STRICT subset of "
            f"full-history set {full_set}. "
            "The recency gate appears to be inactive — both returned the same files."
        )


# ---------------------------------------------------------------------------
# SC-007-C: newest-first ordering contract
# ---------------------------------------------------------------------------

class TestSC007NewestFirst:
    """SC-007-C: multi-file results are ordered newest-first (descending ts).

    CMP callers rely on the first hit being the most recent. This was not
    guaranteed before US1; it is now the contract.
    """

    def test_full_history_results_newest_first(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """``--recent 0`` JSONL output is ordered newest-first by ``ts`` field."""
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--format", "jsonl", "--no-fast"],
        )
        records = _parse_jsonl_records(stdout)

        if len(records) < 2:
            pytest.skip("Need ≥2 records to verify ordering; corpus may not match")

        timestamps = [r.get("ts", "") for r in records]
        for i in range(len(timestamps) - 1):
            a, b = timestamps[i], timestamps[i + 1]
            if a and b:
                assert a >= b, (
                    f"Results must be newest-first (descending ts). "
                    f"ts[{i}]={a!r} < ts[{i+1}]={b!r}. "
                    "CMP callers depend on the first hit being the most recent."
                )

    def test_default_window_results_newest_first(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """Default-window JSONL output is ordered newest-first."""
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        records = _parse_jsonl_records(stdout)

        if len(records) < 2:
            pytest.skip("Need ≥2 records; default window may only hit 1 session")

        timestamps = [r.get("ts", "") for r in records]
        for i in range(len(timestamps) - 1):
            a, b = timestamps[i], timestamps[i + 1]
            if a and b:
                assert a >= b, (
                    f"Default window must be newest-first; "
                    f"ts[{i}]={a!r} < ts[{i+1}]={b!r}"
                )


# ---------------------------------------------------------------------------
# SC-007-D: CMP caller compatibility — ``--files`` and ``--count``
# ---------------------------------------------------------------------------

class TestSC007CMPCallerCompat:
    """SC-007-D: ``--files`` and ``--count`` callers are unchanged by the
    recency-default feature (modulo ordering and the default window).

    The Cascade Memory Protocol's ``--files`` path (step 1: "ANCHOR") and
    ``--count`` path must return valid file paths / integer counts for all
    matched sessions when ``--recent 0`` is passed.
    """

    def test_files_recent_zero_returns_all_matched_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """``--files --recent 0`` emits one path per matched session (all 3)."""
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--files"],
        )
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and not ln.startswith("#")]

        assert len(lines) >= 1, "--files must emit at least one path"
        for ln in lines:
            assert ln.startswith("/") or ln.startswith("."), (
                f"--files output must be absolute/relative paths; got: {ln!r}"
            )

        # All three corpus sessions should appear.
        combined = "\n".join(lines)
        assert "session-today" in combined, "session-today must appear in --files output"
        assert "session-week" in combined, "session-week must appear in --files output"
        assert "session-old" in combined, "session-old must appear in --files output"

    def test_count_recent_zero_emits_integer_tab_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """``--count --recent 0`` emits ``<N>\\t<path>`` lines with integer counts."""
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--count"],
        )
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and not ln.startswith("#")]

        assert len(lines) >= 1, "--count must emit at least one count line"
        for ln in lines:
            parts = ln.split("\t", 1)
            assert len(parts) == 2, (
                f"--count line must be '<N>\\t<path>', got: {ln!r}"
            )
            assert parts[0].isdigit(), (
                f"First column must be integer count, got: {parts[0]!r}"
            )
            count = int(parts[0])
            assert count >= 1, f"Match count must be ≥1, got {count} for {parts[1]!r}"

    def test_files_default_window_subset_of_files_recent_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """``--files`` default ⊆ ``--files --recent 0`` (recency gate applies to --files)."""
        # Full history set via --files.
        full_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--files"],
        )
        full_lines = {
            ln.strip()
            for ln in full_stdout.splitlines()
            if ln.strip() and not ln.startswith("#")
        }

        # Default window via --files.
        default_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--files"],
        )
        default_lines = {
            ln.strip()
            for ln in default_stdout.splitlines()
            if ln.strip() and not ln.startswith("#")
        }

        # Default must be a subset (or equal) of full-history --files.
        phantom = default_lines - full_lines
        assert not phantom, (
            f"--files default window returned paths not in --recent 0: {phantom}"
        )

        # And session-old must not be in the default --files output.
        assert not any("session-old" in p for p in default_lines), (
            "--files default window must exclude session-old (45d)"
        )


# ---------------------------------------------------------------------------
# SC-007-E: ``/memory-search`` CMP protocol flow simulation
# ---------------------------------------------------------------------------

class TestSC007CMPProtocolFlow:
    """SC-007-E: simulate the canonical CMP ``/memory-search`` protocol flow.

    The CASCADE MEMORY PROTOCOL step 1 (ANCHOR) calls:
        session-search "<keywords>" --recent 7 --files

    This must return the same file paths that a pre-feature ``--recent 7 --files``
    returned on the same corpus (i.e. only today's session, since only it is
    within 7 days given the fixture ages).

    The no-regression contract: this flow must find EXACTLY the within-window
    sessions and no more.
    """

    def test_cmp_anchor_step_recent7_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """Step 1 (ANCHOR): ``--recent 7 --files`` returns only the within-window paths.

        Pre-feature: this was an explicit caller choice. Post-feature: it is also
        the default behavior. The output must be identical either way.
        """
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "7", "--files"],
        )
        lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        combined = "\n".join(lines)

        # Only session-today is within the 7-day window given fixture ages.
        assert "session-today" in combined, (
            "CMP step 1: session-today (1h) must be found by --recent 7 --files"
        )
        assert "session-old" not in combined, (
            "CMP step 1: session-old (45d) must be excluded by --recent 7 --files"
        )
        # session-week (8d) is outside the 7-day window.
        assert "session-week" not in combined, (
            "CMP step 1: session-week (8d) must be excluded by --recent 7 --files"
        )

    def test_cmp_widen_recent0_restores_full_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """CMP recovery: ``--recent 0 --files`` after a 0-result window restores all paths.

        When ``--recent 7 --files`` returns 0 results, the CMP protocol directs
        the caller to widen with ``--recent 0``.  This must restore the full set.
        """
        # Use a pattern that exists only in old sessions (to simulate the widen scenario).
        # The fixture embeds "example" in every session, so we just use --recent 0
        # and verify all three appear.
        stdout, _, rc = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "0", "--files"],
        )
        combined = stdout

        assert "session-today" in combined, "Widen with --recent 0 must recover session-today"
        assert "session-week" in combined, "Widen with --recent 0 must recover session-week"
        assert "session-old" in combined, "Widen with --recent 0 must recover session-old"

    def test_default_equals_explicit_recent7(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_corpus: Path,
        freeze_utc_now: str,
    ) -> None:
        """The default search behavior is equivalent to explicit ``--recent 7``.

        A caller that previously passed ``--recent 7`` gets the same result as a
        caller that passes nothing.  This is the CMP protocol-flow no-regression:
        existing explicit ``--recent 7`` callers are unaffected.
        """
        # Explicit --recent 7.
        explicit_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--recent", "7", "--format", "jsonl", "--no-fast"],
        )
        explicit_basenames = _file_basenames(_parse_jsonl_records(explicit_stdout))

        # Default (no --recent flag).
        default_stdout, _, _ = _run_search(
            monkeypatch,
            fake_session_corpus,
            ["example", "--format", "jsonl", "--no-fast"],
        )
        default_basenames = _file_basenames(_parse_jsonl_records(default_stdout))

        assert explicit_basenames == default_basenames, (
            f"Default search must return the same session set as explicit --recent 7. "
            f"Explicit: {explicit_basenames}, Default: {default_basenames}"
        )
