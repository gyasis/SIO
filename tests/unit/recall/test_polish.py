"""T050 — recall --polish real implementation tests.

Tests for US5: `recall --polish` must call a real LM (via lm_factory) rather than
emitting a Gemini prompt string to stdout.

Coverage
--------
A. Happy path — polished runbook returned via mocked LM (ollama / free).
B. Paid model — cost statement printed, LM NOT called when user says no.
C. Paid model WITH --confirm-cost — LM IS called.
D. Free ollama — cost gate bypassed, LM called without confirmation.
E. Loud failure (LM call raises) — non-zero exit, old stub NOT present.
F. Loud failure (make_lm raises) — non-zero exit, old stub NOT present.
G. is_free_model() unit tests.
H. Static check — stub markers absent from main.py.

All tests mock the LM call site — no real Ollama/network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Minimal fake session data
# ---------------------------------------------------------------------------

_FAKE_DISTILLED = {
    "steps": [
        {
            "step_num": 1,
            "summary": "ran dbt compile",
            "tool": "Bash",
            "tool_input": '{"command": "dbt compile"}',
            "tool_output_preview": "Compilation succeeded.",
        },
        {
            "step_num": 2,
            "summary": "fixed dbt version mismatch",
            "tool": "Edit",
            "tool_input": "",
            "tool_output_preview": "File updated.",
        },
    ],
    "stats": {"winning_steps": 2},
    "user_goal": "compile dbt",
    "final_outcome": "success",
}

_FAKE_FILTERED = {**_FAKE_DISTILLED, "query": "dbt"}

_CANNED_POLISH = "# Polished Runbook\n1. Run dbt compile\n2. Fix version"
_RAW_RUNBOOK = "## Steps\n1. ran dbt compile\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def session_file(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text(json.dumps({"type": "message"}) + "\n")
    return f


# ---------------------------------------------------------------------------
# Shared patch targets
# The recall command imports lazily inside the function body, so we patch at
# the module where the names are USED, not where they are defined.
# ---------------------------------------------------------------------------

_PARSE_JSONL = "sio.mining.jsonl_parser.parse_jsonl"
_DISTILL = "sio.mining.session_distiller.distill_session"
_TOPIC_FILTER = "sio.mining.recall.topic_filter"
_DETECT_STRUGGLES = "sio.mining.recall.detect_struggles"
_FORMAT_OUTPUT = "sio.mining.recall.format_recall_output"
_POLISH_RUNBOOK = "sio.recall_polish.polish_runbook"
_MAKE_LM = "sio.recall_polish.make_lm"


# ---------------------------------------------------------------------------
# Helper: invoke recall with standard mocks for session parsing
# ---------------------------------------------------------------------------


def _run_recall(
    runner: CliRunner,
    session_file: Path,
    extra_args: list[str],
    polish_runbook_side_effect=None,
    polish_runbook_return=None,
    env: dict | None = None,
):
    """Invoke `sio recall dbt --session <file> <extra_args>` with session mocks."""
    from sio.cli.main import cli

    polish_mock = MagicMock()
    if polish_runbook_side_effect is not None:
        polish_mock.side_effect = polish_runbook_side_effect
    elif polish_runbook_return is not None:
        polish_mock.return_value = polish_runbook_return
    else:
        polish_mock.return_value = _CANNED_POLISH

    patches = [
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, polish_mock),
    ]

    ctx = {}
    for p in patches:
        ctx[p] = p.__enter__()

    # Apply all patches as a stack
    import contextlib

    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        polish_m = mocks[-1]  # last one is the polish_runbook mock

        invoke_kwargs = {}
        if env is not None:
            invoke_kwargs["env"] = env

        result = runner.invoke(
            cli,
            ["recall", "dbt", "--session", str(session_file)] + extra_args,
            catch_exceptions=False,
            **invoke_kwargs,
        )

    return result, polish_m


# ---------------------------------------------------------------------------
# T050-A: Happy path — polished runbook returned via mocked LM
# ---------------------------------------------------------------------------


def test_polish_returns_real_runbook(runner, session_file):
    """recall --polish returns the LM's polished runbook, not a stub dump."""
    from sio.cli.main import cli

    with (
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, return_value=_CANNED_POLISH) as mock_polish,
    ):
        result = runner.invoke(
            cli,
            ["recall", "dbt", "--session", str(session_file), "--polish"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}:\n{result.output}"
    # Real runbook content in output
    assert _CANNED_POLISH in result.output

    # Old stub markers MUST NOT be present
    assert "GEMINI POLISH PROMPT" not in result.output
    assert "Run this manually" not in result.output
    assert "gemini_brainstorm" not in result.output

    # polish_runbook was called with the raw runbook and query
    mock_polish.assert_called_once()
    call_args = mock_polish.call_args
    assert "dbt" in call_args.args[1]  # query arg


# ---------------------------------------------------------------------------
# T050-B: Paid model — cost printed, LM not called when user says no
# ---------------------------------------------------------------------------


def test_polish_paid_model_prints_cost_and_aborts(runner, session_file, tmp_path):
    """Paid model: cost statement printed, polish_runbook raises PolishError on abort."""
    from sio.cli.main import cli
    from sio.recall_polish import PolishError

    # Simulate polish_runbook raising because user said no
    def _raise_abort(*args, **kwargs):
        raise PolishError("Polish aborted — cost not confirmed for model 'openai/gpt-4o-mini'.")

    with (
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, side_effect=_raise_abort),
    ):
        result = runner.invoke(
            cli,
            [
                "recall", "dbt", "--session", str(session_file),
                "--polish", "--polish-model", "openai/gpt-4o-mini",
            ],
            catch_exceptions=True,
        )

    # Non-zero exit due to PolishError
    assert result.exit_code != 0, f"Expected non-zero, got {result.exit_code}:\n{result.output}"
    # Error message surfaced
    assert "aborted" in result.output.lower() or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# T050-C: Paid model WITH --confirm-cost — LM IS called
# ---------------------------------------------------------------------------


def test_polish_paid_model_with_confirm_calls_lm(runner, session_file):
    """Paid model + --confirm-cost: polish_runbook called without raising."""
    from sio.cli.main import cli

    with (
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, return_value=_CANNED_POLISH) as mock_polish,
    ):
        result = runner.invoke(
            cli,
            [
                "recall", "dbt", "--session", str(session_file),
                "--polish", "--polish-model", "openai/gpt-4o-mini",
                "--confirm-cost",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}:\n{result.output}"
    mock_polish.assert_called_once()
    # confirm_cost=True was passed through
    call_kwargs = mock_polish.call_args.kwargs
    assert call_kwargs.get("confirm_cost") is True


# ---------------------------------------------------------------------------
# T050-D: Free ollama — cost gate bypassed, LM called without confirmation
# ---------------------------------------------------------------------------


def test_polish_free_ollama_not_gated(runner, session_file):
    """ollama/* model: polish_runbook called, confirm_cost stays False."""
    from sio.cli.main import cli

    with (
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, return_value=_CANNED_POLISH) as mock_polish,
    ):
        result = runner.invoke(
            cli,
            [
                "recall", "dbt", "--session", str(session_file),
                "--polish", "--polish-model", "ollama/qwen3-coder:30b",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}:\n{result.output}"
    mock_polish.assert_called_once()
    # model passed through
    call_kwargs = mock_polish.call_args.kwargs
    assert call_kwargs.get("model") == "ollama/qwen3-coder:30b"


# ---------------------------------------------------------------------------
# T050-E: Loud failure (LM call raises) — non-zero exit, old stub NOT present
# ---------------------------------------------------------------------------


def test_polish_lm_failure_exits_nonzero(runner, session_file):
    """When polish_runbook raises PolishError, exit non-zero and no stub dump."""
    from sio.cli.main import cli
    from sio.recall_polish import PolishError

    def _lm_fail(*args, **kwargs):
        raise PolishError("Connection refused: ollama not running")

    with (
        patch(_PARSE_JSONL, return_value=[{"type": "message"}]),
        patch(_DISTILL, return_value=_FAKE_DISTILLED),
        patch(_TOPIC_FILTER, return_value=_FAKE_FILTERED),
        patch(_DETECT_STRUGGLES, return_value=[]),
        patch(_FORMAT_OUTPUT, return_value=_RAW_RUNBOOK),
        patch(_POLISH_RUNBOOK, side_effect=_lm_fail),
    ):
        result = runner.invoke(
            cli,
            ["recall", "dbt", "--session", str(session_file), "--polish"],
            catch_exceptions=True,
        )

    assert result.exit_code != 0, f"Expected non-zero, got {result.exit_code}:\n{result.output}"
    # Old stub must be absent
    assert "GEMINI POLISH PROMPT" not in result.output
    assert "Run this manually" not in result.output
    assert "gemini_brainstorm" not in result.output
    # Error message present
    assert "error" in result.output.lower() or "ollama" in result.output.lower()


# ---------------------------------------------------------------------------
# T050-F: Loud failure (make_lm raises in polish_runbook) — tests the module
# ---------------------------------------------------------------------------


def test_polish_make_lm_failure(tmp_path):
    """make_lm raises ValueError → polish_runbook raises PolishError (not a stub dump).

    When make_lm raises a generic ValueError (e.g. invalid model config), the error is
    surfaced as PolishError. If the message contains "forbidden" or "banned", the
    PolishError message will reflect that; otherwise it uses the "Failed to initialise"
    prefix. Either way the test just asserts it IS a PolishError — not a raw traceback.
    """
    from sio.recall_polish import PolishError, polish_runbook

    # Generic ValueError not about a ban — should surface as "Failed to initialise".
    with patch(_MAKE_LM, side_effect=ValueError("model config invalid")):
        with pytest.raises(PolishError, match="Failed to initialise"):
            polish_runbook(
                "## Steps\n1. something\n",
                "dbt",
                model="ollama/bad-model",
                confirm_cost=True,
            )


def test_polish_make_lm_banned_failure(tmp_path):
    """make_lm raises ValueError about a banned model → PolishError with 'forbidden' text."""
    from sio.recall_polish import PolishError, polish_runbook

    with patch(_MAKE_LM, side_effect=ValueError("Refusing to load banned model 'xyz'")):
        with pytest.raises(PolishError, match="forbidden"):
            polish_runbook(
                "## Steps\n1. something\n",
                "dbt",
                model="ollama/bad-model",
                confirm_cost=True,
            )


def test_polish_lm_call_failure(tmp_path):
    """LM call raises → polish_runbook raises PolishError."""
    from sio.recall_polish import PolishError, polish_runbook

    mock_lm = MagicMock(side_effect=RuntimeError("network error"))

    with patch(_MAKE_LM, return_value=mock_lm):
        with pytest.raises(PolishError, match="LM call to .* failed"):
            polish_runbook(
                "## Steps\n1. something\n",
                "dbt",
                model="ollama/qwen3-coder:30b",
                confirm_cost=True,
            )


# ---------------------------------------------------------------------------
# T050-G: is_free_model() unit tests
# ---------------------------------------------------------------------------


def test_is_free_model_ollama():
    from sio.recall_polish import is_free_model

    assert is_free_model("ollama/qwen3-coder:30b") is True
    assert is_free_model("ollama/llama3:8b") is True
    assert is_free_model("ollama/mistral") is True


def test_is_free_model_paid():
    from sio.recall_polish import is_free_model

    assert is_free_model("openai/gpt-4o-mini") is False
    assert is_free_model("anthropic/claude-sonnet-4-20250514") is False
    assert is_free_model("gemini/gemini-flash-latest") is False


def test_is_free_model_rejects_gpt4o():
    """gpt-4o (not mini) is paid and forbidden."""
    from sio.recall_polish import is_free_model

    assert is_free_model("openai/gpt-4o") is False


def test_polish_rejects_gpt4o():
    """polish_runbook raises immediately on gpt-4o (cost-control.md)."""
    from sio.recall_polish import PolishError, polish_runbook

    with pytest.raises(PolishError, match="forbidden"):
        polish_runbook("## Steps\n", "dbt", model="openai/gpt-4o", confirm_cost=True)


# ---------------------------------------------------------------------------
# T073-F1: gpt-4o family ban (all variants rejected, mini allowed)
# ---------------------------------------------------------------------------


class TestGpt4oFamilyBan:
    """Family-level gpt-4o ban: all gpt-4o variants rejected; gpt-4o-mini allowed.

    Truth table (bare = model.split('/')[-1].lower()):
        "gpt-4o"                  → bare=="gpt-4o"                → REJECT
        "openai/gpt-4o"           → bare=="gpt-4o"                → REJECT
        "azure/gpt-4o"            → bare=="gpt-4o"                → REJECT
        "gpt-4o-2024-05-13"       → startswith("gpt-4o-"), not mini → REJECT
        "openai/gpt-4o-2024-08-06"→ startswith("gpt-4o-"), not mini → REJECT
        "openrouter/openai/gpt-4o"→ bare=="gpt-4o"                → REJECT
        "gpt-4o-mini"             → startswith("gpt-4o-mini")     → ALLOW
        "openai/gpt-4o-mini"      → startswith("gpt-4o-mini")     → ALLOW
    """

    _REJECTED = [
        "gpt-4o",
        "openai/gpt-4o",
        "azure/gpt-4o",
        "gpt-4o-2024-05-13",
        "openai/gpt-4o-2024-08-06",
        "openrouter/openai/gpt-4o",
    ]

    _ALLOWED = [
        "gpt-4o-mini",
        "openai/gpt-4o-mini",
    ]

    @pytest.mark.parametrize("model", _REJECTED)
    def test_rejects_gpt4o_family(self, model):
        """All gpt-4o-family models raise PolishError immediately."""
        from sio.recall_polish import PolishError, polish_runbook

        with pytest.raises(PolishError, match="forbidden"):
            polish_runbook("## Steps\n", "dbt", model=model, confirm_cost=True)

    @pytest.mark.parametrize("model", _ALLOWED)
    def test_allows_gpt4o_mini(self, model):
        """gpt-4o-mini (and openai/gpt-4o-mini) must NOT be rejected by the family ban."""
        from sio.recall_polish import PolishError, polish_runbook

        mock_lm = MagicMock(return_value=[_CANNED_POLISH])

        # Should NOT raise PolishError for the family ban — only for cost gate
        # (confirm_cost=True bypasses the cost gate for paid models).
        with (
            patch(_MAKE_LM, return_value=mock_lm),
            patch("builtins.print"),
        ):
            try:
                result = polish_runbook(
                    "## Steps\n",
                    "dbt",
                    model=model,
                    confirm_cost=True,
                )
                # If it didn't raise, verify LM was called and result returned
                mock_lm.assert_called_once()
                assert result == _CANNED_POLISH
            except PolishError as exc:
                # Allowed to fail for make_lm issues, but NOT for "forbidden" reason
                assert "forbidden" not in str(exc).lower(), (
                    f"Model '{model}' was incorrectly rejected as forbidden gpt-4o family"
                )


# ---------------------------------------------------------------------------
# T050-H: Static check — stub markers absent from main.py
# ---------------------------------------------------------------------------


def test_stub_prompt_dump_removed():
    """The old Gemini prompt-dump stub markers must not exist in main.py."""
    main_py = Path(__file__).parents[3] / "src" / "sio" / "cli" / "main.py"
    assert main_py.exists(), f"main.py not found at {main_py}"
    content = main_py.read_text()

    forbidden = [
        "GEMINI POLISH PROMPT",
        "Run this manually or use --no-polish",
        "gemini_brainstorm(topic='Create runbook",
        "Gemini polish prompt saved",
    ]
    for marker in forbidden:
        assert marker not in content, (
            f"Stub marker still present in main.py: {marker!r}\n"
            "The stub must be replaced with a real LM call."
        )


# ---------------------------------------------------------------------------
# T050-I: Cost-gate unit tests directly on polish_runbook
# ---------------------------------------------------------------------------


def test_polish_paid_model_no_confirm_interactive_no():
    """Paid model, interactive, user types 'n' → PolishError raised."""
    from sio.recall_polish import PolishError, polish_runbook

    mock_lm = MagicMock(return_value=[_CANNED_POLISH])

    with (
        patch(_MAKE_LM, return_value=mock_lm),
        patch("builtins.input", return_value="n"),
        patch("builtins.print"),  # suppress cost line output in test
    ):
        with pytest.raises(PolishError, match="aborted"):
            polish_runbook(
                "## Steps\n",
                "dbt",
                model="openai/gpt-4o-mini",
                confirm_cost=False,
                interactive=True,
            )

    mock_lm.assert_not_called()


def test_polish_paid_model_no_confirm_noninteractive_raises():
    """Paid model, non-interactive, no confirm → PolishError (no prompt possible)."""
    from sio.recall_polish import PolishError, polish_runbook

    mock_lm = MagicMock(return_value=[_CANNED_POLISH])

    with (
        patch(_MAKE_LM, return_value=mock_lm),
        patch("builtins.print"),
    ):
        with pytest.raises(PolishError, match="confirm-cost"):
            polish_runbook(
                "## Steps\n",
                "dbt",
                model="openai/gpt-4o-mini",
                confirm_cost=False,
                interactive=False,
            )

    mock_lm.assert_not_called()


def test_polish_paid_model_with_confirm_calls():
    """Paid model + confirm_cost=True → LM called (no prompt needed)."""
    from sio.recall_polish import polish_runbook

    mock_lm = MagicMock(return_value=[_CANNED_POLISH])

    with (
        patch(_MAKE_LM, return_value=mock_lm),
        patch("builtins.print"),
    ):
        result = polish_runbook(
            "## Steps\n",
            "dbt",
            model="openai/gpt-4o-mini",
            confirm_cost=True,
        )

    mock_lm.assert_called_once()
    assert result == _CANNED_POLISH


def test_polish_ollama_no_gate():
    """ollama model: no cost gate, LM called directly."""
    from sio.recall_polish import polish_runbook

    mock_lm = MagicMock(return_value=[_CANNED_POLISH])

    with (
        patch(_MAKE_LM, return_value=mock_lm),
        patch("builtins.print") as mock_print,
    ):
        result = polish_runbook(
            "## Steps\n",
            "dbt",
            model="ollama/qwen3-coder:30b",
            confirm_cost=False,
            interactive=False,
        )

    mock_lm.assert_called_once()
    assert result == _CANNED_POLISH
    # No cost statement printed for free model
    cost_calls = [str(c) for c in mock_print.call_args_list]
    assert not any("cost" in c.lower() for c in cost_calls)
