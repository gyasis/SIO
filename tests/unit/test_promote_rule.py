"""Unit tests for the violated-rule → PreToolUse hook promotion pipeline.

Covers the deterministic parts (generator, verifier, sample picker)
that don't need a live LM. The LM-dependent extractor was verified
end-to-end during the manual phase walks documented in
``prds/prd-violated-rule-to-pretooluse-hook.md`` — re-running it
in CI would be flaky and metered, so it's excluded here.

Each test isolates SIO state via ``SIO_HOME`` / ``SIO_DB_PATH``
pointing at ``tmp_path`` so nothing the user has installed under
``~/.sio/`` or ``~/.claude/`` is touched.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sio.promote_rule import (
    DetectionPattern,
    HookGenerationResult,
    VerificationResult,
    generate_and_register,
    verify_against_history,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_canonical_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated ~/.sio/sio.db with the full schema applied."""
    sio_home = tmp_path / "sio_home"
    sio_home.mkdir()
    canonical = sio_home / "sio.db"
    monkeypatch.setenv("SIO_HOME", str(sio_home))
    monkeypatch.setenv("SIO_DB_PATH", str(canonical))

    # init_db creates every base table including promoted_hooks
    from sio.core.db.schema import init_db

    init_db(str(canonical)).close()
    return canonical


@pytest.fixture
def isolated_claude_dir(tmp_path: Path) -> Path:
    claude = tmp_path / "claude"
    claude.mkdir()
    return claude


@pytest.fixture
def bash_write_pattern() -> DetectionPattern:
    """The detection pattern the LM produced for rule #1 in real testing.

    Pinned here so tests are deterministic — the LM call is excluded
    from this test file.
    """
    return DetectionPattern(
        matcher_tools=["Bash", "Edit", "Write"],
        detection_expr=(
            "('Bash' in recent_tool_names and tool_name in ('Edit', 'Write')) "
            "or ('Edit' in recent_tool_names and tool_name == 'Bash') "
            "or ('Write' in recent_tool_names and tool_name == 'Bash')"
        ),
        rationale=(
            "Fires when Bash and Write/Edit are interleaved within a "
            "single turn — the parallel-call pattern the rule forbids."
        ),
        promotable=True,
    )


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


class TestGenerateAndRegister:
    def test_writes_hook_script_in_promoted_dir(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        result = generate_and_register(
            bash_write_pattern,
            rule_text="Never call Bash in parallel with Write or Edit.",
            rule_source_file="/some/CLAUDE.md",
            rule_source_line=42,
            mode="warn",
            claude_dir=isolated_claude_dir,
            canonical_db_path=isolated_canonical_db,
        )
        assert result.hook_path.exists()
        assert result.hook_path.parent == isolated_claude_dir / "hooks" / "sio-promoted"
        # Executable
        assert result.hook_path.stat().st_mode & 0o111

    def test_registers_pretooluse_hook_in_settings(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        generate_and_register(
            bash_write_pattern,
            rule_text="Never call Bash in parallel with Write or Edit.",
            rule_source_file="/some/CLAUDE.md",
            rule_source_line=42,
            mode="warn",
            claude_dir=isolated_claude_dir,
            canonical_db_path=isolated_canonical_db,
        )
        settings = json.loads((isolated_claude_dir / "settings.json").read_text())
        pre_tool_use = settings["hooks"]["PreToolUse"]
        assert len(pre_tool_use) == 1
        entry = pre_tool_use[0]
        assert entry["matcher"] == "Bash|Edit|Write"
        # The actual command points at the generated script
        assert "sio-promoted" in entry["hooks"][0]["command"]

    def test_records_audit_row_in_promoted_hooks(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        result = generate_and_register(
            bash_write_pattern,
            rule_text="Never call Bash in parallel with Write or Edit.",
            rule_source_file="/some/CLAUDE.md",
            rule_source_line=42,
            mode="warn",
            claude_dir=isolated_claude_dir,
            canonical_db_path=isolated_canonical_db,
        )
        with sqlite3.connect(isolated_canonical_db) as conn:
            row = conn.execute(
                "SELECT id, rule_text, hook_event, mode, hook_path "
                "FROM promoted_hooks WHERE id=?",
                (result.promoted_hook_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == result.promoted_hook_id
        assert row[1] == "Never call Bash in parallel with Write or Edit."
        assert row[2] == "PreToolUse"
        assert row[3] == "warn"
        assert row[4] == str(result.hook_path)

    def test_settings_idempotent_on_same_hook_path(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        for _ in range(2):
            generate_and_register(
                bash_write_pattern,
                rule_text="Never call Bash in parallel with Write or Edit.",
                rule_source_file="/some/CLAUDE.md",
                rule_source_line=42,
                mode="warn",
                claude_dir=isolated_claude_dir,
                canonical_db_path=isolated_canonical_db,
            )
        settings = json.loads((isolated_claude_dir / "settings.json").read_text())
        # Settings.json is idempotent on hook_path — one entry, not two
        assert len(settings["hooks"]["PreToolUse"]) == 1

    def test_settings_preserves_unrelated_user_hooks(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        # User has their own PreToolUse hook for a different concern
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "/usr/local/bin/my-bash-guard"}
                        ],
                    }
                ]
            }
        }
        (isolated_claude_dir / "settings.json").write_text(json.dumps(existing))

        generate_and_register(
            bash_write_pattern,
            rule_text="Never call Bash in parallel with Write or Edit.",
            rule_source_file="/some/CLAUDE.md",
            rule_source_line=42,
            mode="warn",
            claude_dir=isolated_claude_dir,
            canonical_db_path=isolated_canonical_db,
        )
        settings = json.loads((isolated_claude_dir / "settings.json").read_text())
        commands = [
            inner["command"]
            for entry in settings["hooks"]["PreToolUse"]
            for inner in entry.get("hooks", [])
        ]
        assert any("my-bash-guard" in c for c in commands), \
            "user's existing PreToolUse hook was wiped"
        assert any("sio-promoted" in c for c in commands), \
            "SIO promoted hook was not added alongside user's"

    def test_raises_on_non_promotable_pattern(
        self,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        not_promotable = DetectionPattern(
            matcher_tools=[],
            detection_expr="False",
            rationale="rule is not structurally enforceable",
            promotable=False,
        )
        with pytest.raises(ValueError, match="not promotable"):
            generate_and_register(
                not_promotable,
                rule_text="Always be careful.",
                rule_source_file="/some/CLAUDE.md",
                rule_source_line=1,
                mode="warn",
                claude_dir=isolated_claude_dir,
                canonical_db_path=isolated_canonical_db,
            )

    def test_raises_on_invalid_mode(
        self,
        bash_write_pattern: DetectionPattern,
        isolated_claude_dir: Path,
        isolated_canonical_db: Path,
    ) -> None:
        with pytest.raises(ValueError, match="mode must be"):
            generate_and_register(
                bash_write_pattern,
                rule_text="x",
                rule_source_file="/x",
                rule_source_line=1,
                mode="silent",  # invalid
                claude_dir=isolated_claude_dir,
                canonical_db_path=isolated_canonical_db,
            )


# ---------------------------------------------------------------------------
# Verifier tests
# ---------------------------------------------------------------------------


class TestVerifyAgainstHistory:
    def test_empty_matching_returns_zero_coverage(
        self, bash_write_pattern: DetectionPattern
    ) -> None:
        result = verify_against_history(bash_write_pattern, [])
        assert result.total == 0
        assert result.fires == 0
        assert result.coverage_rate == 0.0
        assert result.by_session == {}

    def test_fires_on_bash_after_edit(
        self, bash_write_pattern: DetectionPattern
    ) -> None:
        # Two-call session: Edit then Bash — the rule fires on the second call
        matching = [
            {
                "tool_name": "Edit",
                "tool_input": '{"file_path": "/tmp/x"}',
                "session_id": "s1",
                "timestamp": "2026-05-01T00:00:00Z",
            },
            {
                "tool_name": "Bash",
                "tool_input": '{"command": "ls"}',
                "session_id": "s1",
                "timestamp": "2026-05-01T00:00:01Z",
            },
        ]
        result = verify_against_history(bash_write_pattern, matching)
        # First call (Edit, no recent) should NOT fire; second (Bash after Edit) should
        assert result.total == 2
        assert result.fires == 1
        assert result.coverage_rate == 0.5

    def test_fires_on_edit_after_bash(
        self, bash_write_pattern: DetectionPattern
    ) -> None:
        matching = [
            {
                "tool_name": "Bash",
                "tool_input": '{"command": "ls"}',
                "session_id": "s1",
                "timestamp": "2026-05-01T00:00:00Z",
            },
            {
                "tool_name": "Edit",
                "tool_input": '{"file_path": "/tmp/x"}',
                "session_id": "s1",
                "timestamp": "2026-05-01T00:00:01Z",
            },
        ]
        result = verify_against_history(bash_write_pattern, matching)
        assert result.fires == 1
        # Edit-after-Bash fires; lone Bash doesn't
        assert result.examples_fired[0]["tool_name"] == "Edit"

    def test_separate_sessions_have_independent_state(
        self, bash_write_pattern: DetectionPattern
    ) -> None:
        # Bash in session A then Edit in session B — should NOT fire (different sessions)
        matching = [
            {
                "tool_name": "Bash",
                "tool_input": "{}",
                "session_id": "A",
                "timestamp": "2026-05-01T00:00:00Z",
            },
            {
                "tool_name": "Edit",
                "tool_input": "{}",
                "session_id": "B",
                "timestamp": "2026-05-01T00:00:01Z",
            },
        ]
        result = verify_against_history(bash_write_pattern, matching)
        assert result.fires == 0  # cross-session → no carry of recent_tool_names

    def test_eval_failure_is_safe_miss(
        self,
    ) -> None:
        # Malformed detection_expr — eval fails, every call counted as miss (no fire)
        bad = DetectionPattern(
            matcher_tools=["Bash"],
            detection_expr="this is not valid python !!!",
            rationale="bad",
            promotable=True,
        )
        matching = [
            {
                "tool_name": "Bash",
                "tool_input": "{}",
                "session_id": "s1",
                "timestamp": "2026-05-01T00:00:00Z",
            },
        ]
        result = verify_against_history(bad, matching)
        assert result.fires == 0
        assert result.misses == 1

    def test_returns_capped_examples(
        self, bash_write_pattern: DetectionPattern
    ) -> None:
        # 20 firing calls — examples should cap at 5
        matching = []
        for i in range(20):
            matching.append(
                {
                    "tool_name": "Bash",
                    "tool_input": "{}",
                    "session_id": f"s{i % 2}",  # 2 sessions
                    "timestamp": f"2026-05-01T00:00:{i:02d}Z",
                }
            )
            matching.append(
                {
                    "tool_name": "Edit",
                    "tool_input": "{}",
                    "session_id": f"s{i % 2}",
                    "timestamp": f"2026-05-01T00:00:{i + 30:02d}Z",
                }
            )
        result = verify_against_history(bash_write_pattern, matching)
        assert len(result.examples_fired) <= 5
        assert len(result.examples_missed) <= 5
