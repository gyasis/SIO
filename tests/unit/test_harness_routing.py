"""Unit tests for multi-agent (Tier-3) harness routing in suggestion generation.

Covers the pure routing helpers that decide WHERE a suggestion is written for
each harness:
- claude-code keeps its tiered config surface (CLAUDE.md / rules/ / skills/).
- codex / gemini / goose each route every suggestion to their single
  persistent-instruction file (AGENTS.md / GEMINI.md / .goosehints).

See PRD.md §3 (Platform Compatibility).
"""

from __future__ import annotations

import pytest

from sio.suggestions.generator import (
    _HARNESS_INSTRUCTION_FILES,
    DEFAULT_HARNESS,
    _infer_change_type,
    _infer_target_file,
)


def test_default_harness_is_claude_code():
    assert DEFAULT_HARNESS == "claude-code"


def test_claude_routing_unchanged_for_known_tool():
    """A known tool still routes to the tiered rulebook/tools path for claude."""
    pattern = {"tool_name": "graphiti"}
    ct = _infer_change_type(pattern)  # default harness
    assert ct == "tool_rule"
    assert _infer_target_file(pattern, ct) == ".claude/rulebook/tools/graphiti.md"


def test_claude_general_pattern_routes_to_claude_md():
    pattern = {"tool_name": "unknown"}
    ct = _infer_change_type(pattern)
    assert ct == "claude_md_rule"
    assert _infer_target_file(pattern, ct) == "CLAUDE.md"


@pytest.mark.parametrize(
    ("harness", "expected_file", "expected_type"),
    [
        ("codex", "~/.codex/AGENTS.md", "agents_md_rule"),
        ("gemini", "~/.gemini/GEMINI.md", "gemini_md_rule"),
        ("goose", "~/.config/goose/.goosehints", "goosehints_rule"),
    ],
)
def test_non_claude_harness_routes_to_single_instruction_file(
    harness, expected_file, expected_type
):
    """Every non-claude harness ignores tool tiering and targets one file."""
    # Even a pattern that WOULD route to a tiered claude file collapses to the
    # harness's single instruction file.
    pattern = {"tool_name": "graphiti"}
    ct = _infer_change_type(pattern, harness)
    assert ct == expected_type
    assert _infer_target_file(pattern, ct, harness) == expected_file


def test_registry_matches_inference():
    """The registry is the single source of truth the helpers read from."""
    for harness, (target, change_type) in _HARNESS_INSTRUCTION_FILES.items():
        pattern = {"tool_name": "anything"}
        assert _infer_change_type(pattern, harness) == change_type
        assert _infer_target_file(pattern, "ignored", harness) == target


def test_claude_bootstrap_rules_install_into_rulebook(tmp_path):
    """Bootstrap rules/ land in ~/.claude/rulebook/, never the auto-loaded rules/."""
    from pathlib import Path

    from sio.harnesses.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter(config_dir=tmp_path / ".claude")
    assert a._resolve_target(Path("rules/tools/sio.md")) == tmp_path / ".claude/rulebook/tools/sio.md"
    assert a._resolve_target(Path("skills/x/SKILL.md")) == tmp_path / ".claude/skills/x/SKILL.md"
