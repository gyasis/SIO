"""Rule readers must see ~/.claude/rulebook/, not only ~/.claude/rules/.

Claude Code auto-loads every .md under ~/.claude/rules/, so on-demand rules
moved to ~/.claude/rulebook/ (2026-10-01). Readers that scanned only rules/
went blind to them: violations, budget, dedupe, rule-audit, the suggest
consultant, active-rule stamping and cohort snapshots.
"""
from __future__ import annotations

from pathlib import Path

from sio import rules_snapshot
from sio.core.cohort.snapshot import build_manifest
from sio.core.paths import claude_rule_dirs, iter_claude_rule_files


def _home(tmp_path: Path) -> Path:
    claude = tmp_path / ".claude"
    (claude / "rules" / "core").mkdir(parents=True)
    (claude / "rulebook" / "domains").mkdir(parents=True)
    (claude / "rules" / "core" / "memory.md").write_text("# Memory\nNEVER x\n")
    (claude / "rulebook" / "domains" / "databricks.md").write_text("# DBX\nALWAYS y\n")
    # alias symlink -> must count once
    (claude / "rulebook" / "domains" / "dbx.md").symlink_to("databricks.md")
    return claude


def test_claude_rule_dirs_names_rules_and_rulebook(tmp_path):
    claude = tmp_path / ".claude"
    assert claude_rule_dirs(claude) == [claude / "rules", claude / "rulebook"]


def test_iter_rule_files_covers_rulebook_and_dedupes_aliases(tmp_path):
    claude = _home(tmp_path)
    names = sorted(p.name for p in iter_claude_rule_files(claude))
    assert names == ["databricks.md", "memory.md"]


def test_iter_rule_files_tolerates_missing_dirs(tmp_path):
    assert iter_claude_rule_files(tmp_path / ".claude") == []


def test_rules_snapshot_includes_rulebook_with_continuous_ids(tmp_path, monkeypatch):
    claude = _home(tmp_path)
    monkeypatch.setattr(rules_snapshot, "_RULES_ROOT", claude / "rules")
    ids = sorted(
        filter(None, (rules_snapshot._rule_id(p) for p in rules_snapshot._walk_rule_files()))
    )
    paths = [i.split("#", 1)[0] for i in ids]
    # rulebook rule keeps its pre-move id ("domains/x.md"), core keeps "core/x.md"
    assert "domains/databricks.md" in paths
    assert "core/memory.md" in paths


def test_cohort_rules_hash_unchanged_without_rulebook(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "rules").mkdir(parents=True)
    (claude / "rules" / "r.md").write_text("rule r")
    assert list(build_manifest(claude_home=claude)["rules"]) == ["r.md"]


def test_cohort_rules_hash_includes_rulebook(tmp_path):
    claude = _home(tmp_path)
    rules = build_manifest(claude_home=claude)["rules"]
    assert "core/memory.md" in rules
    assert "rulebook/domains/databricks.md" in rules
