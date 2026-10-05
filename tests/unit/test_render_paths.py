"""`sio render` must not flood every Claude Code session.

Claude Code auto-loads every .md under ~/.claude/rules/. The claude-md format used
to default to ~/.claude/rules/auto/, so each render added always-loaded context;
the skill format wrote a flat ~/.claude/skills/<name>.md that is not a skill folder
and never replaced the placeholder `sio init` ships.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from sio.cli import render
from sio.render.templates import render_claude_md


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_default_paths_never_land_in_rules_outside_core(home):
    c = home / ".claude"
    assert render.default_output_path("skill", "sio-rule-generator", 15) == (
        c / "skills" / "sio-rule-generator" / "SKILL.md"
    )
    assert render.default_output_path("claude-md", "sio-rule-generator", 15) == (
        c / "rulebook" / "domains" / "sio-rule-generator-15.md"
    )
    assert render.default_output_path("claude-md", "foo", 3, always_load=True) == (
        c / "rules" / "core" / "sio-foo-3.md"
    )
    assert render.default_output_path("system-prompt", "x", 7) == Path("/tmp/sio-prompt-7.txt")


def test_warning_only_for_always_loaded_locations(home):
    c = home / ".claude"
    assert render.always_loaded_warning(c / "rulebook" / "domains" / "r.md", 100) is None
    assert render.always_loaded_warning(home / "elsewhere.md", 100) is None

    auto = render.always_loaded_warning(c / "rules" / "auto" / "r.md", 1234)
    assert auto and "EVERY session" in auto and "rulebook/domains" in auto and "1,234" in auto

    core = render.always_loaded_warning(c / "rules" / "core" / "r.md", 10)
    assert core and "EVERY session" in core and "rulebook" not in core


def test_claude_md_triggers_line():
    art = {"instruction": "Do the thing.", "demos": [], "fields": []}
    meta = {"id": 15, "module_type": "rule_generator"}
    with_t = render_claude_md(art, meta, triggers=render.render_triggers("sio-rule-generator"))
    assert with_t.startswith("<!-- triggers: sio suggest, sio apply, sio optimize, "
                             "sio-rule-generator -->")
    assert not render_claude_md(art, meta).startswith("<!-- triggers:")


def test_always_load_rejected_for_non_claude_md_formats():
    result = CliRunner().invoke(render.render_cmd, ["15", "--always-load"])
    assert result.exit_code == 1
    assert "only applies to --format claude-md" in result.output
