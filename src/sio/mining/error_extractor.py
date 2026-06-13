"""Error extractor — classifies parsed conversation messages into five error
categories and emits ErrorRecord dicts suitable for insertion into the v2
``error_records`` table.

Exported API
------------
extract_errors(parsed_messages, source_file, source_type) -> list[dict]

Error types detected
--------------------
tool_failure       — assistant message whose ``error`` field is non-null / non-empty
user_correction    — human message containing correction phrasing (word-boundary aware)
repeated_attempt   — same tool_name called 3+ consecutive times with similar input
undo               — human message containing undo / revert signals
agent_admission    — assistant message where the AI admits a mistake, oversight, or
                     incorrect action (e.g. "I made a mistake", "I should have",
                     "I accidentally", "I missed", "my apologies")
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any  # noqa: UP035

from sio.mining.tagging import derive_all  # Stage-1 structural tags (project/command/time)


def _to_text(value: Any) -> str | None:
    """Coerce a value to a TEXT-safe string for SQLite storage."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Phrases that indicate the user is correcting the assistant.
# Each sub-pattern is anchored with \b to prevent partial-word matches.
# Ordered from longest/most-specific to shortest to avoid greedy ambiguity.
_CORRECTION_PATTERNS: list[re.Pattern[str]] = [
    # "No, actually …" — most specific first
    re.compile(r"\bno,?\s+actually\b", re.IGNORECASE),
    # Bare "No," at the start of a message or sentence (correction opener)
    # Matches "No," optionally followed by a space — anchored by \b on both sides
    # of "no" and a literal comma to avoid matching "nobody", "nothing", etc.
    re.compile(r"(?:^|(?<=\s)|\A)no,", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+wrong\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+not\s+right\b", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+i\s+wanted\b", re.IGNORECASE),
    re.compile(r"\bnot\s+that\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+file\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+path\b", re.IGNORECASE),
    re.compile(r"\bi\s+meant\b", re.IGNORECASE),
    re.compile(r"\bi\s+said\b", re.IGNORECASE),
    re.compile(r"\bnot\s+correct\b", re.IGNORECASE),
]

# Phrases that indicate the user wants to revert / undo a change.
# "git push" must NOT be caught — we match "git checkout" and "git revert"
# explicitly; the generic patterns use word boundaries.
_UNDO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bgit\s+checkout\b", re.IGNORECASE),
    re.compile(r"\bgit\s+revert\b", re.IGNORECASE),
    re.compile(r"\bundo\s+that\b", re.IGNORECASE),
    re.compile(r"\brevert\s+that\b", re.IGNORECASE),
    re.compile(r"\bundo\s+the\s+change\b", re.IGNORECASE),
    re.compile(r"\broll\s+back\b", re.IGNORECASE),
    re.compile(r"\brollback\b", re.IGNORECASE),
]

# Phrases where the AI agent admits it made a mistake or oversight.
# These fire on assistant messages only — they capture self-awareness moments
# that reveal prompting/skill/tool-chain gaps.
_ADMISSION_PATTERNS: list[re.Pattern[str]] = [
    # Direct mistake admissions
    re.compile(r"\bi\s+made\s+a\s+mistake\b", re.IGNORECASE),
    re.compile(r"\bi\s+made\s+an\s+error\b", re.IGNORECASE),
    re.compile(r"\bthat\s+was\s+(?:my\s+)?(?:a\s+)?mistake\b", re.IGNORECASE),
    re.compile(r"\bi\s+was\s+wrong\b", re.IGNORECASE),
    # "I should have" / "I should not have"
    re.compile(r"\bi\s+should\s+have\b", re.IGNORECASE),
    re.compile(r"\bi\s+should\s+not\s+have\b", re.IGNORECASE),
    re.compile(r"\bi\s+shouldn['']t\s+have\b", re.IGNORECASE),
    # Accidental actions
    re.compile(r"\bi\s+accidentally\b", re.IGNORECASE),
    re.compile(r"\bi\s+mistakenly\b", re.IGNORECASE),
    re.compile(r"\bi\s+incorrectly\b", re.IGNORECASE),
    # Missed / overlooked
    re.compile(r"\bi\s+missed\b", re.IGNORECASE),
    re.compile(r"\bi\s+overlooked\b", re.IGNORECASE),
    re.compile(r"\bi\s+forgot\s+to\b", re.IGNORECASE),
    re.compile(r"\bi\s+failed\s+to\b", re.IGNORECASE),
    re.compile(r"\bi\s+neglected\s+to\b", re.IGNORECASE),
    # Apologies that signal error awareness
    re.compile(r"\bmy\s+apologies\b", re.IGNORECASE),
    re.compile(r"\bsorry\s+about\s+that\b", re.IGNORECASE),
    re.compile(r"\bsorry\s+for\s+the\s+(?:error|mistake|confusion|oversight)\b", re.IGNORECASE),
    re.compile(r"\bapologize\s+for\b", re.IGNORECASE),
    # Self-correction language
    re.compile(r"\blet\s+me\s+(?:fix|correct|redo)\s+that\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+not\s+(?:right|correct)\b.*\blet\s+me\b", re.IGNORECASE),
    re.compile(r"\bi\s+need\s+to\s+(?:fix|correct|redo)\b", re.IGNORECASE),
    # "didn't" patterns
    re.compile(
        r"\bi\s+didn['']t\s+(?:account|consider|notice|check|read|realize)\b",
        re.IGNORECASE,
    ),
]

# Guard against false undo from "git push …"
_GIT_PUSH_PATTERN: re.Pattern[str] = re.compile(r"\bgit\s+push\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Agent-recognizer corpus — categories of agent SELF-STATE (not errors).
# Grounded in research/papers/ and documented in docs/agent-recognizers.md.
# Exposed via detect_agent_states(); intentionally NOT wired into
# extract_errors() — the uncertainty/overconfidence/assumption markers are
# common enough that emitting them as mined-error records would flood the
# top-N rankings (the same pollution _is_hook_block_noise guards against).
# ---------------------------------------------------------------------------

# Epistemic hedging / weakened commitment.  Source: arxiv:2302.13439 (Table 6 weakeners, Table 5)
_UNCERTAINTY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi\s+think\b", re.IGNORECASE),
    re.compile(r"\b(?:it\s+)?might\s+be\b", re.IGNORECASE),
    re.compile(r"\b(?:it\s+)?could\s+be\b", re.IGNORECASE),
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\bperhaps\b", re.IGNORECASE),
    re.compile(r"\bprobably\b", re.IGNORECASE),
    re.compile(r"\bpossibly\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+not\s+(?:sure|certain)\b", re.IGNORECASE),
    re.compile(r"\bi\s+suppose\b", re.IGNORECASE),
    re.compile(r"\bi\s+guess\b", re.IGNORECASE),
    re.compile(r"\bi\s+believe\b", re.IGNORECASE),
    re.compile(r"\bif\s+i\s+recall\b", re.IGNORECASE),
]

# Asserted certainty / boosters.  Source: arxiv:2302.13439 (Table 6 strengtheners, Table 5)
# NOTE: low precision — these also occur in benign prose; tune before using as a hard signal.
_OVERCONFIDENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bdefinitely\b", re.IGNORECASE),
    re.compile(r"\bcertainly\b", re.IGNORECASE),
    re.compile(r"\bundoubtedly\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+a\s+doubt\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+(?:certain|sure)\b", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+(?:certain|sure)\b", re.IGNORECASE),
    re.compile(r"\b100%\s+(?:sure|confident|certain)\b", re.IGNORECASE),
    re.compile(r"\bit\s+must\s+be\b", re.IGNORECASE),
    re.compile(r"\bguaranteed\b", re.IGNORECASE),
    re.compile(r"\b(?:obviously|evidently)\b", re.IGNORECASE),
]

# Post-failure reflection + revised strategy.  Source: arxiv:2303.11366, arxiv:2409.12917
_SELF_REFLECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bon\s+reflection\b", re.IGNORECASE),
    re.compile(r"\blooking\s+back\b", re.IGNORECASE),
    re.compile(r"\bmy\s+previous\s+attempt\b", re.IGNORECASE),
    re.compile(r"\bfailed\s+because\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+next\s+(?:trial|attempt|time)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:now\s+)?realize\s+that\b", re.IGNORECASE),
    re.compile(r"\bi\s+misunderstood\b", re.IGNORECASE),
    re.compile(r"\bi\s+did\s+not\s+take\s+into\s+account\b", re.IGNORECASE),
    re.compile(r"\bi\s+need\s+to\s+reconsider\b", re.IGNORECASE),
]

# Critique of own current output before revising.  Source: arxiv:2305.11738, arxiv:2303.17651
_SELF_CRITIQUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\blet\s+me\s+verify\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+double[-\s]?check\b", re.IGNORECASE),
    re.compile(r"\bchecking\s+my\s+work\b", re.IGNORECASE),
    re.compile(r"\bthe\s+answer\s+is\s+not\s+reasonable\b", re.IGNORECASE),
    re.compile(r"\bthis\s+could\s+be\s+improved\b", re.IGNORECASE),
    re.compile(r"\ba\s+better\s+(?:approach|solution)\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+reconsider\b", re.IGNORECASE),
]

# Explicit assumption / inferred-info flag.  Source: arxiv:2302.13439 (plausibility shields)
_ASSUMPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi['']?ll\s+assume\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:would\s+)?assume\b", re.IGNORECASE),
    re.compile(r"\bassuming\s+that\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+guessing\b", re.IGNORECASE),
    re.compile(r"\bpresumably\b", re.IGNORECASE),
    re.compile(r"\bto\s+the\s+best\s+of\s+my\s+knowledge\b", re.IGNORECASE),
    re.compile(r"\bas\s+far\s+as\s+i['']?m?\s+aware\b", re.IGNORECASE),
]

# Impasse / repeated failure (tier C — validate before trusting).  Source: arxiv:2303.11366/17651/2409.12917
_STUCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi['']?m\s+(?:not\s+able|unable)\s+to\b", re.IGNORECASE),
    re.compile(r"\bi\s+can['']?t\s+figure\s+(?:this\s+)?out\b", re.IGNORECASE),
    re.compile(r"\bi\s+keep\s+failing\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+stuck\b", re.IGNORECASE),
    re.compile(r"\bi\s+cannot\s+(?:solve|determine)\b", re.IGNORECASE),
    re.compile(r"\bi['']?ve\s+tried\s+(?:multiple|several)\b", re.IGNORECASE),
]


# Hook-success / guardrail-intervention signatures that look like tool failures
# but are actually the guardrail working as designed. Filtering these at ingest
# prevents them from polluting the top-N error pattern rankings, where they
# crowd out genuine agent misbehavior.
#
# Each pattern targets the *error_text* (or its leading content) of a record
# that would otherwise be classified as ``tool_failure``.  When a pattern
# matches, the record is skipped entirely — it's downgraded to telemetry, not
# an error.
#
# Add new entries here when a hook starts emitting recognizable block messages.
_HOOK_BLOCK_PATTERNS: list[re.Pattern[str]] = [
    # PreToolUse hooks denying tools (Read, Bash, Edit, etc.)
    re.compile(r"\bPreToolUse:[A-Za-z]+\s+(?:hook\s+)?(?:denied|blocked)\b", re.IGNORECASE),
    re.compile(r"\bHook\s+PreToolUse:[A-Za-z]+\s+denied\b", re.IGNORECASE),
    # hhdev preflight hook (AP-010 / config.toml / patch-scope blocks)
    re.compile(r"\bhhdev-preflight\b", re.IGNORECASE),
    re.compile(r"\bAP-010\s+(?:block|preflight)\b", re.IGNORECASE),
    # retry-guard circuit breaker (the hook's own block message, not the
    # underlying repeated_attempt which we still want classified separately
    # via the consecutive-tool detector below).
    re.compile(r"\bretry-guard\b.*\bblock(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\[retry-guard\]\s+BLOCK", re.IGNORECASE),
    # docker-down-gate, batch-guard, cascade-shield, claudemd-cap, done-before-gate
    re.compile(r"\bdocker-down-gate\b", re.IGNORECASE),
    re.compile(r"\bbatch-guard\b.*\bblock(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bcascade-shield\b.*\bblock(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\[claudemd-cap\]\s+BLOCK", re.IGNORECASE),
    re.compile(r"\[done-before-gate\]", re.IGNORECASE),
    # Generic "PreToolUse:<Tool> hook error:" from settings.json command hooks
    re.compile(r"\bPreToolUse:[A-Za-z]+\s+hook\s+error\b", re.IGNORECASE),
]

# Rule-injection echoes: when a rule's own text appears verbatim in an error
# record's context (because the rules-injector hook emitted a system-reminder
# right before a tool call that then failed), the rule text gets counted as a
# "violation mention." These tags identify the BLOCKING rule headers that
# should NOT count as evidence of violation.
_RULE_INJECTION_HEADERS: list[re.Pattern[str]] = [
    re.compile(r"ZENO\s+RETRY-LOOP\s+RULE", re.IGNORECASE),
    re.compile(r"HOOK-BYPASS\s+RULE", re.IGNORECASE),
    re.compile(r"DBT/CUBE\s+SOURCE\s+BINDING", re.IGNORECASE),
    re.compile(r"CONFIG\.TOML\s+BINDING\s+\+\s+HH_WORKTREE_MODE", re.IGNORECASE),
]


def _is_hook_block_noise(error_text: str | None) -> bool:
    """Return True when ``error_text`` is a hook-success block message rather
    than a real tool failure.  Used to skip ingestion of guardrail interventions
    that would otherwise pollute error rankings.
    """
    if not error_text:
        return False
    for pat in _HOOK_BLOCK_PATTERNS:
        if pat.search(error_text):
            return True
    # If the entire error_text is dominated by rule-injection headers (i.e.
    # the "failure" is actually the rules-injector having emitted a rule
    # block that the tool response captured), skip it.  We require at least
    # one rule-injection header AND total length < 4 KB to avoid suppressing
    # real failures that happen to mention a rule name.
    if len(error_text) < 4096:
        for pat in _RULE_INJECTION_HEADERS:
            if pat.search(error_text):
                return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _session_id_from_source(source_file: str) -> str:
    """Extract a session identifier from a SpecStory filename.

    SpecStory filenames follow the pattern::

        YYYY-MM-DD_HH-MM-SSZ-<slug>.md

    The slug after the ``Z-`` separator is returned as the session_id.
    If the filename does not match the pattern the whole stem is returned.

    Examples
    --------
    >>> _session_id_from_source("2026-02-25_10-00-00Z-test-session.md")
    'test-session'
    >>> _session_id_from_source("plain-name.md")
    'plain-name'
    """
    # Strip directory component and extension
    stem = source_file.rsplit("/", 1)[-1]
    if stem.endswith(".md") or stem.endswith(".jsonl"):
        stem = stem.rsplit(".", 1)[0]
    # SpecStory pattern: anything after the first "Z-"
    z_idx = stem.find("Z-")
    if z_idx != -1:
        return stem[z_idx + 2 :]
    return stem


def _content_of(msg: dict[str, Any]) -> str:
    """Return a non-None string representing a message's displayable content."""
    return msg.get("content") or ""


def _tool_input_fingerprint(tool_input: dict[str, Any] | None) -> str:
    """Produce a stable string fingerprint of a tool_input dict for similarity comparison."""
    if not tool_input:
        return ""
    try:
        return json.dumps(tool_input, sort_keys=True)
    except (TypeError, ValueError):
        return str(tool_input)


def _build_record(
    *,
    msg: dict[str, Any],
    idx: int,
    messages: list[dict[str, Any]],
    source_file: str,
    source_type: str,
    error_type: str,
    error_text: str,
    tool_name: str | None,
    user_message: str | None,
    mined_at: str,
) -> dict[str, Any]:
    """Assemble a complete ErrorRecord dict from component parts."""
    context_before: str | None = _content_of(messages[idx - 1]) if idx > 0 else None
    context_after: str | None = _content_of(messages[idx + 1]) if idx < len(messages) - 1 else None

    # Prefer session_id embedded in the message; fall back to filename derivation.
    session_id: str = msg.get("session_id") or _session_id_from_source(source_file)

    timestamp: str = msg.get("timestamp") or _now_iso()

    tool_input_text = _to_text(msg.get("tool_input"))
    # Stage-1 structural tags, derived generically (no per-project hardcoding) so the
    # autopsy/cluster stage reads persisted tags instead of recomputing per run.
    tags = derive_all(source_file, tool_name, tool_input_text, timestamp)

    return {
        "session_id": session_id,
        "timestamp": timestamp,
        "source_type": source_type,
        "source_file": source_file,
        "tool_name": tool_name,
        "error_text": error_text,
        "user_message": user_message,
        "context_before": context_before,
        "context_after": context_after,
        "error_type": error_type,
        "tool_input": tool_input_text,
        "tool_output": _to_text(msg.get("tool_output")),
        "mined_at": mined_at,
        "project_tag": tags["project_tag"],
        "command_category": tags["command_category"],
        "time_bucket": tags["time_bucket"],
    }


def _last_human_message(messages: list[dict[str, Any]], before_idx: int) -> str | None:
    """Return the content of the most recent human/user message before *before_idx*."""
    for i in range(before_idx - 1, -1, -1):
        if messages[i].get("role") in ("human", "user"):
            return _content_of(messages[i])
    return None


def _is_correction(content: str) -> bool:
    """Return True when *content* matches any correction phrase."""
    return any(pat.search(content) for pat in _CORRECTION_PATTERNS)


def _is_undo(content: str) -> bool:
    """Return True when *content* matches an undo signal.

    Explicitly excludes "git push" from triggering the undo classification.
    """
    # Reject messages that are purely a git-push command.
    if _GIT_PUSH_PATTERN.search(content):
        # Only suppress if none of the undo patterns are also present — a
        # message could theoretically contain both "git push" and "git revert".
        # In practice we treat any "git push" presence as disqualifying when
        # no revert/undo text exists beyond the push itself.
        content_without_push = _GIT_PUSH_PATTERN.sub("", content)
        return any(pat.search(content_without_push) for pat in _UNDO_PATTERNS)
    return any(pat.search(content) for pat in _UNDO_PATTERNS)


def _is_admission(content: str) -> bool:
    """Return True when *content* contains an agent self-admission of error.

    Only fires on assistant messages.  Detects phrases where the AI admits
    it made a mistake, missed something, or needs to correct its own work.
    """
    return any(pat.search(content) for pat in _ADMISSION_PATTERNS)


# Registry of the agent-recognizer corpus: label -> compiled patterns.
# agent_admission is handled by _is_admission (kept as the canonical shipped
# recognizer); the rest are the docs/agent-recognizers.md expansion.
_AGENT_STATE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "agent_uncertainty": _UNCERTAINTY_PATTERNS,
    "agent_overconfidence": _OVERCONFIDENCE_PATTERNS,
    "agent_self_reflection": _SELF_REFLECTION_PATTERNS,
    "agent_self_critique": _SELF_CRITIQUE_PATTERNS,
    "agent_assumption": _ASSUMPTION_PATTERNS,
    "agent_stuck": _STUCK_PATTERNS,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_agent_states(content: str) -> list[str]:
    """Return every agent-recognizer label whose patterns match *content*.

    Multi-label: one message can express several states at once (e.g.
    uncertainty + assumption). ``agent_admission`` is included for completeness.
    This is a pure classifier over agent self-state — it does NOT emit error
    records and is independent of :func:`extract_errors`. Callers decide what to
    do with the labels.

    See ``docs/agent-recognizers.md`` for the corpus, confidence tiers, and the
    arXiv grounding of each pattern set.
    """
    if not content:
        return []
    labels: list[str] = []
    if _is_admission(content):
        labels.append("agent_admission")
    for label, patterns in _AGENT_STATE_PATTERNS.items():
        if any(pat.search(content) for pat in patterns):
            labels.append(label)
    return labels


def extract_errors(
    parsed_messages: list[dict[str, Any]],
    source_file: str,
    source_type: str,
) -> list[dict[str, Any]]:
    """Classify parsed conversation messages into ErrorRecord dicts.

    Parameters
    ----------
    parsed_messages:
        List of message dicts produced by a SpecStory or JSONL parser.
        Each dict must carry at minimum: ``role``, ``content``, ``tool_name``,
        ``tool_input``, ``tool_output``, ``error``.  Optional fields:
        ``session_id``, ``timestamp``.
    source_file:
        The originating file path or filename; propagated to every record.
    source_type:
        One of ``"specstory"`` or ``"jsonl"``; propagated to every record.

    Returns
    -------
    list[dict]
        Zero or more ErrorRecord dicts, each containing the keys:
        session_id, timestamp, source_type, source_file, tool_name,
        error_text, user_message, context_before, context_after,
        error_type, mined_at.
    """
    if not parsed_messages:
        return []

    records: list[dict[str, Any]] = []
    mined_at = _now_iso()

    # ------------------------------------------------------------------
    # State for repeated_attempt detection.
    # We track consecutive runs of the same tool_name.  A run breaks when:
    #   - the role changes from assistant to human, OR
    #   - the tool_name differs from the previous tool call.
    # When a run reaches exactly 3 we emit one record; we do NOT emit on
    # every subsequent call (4th, 5th, …) to avoid record explosion, though
    # emitting on each additional call beyond 3 would also be valid.
    # ------------------------------------------------------------------
    consecutive_tool: str | None = None
    consecutive_count: int = 0

    for idx, msg in enumerate(parsed_messages):
        role: str = msg.get("role", "")
        content: str = _content_of(msg)
        tool_name: str | None = msg.get("tool_name")
        error: str | None = msg.get("error")

        # ------------------------------------------------------------------
        # 1. tool_failure
        # ------------------------------------------------------------------
        if error:  # non-None and non-empty
            # Denoise: skip hook-success block messages and rule-injection
            # echoes. These are guardrail interventions working as designed,
            # not agent failures. Filtering them at ingest prevents them
            # from polluting the top-N pattern rankings.
            if _is_hook_block_noise(error):
                pass  # skip ingestion
            else:
                user_msg = _last_human_message(parsed_messages, idx)
                records.append(
                    _build_record(
                        msg=msg,
                        idx=idx,
                        messages=parsed_messages,
                        source_file=source_file,
                        source_type=source_type,
                        error_type="tool_failure",
                        error_text=error,
                        tool_name=tool_name,
                        user_message=user_msg,
                        mined_at=mined_at,
                    )
                )

        # ------------------------------------------------------------------
        # 2. user_correction  (human messages only)
        # ------------------------------------------------------------------
        if role in ("human", "user") and _is_correction(content):
            records.append(
                _build_record(
                    msg=msg,
                    idx=idx,
                    messages=parsed_messages,
                    source_file=source_file,
                    source_type=source_type,
                    error_type="user_correction",
                    error_text=f"User correction: {content}",
                    tool_name=None,
                    user_message=content,
                    mined_at=mined_at,
                )
            )

        # ------------------------------------------------------------------
        # 4. undo  (human messages only)
        # ------------------------------------------------------------------
        if role in ("human", "user") and _is_undo(content):
            records.append(
                _build_record(
                    msg=msg,
                    idx=idx,
                    messages=parsed_messages,
                    source_file=source_file,
                    source_type=source_type,
                    error_type="undo",
                    error_text=f"Undo requested: {content}",
                    tool_name=None,
                    user_message=content,
                    mined_at=mined_at,
                )
            )

        # ------------------------------------------------------------------
        # 5. agent_admission  (assistant messages only)
        # ------------------------------------------------------------------
        if role == "assistant" and content and _is_admission(content):
            # Truncate to first 200 chars for the error_text — the full
            # content is preserved in context_before/context_after.
            snippet = content[:200] + ("…" if len(content) > 200 else "")
            user_msg = _last_human_message(parsed_messages, idx)
            records.append(
                _build_record(
                    msg=msg,
                    idx=idx,
                    messages=parsed_messages,
                    source_file=source_file,
                    source_type=source_type,
                    error_type="agent_admission",
                    error_text=f"Agent admission: {snippet}",
                    tool_name=None,
                    user_message=user_msg,
                    mined_at=mined_at,
                )
            )

        # ------------------------------------------------------------------
        # 3. repeated_attempt — track consecutive tool runs
        # ------------------------------------------------------------------
        if role == "assistant" and tool_name:
            if tool_name == consecutive_tool:
                consecutive_count += 1
                # Emit exactly one record when the threshold (3) is first crossed.
                if consecutive_count == 3:
                    # Use the index of the third (current) message for context.
                    user_msg = _last_human_message(parsed_messages, idx)
                    records.append(
                        _build_record(
                            msg=msg,
                            idx=idx,
                            messages=parsed_messages,
                            source_file=source_file,
                            source_type=source_type,
                            error_type="repeated_attempt",
                            error_text=(
                                f"Tool '{tool_name}' called {consecutive_count} "
                                f"consecutive times with similar input."
                            ),
                            tool_name=tool_name,
                            user_message=user_msg,
                            mined_at=mined_at,
                        )
                    )
            else:
                # Different tool or first tool seen — reset run tracking.
                consecutive_tool = tool_name
                consecutive_count = 1
        elif role in ("human", "user"):
            # A human turn breaks any consecutive assistant tool run.
            consecutive_tool = None
            consecutive_count = 0

    return records
