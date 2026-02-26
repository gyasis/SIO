"""sio.ground_truth.seeder -- Seed ground truth with representative examples.

Generates hardcoded synthetic examples covering all 7 target surfaces.
No LLM is needed; these are pre-approved seed entries for bootstrapping
the training corpus.

Public API
----------
    seed_ground_truth(config, conn) -> list[int]
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from sio.core.db.queries import insert_ground_truth


def _examples(*pairs: tuple[str, str | None]) -> str:
    """Build JSON error_examples_json from (error_text, tool_name) pairs."""
    return json.dumps(
        [{"error_text": t, "tool_name": n} for t, n in pairs]
    )


# ---------------------------------------------------------------------------
# Seed data: 10 entries covering all 7 surfaces
# (some surfaces get 2 entries to reach 10)
# ---------------------------------------------------------------------------

_SEED_ENTRIES: list[dict[str, str]] = [
    # 1. claude_md_rule
    {
        "pattern_id": "seed-tool-timeout-001",
        "error_examples_json": _examples(
            ("TimeoutError: tool execution exceeded 30s limit", "Bash"),
            ("TimeoutError: tool execution exceeded 30s limit", "Bash"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "Bash tool repeatedly times out on long-running commands."
        ),
        "target_surface": "claude_md_rule",
        "rule_title": "Add timeout guidance for long-running commands",
        "prevention_instructions": (
            "When running commands that may take >30 seconds "
            "(builds, large test suites, database migrations), "
            "always use `timeout` parameter or run in background. "
            "Example: `bash command --timeout 120000`."
        ),
        "rationale": (
            "Repeated Bash timeouts waste tokens and frustrate "
            "users. Explicit timeout guidance prevents the agent "
            "from blocking on long commands."
        ),
    },
    # 2. skill_update
    {
        "pattern_id": "seed-file-not-found-002",
        "error_examples_json": _examples(
            ("FileNotFoundError: /tmp/missing.py", "Read"),
            ("FileNotFoundError: /src/old_name.py", "Read"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "Read tool fails because file paths are guessed "
            "instead of verified."
        ),
        "target_surface": "skill_update",
        "rule_title": "Verify file existence before reading",
        "prevention_instructions": (
            "Before calling Read, use Glob to verify the file "
            "exists. If the path is uncertain, run `ls` or "
            "`Glob` first to confirm. Never guess file paths."
        ),
        "rationale": (
            "File-not-found errors are the most common tool "
            "failure. A pre-check eliminates wasted Read calls "
            "and improves reliability."
        ),
    },
    # 3. hook_config
    {
        "pattern_id": "seed-pre-commit-fail-003",
        "error_examples_json": _examples(
            ("pre-commit hook failed: ruff found 3 errors", "Bash"),
            ("pre-commit hook failed: mypy type errors", "Bash"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "Git commits fail because pre-commit hooks catch "
            "lint errors."
        ),
        "target_surface": "hook_config",
        "rule_title": "Run linter before committing",
        "prevention_instructions": (
            "Configure a PreToolUse hook that runs `ruff check` "
            "on staged Python files before any `git commit` "
            "command. This catches errors early."
        ),
        "rationale": (
            "Pre-commit failures require re-staging and "
            "recommitting. Running the linter proactively saves "
            "a full commit-fix-recommit cycle."
        ),
    },
    # 4. mcp_config
    {
        "pattern_id": "seed-mcp-timeout-004",
        "error_examples_json": _examples(
            ("MCP timeout: search_nodes took >60s", "graphiti"),
            ("MCP connection refused: server down", "graphiti"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "MCP tool calls fail due to server timeouts or "
            "connectivity."
        ),
        "target_surface": "mcp_config",
        "rule_title": "Increase MCP timeout and add retry",
        "prevention_instructions": (
            "Set `timeout_seconds: 120` for Graphiti MCP server "
            "in settings. Add health check before first MCP "
            "call in a session."
        ),
        "rationale": (
            "MCP servers sometimes take longer to respond due "
            "to graph traversal. Higher timeout prevents "
            "premature failure."
        ),
    },
    # 5. settings_config
    {
        "pattern_id": "seed-model-config-005",
        "error_examples_json": _examples(
            ("API rate limit exceeded for gpt-4o", "dspy.LM"),
            ("429 Too Many Requests", "dspy.LM"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "LLM API rate limits cause suggestion generation "
            "failures."
        ),
        "target_surface": "settings_config",
        "rule_title": "Configure rate limiting and fallback model",
        "prevention_instructions": (
            "In `.claude/settings.json`, set "
            "`max_concurrent_requests: 2` and configure a "
            "fallback model (e.g., `gpt-4o-mini`) for when "
            "the primary model hits rate limits."
        ),
        "rationale": (
            "Rate limit errors cascade through the pipeline. "
            "A fallback model ensures suggestions continue "
            "generating even under load."
        ),
    },
    # 6. agent_profile
    {
        "pattern_id": "seed-wrong-language-006",
        "error_examples_json": _examples(
            ("User correction: I said Python, not TS", None),
            ("User correction: Use pytest not jest", None),
        ),
        "error_type": "user_correction",
        "pattern_summary": (
            "Agent generates code in wrong language or framework."
        ),
        "target_surface": "agent_profile",
        "rule_title": "Enforce project language preferences",
        "prevention_instructions": (
            "Update agent profile to specify: 'This project "
            "uses Python 3.11+ with pytest for testing. Never "
            "suggest JavaScript/TypeScript alternatives.'"
        ),
        "rationale": (
            "Without explicit language constraints in the agent "
            "profile, the LLM may default to more common "
            "languages seen in training data."
        ),
    },
    # 7. project_config
    {
        "pattern_id": "seed-wrong-directory-007",
        "error_examples_json": _examples(
            ("FileNotFoundError: /home/user/wrong/main.py", "Read"),
            ("No such directory: /home/user/old/", "Bash"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": (
            "Agent operates in wrong directory or project root."
        ),
        "target_surface": "project_config",
        "rule_title": "Pin project root in configuration",
        "prevention_instructions": (
            "Set `project_root` in `.claude/project-config.json` "
            "to the absolute path of the repository. All file "
            "operations should be relative to this root."
        ),
        "rationale": (
            "When the project root is ambiguous, the agent may "
            "navigate to stale or incorrect directories, causing "
            "cascading file-not-found errors."
        ),
    },
    # 8. claude_md_rule (second entry for this surface)
    {
        "pattern_id": "seed-undo-pattern-008",
        "error_examples_json": _examples(
            ("User: undo that change", "Edit"),
            ("User: revert the last edit", "Edit"),
        ),
        "error_type": "undo",
        "pattern_summary": (
            "User frequently undoes agent edits, suggesting "
            "low-quality changes."
        ),
        "target_surface": "claude_md_rule",
        "rule_title": "Confirm before multi-file edits",
        "prevention_instructions": (
            "Before applying edits that touch more than 2 files, "
            "present a summary of proposed changes and ask for "
            "confirmation. Single-file edits can proceed."
        ),
        "rationale": (
            "Undo patterns indicate the agent is making changes "
            "the user did not want. A confirmation step for "
            "larger changes reduces unwanted edits."
        ),
    },
    # 9. skill_update (second entry)
    {
        "pattern_id": "seed-repeated-search-009",
        "error_examples_json": _examples(
            ("Grep returned 0 results for 'def main'", "Grep"),
            ("Grep returned 0 results for 'function main'", "Grep"),
            ("Grep returned 0 results for 'main()'", "Grep"),
        ),
        "error_type": "repeated_attempt",
        "pattern_summary": (
            "Agent repeatedly searches with variations instead "
            "of broadening strategy."
        ),
        "target_surface": "skill_update",
        "rule_title": "Escalate search strategy after 2 failures",
        "prevention_instructions": (
            "If Grep returns empty results twice, switch "
            "strategies: try Glob for file discovery, or use "
            "broader patterns. Do not repeat similar search "
            "queries more than twice."
        ),
        "rationale": (
            "Repeated failed searches waste tokens and indicate "
            "the agent is stuck. A forced strategy change after "
            "2 attempts breaks the loop."
        ),
    },
    # 10. hook_config (second entry)
    {
        "pattern_id": "seed-large-commit-010",
        "error_examples_json": _examples(
            ("pre-commit hook: file too large (>1MB)", "Bash"),
            ("git push rejected: file exceeds 100MB", "Bash"),
        ),
        "error_type": "tool_failure",
        "pattern_summary": "Large files accidentally committed to git.",
        "target_surface": "hook_config",
        "rule_title": "Block large file commits",
        "prevention_instructions": (
            "Add a PreToolUse hook that checks staged file sizes "
            "before `git commit`. Reject commits containing "
            "files >500KB unless explicitly overridden."
        ),
        "rationale": (
            "Large binary files in git cause repository bloat "
            "and push failures. A size check hook prevents "
            "accidental commits."
        ),
    },
]


def seed_ground_truth(
    config: Any, conn: sqlite3.Connection
) -> list[int]:
    """Insert seed ground truth entries covering all 7 surfaces.

    Seed entries are hardcoded representative examples that do not
    require an LLM. They are inserted with ``source='seed'`` and
    ``label='positive'`` (pre-approved).

    Args:
        config: ``SIOConfig`` instance (unused, for interface).
        conn: SQLite connection with SIO schema.

    Returns:
        List of inserted row IDs.
    """
    from sio.core.db.queries import update_ground_truth_label

    row_ids: list[int] = []

    for entry in _SEED_ENTRIES:
        row_id = insert_ground_truth(
            conn,
            pattern_id=entry["pattern_id"],
            error_examples_json=entry["error_examples_json"],
            error_type=entry["error_type"],
            pattern_summary=entry["pattern_summary"],
            target_surface=entry["target_surface"],
            rule_title=entry["rule_title"],
            prevention_instructions=entry["prevention_instructions"],
            rationale=entry["rationale"],
            source="seed",
            confidence=1.0,
            file_path=None,
        )
        # Seeds are pre-approved
        update_ground_truth_label(
            conn, row_id, label="positive", source="seed"
        )
        row_ids.append(row_id)

    return row_ids
