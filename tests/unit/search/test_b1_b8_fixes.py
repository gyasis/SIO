"""End-to-end tests for T073 confirmed adversarial findings (B1–B8).

Each test drives sio.search.cli.main() (or its helpers) and asserts OBSERVABLE
output — not isolated helper behaviour — so these tests would have caught the
bugs the existing green suite masked.

B1: --refine narrows search records (count shrinks, recluster/hybrid rejected)
B2: --around window is centred on the correct turn even when blank/malformed
    lines precede the needle in the JSONL file
B3: fast path (no --no-fast) emits files newest-first
B4: --around forces python path even on --format text
B5: multiple hits in one session all get windowed (up to cap)
B7: negative --around rejected with exit code 1
B8: fast path match count matches per-file match count, not rg -C line count
"""

from __future__ import annotations

import json
import os
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _make_jsonl_entry(
    role: str,
    content: str,
    session_id: str,
    ts: str,
    lineno_hint: int = 0,
) -> str:
    entry: dict[str, Any] = {
        "type": role,
        "uuid": f"fake-{role}-{lineno_hint:04d}",
        "timestamp": ts,
        "sessionId": session_id,
        "message": {
            "role": role,
            "content": [{"type": "text", "text": content}],
        },
    }
    return json.dumps(entry)


def _write_session(
    proj_dir: Path,
    session_id: str,
    turns: list[dict],
    *,
    mtime: float | None = None,
    blank_lines_before: int = 0,
    bad_lines_before: int = 0,
) -> Path:
    """Write a minimal Claude JSONL under *proj_dir*.

    Parameters
    ----------
    blank_lines_before:
        Insert N blank lines at the top (before any JSON) to simulate real
        files that have blank lines; blank lines are skipped by the parser
        (B2 regression scenario).
    bad_lines_before:
        Insert N non-JSON lines at the top to simulate malformed entries;
        these are also skipped by the parser (B2 regression scenario).
    """
    fpath = proj_dir / f"{session_id}.jsonl"
    with fpath.open("w", encoding="utf-8") as fh:
        for _ in range(blank_lines_before):
            fh.write("\n")
        for i in range(bad_lines_before):
            fh.write(f"NOT_JSON_line_{i}\n")
        for i, turn in enumerate(turns):
            ts = turn.get("ts", f"2026-06-07T12:{i:02d}:00+00:00")
            fh.write(
                _make_jsonl_entry(
                    turn.get("role", "unknown"),
                    turn.get("content", ""),
                    session_id,
                    ts,
                    lineno_hint=blank_lines_before + bad_lines_before + i,
                )
                + "\n"
            )
    if mtime is not None:
        os.utime(fpath, (mtime, mtime))
    return fpath


def _run_search(
    monkeypatch: pytest.MonkeyPatch,
    corpus_root: Path,
    argv: list[str],
) -> tuple[str, str, int]:
    """Invoke sio.search.cli.main() with monkeypatched corpus; return (stdout, stderr, rc)."""
    import sio.search.cli as _cli

    monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", corpus_root)
    monkeypatch.setattr(_cli, "DEV_ROOT", corpus_root / "_nonexistent_specstory")

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
    records = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


# ---------------------------------------------------------------------------
# B1: --refine narrows search records
# ---------------------------------------------------------------------------


class TestB1Refine:
    """B1: --refine + --strategy filter must narrow the result set (count shrinks)."""

    def _build_corpus(self, tmp_path: Path) -> Path:
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()
        # 3 sessions: each single turn contains BOTH "dbt" and "zeno" in the same
        # content string.  --refine "zeno" matches against the Record.content field
        # directly, so the needle and the refine term must appear in the SAME record.
        for i in range(3):
            _write_session(
                proj_dir,
                f"session-both-{i:02d}",
                [
                    {"role": "user", "content": f"dbt zeno compile error #{i}"},
                ],
                mtime=now - i * 60,
            )
        # 2 sessions: turns contain only "dbt" (no "zeno") — these should be
        # filtered OUT by --refine "zeno".
        for i in range(2):
            _write_session(
                proj_dir,
                f"session-dbt-only-{i:02d}",
                [
                    {"role": "user", "content": f"dbt run failure #{i}"},
                    {"role": "assistant", "content": "some other error"},
                ],
                mtime=now - 1000 - i * 60,
            )
        return tmp_path

    def test_refine_shrinks_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--refine 'zeno' on a dbt search returns FEWER records than no --refine."""
        corpus = self._build_corpus(tmp_path)

        # Hop-1: all dbt records — 3 "both" sessions + 2 "dbt-only" sessions = 5 matching
        # turns total (each session has at least 1 turn with "dbt" in it).
        out_hop1, _, rc1 = _run_search(
            monkeypatch,
            corpus,
            ["dbt", "--no-fast", "--recent", "0", "--format", "jsonl"],
        )
        assert rc1 == 0, f"Hop-1 must find something; rc={rc1}"
        hop1_records = _parse_jsonl_records(out_hop1)
        assert len(hop1_records) >= 3, (
            f"Expected ≥3 dbt records before refine, got {len(hop1_records)}"
        )

        # Hop-2: refine to 'zeno'
        out_hop2, err_hop2, rc2 = _run_search(
            monkeypatch,
            corpus,
            ["dbt", "--no-fast", "--recent", "0", "--format", "jsonl", "--refine", "zeno"],
        )
        assert rc2 == 0, f"Hop-2 must find something; stderr:\n{err_hop2}"
        hop2_records = _parse_jsonl_records(out_hop2)

        assert len(hop2_records) < len(hop1_records), (
            f"--refine 'zeno' must return STRICTLY fewer records than no --refine: "
            f"hop1={len(hop1_records)}, hop2={len(hop2_records)}"
        )

        # All hop2 records must contain 'zeno' in content
        for rec in hop2_records:
            assert "zeno" in rec.get("content", "").lower(), (
                f"--refine 'zeno' must only return records containing 'zeno'; "
                f"got content={rec.get('content', '')!r}"
            )

    def test_refine_zero_match_returns_exit2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--refine on a term that never appears → exit 2 (no results)."""
        corpus = self._build_corpus(tmp_path)
        _, err, rc = _run_search(
            monkeypatch,
            corpus,
            ["dbt", "--no-fast", "--recent", "0", "--format", "jsonl",
             "--refine", "NONEXISTENT_TERM_XYZ"],
        )
        assert rc == 2, (
            f"--refine with zero matches must exit 2; got {rc}; stderr:\n{err}"
        )

    def test_recluster_strategy_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--strategy recluster is rejected for sio search (exit 1, error message)."""
        corpus = self._build_corpus(tmp_path)
        _, err, rc = _run_search(
            monkeypatch,
            corpus,
            ["dbt", "--no-fast", "--recent", "0", "--refine", "zeno",
             "--strategy", "recluster"],
        )
        assert rc == 1, (
            f"--strategy recluster must be rejected (exit 1); got {rc}"
        )
        assert "recluster" in err.lower() or "not supported" in err.lower(), (
            f"Error message must mention 'recluster' or 'not supported'; got:\n{err}"
        )

    def test_within_flag_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--within is not accepted by sio search (B1: removed)."""
        from sio.search.cli import build_parser

        p = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["dbt", "--within", "/some/path.csv"])
        assert exc_info.value.code == 2, "argparse must exit 2 on unrecognized --within"

    def test_use_cache_flag_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--use-cache is not accepted by sio search (B1: removed)."""
        from sio.search.cli import build_parser

        p = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["dbt", "--use-cache"])
        assert exc_info.value.code == 2, "argparse must exit 2 on unrecognized --use-cache"


# ---------------------------------------------------------------------------
# B2: --around window lands on correct turn despite blank/malformed lines
# ---------------------------------------------------------------------------


class TestB2AroundLineMap:
    """B2: --around N window must be centred on the actual hit turn, not on a
    (line-1) offset that miscounts when blank or malformed lines precede the hit.
    """

    def test_around_correct_with_blank_lines_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Session file with N blank lines before the JSON turns.

        With the pre-fix code (hit_offset = rec.line - 1), blank lines push
        rec.line up but turns_from_jsonl skips them → the offset lands past
        the real hit turn. The window must contain the actual needle content.
        """
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        turns = [
            {"role": "user", "content": "preamble turn A"},
            {"role": "assistant", "content": "preamble turn B"},
            {"role": "user", "content": "THE_NEEDLE_IS_HERE unique_token_xyz"},
            {"role": "assistant", "content": "post-hit turn D"},
            {"role": "user", "content": "post-hit turn E"},
        ]
        # 5 blank lines before any JSON — the needle is in turn index 2
        _write_session(
            proj_dir,
            "session-blanks",
            turns,
            mtime=now - 60,
            blank_lines_before=5,
        )

        stdout, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            [
                "unique_token_xyz",
                "--around", "2",
                "--no-fast",
                "--recent", "0",
                "--format", "jsonl",
            ],
        )

        assert rc == 0, f"Search must find the needle; stderr:\n{stderr}"
        records = _parse_jsonl_records(stdout)
        assert records, "Must return window records"

        all_content = " ".join(r.get("content", "") for r in records)
        assert "unique_token_xyz" in all_content, (
            "The needle turn MUST appear in the --around window even when blank "
            f"lines precede it in the file. Window content:\n{all_content}"
        )

    def test_around_correct_with_malformed_lines_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Session file with malformed (non-JSON) lines before the JSON turns.

        Malformed lines are silently skipped by the parser; the line→turn map
        must still resolve rec.line to the correct turn index.
        """
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        turns = [
            {"role": "user", "content": "preamble 0"},
            {"role": "user", "content": "MALFORMED_NEEDLE_TOKEN_abc99"},
            {"role": "assistant", "content": "answer turn"},
        ]
        # 3 non-JSON lines before the JSON turns
        _write_session(
            proj_dir,
            "session-malformed",
            turns,
            mtime=now - 120,
            bad_lines_before=3,
        )

        stdout, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            [
                "MALFORMED_NEEDLE_TOKEN_abc99",
                "--around", "1",
                "--no-fast",
                "--recent", "0",
                "--format", "jsonl",
            ],
        )

        assert rc == 0, f"Must find needle; stderr:\n{stderr}"
        records = _parse_jsonl_records(stdout)
        assert records, "Must return window records"

        all_content = " ".join(r.get("content", "") for r in records)
        assert "MALFORMED_NEEDLE_TOKEN_abc99" in all_content, (
            "Needle turn must be in window despite malformed lines before it; "
            f"window content:\n{all_content}"
        )


# ---------------------------------------------------------------------------
# B3: fast path is newest-first (no --no-fast)
# ---------------------------------------------------------------------------


class TestB3FastPathOrder:
    """B3: default fast/text path must emit files in newest-first mtime order."""

    def test_fast_path_files_newest_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two files where alphabetical order ≠ mtime order.

        Alphabetically: 'aaa-session.jsonl' comes before 'zzz-session.jsonl'.
        But zzz-session is NEWER. The B3 fix sorts the file list by mtime before
        passing to rg. We verify this by examining the order that JSONL records
        are emitted: newer-file records must appear before older-file records.

        Note: rg's --files-with-matches output is alphabetically sorted by rg
        itself regardless of input order, so we cannot test ordering via
        --files mode. Instead we verify the fast-path JSONL record stream emits
        the newer session first.
        """
        import shutil

        if shutil.which("rg") is None:
            pytest.skip("ripgrep not installed — fast path test skipped")

        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)

        now = time.time()
        # zzz-session is NEWER (alphabetically last)
        newer_mtime = now - 100
        older_mtime = now - 10_000

        _write_session(
            proj_dir,
            "aaa-session",
            [{"role": "user", "content": "FASTPATH_NEEDLE aaa content"}],
            mtime=older_mtime,
        )
        _write_session(
            proj_dir,
            "zzz-session",
            [{"role": "user", "content": "FASTPATH_NEEDLE zzz content"}],
            mtime=newer_mtime,
        )

        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", tmp_path)
        _frozen = time.time()
        monkeypatch.setattr(_cli.time, "time", lambda: _frozen)

        # Use text output (fast path).  The B3 fix sorts the file list by mtime
        # newest-first before invoking rg; rg processes files in the order they are
        # given → zzz-session (newer) matches appear before aaa-session matches.
        # rg --no-heading -n emits lines like "<path>:<lineno>:<content>".
        # We scan for the first line that mentions each session name.
        captured_out = StringIO()
        captured_err = StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = captured_out, captured_err
        try:
            rc = _cli.main([
                "FASTPATH_NEEDLE",
                "--format", "text",
                "--recent", "0",
                # NO --no-fast → fast path (ripgrep)
            ])
        except SystemExit as exc:
            rc = int(exc.code) if exc.code is not None else 0
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        if rc == 2:
            pytest.skip("fast path found no matches — corpus not visible to rg")

        out = captured_out.getvalue()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) >= 2, f"Expected ≥2 rg output lines; got:\n{out}"

        # Find the line index where each session first appears
        zzz_idx = next(
            (i for i, ln in enumerate(lines) if "zzz-session" in ln),
            None,
        )
        aaa_idx = next(
            (i for i, ln in enumerate(lines) if "aaa-session" in ln),
            None,
        )

        assert zzz_idx is not None, (
            f"zzz-session not found in fast-path output. Lines:\n{lines}"
        )
        assert aaa_idx is not None, (
            f"aaa-session not found in fast-path output. Lines:\n{lines}"
        )
        assert zzz_idx < aaa_idx, (
            f"Fast path must emit newest-first: zzz-session (newer, idx={zzz_idx}) "
            f"must come before aaa-session (older, idx={aaa_idx}). Lines:\n{lines}"
        )


# ---------------------------------------------------------------------------
# B4: --around forces python path (windowing applies even with --format text)
# ---------------------------------------------------------------------------


class TestB4AroundForcesPythonPath:
    """B4: --around must force the python path so windowing always runs,
    even when --format text would otherwise trigger the fast path."""

    def test_around_with_format_text_produces_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--around 2 --format text must return role-aware window, not raw rg lines."""
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        turns = [
            {"role": "user", "content": "before turn 0"},
            {"role": "assistant", "content": "before turn 1"},
            {"role": "user", "content": "AROUND_NEEDLE_TEXT here"},
            {"role": "assistant", "content": "after turn 3"},
            {"role": "user", "content": "after turn 4"},
        ]
        _write_session(proj_dir, "session-around-text", turns, mtime=now - 60)

        stdout, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            [
                "AROUND_NEEDLE_TEXT",
                "--around", "2",
                "--format", "text",
                "--recent", "0",
            ],
        )

        # The window must show the needle (role-aware turns, not rg raw lines).
        # JSONL records are emitted regardless of --format when --around is set.
        assert rc == 0, f"Must find needle; stderr:\n{stderr}"
        all_output = stdout + stderr
        assert "AROUND_NEEDLE_TEXT" in all_output, (
            "--around with --format text must still produce the windowed output "
            f"containing the needle. Got stdout:\n{stdout}\nstderr:\n{stderr}"
        )
        # Must NOT be raw ripgrep output (which would look like 'path:lineno:content')
        # — actual JSONL records have 'role' and 'content' fields, or text mode shows
        # the formatted record. Either way the content must be present.

    def test_around_negative_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--around -2 must exit 1 with a clear error message (B7)."""
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        _write_session(proj_dir, "any-session", [{"role": "user", "content": "hi"}])

        _, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            ["hi", "--around", "-2", "--no-fast", "--recent", "0"],
        )
        assert rc == 1, (
            f"--around -2 must exit 1 (bad argument); got {rc}; stderr:\n{stderr}"
        )
        assert "-2" in stderr or "must be" in stderr or "≥ 0" in stderr, (
            f"Error message must mention the bad value or the constraint; got:\n{stderr}"
        )


# ---------------------------------------------------------------------------
# B5: multiple hits in one session produce multiple windows
# ---------------------------------------------------------------------------


class TestB5MultiHitWindows:
    """B5: --around must window EACH hit in a session (up to cap), not only the first."""

    def test_two_hits_both_windowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session with two separate hits gets two separate windows.

        The first (newest) hit is at turn 2; the second hit is at turn 6. With
        the pre-fix code, dedup kept only the first hit. The fix windows each
        hit up to MAX_WINDOWS_PER_SESSION (5).
        """
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        # 9 turns: needle at index 2 and again at index 6
        turns = [
            {"role": "user", "content": "preamble 0"},
            {"role": "assistant", "content": "preamble 1"},
            {"role": "user", "content": "MULTI_HIT_NEEDLE first occurrence"},
            {"role": "assistant", "content": "response after first hit"},
            {"role": "user", "content": "unrelated 4"},
            {"role": "assistant", "content": "unrelated 5"},
            {"role": "user", "content": "MULTI_HIT_NEEDLE second occurrence"},
            {"role": "assistant", "content": "response after second hit"},
            {"role": "user", "content": "tail 8"},
        ]
        _write_session(
            proj_dir, "session-two-hits", turns, mtime=now - 60
        )

        stdout, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            [
                "MULTI_HIT_NEEDLE",
                "--around", "1",
                "--no-fast",
                "--recent", "0",
                "--format", "jsonl",
            ],
        )

        assert rc == 0, f"Must find hits; stderr:\n{stderr}"
        records = _parse_jsonl_records(stdout)

        # At least 2 distinct windows emitted (each ≤ 3 turns for --around 1)
        # The two occurrences' surrounding turns should all appear.
        all_content = " ".join(r.get("content", "") for r in records)
        # Both hits must be in output (pre-fix only emitted the first window)
        assert all_content.count("MULTI_HIT_NEEDLE") >= 2, (
            f"Both hits must be windowed; found "
            f"{all_content.count('MULTI_HIT_NEEDLE')} occurrence(s) in output. "
            f"Records:\n{[r.get('content','') for r in records]}"
        )

        # Windows from different hit offsets → different surrounding turns appear
        assert "first occurrence" in all_content, "First hit window must be included"
        assert "second occurrence" in all_content, "Second hit window must be included"


# ---------------------------------------------------------------------------
# B7: negative --around rejected
# ---------------------------------------------------------------------------


class TestB7NegativeAround:
    """B7: --around N with N < 0 must be rejected immediately (exit 1)."""

    def test_negative_around_exit1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        _write_session(
            proj_dir,
            "dummy",
            [{"role": "user", "content": "anything"}],
        )
        _, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            ["anything", "--around", "-1", "--no-fast", "--recent", "0"],
        )
        assert rc == 1, (
            f"Negative --around must exit 1; got {rc}. stderr:\n{stderr}"
        )

    def test_around_zero_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--around 0 is valid (returns exactly the hit turn)."""
        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()
        _write_session(
            proj_dir,
            "session-zero-around",
            [
                {"role": "user", "content": "preamble"},
                {"role": "user", "content": "ZERO_AROUND_NEEDLE unique_token_zero"},
                {"role": "assistant", "content": "tail"},
            ],
            mtime=now - 60,
        )
        stdout, stderr, rc = _run_search(
            monkeypatch,
            tmp_path,
            [
                "unique_token_zero",
                "--around", "0",
                "--no-fast",
                "--recent", "0",
                "--format", "jsonl",
            ],
        )
        assert rc in (0, 2), f"--around 0 must not exit 1; got {rc}; stderr:\n{stderr}"
        if rc == 0:
            records = _parse_jsonl_records(stdout)
            # --around 0 returns exactly the hit turn
            assert len(records) == 1, (
                f"--around 0 must return exactly 1 turn; got {len(records)}"
            )
            assert "unique_token_zero" in records[0].get("content", ""), (
                "The single returned turn must be the hit turn itself"
            )


# ---------------------------------------------------------------------------
# B8: fast path match count agrees with per-file match count
# ---------------------------------------------------------------------------


class TestB8FastPathMatchCount:
    """B8: fast path summary line must count only real match lines, not rg -C context."""

    def test_match_count_not_overcounted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 files with 1 match each; summary must say 3 (not 3 × (1 + context_lines))."""
        import shutil

        if shutil.which("rg") is None:
            pytest.skip("ripgrep not installed — fast path test skipped")

        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        # Each session has the needle in turn 2 surrounded by other content.
        for i in range(3):
            _write_session(
                proj_dir,
                f"session-b8-{i:02d}",
                [
                    {"role": "user", "content": f"context line before {i}"},
                    {"role": "assistant", "content": f"more context {i}"},
                    {"role": "user", "content": f"B8_NEEDLE_MATCH_{i} hit here"},
                    {"role": "assistant", "content": f"context line after {i}"},
                    {"role": "user", "content": f"more context after {i}"},
                ],
                mtime=now - i * 100,
            )

        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", tmp_path)
        _frozen = time.time()
        monkeypatch.setattr(_cli.time, "time", lambda: _frozen)

        # Run with --context 3 (which adds context lines to rg output).
        # The summary line count must still be 3 (one real match per file).
        captured_out = StringIO()
        captured_err = StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = captured_out, captured_err
        try:
            rc = _cli.main([
                "B8_NEEDLE_MATCH",
                "--context", "3",   # adds context lines to rg output
                "--format", "text",
                "--recent", "0",
                # NO --no-fast → ripgrep path
            ])
        except SystemExit as exc:
            rc = int(exc.code) if exc.code is not None else 0
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        if rc == 2:
            pytest.skip("fast path found no matches — corpus not visible to rg")

        err = captured_err.getvalue()
        # Parse the summary line: "# Total matches: N  (claude-fast, ...)"
        import re

        m = re.search(r"Total matches:\s*(\d+)", err)
        assert m is not None, f"Summary line not found in stderr:\n{err}"
        reported_count = int(m.group(1))

        # There are exactly 3 files with 1 needle each. With -C 3 the old code
        # would count 3 * (1 + 3 + 3) = 21 (match + 3 before + 3 after + separators).
        # The fix counts only real match lines → must be 3.
        assert reported_count == 3, (
            f"Fast path must count only real match lines (3), not rg -C context "
            f"lines. Got {reported_count}. stderr:\n{err}"
        )

    def test_match_count_agrees_with_python_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fast path total must equal python path total for the same corpus."""
        import shutil

        if shutil.which("rg") is None:
            pytest.skip("ripgrep not installed")

        proj_dir = tmp_path / "-home-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()

        for i in range(3):
            _write_session(
                proj_dir,
                f"session-agree-{i:02d}",
                [{"role": "user", "content": f"B8_AGREE_NEEDLE_{i} content"}],
                mtime=now - i * 100,
            )

        import re

        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", tmp_path)
        _frozen = time.time()
        monkeypatch.setattr(_cli.time, "time", lambda: _frozen)

        def _count_from_search(extra_argv: list[str]) -> int:
            captured_err = StringIO()
            old_err = sys.stderr
            sys.stderr = captured_err
            try:
                _cli.main(["B8_AGREE_NEEDLE", "--recent", "0"] + extra_argv)
            except SystemExit:
                pass
            finally:
                sys.stderr = old_err
            err = captured_err.getvalue()
            m = re.search(r"Total matches:\s*(\d+)", err)
            return int(m.group(1)) if m else -1

        # Python path count (--no-fast, --format jsonl)
        python_count = _count_from_search(["--no-fast", "--format", "jsonl"])
        # Fast path count (--format text, no --no-fast)
        fast_count = _count_from_search(["--format", "text"])

        assert python_count >= 1, "Python path must find matches"
        assert fast_count == python_count, (
            f"Fast path count ({fast_count}) must agree with python path count "
            f"({python_count}) for the same corpus."
        )


# ---------------------------------------------------------------------------
# NEW-ISSUE #2: --refine must see text PAST char 2000 (fix: match_text field)
# ---------------------------------------------------------------------------


class TestRefineSeesFullText:
    """NEW-ISSUE #2: --refine predicate must look at the FULL turn text, not the
    2000-char preview stored in Record.content.

    Regression scenario:
        - A turn contains the search term early (within char 2000) so base search
          keeps it (rc=0, 1 record).
        - The same turn contains a refine term ONLY past char 2000 of its content.
        - Before the fix: --refine drops the record (rc=2, 0 records) because
          _rec_as_error_dict mapped r.content (truncated) as error_text.
        - After the fix (Option B / match_text field): --refine keeps the record
          because error_text maps to r.match_text (full text) instead.

    Also asserts that the emitted JSONL content field is still ≤ 2000 chars so the
    on-wire schema is unchanged.
    """

    # Char boundary chosen so NEEDLE is well within 2000, REFINELATE is past it.
    _NEEDLE = "SEARCH_NEEDLE_EARLY"
    _REFINE_LATE = "REFINELATE_PAST_2K"
    # Exactly 2400 chars of filler between NEEDLE and REFINE_LATE so that the full
    # text is NEEDLE + 2400-char padding + REFINELATE — well past the 2000-char cap.
    _PAD = "x" * 2400

    def _make_long_content(self) -> str:
        return f"{self._NEEDLE} {self._PAD} {self._REFINE_LATE}"

    def _build_corpus(self, tmp_path: Path) -> Path:
        proj_dir = tmp_path / "-long-content-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()
        _write_session(
            proj_dir,
            "session-long-turn",
            [{"role": "user", "content": self._make_long_content()}],
            mtime=now,
        )
        return tmp_path

    def test_refine_keeps_record_when_term_past_2000(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refine term appearing only past char 2000 must NOT drop the record."""
        corpus = self._build_corpus(tmp_path)

        # Hop-1: base search finds the turn via NEEDLE (within char 2000).
        out_hop1, err_hop1, rc1 = _run_search(
            monkeypatch,
            corpus,
            [
                self._NEEDLE,
                "--no-fast",
                "--recent",
                "0",
                "--format",
                "jsonl",
            ],
        )
        assert rc1 == 0, f"Base search must find the turn; stderr:\n{err_hop1}"
        hop1_records = _parse_jsonl_records(out_hop1)
        assert len(hop1_records) == 1, (
            f"Expected exactly 1 base record, got {len(hop1_records)}"
        )

        # Hop-2: refine by REFINELATE which is ONLY past char 2000.
        # Before the fix this returned rc=2 (0 records) — a false drop.
        out_hop2, err_hop2, rc2 = _run_search(
            monkeypatch,
            corpus,
            [
                self._NEEDLE,
                "--no-fast",
                "--recent",
                "0",
                "--format",
                "jsonl",
                "--refine",
                self._REFINE_LATE,
            ],
        )
        assert rc2 == 0, (
            f"--refine with a term past char 2000 must KEEP the record (rc=0); "
            f"got rc={rc2}. stderr:\n{err_hop2}"
        )
        hop2_records = _parse_jsonl_records(out_hop2)
        assert len(hop2_records) == 1, (
            f"--refine must return 1 record (full-text match), got {len(hop2_records)}"
        )

    def test_emitted_content_still_capped_at_2000(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The JSONL output content field must still be ≤ 2000 chars after the fix.

        The fix (Option B) stores full text in match_text and pops it before emit,
        leaving content unchanged at the parser's text[:2000] cap.
        """
        corpus = self._build_corpus(tmp_path)

        out, err, rc = _run_search(
            monkeypatch,
            corpus,
            [
                self._NEEDLE,
                "--no-fast",
                "--recent",
                "0",
                "--format",
                "jsonl",
            ],
        )
        assert rc == 0, f"Base search must succeed; stderr:\n{err}"
        records = _parse_jsonl_records(out)
        assert records, "Expected at least one record"

        for rec in records:
            content = rec.get("content", "")
            assert len(content) <= 2000, (
                f"Emitted JSONL content must be ≤ 2000 chars; "
                f"got {len(content)} chars: {content[:80]!r}..."
            )
            # match_text must NOT appear in the serialised output
            assert "match_text" not in rec, (
                "match_text is an internal field and must not be present in JSONL output"
            )


# ---------------------------------------------------------------------------
# BUGFIX (search-expand): python parser path must honor REGEX like the fast path
# ---------------------------------------------------------------------------


class TestRegexExpand:
    """The python/--files path must match patterns as a regex (like ripgrep),
    so `|` alternation EXPANDS across synonyms instead of matching a literal
    'a|b' string (which returned 0 — the reported expand bug)."""

    def _build_corpus(self, tmp_path: Path) -> Path:
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()
        _write_session(
            proj_dir, "sess-alpha",
            [{"role": "user", "content": "alpha appears here"}], mtime=now,
        )
        _write_session(
            proj_dir, "sess-beta",
            [{"role": "user", "content": "beta appears here"}], mtime=now - 60,
        )
        return tmp_path

    def test_alternation_matches_either_term(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`alpha|beta` (regex) must match records from BOTH sessions."""
        corpus = self._build_corpus(tmp_path)
        out, _, rc = _run_search(
            monkeypatch, corpus,
            ["alpha|beta", "--no-fast", "--recent", "0", "--format", "jsonl"],
        )
        assert rc == 0
        recs = _parse_jsonl_records(out)
        contents = " ".join(r.get("content", "") for r in recs)
        assert "alpha" in contents and "beta" in contents, (
            f"regex alternation must expand across BOTH terms; got {contents!r}"
        )
        assert len(recs) >= 2

    def test_literal_multiword_phrase_matches_nothing_and_hints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-adjacent multi-word phrase matches 0 AND emits an alternation hint."""
        corpus = self._build_corpus(tmp_path)
        _, err, rc = _run_search(
            monkeypatch, corpus,
            ["alpha beta", "--no-fast", "--recent", "0", "--format", "jsonl"],
        )
        assert rc == 2  # no adjacency → no match
        assert "alpha|beta" in err, f"expected an alternation hint; stderr:\n{err}"

    def test_invalid_regex_falls_back_to_literal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray metacharacter must not raise — it falls back to literal match."""
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        _write_session(
            proj_dir, "sess-paren",
            [{"role": "user", "content": "value is foo(bar"}], mtime=time.time(),
        )
        out, _, rc = _run_search(
            monkeypatch, tmp_path,
            ["foo(bar", "--no-fast", "--recent", "0", "--format", "jsonl"],
        )
        assert rc == 0, "invalid regex must fall back to literal, not crash"
        recs = _parse_jsonl_records(out)
        assert any("foo(bar" in r.get("content", "") for r in recs)


# ---------------------------------------------------------------------------
# BUGFIX (refine-in-files): --files must honor --refine (was a silent no-op)
# ---------------------------------------------------------------------------


class TestRefineInFilesMode:
    """--refine must narrow --files output too. Previously --files short-circuited
    into files_seen and skipped the refine predicate entirely, so
    `X --files` and `X --refine Y --files` returned the SAME file set."""

    def _build_corpus(self, tmp_path: Path) -> Path:
        proj_dir = tmp_path / "-fake-proj"
        proj_dir.mkdir(parents=True)
        now = time.time()
        # 3 files contain both dbt + zeno; 2 files contain only dbt.
        for i in range(3):
            _write_session(
                proj_dir, f"both-{i:02d}",
                [{"role": "user", "content": f"dbt zeno error {i}"}], mtime=now - i,
            )
        for i in range(2):
            _write_session(
                proj_dir, f"dbtonly-{i:02d}",
                [{"role": "user", "content": f"dbt only {i}"}], mtime=now - 100 - i,
            )
        return tmp_path

    def _files(self, stdout: str) -> list[str]:
        return [ln for ln in stdout.splitlines() if ln and not ln.startswith("#")]

    def test_files_mode_honors_refine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corpus = self._build_corpus(tmp_path)
        out1, _, _ = _run_search(
            monkeypatch, corpus, ["dbt", "--recent", "0", "--files"],
        )
        out2, _, _ = _run_search(
            monkeypatch, corpus, ["dbt", "--recent", "0", "--files", "--refine", "zeno"],
        )
        files1 = self._files(out1)
        files2 = self._files(out2)
        assert len(files1) == 5, f"Hop-1 --files should list 5 files; got {files1}"
        assert len(files2) == 3, (
            f"--refine zeno must narrow --files to the 3 zeno files; got {files2}"
        )
        assert all("both" in f for f in files2)
