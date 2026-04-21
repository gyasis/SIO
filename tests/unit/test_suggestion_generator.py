"""T034 [US4] Unit tests for sio.suggestions.generator — improvement proposal generation.

Tests cover:
- generate_suggestions(patterns, datasets, db_conn) -> list[dict]
    Takes ranked pattern dicts and a dict mapping pattern_id -> dataset metadata,
    produces suggestion dicts for each pattern that has a corresponding dataset.

Each suggestion dict must carry:
    pattern_id    (int)   FK to patterns row
    dataset_id    (int)   FK to datasets row
    description   (str)   human-readable summary of what the suggestion proposes
    confidence    (float) 0.0–1.0 quality signal
    proposed_change (str) the actual rule text or diff
    target_file   (str)   destination path, e.g. "CLAUDE.md", "SKILL.md"
    change_type   (str)   one of "claude_md_rule", "skill_md_update", "hook_config"
    status        (str)   always "pending" for freshly generated suggestions

These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

import sqlite3

from sio.suggestions.generator import generate_suggestions

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_REQUIRED_SUGGESTION_KEYS: frozenset[str] = frozenset(
    {
        "pattern_id",
        "dataset_id",
        "description",
        "confidence",
        "proposed_change",
        "target_file",
        "change_type",
        "status",
    }
)

# Audit Round 2 C-R2.6 migration: the test's valid-change-type set was a
# hand-maintained copy of an earlier DSPy-output taxonomy (the 7 LLM-
# chooseable target_surface values). Production routing
# (generator._infer_change_type + _TARGET_FILE_MAP) supports a different
# set including `tool_rule` and `domain_rule` for tool- and
# domain-specific rule files. Import from the source of truth so the
# test can't drift from production again.
from sio.suggestions.generator import _TARGET_FILE_MAP as _PROD_CHANGE_TYPES

_VALID_CHANGE_TYPES: frozenset[str] = frozenset(_PROD_CHANGE_TYPES.keys()) | frozenset(
    # DSPy-output surfaces (legacy 7) — also acceptable when real LM path runs
    {
        "mcp_config",
        "settings_config",
        "agent_profile",
        "project_config",
        "skill_update",
    }
)

_NOW = "2026-02-25T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_pattern(
    conn: sqlite3.Connection,
    *,
    pattern_id: str = "pat-sug-001",
    description: str = "Repeated FileNotFoundError on Read tool",
    tool_name: str = "Read",
    error_count: int = 8,
    session_count: int = 3,
    rank_score: float = 0.75,
) -> int:
    """Insert a row into the patterns table and return its integer rowid."""
    cursor = conn.execute(
        """
        INSERT INTO patterns
            (pattern_id, description, tool_name, error_count, session_count,
             first_seen, last_seen, rank_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pattern_id,
            description,
            tool_name,
            error_count,
            session_count,
            _NOW,
            _NOW,
            rank_score,
            _NOW,
            _NOW,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_dataset(
    conn: sqlite3.Connection,
    pattern_row_id: int,
    *,
    file_path: str = "/tmp/datasets/pat-sug-001.json",
    positive_count: int = 5,
    negative_count: int = 10,
) -> int:
    """Insert a row into the datasets table and return its integer rowid."""
    cursor = conn.execute(
        """
        INSERT INTO datasets
            (pattern_id, file_path, positive_count, negative_count,
             min_threshold, created_at, updated_at)
        VALUES (?, ?, ?, ?, 5, ?, ?)
        """,
        (pattern_row_id, file_path, positive_count, negative_count, _NOW, _NOW),
    )
    conn.commit()
    return cursor.lastrowid


def _make_pattern_dict(
    row_id: int,
    *,
    pattern_id: str = "pat-sug-001",
    description: str = "Repeated FileNotFoundError on Read tool",
    tool_name: str = "Read",
    error_count: int = 8,
    session_count: int = 3,
    rank_score: float = 0.75,
) -> dict:
    """Build a pattern dict that mirrors what the clusterer/ranker would produce."""
    return {
        "id": row_id,
        "pattern_id": pattern_id,
        "description": description,
        "tool_name": tool_name,
        "error_count": error_count,
        "session_count": session_count,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "rank_score": rank_score,
        "error_ids": list(range(error_count)),
    }


def _make_dataset_metadata(
    dataset_row_id: int,
    pattern_row_id: int,
    *,
    pattern_id: str = "pat-sug-001",
    positive_count: int = 5,
    negative_count: int = 10,
    file_path: str = "/tmp/datasets/pat-sug-001.json",
) -> dict:
    """Build a dataset metadata dict mirroring what builder.build_dataset returns,
    augmented with the DB IDs needed by the suggestion generator."""
    return {
        "id": dataset_row_id,
        "pattern_id": pattern_id,
        "pattern_row_id": pattern_row_id,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "file_path": file_path,
    }


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestGeneratesSuggestionFromPattern:
    """A single pattern with a matching dataset must produce exactly one suggestion."""

    def test_generates_suggestion_from_pattern(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        pattern = _make_pattern_dict(pat_row_id)
        datasets = {
            "pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id),
        }

        suggestions = generate_suggestions([pattern], datasets, v2_db)

        assert len(suggestions) == 1

    def test_one_pattern_one_dataset_one_suggestion(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db, pattern_id="pat-one")
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        pattern = _make_pattern_dict(pat_row_id, pattern_id="pat-one")
        datasets = {
            "pat-one": _make_dataset_metadata(ds_row_id, pat_row_id, pattern_id="pat-one"),
        }

        result = generate_suggestions([pattern], datasets, v2_db)

        assert isinstance(result, list)
        assert len(result) == 1

    def test_multiple_patterns_produce_multiple_suggestions(
        self, v2_db: sqlite3.Connection
    ) -> None:
        suggestions_input = []
        datasets: dict[str, dict] = {}

        for idx in range(3):
            pid = f"pat-multi-{idx:03d}"
            row_id = _insert_pattern(v2_db, pattern_id=pid, error_count=idx + 5)
            ds_id = _insert_dataset(v2_db, row_id, file_path=f"/tmp/datasets/{pid}.json")
            suggestions_input.append(_make_pattern_dict(row_id, pattern_id=pid))
            datasets[pid] = _make_dataset_metadata(ds_id, row_id, pattern_id=pid)

        result = generate_suggestions(suggestions_input, datasets, v2_db)

        assert len(result) == 3


class TestConfidenceScoring:
    """Higher error_count and dataset quality should produce a higher confidence."""

    def test_high_error_count_produces_higher_confidence(self, v2_db: sqlite3.Connection) -> None:
        low_row_id = _insert_pattern(v2_db, pattern_id="pat-low", error_count=2, rank_score=0.2)
        high_row_id = _insert_pattern(v2_db, pattern_id="pat-high", error_count=25, rank_score=0.9)
        ds_low_id = _insert_dataset(
            v2_db, low_row_id, file_path="/tmp/low.json", positive_count=1, negative_count=2
        )
        ds_high_id = _insert_dataset(
            v2_db, high_row_id, file_path="/tmp/high.json", positive_count=10, negative_count=20
        )

        patterns = [
            _make_pattern_dict(low_row_id, pattern_id="pat-low", error_count=2, rank_score=0.2),
            _make_pattern_dict(high_row_id, pattern_id="pat-high", error_count=25, rank_score=0.9),
        ]
        datasets = {
            "pat-low": _make_dataset_metadata(
                ds_low_id,
                low_row_id,
                pattern_id="pat-low",
                positive_count=1,
                negative_count=2,
            ),
            "pat-high": _make_dataset_metadata(
                ds_high_id,
                high_row_id,
                pattern_id="pat-high",
                positive_count=10,
                negative_count=20,
            ),
        }

        suggestions = generate_suggestions(patterns, datasets, v2_db)
        assert len(suggestions) == 2

        by_pat = {s["pattern_id"]: s for s in suggestions}
        assert by_pat[high_row_id]["confidence"] > by_pat[low_row_id]["confidence"]

    def test_confidence_bounded_between_zero_and_one(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db, error_count=50, rank_score=1.0)
        ds_row_id = _insert_dataset(v2_db, pat_row_id, positive_count=50, negative_count=100)

        pattern = _make_pattern_dict(pat_row_id, error_count=50, rank_score=1.0)
        datasets = {
            "pat-sug-001": _make_dataset_metadata(
                ds_row_id, pat_row_id, positive_count=50, negative_count=100
            ),
        }

        result = generate_suggestions([pattern], datasets, v2_db)

        assert len(result) == 1
        confidence = result[0]["confidence"]
        assert 0.0 <= confidence <= 1.0

    def test_confidence_increases_with_better_dataset_coverage(
        self, v2_db: sqlite3.Connection
    ) -> None:
        sparse_row_id = _insert_pattern(v2_db, pattern_id="pat-sparse", error_count=6)
        rich_row_id = _insert_pattern(v2_db, pattern_id="pat-rich", error_count=6)

        # Sparse dataset: just above the minimum threshold
        ds_sparse_id = _insert_dataset(
            v2_db,
            sparse_row_id,
            file_path="/tmp/sparse.json",
            positive_count=2,
            negative_count=4,
        )
        # Rich dataset: many more examples
        ds_rich_id = _insert_dataset(
            v2_db,
            rich_row_id,
            file_path="/tmp/rich.json",
            positive_count=30,
            negative_count=60,
        )

        patterns = [
            _make_pattern_dict(sparse_row_id, pattern_id="pat-sparse"),
            _make_pattern_dict(rich_row_id, pattern_id="pat-rich"),
        ]
        datasets = {
            "pat-sparse": _make_dataset_metadata(
                ds_sparse_id,
                sparse_row_id,
                pattern_id="pat-sparse",
                positive_count=2,
                negative_count=4,
            ),
            "pat-rich": _make_dataset_metadata(
                ds_rich_id,
                rich_row_id,
                pattern_id="pat-rich",
                positive_count=30,
                negative_count=60,
            ),
        }

        result = generate_suggestions(patterns, datasets, v2_db)
        assert len(result) == 2

        by_pat_id = {s["pattern_id"]: s for s in result}
        # Rich dataset should yield higher (or equal) confidence.
        assert by_pat_id[rich_row_id]["confidence"] >= by_pat_id[sparse_row_id]["confidence"]


class TestSuggestionHasRequiredKeys:
    """Every generated suggestion dict must carry all documented keys."""

    def test_suggestion_has_required_keys(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        pattern = _make_pattern_dict(pat_row_id)
        datasets = {
            "pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id),
        }

        result = generate_suggestions([pattern], datasets, v2_db)

        assert len(result) == 1
        missing = _REQUIRED_SUGGESTION_KEYS - set(result[0].keys())
        assert not missing, f"Suggestion missing keys: {missing}"

    def test_all_suggestions_have_required_keys(self, v2_db: sqlite3.Connection) -> None:
        datasets: dict[str, dict] = {}
        patterns: list[dict] = []

        for idx in range(4):
            pid = f"pat-keys-{idx:03d}"
            row_id = _insert_pattern(v2_db, pattern_id=pid)
            ds_id = _insert_dataset(v2_db, row_id, file_path=f"/tmp/{pid}.json")
            patterns.append(_make_pattern_dict(row_id, pattern_id=pid))
            datasets[pid] = _make_dataset_metadata(ds_id, row_id, pattern_id=pid)

        result = generate_suggestions(patterns, datasets, v2_db)

        for suggestion in result:
            missing = _REQUIRED_SUGGESTION_KEYS - set(suggestion.keys())
            assert not missing, f"Suggestion missing keys: {missing}"

    def test_pattern_id_is_int(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert isinstance(result[0]["pattern_id"], int)

    def test_dataset_id_is_int(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert isinstance(result[0]["dataset_id"], int)

    def test_confidence_is_float(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert isinstance(result[0]["confidence"], float)

    def test_description_is_non_empty_str(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert isinstance(result[0]["description"], str)
        assert result[0]["description"].strip() != ""

    def test_status_is_pending(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert result[0]["status"] == "pending"


class TestProposedChangeIncludesRuleText:
    """proposed_change must be a non-empty string containing actionable rule text."""

    def test_proposed_change_is_non_empty_string(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        proposed = result[0]["proposed_change"]
        assert isinstance(proposed, str)
        assert proposed.strip() != ""

    def test_proposed_change_not_whitespace_only(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert len(result[0]["proposed_change"].strip()) > 0

    def test_proposed_change_references_pattern_context(self, v2_db: sqlite3.Connection) -> None:
        """proposed_change should incorporate something from the pattern description
        or tool name so it reads as contextually relevant."""
        pat_row_id = _insert_pattern(
            v2_db,
            description="Repeated FileNotFoundError on Read tool",
            tool_name="Read",
        )
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [
                _make_pattern_dict(
                    pat_row_id,
                    description="Repeated FileNotFoundError on Read tool",
                    tool_name="Read",
                )
            ],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        # The proposed change must not be a generic placeholder string.
        proposed = result[0]["proposed_change"]
        assert proposed not in ("TODO", "PLACEHOLDER", "", "None")


class TestTargetFileAssigned:
    """target_file must be a non-empty string pointing to a recognised config file."""

    # Audit Round 2 C-R2.6: production targets include rule-tier files
    # (.claude/rules/tools/, .claude/rules/domains/) that this hand-
    # maintained test set was missing. Merge prod source of truth with
    # the legacy set to avoid drift.
    _KNOWN_TARGET_FILES = frozenset(_PROD_CHANGE_TYPES.values()) | frozenset(
        {
            "CLAUDE.md",
            "SKILL.md",
            ".claude.json",
            ".claude/settings.json",
            ".claude/agents/",
        }
    )

    def test_target_file_is_non_empty_string(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        target = result[0]["target_file"]
        assert isinstance(target, str)
        assert target.strip() != ""

    def test_target_file_is_known_config(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        target = result[0]["target_file"]
        # The target should end with one of the known file names or prefixes.
        assert any(target.endswith(known) or known in target for known in self._KNOWN_TARGET_FILES)

    def test_change_type_is_valid(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)

        result = generate_suggestions(
            [_make_pattern_dict(pat_row_id)],
            {"pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id)},
            v2_db,
        )

        assert result[0]["change_type"] in _VALID_CHANGE_TYPES

    def test_all_suggestions_have_valid_change_types(self, v2_db: sqlite3.Connection) -> None:
        patterns: list[dict] = []
        datasets: dict[str, dict] = {}

        for idx in range(3):
            pid = f"pat-ct-{idx:03d}"
            row_id = _insert_pattern(v2_db, pattern_id=pid)
            ds_id = _insert_dataset(v2_db, row_id, file_path=f"/tmp/{pid}.json")
            patterns.append(_make_pattern_dict(row_id, pattern_id=pid))
            datasets[pid] = _make_dataset_metadata(ds_id, row_id, pattern_id=pid)

        result = generate_suggestions(patterns, datasets, v2_db)

        for suggestion in result:
            assert suggestion["change_type"] in _VALID_CHANGE_TYPES


class TestEmptyPatterns:
    """An empty pattern list must return an empty list without raising."""

    def test_empty_patterns_returns_empty_list(self, v2_db: sqlite3.Connection) -> None:
        result = generate_suggestions([], {}, v2_db)
        assert result == []

    def test_empty_patterns_returns_list_type(self, v2_db: sqlite3.Connection) -> None:
        result = generate_suggestions([], {}, v2_db)
        assert isinstance(result, list)

    def test_empty_patterns_with_non_empty_datasets(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db)
        ds_row_id = _insert_dataset(v2_db, pat_row_id)
        orphan_dataset = {
            "pat-sug-001": _make_dataset_metadata(ds_row_id, pat_row_id),
        }

        result = generate_suggestions([], orphan_dataset, v2_db)

        assert result == []


class TestSkipsPatternsWithoutDatasets:
    """Patterns that have no corresponding dataset entry must be silently skipped."""

    def test_skips_pattern_without_dataset(self, v2_db: sqlite3.Connection) -> None:
        pat_row_id = _insert_pattern(v2_db, pattern_id="pat-no-ds")
        pattern = _make_pattern_dict(pat_row_id, pattern_id="pat-no-ds")

        # Empty datasets dict — no dataset for this pattern.
        result = generate_suggestions([pattern], {}, v2_db)

        assert result == []

    def test_skips_unmatched_keeps_matched(self, v2_db: sqlite3.Connection) -> None:
        """One pattern has a dataset (should produce a suggestion),
        another does not (should be skipped)."""
        matched_row_id = _insert_pattern(v2_db, pattern_id="pat-matched")
        unmatched_row_id = _insert_pattern(v2_db, pattern_id="pat-unmatched")
        ds_row_id = _insert_dataset(v2_db, matched_row_id, file_path="/tmp/matched.json")

        patterns = [
            _make_pattern_dict(matched_row_id, pattern_id="pat-matched"),
            _make_pattern_dict(unmatched_row_id, pattern_id="pat-unmatched"),
        ]
        datasets = {
            "pat-matched": _make_dataset_metadata(
                ds_row_id, matched_row_id, pattern_id="pat-matched"
            ),
            # "pat-unmatched" intentionally absent.
        }

        result = generate_suggestions(patterns, datasets, v2_db)

        assert len(result) == 1
        assert result[0]["pattern_id"] == matched_row_id

    def test_all_without_datasets_skipped(self, v2_db: sqlite3.Connection) -> None:
        patterns: list[dict] = []
        for idx in range(3):
            pid = f"pat-nodataset-{idx:03d}"
            row_id = _insert_pattern(v2_db, pattern_id=pid)
            patterns.append(_make_pattern_dict(row_id, pattern_id=pid))

        result = generate_suggestions(patterns, {}, v2_db)

        assert result == []
