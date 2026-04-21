"""T088 [US5] — Flow extractor bug regression tests (FR-021, FR-022, FR-026).

Bug 1 (M5): compute_ngrams / indexed_ngrams used range(n_min, n_max) — upper bound exclusive.
            Fix: range(n_min, n_max + 1) so n_range=(2,5) produces 2,3,4,5-grams.

Bug 2 (L1): Extension allowlist missing .rs, .go, .java, .cpp, .ipynb.
            Fix: Add those extensions to the allowlist in _extract_extension().

Bug 3 (L3): is_success_signal() returned True for short messages with no negatives,
            even without explicit positive marker.
            Fix: Only explicit positive keywords count. No-marker -> was_successful=0.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# T088-1: compute_ngrams upper-bound inclusive (Bug 1 / FR-022 / audit M5)
# ---------------------------------------------------------------------------


def test_compute_ngrams_upper_bound_inclusive():
    """n_range=(2, 5) must produce 2, 3, 4, AND 5-grams (upper bound inclusive)."""
    from sio.mining.flow_extractor import compute_ngrams  # noqa: PLC0415

    compressed = ["Read(.py)", "Edit(.py)", "Bash", "Write(.py)", "Glob", "Read(.md)"]
    ngrams = compute_ngrams(compressed, n_range=(2, 5))

    # Verify 5-grams exist (previously missing when upper bound was exclusive)
    five_grams = [ng for ng in ngrams if len(ng) == 5]
    assert len(five_grams) > 0, (
        "compute_ngrams(n_range=(2,5)) must produce 5-grams. "
        "Check that range uses n_max + 1 for inclusive upper bound."
    )

    # Verify 2,3,4-grams also present
    for n in (2, 3, 4):
        n_grams = [ng for ng in ngrams if len(ng) == n]
        assert len(n_grams) > 0, f"Missing {n}-grams from compute_ngrams"


def test_indexed_ngrams_upper_bound_inclusive():
    """indexed_ngrams must also include the upper-bound n (same fix as compute_ngrams)."""
    from sio.mining.flow_extractor import indexed_ngrams  # noqa: PLC0415

    compressed = ["A", "B", "C", "D", "E", "F"]
    results = indexed_ngrams(compressed, n_range=(2, 5))

    five_gram_results = [(ng, pos) for ng, pos in results if len(ng) == 5]
    assert len(five_gram_results) > 0, "indexed_ngrams(n_range=(2,5)) must produce 5-grams"


# ---------------------------------------------------------------------------
# T088-2: Extension allowlist includes .rs, .go, .java, .cpp, .ipynb (Bug 2 / FR-026 / audit L1)
# ---------------------------------------------------------------------------


def test_rs_extension_recognized():
    """.rs files must be included in the extension allowlist."""
    from sio.mining.flow_extractor import _extract_extension  # noqa: PLC0415

    tool_input = '{"path": "/home/user/project/main.rs"}'
    ext = _extract_extension(tool_input)
    assert ext == ".rs", f"Expected '.rs', got {ext!r}"


def test_go_extension_recognized():
    """.go files must be included in the extension allowlist."""
    from sio.mining.flow_extractor import _extract_extension  # noqa: PLC0415

    tool_input = '{"file_path": "src/handler.go"}'
    ext = _extract_extension(tool_input)
    assert ext == ".go", f"Expected '.go', got {ext!r}"


def test_java_extension_recognized():
    """.java files must be included in the extension allowlist."""
    from sio.mining.flow_extractor import _extract_extension  # noqa: PLC0415

    tool_input = '{"path": "com/example/Service.java"}'
    ext = _extract_extension(tool_input)
    assert ext == ".java", f"Expected '.java', got {ext!r}"


def test_cpp_extension_recognized():
    """.cpp files must be included in the extension allowlist."""
    from sio.mining.flow_extractor import _extract_extension  # noqa: PLC0415

    tool_input = '{"file_path": "/project/src/main.cpp"}'
    ext = _extract_extension(tool_input)
    assert ext == ".cpp", f"Expected '.cpp', got {ext!r}"


def test_ipynb_extension_recognized():
    """.ipynb files must be included in the extension allowlist."""
    from sio.mining.flow_extractor import _extract_extension  # noqa: PLC0415

    tool_input = '{"path": "notebooks/analysis.ipynb"}'
    ext = _extract_extension(tool_input)
    assert ext == ".ipynb", f"Expected '.ipynb', got {ext!r}"


# ---------------------------------------------------------------------------
# T088-3: is_success_signal requires explicit positive marker (Bug 3 / FR-021 / audit L3)
# ---------------------------------------------------------------------------


def test_no_positive_marker_defaults_to_not_success():
    """A short neutral message with no negative keywords must NOT be a success signal.

    FR-021: Require explicit positive marker, not absence of negative.
    """
    from sio.mining.flow_extractor import is_success_signal  # noqa: PLC0415

    # Short, no negatives — but also no positive keywords
    # Previously this would return True (absence-of-negative bug)
    assert not is_success_signal("Alright"), (
        "Short neutral message 'Alright' must NOT be success signal without explicit positive keyword"
    )
    assert not is_success_signal("Understood."), (
        "Short neutral message 'Understood.' must NOT be success signal"
    )
    assert not is_success_signal("Okay"), "Short neutral message 'Okay' must NOT be success signal"


def test_explicit_positive_keyword_is_success():
    """Messages with explicit positive keywords (thanks, perfect, etc.) ARE success signals."""
    from sio.mining.flow_extractor import is_success_signal  # noqa: PLC0415

    assert is_success_signal("thanks"), "Expected 'thanks' to be a success signal"
    assert is_success_signal("perfect!"), "Expected 'perfect!' to be a success signal"
    assert is_success_signal("great work"), "Expected 'great work' to be a success signal"
    assert is_success_signal("lgtm"), "Expected 'lgtm' to be a success signal"


def test_empty_message_is_not_success():
    """Empty or whitespace-only messages must not be success signals."""
    from sio.mining.flow_extractor import is_success_signal  # noqa: PLC0415

    assert not is_success_signal(""), "Empty string must not be success signal"
    assert not is_success_signal("   "), "Whitespace-only must not be success signal"
    assert not is_success_signal(None), "None must not be success signal"
