"""Session distiller — extracts the "winning path" from a long exploratory session.

Takes a messy session with dead ends, retries, and errors, and produces
a clean, ordered list of steps that actually worked.

Two tiers:
- Cheap: Pure filtering (no LLM). Removes failures, keeps successes.
- Expensive: LLM polishes the winning path into a human-readable runbook.

Public API
----------
    distill_session(parsed_messages) -> dict
        Returns {steps: list[dict], summary: dict} with the winning path.

    format_playbook(steps, title="") -> str
        Formats distilled steps as a readable markdown playbook.
"""

from __future__ import annotations

import json
import re
from datetime import datetime


def _is_failed(msg: dict) -> bool:
    """Check if a message represents a failed tool call."""
    if msg.get("error"):
        return True
    # Check for common error patterns in tool output
    output = msg.get("tool_output") or msg.get("content") or ""
    if isinstance(output, str) and len(output) < 500:
        if re.search(r"\b(Error|Exception|FAILED|Permission denied|not found|No such file)\b", output, re.IGNORECASE):
            return True
    return False


def _is_retry(msg: dict, prev_msg: dict | None) -> bool:
    """Check if this tool call is a retry of the previous one."""
    if not prev_msg:
        return False
    if msg.get("tool_name") != prev_msg.get("tool_name"):
        return False
    # Same tool, check if inputs are very similar
    inp1 = msg.get("tool_input") or ""
    inp2 = prev_msg.get("tool_input") or ""
    if inp1 == inp2:
        return True
    # Check if >80% character overlap (fuzzy retry)
    if inp1 and inp2 and len(inp1) > 20:
        common = sum(1 for a, b in zip(inp1, inp2) if a == b)
        similarity = common / max(len(inp1), len(inp2))
        if similarity > 0.8:
            return True
    return False


def _is_undo_message(msg: dict) -> bool:
    """Check if a user message is requesting an undo/revert."""
    content = (msg.get("content") or "").lower()
    return bool(re.search(
        r"\b(undo|revert|rollback|go back|restore|put it back|that.s wrong)\b",
        content,
    ))


def _extract_step_summary(msg: dict) -> str:
    """Create a human-readable summary of what a tool call did."""
    tool = msg.get("tool_name") or "unknown"
    tool_input = msg.get("tool_input") or ""

    # Try to extract key info from tool input
    if isinstance(tool_input, str):
        try:
            params = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            params = {}
    else:
        params = tool_input if isinstance(tool_input, dict) else {}

    if tool in ("Read", "read_file"):
        path = params.get("file_path", params.get("path", ""))
        return f"Read {path}"
    elif tool in ("Write", "write_file"):
        path = params.get("file_path", params.get("path", ""))
        return f"Write {path}"
    elif tool in ("Edit", "edit_file"):
        path = params.get("file_path", params.get("path", ""))
        return f"Edit {path}"
    elif tool == "Bash":
        cmd = params.get("command", "")
        if len(cmd) > 120:
            cmd = cmd[:117] + "..."
        return f"Run: {cmd}"
    elif tool == "Grep":
        pattern = params.get("pattern", "")
        path = params.get("path", "")
        return f"Search for '{pattern}' in {path}"
    elif tool == "Glob":
        pattern = params.get("pattern", "")
        return f"Find files: {pattern}"
    elif tool.startswith("mcp__gemini"):
        topic = params.get("topic", params.get("query", ""))
        if len(topic) > 80:
            topic = topic[:77] + "..."
        return f"{tool.split('__')[-1]}: {topic}"
    elif tool.startswith("mcp__graphiti"):
        query = params.get("query", params.get("name", ""))
        if len(query) > 80:
            query = query[:77] + "..."
        return f"Graphiti {tool.split('__')[-1]}: {query}"
    elif tool.startswith("mcp__"):
        # Generic MCP tool
        short_name = tool.replace("mcp__", "").replace("__", ".")
        return f"MCP: {short_name}"
    else:
        return f"{tool}"


def distill_session(parsed_messages: list[dict]) -> dict:
    """Extract the winning path from a parsed session.

    Returns:
        {
            "steps": [
                {
                    "step_num": int,
                    "tool": str,
                    "summary": str,
                    "tool_input": str,
                    "tool_output_preview": str,
                    "timestamp": str,
                    "phase": str,  # "explore", "implement", "verify"
                }
            ],
            "stats": {
                "total_messages": int,
                "total_tool_calls": int,
                "failed_calls": int,
                "retries": int,
                "winning_steps": int,
                "compression_ratio": float,  # winning/total
            },
            "user_goal": str,  # First user message (the task)
            "final_outcome": str,  # Last user message
        }
    """
    # Phase 1: Extract user goal (first substantial user message)
    user_goal = ""
    for msg in parsed_messages:
        if msg.get("role") == "user" and not msg.get("tool_name"):
            content = (msg.get("content") or "").strip()
            if len(content) > 10:
                user_goal = content[:500]
                break

    # Phase 2: Extract final outcome (last user message)
    final_outcome = ""
    for msg in reversed(parsed_messages):
        if msg.get("role") == "user" and not msg.get("tool_name"):
            content = (msg.get("content") or "").strip()
            if len(content) > 3:
                final_outcome = content[:300]
                break

    # Phase 3: Filter tool calls — remove failures, retries, and undo targets
    tool_calls = []
    failed_count = 0
    retry_count = 0

    # First pass: identify undo windows (messages after undo request should be excluded)
    undo_ranges = set()
    for i, msg in enumerate(parsed_messages):
        if msg.get("role") == "user" and _is_undo_message(msg):
            # Mark the 5 messages before the undo as potentially reverted
            for j in range(max(0, i - 5), i):
                undo_ranges.add(j)

    prev_tool = None
    for i, msg in enumerate(parsed_messages):
        if not msg.get("tool_name"):
            continue
        if msg.get("role") != "assistant":
            continue
        if i in undo_ranges:
            continue

        if _is_failed(msg):
            failed_count += 1
            prev_tool = msg
            continue

        if _is_retry(msg, prev_tool):
            retry_count += 1
            prev_tool = msg
            continue

        tool_calls.append((i, msg))
        prev_tool = msg

    # Phase 4: Deduplicate consecutive identical reads of same file
    deduped = []
    prev_summary = None
    for idx, msg in tool_calls:
        summary = _extract_step_summary(msg)
        if summary == prev_summary and msg.get("tool_name") in ("Read", "read_file"):
            continue
        deduped.append((idx, msg, summary))
        prev_summary = summary

    # Phase 5: Build step list with phase detection
    total_steps = len(deduped)
    steps = []
    for step_i, (idx, msg, summary) in enumerate(deduped):
        # Simple phase heuristic based on position
        progress = step_i / max(total_steps, 1)
        if progress < 0.3:
            phase = "explore"
        elif progress < 0.8:
            phase = "implement"
        else:
            phase = "verify"

        # Preview of tool output (truncated)
        output = msg.get("tool_output") or msg.get("content") or ""
        if isinstance(output, str) and len(output) > 200:
            output_preview = output[:197] + "..."
        else:
            output_preview = output

        steps.append({
            "step_num": step_i + 1,
            "tool": msg.get("tool_name", "unknown"),
            "summary": summary,
            "tool_input": (msg.get("tool_input") or "")[:500],
            "tool_output_preview": output_preview[:200] if isinstance(output_preview, str) else "",
            "timestamp": msg.get("timestamp", ""),
            "phase": phase,
        })

    total_tool_calls = sum(1 for m in parsed_messages if m.get("tool_name") and m.get("role") == "assistant")

    return {
        "steps": steps,
        "stats": {
            "total_messages": len(parsed_messages),
            "total_tool_calls": total_tool_calls,
            "failed_calls": failed_count,
            "retries": retry_count,
            "winning_steps": len(steps),
            "compression_ratio": round(len(steps) / max(total_tool_calls, 1), 2),
        },
        "user_goal": user_goal,
        "final_outcome": final_outcome,
    }


def format_playbook(distilled: dict, title: str = "Session Playbook") -> str:
    """Format distilled session as a markdown playbook.

    This is the CHEAP tier output — no LLM, just structured formatting.
    """
    lines = [f"# {title}", ""]

    # Goal
    if distilled["user_goal"]:
        goal = distilled["user_goal"]
        if len(goal) > 200:
            goal = goal[:197] + "..."
        lines.append(f"**Goal:** {goal}")
        lines.append("")

    # Stats
    stats = distilled["stats"]
    lines.append(f"**Session Stats:** {stats['total_tool_calls']} tool calls → "
                 f"{stats['winning_steps']} winning steps "
                 f"({stats['compression_ratio']:.0%} compression). "
                 f"Filtered out {stats['failed_calls']} failures, {stats['retries']} retries.")
    lines.append("")

    # Steps by phase
    current_phase = None
    phase_labels = {"explore": "Phase 1: Exploration", "implement": "Phase 2: Implementation", "verify": "Phase 3: Verification"}

    for step in distilled["steps"]:
        if step["phase"] != current_phase:
            current_phase = step["phase"]
            lines.append(f"## {phase_labels.get(current_phase, current_phase)}")
            lines.append("")

        lines.append(f"{step['step_num']}. **{step['summary']}**")

        # Add tool input detail for actionable steps
        if step["tool"] == "Bash" and step["tool_input"]:
            try:
                params = json.loads(step["tool_input"])
                cmd = params.get("command", "")
                if cmd and len(cmd) < 200:
                    lines.append(f"   ```bash")
                    lines.append(f"   {cmd}")
                    lines.append(f"   ```")
            except (json.JSONDecodeError, TypeError):
                pass

        lines.append("")

    # Outcome
    if distilled["final_outcome"]:
        lines.append("## Outcome")
        outcome = distilled["final_outcome"]
        if len(outcome) > 300:
            outcome = outcome[:297] + "..."
        lines.append(f"> {outcome}")
        lines.append("")

    return "\n".join(lines)
