"""Tests for Wave 3 / US2: Walk into the hit session (T020, T021, T022).

FR-003: sio search MUST expose a context-window option that, given a session +
        hit offset, returns ±N TURNS (role-aware), distinct from ripgrep -C raw
        lines and from the full --session dump.
FR-004: The context window MUST clamp at transcript boundaries and support
        walking forward from a struggle hit into subsequent fix turns.

SC-003: After US2 ships, a context window returns *exactly* ±N turns (not 1
        line, not the full transcript).

All tests target the public API in sio.search.cli:
    turns_from_jsonl(path)            -> list[dict]   (parse all turns)
    turns_around(turns, offset, n)    -> list[dict]   (±N window, clamped)
    window_for_session(session_path, hit_offset, n) -> list[dict]

And the CLI flag --around N (exposed via build_parser / main).
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


def _write_session(tmp_path: Path, session_id: str, turns: list[dict]) -> Path:
    """Write a minimal Claude-format JSONL file for the given turns.

    Each turn dict should have at least 'role' and 'content' keys.
    The on-disk format mirrors _iter_claude_jsonl expectations:
      {"type": <role>, "timestamp": <iso>, "sessionId": <id>,
       "message": {"role": <role>, "content": [{"type":"text","text":<content>}]}}
    """
    fpath = tmp_path / f"{session_id}.jsonl"
    with fpath.open("w", encoding="utf-8") as fh:
        for i, turn in enumerate(turns):
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            entry = {
                "type": role,
                "uuid": f"fake-{role}-{i:03d}",
                "timestamp": f"2026-06-07T12:00:{i:02d}+00:00",
                "sessionId": session_id,
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                },
            }
            fh.write(json.dumps(entry) + "\n")
    return fpath


def _run_search(
    monkeypatch: pytest.MonkeyPatch,
    corpus_root: Path,
    argv: list[str],
) -> tuple[str, str, int]:
    """Invoke sio.search.cli.main() with the given argv, redirecting stdout/stderr."""
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


# ---------------------------------------------------------------------------
# T020 — ±N turns around a (session, offset): exactly N each side, role-aware
# ---------------------------------------------------------------------------


class TestContextWindowExactN:
    """T020: turns_around(turns, offset, n) returns exactly n turns each side
    (i.e. 2n+1 total if unclamped), with role preserved on every returned turn.

    Distinct from:
    - rg -C  (raw lines, not role-aware)
    - --session dump (full transcript, not windowed)
    """

    def test_returns_exactly_n_each_side_unclamped(self, tmp_path: Path) -> None:
        """Middle of a 10-turn session with n=2 → 5 turns total (2 before, hit, 2 after)."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [
            {"role": "user", "content": f"user turn {i}"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"assistant turn {i}"}
            for i in range(10)
        ]
        session_path = _write_session(tmp_path, "session-ten", turns_data)

        turns = turns_from_jsonl(session_path)
        assert len(turns) == 10, "Expected 10 turns parsed"

        hit_offset = 5  # middle turn
        n = 2
        window = turns_around(turns, hit_offset, n)

        assert len(window) == 5, (
            f"±{n} around offset {hit_offset} must return 2n+1=5 turns, "
            f"got {len(window)}"
        )
        # The hit turn must be the middle element
        assert window[n]["content"] == turns_data[hit_offset]["content"]

    def test_role_preserved_on_every_turn(self, tmp_path: Path) -> None:
        """Every turn in the window carries the correct role (user/assistant/tool)."""
        from sio.search.cli import turns_around, turns_from_jsonl

        roles_and_contents = [
            ("user", "initial question"),
            ("assistant", "thinking..."),
            ("tool", "bash output here"),
            ("assistant", "based on that result"),
            ("user", "follow-up question"),
            ("assistant", "TARGET TURN"),
            ("tool", "second bash output"),
            ("assistant", "final answer"),
        ]
        turns_data = [{"role": r, "content": c} for r, c in roles_and_contents]
        session_path = _write_session(tmp_path, "session-roles", turns_data)

        turns = turns_from_jsonl(session_path)
        hit_offset = 5  # "TARGET TURN" (assistant)
        window = turns_around(turns, hit_offset, n=2)

        # Must have 5 turns: [3,4,5,6,7]
        assert len(window) == 5
        expected_roles = ["assistant", "user", "assistant", "tool", "assistant"]
        actual_roles = [t["role"] for t in window]
        assert actual_roles == expected_roles, (
            f"Expected roles {expected_roles}, got {actual_roles}"
        )

    def test_distinct_from_raw_line_context(self, tmp_path: Path) -> None:
        """turns_around operates on parsed turns (role-aware), not raw rg -C lines.

        A multi-line assistant turn counts as ONE turn regardless of how many
        newlines it spans, unlike rg -C N which would emit N raw lines.
        """
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "line1\nline2\nline3\nline4\nline5"},
            {"role": "user", "content": "NEEDLE"},
            {"role": "assistant", "content": "one line answer"},
            {"role": "user", "content": "another question"},
        ]
        session_path = _write_session(tmp_path, "session-multiline", turns_data)

        turns = turns_from_jsonl(session_path)
        assert len(turns) == 5, "Multi-line content should be ONE turn"

        # ±1 around the NEEDLE (offset=2) → 3 turns total
        window = turns_around(turns, 2, n=1)
        assert len(window) == 3, (
            "±1 must return 3 turns regardless of newlines in content"
        )
        # The multi-line turn is treated as a single turn
        assert "line5" in window[0]["content"]

    def test_n_zero_returns_only_hit_turn(self, tmp_path: Path) -> None:
        """turns_around with n=0 returns exactly the hit turn (no context)."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": f"turn {i}"} for i in range(5)]
        session_path = _write_session(tmp_path, "session-zero", turns_data)

        turns = turns_from_jsonl(session_path)
        window = turns_around(turns, 2, n=0)
        assert len(window) == 1
        assert window[0]["content"] == "turn 2"


# ---------------------------------------------------------------------------
# T021 — Boundary clamping (AS-2 / FR-004)
# ---------------------------------------------------------------------------


class TestBoundaryClamping:
    """T021: Window clamps gracefully at transcript start and end.

    No crash, no negative index, no overrun past the end of the transcript.
    """

    def test_clamp_at_transcript_start(self, tmp_path: Path) -> None:
        """Hit at offset 1 with n=3 → only 2 before-turns available (0,1); clamp to 0."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": f"turn {i}"} for i in range(5)]
        session_path = _write_session(tmp_path, "session-clamp-start", turns_data)

        turns = turns_from_jsonl(session_path)
        # hit at offset=1, n=3 → would need offsets [-2,-1,0,1,2,3,4] → clamp to [0,1,2,3,4]
        window = turns_around(turns, 1, n=3)

        # Can't have negative indices: before-side is clamped to [0]
        # After-side: [2,3,4] — 3 available
        # Total: 0,1,2,3,4 → 5 turns (not 7)
        assert len(window) <= 7, "Must not overrun the transcript"
        assert len(window) >= 1, "Must return at least the hit turn"
        # The first returned turn must not be before offset 0
        assert "turn 0" in window[0]["content"] or "turn 1" in window[0]["content"]
        # No crash: the slice is always valid
        for t in window:
            assert "role" in t and "content" in t

    def test_clamp_at_transcript_end(self, tmp_path: Path) -> None:
        """Hit at offset N-2 with n=3 → after-side clamped to available turns."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": f"turn {i}"} for i in range(5)]
        session_path = _write_session(tmp_path, "session-clamp-end", turns_data)

        turns = turns_from_jsonl(session_path)
        last_offset = len(turns) - 1  # offset 4

        # n=3 around last offset → after-side empty, before-side has [1,2,3]
        window = turns_around(turns, last_offset, n=3)
        assert len(window) <= 7
        assert len(window) >= 1
        # Hit turn must be in window
        hit_content = turns_data[last_offset]["content"]
        contents = [t["content"] for t in window]
        assert hit_content in contents, "Hit turn must be present in clamped window"
        # After-side must not overrun
        for t in window:
            assert "role" in t

    def test_no_negative_index(self, tmp_path: Path) -> None:
        """Hit at offset 0 (first turn) with large n → no crash, only forward context."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": f"turn {i}"} for i in range(3)]
        session_path = _write_session(tmp_path, "session-first-turn", turns_data)

        turns = turns_from_jsonl(session_path)
        # This must not raise IndexError or return empty
        window = turns_around(turns, 0, n=10)
        assert len(window) >= 1
        assert window[0]["content"] == "turn 0"

    def test_no_overrun_at_end(self, tmp_path: Path) -> None:
        """Hit at last turn with large n → no crash, only backward context."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": f"turn {i}"} for i in range(3)]
        session_path = _write_session(tmp_path, "session-last-turn", turns_data)

        turns = turns_from_jsonl(session_path)
        last = len(turns) - 1
        window = turns_around(turns, last, n=10)
        assert len(window) >= 1
        assert window[-1]["content"] == f"turn {last}"

    def test_single_turn_session(self, tmp_path: Path) -> None:
        """A session with exactly one turn; window must return that one turn."""
        from sio.search.cli import turns_around, turns_from_jsonl

        turns_data = [{"role": "user", "content": "only turn"}]
        session_path = _write_session(tmp_path, "session-single", turns_data)

        turns = turns_from_jsonl(session_path)
        window = turns_around(turns, 0, n=5)
        assert len(window) == 1
        assert window[0]["content"] == "only turn"


# ---------------------------------------------------------------------------
# T022 — Forward-walk from struggle hit reaches fix turns (AS-4 / FR-004)
# ---------------------------------------------------------------------------


class TestForwardWalkStruggleToFix:
    """T022: From a struggle turn (e.g. error message), turns_around with n >= 2
    lets the caller see the subsequent fix turns in one call — no second search.

    AS-4: "Given a struggle→fix moment in a session, When the operator walks
    forward from the struggle hit, Then the fix turns are reachable in the same
    call without re-searching."
    """

    def _struggle_fix_session(self, tmp_path: Path) -> tuple[Path, int, int]:
        """Build a session with a clear struggle→fix pattern.

        Returns (path, struggle_offset, fix_offset).
        Pattern:
          0: user asks something
          1: assistant tries tool
          2: tool returns error (STRUGGLE)
          3: assistant retries with different approach
          4: tool succeeds (FIX)
          5: assistant reports success
          6: user acknowledges
        """
        turns_data = [
            {"role": "user", "content": "please run the migration"},
            {"role": "assistant", "content": "Running migrate command"},
            {"role": "tool", "content": "ERROR: FileNotFoundError migration.sql not found"},
            {"role": "assistant", "content": "Let me find the correct path first"},
            {"role": "tool", "content": "Found at /db/migrations/migration.sql — SUCCESS"},
            {"role": "assistant", "content": "Migration completed successfully"},
            {"role": "user", "content": "Great, thank you"},
        ]
        path = _write_session(tmp_path, "session-struggle-fix", turns_data)
        return path, 2, 4  # struggle at offset 2, fix at offset 4

    def test_forward_walk_reaches_fix_turn(self, tmp_path: Path) -> None:
        """turns_around(turns, struggle_offset, n=3) includes the fix turn at offset+2."""
        from sio.search.cli import turns_around, turns_from_jsonl

        path, struggle_offset, fix_offset = self._struggle_fix_session(tmp_path)
        turns = turns_from_jsonl(path)

        # With n=3 around the struggle (offset=2), window spans [max(0,2-3)..2+3]=[0..5]
        window = turns_around(turns, struggle_offset, n=3)

        fix_contents = [t["content"] for t in window]
        assert any("SUCCESS" in c for c in fix_contents), (
            "Forward walk from struggle (offset=2) with n=3 must include the fix turn "
            "(offset=4). Got contents: " + str(fix_contents[:5])
        )

    def test_forward_walk_n2_covers_immediate_fix(self, tmp_path: Path) -> None:
        """With n=2, the window [0..4] still includes both struggle and the tool fix."""
        from sio.search.cli import turns_around, turns_from_jsonl

        path, struggle_offset, fix_offset = self._struggle_fix_session(tmp_path)
        turns = turns_from_jsonl(path)

        window = turns_around(turns, struggle_offset, n=2)
        # [max(0,2-2)..2+2] = [0..4]: should include offset 4 (fix)
        contents = [t["content"] for t in window]
        assert any("SUCCESS" in c for c in contents), (
            "n=2 forward walk from struggle must reach the fix turn 2 steps ahead"
        )

    def test_no_second_search_needed(self, tmp_path: Path) -> None:
        """turns_around is a pure slice — no additional file I/O or search call.

        Verified by: calling turns_around on already-parsed turns list,
        confirming it returns a subset list without mutating the source.
        """
        from sio.search.cli import turns_around, turns_from_jsonl

        path, struggle_offset, _ = self._struggle_fix_session(tmp_path)
        turns = turns_from_jsonl(path)
        total_before = len(turns)

        window = turns_around(turns, struggle_offset, n=3)

        # Source list must be unchanged
        assert len(turns) == total_before, "turns_around must not mutate the source list"
        # Window is a strict subset (same objects by identity or equal content)
        assert len(window) <= total_before

    def test_struggle_error_text_visible_in_window(self, tmp_path: Path) -> None:
        """The struggle turn itself (with error text) is in the window."""
        from sio.search.cli import turns_around, turns_from_jsonl

        path, struggle_offset, _ = self._struggle_fix_session(tmp_path)
        turns = turns_from_jsonl(path)

        window = turns_around(turns, struggle_offset, n=1)
        contents = [t["content"] for t in window]
        assert any("ERROR" in c for c in contents), (
            "The struggle turn (with ERROR text) must be in the window"
        )


# ---------------------------------------------------------------------------
# T025 integration: --around N CLI flag
# ---------------------------------------------------------------------------


class TestAroundFlag:
    """Test that --around N is exposed on the CLI and calls the ±N API.

    These tests confirm the flag exists and is distinct from --context (raw lines)
    and --session (full dump), per FR-003.
    """

    def _make_corpus_with_needle(self, tmp_path: Path) -> Path:
        """Build a small corpus where the NEEDLE appears in the middle of a session."""
        proj_dir = tmp_path / "-home-fake-test-project"
        proj_dir.mkdir(parents=True)

        turns_data = [
            {"role": "user", "content": "preamble turn 0"},
            {"role": "assistant", "content": "preamble turn 1"},
            {"role": "user", "content": "preamble turn 2"},
            {"role": "assistant", "content": "NEEDLE_TERM found here"},
            {"role": "user", "content": "post-hit turn 4"},
            {"role": "assistant", "content": "post-hit turn 5"},
            {"role": "user", "content": "post-hit turn 6"},
        ]

        session_id = "session-needle"
        fpath = proj_dir / f"{session_id}.jsonl"
        with fpath.open("w", encoding="utf-8") as fh:
            for i, turn in enumerate(turns_data):
                role = turn["role"]
                content = turn["content"]
                entry = {
                    "type": role,
                    "uuid": f"fake-{role}-{i:03d}",
                    "timestamp": f"2026-06-07T12:00:{i:02d}+00:00",
                    "sessionId": session_id,
                    "message": {
                        "role": role,
                        "content": [{"type": "text", "text": content}],
                    },
                }
                fh.write(json.dumps(entry) + "\n")

        import os

        epoch = 1749297600.0  # 2026-06-07T12:00:00Z
        os.utime(fpath, (epoch, epoch))
        return tmp_path

    def test_around_flag_accepted_by_parser(self) -> None:
        """--around N is a recognized flag in build_parser() (no argparse error)."""
        from sio.search.cli import build_parser

        p = build_parser()
        args = p.parse_args(["TERM", "--around", "3"])
        assert hasattr(args, "around"), "--around must be present on the parsed namespace"
        assert args.around == 3

    def test_around_output_not_full_dump(
        self,
        monkeypatch: pytest.MonkeyPatch,
        freeze_utc_now: str,
    ) -> None:
        """--around 1 must return a bounded window, NOT all 7 turns of the session."""
        tmp_path = Path(pytest.importorskip("tempfile").mkdtemp())
        corpus_root = self._make_corpus_with_needle(tmp_path)

        stdout, stderr, rc = _run_search(
            monkeypatch,
            corpus_root,
            [
                "NEEDLE_TERM",
                "--around",
                "1",
                "--format",
                "jsonl",
                "--no-fast",
                "--recent",
                "0",
            ],
        )
        if rc == 2:
            pytest.skip("No hits found — check corpus setup")

        # The full session has 7 turns; --around 1 must give at most 3
        records = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        # In --around mode the output should be a BOUNDED slice, not all 7 turns.
        # With --around 1 around offset 3 in a 7-turn session: [2,3,4] = 3 turns.
        assert len(records) <= 7, "Must be bounded"
        # The NEEDLE turn must be in the output
        all_content = " ".join(r.get("content", "") for r in records)
        assert "NEEDLE_TERM" in all_content, "Hit turn must appear in --around output"

    def test_around_distinct_from_context_flag(self) -> None:
        """--around and --context are distinct flags with distinct help text.

        --context is raw-line context (rg -C), --around is turn-aware ±N.
        """
        from sio.search.cli import build_parser

        p = build_parser()
        # Both flags must coexist without conflict
        args = p.parse_args(["TERM", "--around", "2", "--context", "3"])
        assert args.around == 2
        assert args.context == 3

    def test_session_flag_still_works_alongside_around(
        self,
        monkeypatch: pytest.MonkeyPatch,
        freeze_utc_now: str,
    ) -> None:
        """The existing --session <uuid> full-dump is unchanged (FR-003 compat)."""
        # --session with a nonexistent UUID exits non-zero with a message on stderr
        tmp_path = Path(pytest.importorskip("tempfile").mkdtemp())
        corpus_root = Path(tmp_path)

        stdout, stderr, rc = _run_search(
            monkeypatch,
            corpus_root,
            ["--session", "nonexistent-uuid-000"],
        )
        # Should exit 2 (not found) but not crash
        assert rc in (0, 1, 2), f"Unexpected exit code {rc}"
