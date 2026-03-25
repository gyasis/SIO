"""SIO Recall — topic-filtered distill + Gemini polish.

Takes raw distill output (632 steps) and:
1. Topic-filters to relevant steps (~30)
2. Detects struggle→fix transitions
3. Optionally polishes via Gemini into a clean runbook

Public API
----------
    topic_filter(distilled, query) -> dict
        Filters distilled steps to only those matching the query topic.

    detect_struggles(steps) -> list[dict]
        Finds failure→fix transitions in filtered steps.

    format_recall_output(filtered, query) -> str
        Formats filtered + struggle-detected steps as a concise runbook.
"""

from __future__ import annotations

import json
import re


def _build_topic_regex(query: str) -> re.Pattern:
    """Build a regex pattern from the user's query.

    Expands keywords to include related terms:
    - "dbt" also matches profiles.yml, models/, target/
    - "hhdev" also matches hh-dev, start.sh, stop.sh
    """
    # Split query into keywords
    keywords = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]

    # Expansion map for common terms
    expansions = {
        "dbt": ["dbt", "profiles\\.yml", "dbt_project", "models/", "target/", "seeds/"],
        "hhdev": ["hhdev", "hh-dev", "start\\.sh", "stop\\.sh", "local-dev"],
        "cube": ["cube", "cubejs", "cube\\.js", "schema/", "\\.yml"],
        "snowflake": ["snowflake", "snowsql", "TWICE", "H_EXP"],
        "superset": ["superset", "viz\\.hertek", "dataset", "chart"],
        "tableau": ["tableau", "twb", "tds", "workbook"],
        "prefect": ["prefect", "flow", "deployment", "work.pool"],
        "navina": ["navina", "hcc", "suggestion", "assessment"],
        "raf": ["raf", "hcc", "demographic", "snapshot", "monthly"],
    }

    all_patterns = []
    for kw in keywords:
        all_patterns.append(re.escape(kw))
        if kw in expansions:
            all_patterns.extend(expansions[kw])

    if not all_patterns:
        return re.compile(r".*", re.IGNORECASE)

    pattern = "|".join(all_patterns)
    return re.compile(pattern, re.IGNORECASE)


def topic_filter(distilled: dict, query: str) -> dict:
    """Filter distilled steps to only those matching the query topic.

    Returns a new distilled dict with only relevant steps.
    """
    pattern = _build_topic_regex(query)

    filtered_steps = []
    for step in distilled.get("steps", []):
        # Check summary, tool_input, and tool for matches
        searchable = " ".join([
            step.get("summary", ""),
            step.get("tool_input", ""),
            step.get("tool", ""),
            step.get("tool_output_preview", ""),
        ])
        if pattern.search(searchable):
            filtered_steps.append(step)

    # Also include steps immediately before/after matches (context window)
    all_steps = distilled.get("steps", [])
    expanded_indices = set()
    for step in filtered_steps:
        idx = step["step_num"] - 1
        for j in range(max(0, idx - 1), min(len(all_steps), idx + 2)):
            expanded_indices.add(j)

    expanded_steps = [all_steps[i] for i in sorted(expanded_indices) if i < len(all_steps)]

    # Renumber
    for i, step in enumerate(expanded_steps):
        step["step_num"] = i + 1

    return {
        "steps": expanded_steps,
        "stats": {
            **distilled.get("stats", {}),
            "topic_filtered_steps": len(expanded_steps),
            "original_winning_steps": distilled.get("stats", {}).get("winning_steps", 0),
        },
        "user_goal": distilled.get("user_goal", ""),
        "final_outcome": distilled.get("final_outcome", ""),
        "query": query,
    }


def detect_struggles(steps: list[dict]) -> list[dict]:
    """Detect struggle→fix transitions in filtered steps.

    A struggle is: step with "Error" or "fail" in output, followed within
    3 steps by a step touching the same file/tool that succeeds.

    Returns list of {failure_step, fix_step, description}.
    """
    struggles = []
    error_patterns = re.compile(
        r"(Error|Exception|FAILED|exit code [1-9]|Permission denied|not found|No such file)",
        re.IGNORECASE,
    )

    for i, step in enumerate(steps):
        output = step.get("tool_output_preview", "") + " " + step.get("summary", "")
        if error_patterns.search(output):
            # Look ahead for a fix (same tool or same file, no error)
            for j in range(i + 1, min(i + 4, len(steps))):
                fix_candidate = steps[j]
                # Same tool or similar summary = potential fix
                if (fix_candidate.get("tool") == step.get("tool") or
                        _files_overlap(step.get("summary", ""), fix_candidate.get("summary", ""))):
                    fix_output = fix_candidate.get("tool_output_preview", "")
                    if not error_patterns.search(fix_output):
                        struggles.append({
                            "failure_step": step["step_num"],
                            "fix_step": fix_candidate["step_num"],
                            "failure_summary": step.get("summary", ""),
                            "fix_summary": fix_candidate.get("summary", ""),
                        })
                        break

    return struggles


def _files_overlap(summary1: str, summary2: str) -> bool:
    """Check if two step summaries reference the same file."""
    # Extract file paths from summaries
    paths1 = set(re.findall(r'[/\w.-]+\.\w{1,6}', summary1))
    paths2 = set(re.findall(r'[/\w.-]+\.\w{1,6}', summary2))
    return bool(paths1 & paths2)


def format_recall_output(filtered: dict, struggles: list[dict]) -> str:
    """Format the recall output as a concise markdown runbook."""
    query = filtered.get("query", "unknown")
    steps = filtered.get("steps", [])
    stats = filtered.get("stats", {})

    lines = [
        f"# Recall: {query}",
        f"*Source: {stats.get('original_winning_steps', '?')} steps distilled → "
        f"{stats.get('topic_filtered_steps', len(steps))} topic-relevant*",
        "",
    ]

    # Struggle summary at top
    if struggles:
        lines.append("## Key Fixes Found")
        for s in struggles:
            lines.append(f"- **Problem** (step {s['failure_step']}): {s['failure_summary']}")
            lines.append(f"  **Fix** (step {s['fix_step']}): {s['fix_summary']}")
        lines.append("")

    # Steps
    lines.append("## Steps")
    lines.append("")

    for step in steps:
        lines.append(f"{step['step_num']}. **{step.get('summary', '?')}**")

        # Show bash commands
        if step.get("tool") == "Bash" and step.get("tool_input"):
            try:
                params = json.loads(step["tool_input"])
                cmd = params.get("command", "")
                if cmd and len(cmd) < 300:
                    lines.append(f"   ```bash")
                    lines.append(f"   {cmd}")
                    lines.append(f"   ```")
            except (json.JSONDecodeError, TypeError):
                pass

        lines.append("")

    return "\n".join(lines)


def build_gemini_polish_prompt(filtered: dict, struggles: list[dict], query: str) -> str:
    """Build the prompt for Gemini to polish the raw recall output into a clean runbook.

    Returns a string suitable for gemini_brainstorm context parameter.
    """
    steps_text = []
    for step in filtered.get("steps", [])[:50]:  # Cap at 50 for token budget
        status = "success"
        if any(s["failure_step"] == step["step_num"] for s in struggles):
            status = "FAILED"
        steps_text.append(f"Step {step['step_num']}: [{status}] {step.get('summary', '?')}")

    struggle_text = ""
    if struggles:
        struggle_text = "\n\nSTRUGGLE→FIX TRANSITIONS:\n"
        for s in struggles:
            struggle_text += f"- Problem: {s['failure_summary']} → Fix: {s['fix_summary']}\n"

    return (
        f"Create a clean 10-15 step runbook for: {query}\n\n"
        f"Below are the raw steps extracted from a session (failures removed, topic-filtered).\n"
        f"Rules:\n"
        f"1. Identify the Fix: Format as 'Problem: X → Fix: Y'\n"
        f"2. Prune noise: Skip ls, cat, git status unless they reveal something\n"
        f"3. Consolidate: If the same command ran 5 times, show only the final successful one\n"
        f"4. Environment first: Put exports, cd, and setup in the first 3 steps\n"
        f"5. Include the EXACT bash commands that worked\n\n"
        f"RAW STEPS:\n" + "\n".join(steps_text) + struggle_text
    )
