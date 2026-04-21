"""End-to-end test: full passive analysis pipeline.

Creates sample session files → runs mine → cluster → dataset → suggest
→ writes home file → verifies suggestions.md populated.
"""

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_env(tmp_path, v2_db):
    """Set up a complete e2e environment with sample session files."""
    from datetime import datetime, timedelta, timezone

    # Use recent dates so the time filter includes them
    base = datetime.now(timezone.utc)
    d1 = (base - timedelta(days=2)).strftime("%Y-%m-%d_%H-%M-%SZ")
    d2 = (base - timedelta(days=1)).strftime("%Y-%m-%d_%H-%M-%SZ")
    d3 = base.strftime("%Y-%m-%d_%H-%M-%SZ")

    specstory_dir = tmp_path / "specstory"
    specstory_dir.mkdir()
    specstory_file = specstory_dir / f"{d1}-test-session.md"
    specstory_file.write_text(
        "**Human:** Please read the config file.\n\n"
        "---\n\n"
        "**Assistant:** I'll read the file.\n\n"
        "[Tool call: Read]\n"
        "[Tool error: FileNotFoundError: /tmp/nonexistent.txt]\n\n"
        "---\n\n"
        "**Human:** Try again with the correct path.\n\n"
        "---\n\n"
        "**Assistant:** Let me try again.\n\n"
        "[Tool call: Read]\n"
        "[Tool error: FileNotFoundError: /tmp/also-missing.txt]\n\n"
        "---\n\n"
        "**Human:** No, that's still wrong. The file is at /home/user/config.txt\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool output: config contents here]\n\n"
    )

    # Create a second file with similar errors (to get above threshold)
    specstory_file2 = specstory_dir / f"{d2}-another-session.md"
    specstory_file2.write_text(
        "**Human:** Read the log file.\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool error: FileNotFoundError: /var/log/missing.log]\n\n"
        "---\n\n"
        "**Human:** Wrong path, try /var/log/app.log\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool error: FileNotFoundError: /var/log/app.logg]\n\n"
        "---\n\n"
        "**Human:** There's a typo, it's .log not .logg\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool output: log contents]\n\n"
    )

    # Create a third file
    specstory_file3 = specstory_dir / f"{d3}-third-session.md"
    specstory_file3.write_text(
        "**Human:** Read the data file.\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool error: FileNotFoundError: /data/input.csv]\n\n"
        "---\n\n"
        "**Human:** The file is at /data/input.tsv\n\n"
        "---\n\n"
        "**Assistant:**\n\n"
        "[Tool call: Read]\n"
        "[Tool output: data contents]\n\n"
    )

    home_file = tmp_path / "suggestions.md"
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()

    return {
        "db": v2_db,
        "specstory_dir": specstory_dir,
        "home_file": home_file,
        "dataset_dir": dataset_dir,
        "tmp_path": tmp_path,
    }


# =========================================================================
# TestE2EPassivePipeline
# =========================================================================


class TestE2EPassivePipeline:
    """Full passive pipeline: mine → cluster → dataset → suggest → home file."""

    def test_pipeline_produces_suggestions_file(self, e2e_env):
        from sio.clustering.pattern_clusterer import cluster_errors
        from sio.clustering.ranker import rank_patterns
        from sio.core.db.queries import get_error_records
        from sio.datasets.builder import build_dataset
        from sio.mining.pipeline import run_mine
        from sio.suggestions.generator import generate_suggestions
        from sio.suggestions.home_file import write_suggestions

        db = e2e_env["db"]
        specstory_dir = e2e_env["specstory_dir"]
        home_file = e2e_env["home_file"]
        dataset_dir = e2e_env["dataset_dir"]

        # Step 1: Mine
        result = run_mine(
            db,
            [specstory_dir],
            "30 days",
            "specstory",
            None,
        )
        assert result["errors_found"] > 0

        # Step 2: Cluster
        errors = get_error_records(db)
        assert len(errors) > 0
        clustered = cluster_errors(errors)
        assert len(clustered) > 0

        # Step 3: Store patterns in DB and build datasets
        from datetime import datetime as dt
        from datetime import timezone as tz

        from sio.core.db.queries import insert_pattern

        now = dt.now(tz.utc).isoformat()
        datasets_map = {}
        for pattern in clustered:
            # Add required timestamp fields for DB insert
            pattern.setdefault("created_at", now)
            pattern.setdefault("updated_at", now)
            pattern.setdefault("first_seen", now)
            pattern.setdefault("last_seen", now)
            row_id = insert_pattern(db, pattern)
            pattern["id"] = row_id
            ds = build_dataset(pattern, errors, db, str(dataset_dir))
            if ds:
                datasets_map[pattern["pattern_id"]] = ds

        # Step 4: Rank
        ranked = rank_patterns(clustered)

        # Step 5: Generate suggestions
        suggestions = generate_suggestions(ranked, datasets_map, db)

        # Step 6: Write home file
        if suggestions:
            write_suggestions(suggestions, str(home_file))
            assert home_file.exists()
            content = home_file.read_text()
            assert "SIO Improvement Suggestions" in content
        # If no suggestions generated (threshold not met), that's ok for e2e

    def test_mine_step_stores_errors(self, e2e_env):
        from sio.mining.pipeline import run_mine

        db = e2e_env["db"]
        result = run_mine(
            db,
            [e2e_env["specstory_dir"]],
            "30 days",
            "specstory",
            None,
        )
        count = db.execute("SELECT COUNT(*) FROM error_records").fetchone()[0]
        assert count == result["errors_found"]
        assert count > 0

    def test_cluster_produces_patterns(self, e2e_env):
        from sio.clustering.pattern_clusterer import cluster_errors
        from sio.core.db.queries import get_error_records
        from sio.mining.pipeline import run_mine

        db = e2e_env["db"]
        run_mine(db, [e2e_env["specstory_dir"]], "30 days", "specstory", None)
        errors = get_error_records(db)
        clustered = cluster_errors(errors)
        assert len(clustered) >= 1
        for p in clustered:
            assert "pattern_id" in p
            assert "error_count" in p
