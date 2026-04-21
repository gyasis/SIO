"""Tests for sio.ground_truth.seeder — T039."""

from __future__ import annotations

import pytest

from sio.core.config import SIOConfig
from sio.core.db.schema import init_db


@pytest.fixture
def mem_db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def config():
    return SIOConfig()


_ALL_SURFACES = frozenset(
    {
        "claude_md_rule",
        "skill_update",
        "hook_config",
        "mcp_config",
        "settings_config",
        "agent_profile",
        "project_config",
    }
)


class TestSeedGroundTruth:
    def test_generates_10_entries(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db)
        assert len(ids) == 10

    def test_covers_all_7_surfaces(self, config, mem_db):
        """Seed entries should cover all 7 target surfaces."""
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)

        rows = mem_db.execute("SELECT DISTINCT target_surface FROM ground_truth").fetchall()
        surfaces = {dict(r)["target_surface"] for r in rows}

        assert surfaces == _ALL_SURFACES

    def test_all_entries_source_seed(self, config, mem_db):
        """All seed entries should have source='seed'."""
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)

        rows = mem_db.execute("SELECT source FROM ground_truth").fetchall()
        for row in rows:
            assert dict(row)["source"] == "seed"

    def test_all_entries_label_positive(self, config, mem_db):
        """Seed entries are pre-approved (label='positive')."""
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)

        rows = mem_db.execute("SELECT label FROM ground_truth").fetchall()
        for row in rows:
            assert dict(row)["label"] == "positive"

    def test_returns_valid_row_ids(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db)

        for row_id in ids:
            row = mem_db.execute("SELECT id FROM ground_truth WHERE id = ?", (row_id,)).fetchone()
            assert row is not None

    def test_entries_have_required_fields(self, config, mem_db):
        """Every seed entry should have non-empty required fields."""
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)

        rows = mem_db.execute("SELECT * FROM ground_truth").fetchall()
        for row in rows:
            d = dict(row)
            assert d["pattern_id"]
            assert d["error_examples_json"]
            assert d["error_type"]
            assert d["pattern_summary"]
            assert d["target_surface"]
            assert d["rule_title"]
            assert d["prevention_instructions"]
            assert d["rationale"]

    def test_no_llm_needed(self, config, mem_db):
        """Seeder should work without any LLM configuration."""
        config.llm_model = None

        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db)
        assert len(ids) == 10
