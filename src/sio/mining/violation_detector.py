"""Violation detector — parses instruction file rules and detects when mined
errors indicate violations of existing rules.

Exported API
------------
parse_rules(file_path) -> list[Rule]
detect_violations(rules, error_records) -> list[Violation]
get_violation_report(db, rule_file_paths) -> dict

Rule violation detection (FR-026, FR-027) identifies enforcement failures:
rules that exist in instruction files but are being ignored by the assistant.
Violations are flagged at higher priority than new patterns since they indicate
the rule text is insufficient or the assistant is failing to follow it.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, NamedTuple

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Rule(NamedTuple):
    """A parsed imperative rule from an instruction file."""

    text: str
    file_path: str
    line_number: int


class Violation(NamedTuple):
    """A detected rule violation — an error that matches an existing rule."""

    rule: Rule
    error_record: dict
    match_type: str  # 'keyword' | 'semantic'
    confidence: float


# ---------------------------------------------------------------------------
# Imperative pattern matching
# ---------------------------------------------------------------------------

# Patterns that identify imperative rule language.
# Matches lines containing NEVER/ALWAYS/MUST/DO NOT and their lowercase variants.
_IMPERATIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bnever\b", re.IGNORECASE),
    re.compile(r"\balways\b", re.IGNORECASE),
    re.compile(r"\bmust\s+not\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\b", re.IGNORECASE),
]

# Lines that start as markdown headings, blank, or HTML comments — skip these.
_SKIP_LINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*$"),  # blank
    re.compile(r"^\s*#+\s"),  # heading
    re.compile(r"^\s*<!--"),  # HTML comment start
    re.compile(r"^\s*-->"),  # HTML comment end
    re.compile(r"^\s*```"),  # code fence
]


def _is_skip_line(line: str) -> bool:
    """Return True if the line should be skipped (heading, blank, comment, fence)."""
    return any(pat.match(line) for pat in _SKIP_LINE_PATTERNS)


# System-reminder blocks injected by the rules-injector PreToolUse hook contain
# the verbatim text of the rules they're enforcing. When violation_detector
# keyword-searches an error record's context_before / context_after fields, the
# rule's own injected text reads as a "mention" of the rule and inflates the
# violation count.  (Symptom: ZENO RETRY-LOOP RULE counted 5044x across 58
# sessions when actual violations are in the dozens.)
#
# Stripping these blocks from searchable text before keyword matching ensures
# only real error context counts as evidence.
_SYSTEM_REMINDER_PATTERN: re.Pattern[str] = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_rule_injections(text: str) -> str:
    """Remove any ``<system-reminder>...</system-reminder>`` blocks from *text*.

    The rules-injector hook wraps every injected rule file in such a block.
    Counting that text against the rule itself is self-referential and
    produces the 1000x+ inflated violation counts observed in production.
    """
    if not text or "<system-reminder>" not in text:
        return text
    return _SYSTEM_REMINDER_PATTERN.sub("", text)


def _has_imperative(line: str) -> bool:
    """Return True if the line contains imperative rule language."""
    return any(pat.search(line) for pat in _IMPERATIVE_PATTERNS)


def _clean_rule_text(line: str) -> str:
    """Strip markdown bullet prefix and leading/trailing whitespace."""
    text = line.strip()
    # Remove leading bullet markers: "- ", "* ", "1. ", etc.
    text = re.sub(r"^[-*]\s+", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    # Remove bold/italic markdown
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Keyword extraction for matching
# ---------------------------------------------------------------------------

# Common stop words and markdown artifacts to exclude from keyword extraction.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        # Basic English function words
        "a", "an", "the",
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had",
        "do", "does", "did",
        "will", "would", "shall", "should", "may", "might", "can", "could",
        "must", "need", "not", "never", "always",
        "and", "or", "but", "if", "then", "else",
        "when", "where", "what", "which", "who", "how",
        "that", "this", "it", "its",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "up", "down", "out", "off", "over", "under", "again", "further",
        "than", "too", "very", "just", "about",
        "all", "any", "each", "every", "both", "few",
        "more", "most", "other", "some", "such",
        "no", "nor", "only", "same", "so", "also",
        "use", "using",
        # ------------------------------------------------------------------
        # Rule/imperative noise (added 2026-05-13): these terms appear in
        # almost every rule (BLOCKING, MANDATORY, RULE, etc.) AND in almost
        # every error context, so matching on them produces false-positive
        # violations at 1000x+ inflation. The observed effect:
        #   ZENO RETRY-LOOP RULE → matches every error context that contains
        #   "RETRY" or "LOOP" or "RULE" → 42,432 false violations.
        # Excluding the structural/imperative vocabulary collapses these to
        # the real (small) violation count.
        # ------------------------------------------------------------------
        # Rule-meta terms
        "rule", "rules", "blocking", "mandatory", "required", "optional",
        "origin", "note", "notes", "warning", "warnings", "exception",
        "exemption", "exceptions", "exemptions", "trigger", "triggers",
        "triggered", "triggering",
        # Generic action verbs / status words appearing in both rules + errors
        "fire", "fires", "firing", "fired",
        "block", "blocks", "blocked", "blocking",
        "fail", "fails", "failed", "failing", "failure", "failures",
        "error", "errors", "errored",
        "call", "calls", "called", "calling",
        "run", "runs", "running", "ran",
        "stop", "stops", "stopping", "stopped",
        "start", "starts", "starting", "started",
        "skip", "skips", "skipping", "skipped",
        "drop", "drops", "dropping", "dropped",
        "retry", "retries", "retrying", "retried",
        "loop", "loops", "looping", "looped",
        "violation", "violations", "violated", "violating",
        # Pronoun-ish noise
        "you", "your", "yours", "i", "me", "my", "we", "our", "us",
        "they", "them", "their",
        # Tool/agent meta terms (these appear in almost every error record)
        "tool", "tools", "agent", "agents", "user", "users",
        "claude", "session", "sessions",
        # Time / counting
        "time", "times", "once", "twice", "first", "second", "third",
        "next", "last", "previous", "new", "old",
        # Misc connective
        "see", "via", "per", "etc",
    }
)


def _extract_key_terms(rule_text: str) -> list[str]:
    """Extract meaningful key terms from a rule for keyword matching.

    Returns multi-word phrases first (e.g., "SELECT *"), then significant
    single words. This allows matching compound terms that carry more
    semantic weight.
    """
    terms: list[str] = []

    # 1. Look for quoted phrases or code-like terms (backtick-wrapped)
    # These are high-value compound terms.
    for match in re.finditer(r'["`]([^"`]+)["`]', rule_text):
        term = match.group(1).strip()
        if len(term) >= 2:
            terms.append(term)

    # 2. Look for known compound patterns — e.g., "SELECT *"
    compound_patterns = [
        r"SELECT\s+\*",
        r"git\s+push\s+--force",
        r"git\s+reset\s+--hard",
        r"absolute\s+paths?",
        r"relative\s+paths?",
        r"type\s+hints?",
        r"error\s+handling",
        r"unused\s+imports?",
    ]
    for pat_str in compound_patterns:
        match = re.search(pat_str, rule_text, re.IGNORECASE)
        if match:
            terms.append(match.group(0))

    # 3. Extract significant single words (3+ chars, not stop words)
    #    Also add simple singular/plural variants for better recall.
    words = re.findall(r"[A-Za-z_*]+(?:\s*\*)?", rule_text)
    for word in words:
        w_lower = word.lower().strip()
        if len(w_lower) >= 3 and w_lower not in _STOP_WORDS:
            terms.append(w_lower)
            # Add singular variant if word ends with 's'
            if w_lower.endswith("s") and len(w_lower) >= 4:
                terms.append(w_lower[:-1])
            # Add plural variant
            elif not w_lower.endswith("s"):
                terms.append(w_lower + "s")

    return terms


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_rules(file_path: str | Path) -> list[Rule]:
    """Parse a markdown instruction file and extract imperative rules.

    Scans the file line by line. A line qualifies as a rule if:
    1. It is not a heading, blank line, code fence, or HTML comment, AND
    2. It contains imperative language (NEVER, ALWAYS, MUST, DO NOT, etc.), OR
    3. It starts with a bullet point ("- ") followed by imperative language.

    Parameters
    ----------
    file_path:
        Path to the markdown instruction file.

    Returns
    -------
    list[Rule]
        Parsed rules with original text, file path, and line number.
    """
    path = Path(file_path)
    if not path.exists():
        return []

    rules: list[Rule] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        from sio.core.observability import log_failure  # noqa: PLC0415
        log_failure("parse_errors", str(path), e, stage="claude_md_read")
        return []

    in_code_block = False

    for line_num, line in enumerate(lines, start=1):
        # Track code fences to skip content inside them.
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        if _is_skip_line(line):
            continue

        if _has_imperative(line):
            cleaned = _clean_rule_text(line)
            if cleaned:
                rules.append(
                    Rule(
                        text=cleaned,
                        file_path=str(path),
                        line_number=line_num,
                    )
                )

    return rules


def detect_violations(
    rules: list[Rule],
    error_records: list[dict],
) -> list[Violation]:
    """Match mined error records against parsed rules to detect violations.

    For each error record, checks against each rule using keyword matching:
    key terms are extracted from the rule text and checked against the error's
    text fields (error_text, user_message, context_before, context_after).

    Violations are sorted by frequency (same rule violated multiple times ranks
    higher) and recency (more recent violations first within same frequency).
    Confidence is set to 1.0 for keyword matches, reflecting FR-027's
    requirement that violations are flagged at higher priority than new patterns.

    Parameters
    ----------
    rules:
        Parsed rules from instruction files.
    error_records:
        Error record dicts from the SIO database (error_records table).

    Returns
    -------
    list[Violation]
        Detected violations sorted by frequency then recency.
    """
    if not rules or not error_records:
        return []

    violations: list[Violation] = []

    # Pre-compute key terms for each rule.
    rule_terms: dict[int, list[str]] = {}
    for i, rule in enumerate(rules):
        rule_terms[i] = _extract_key_terms(rule.text)

    for error in error_records:
        # Build a combined searchable text from all relevant error fields.
        # Strip <system-reminder> rule-injection blocks first — counting a
        # rule's own injected text against itself is self-referential and
        # was causing 1000x+ inflated violation counts (e.g. ZENO RETRY-LOOP
        # RULE showed 5044 violations across 58 sessions when real
        # violations are in the dozens).
        searchable_parts: list[str] = []
        for field in (
            "error_text",
            "user_message",
            "context_before",
            "context_after",
            "tool_input",
            "tool_output",
        ):
            val = error.get(field)
            if val:
                searchable_parts.append(_strip_rule_injections(str(val)))
        searchable = " ".join(searchable_parts)
        searchable_lower = searchable.lower()

        for i, rule in enumerate(rules):
            terms = rule_terms[i]
            if not terms:
                continue

            # Multi-keyword match policy (added 2026-05-13).
            #
            # Single-keyword matching was producing 1000x+ false positives:
            # generic words common to many rules (RETRY, LOOP, HOOK, etc.)
            # appear in many error contexts naturally, so a single-keyword
            # hit on a long rule produced thousands of "violations" of
            # rules the agent never actually broke.
            #
            # Policy:
            #   - "Strong" terms (quoted phrases / compound patterns like
            #     `git push --force` or `SELECT *`) → single match wins.
            #   - Plain-word terms → require N distinct matches, where N
            #     scales with the rule's base content-word count (computed
            #     from the rule text itself, NOT the expanded variants
            #     list, which inflates the count via plural/singular
            #     pairs):
            #         <=3 base content words: N=1  (short focused rule;
            #             every word carries weight)
            #         >3 base content words:  N=2  (longer rule has more
            #             chance for spurious single-word matches)
            base_content_words = {
                w.lower()
                for w in re.findall(r"\b[A-Za-z]{3,}\b", rule.text)
                if w.lower() not in _STOP_WORDS
            }
            min_word_matches = 1 if len(base_content_words) <= 3 else 2

            distinct_matches: set[str] = set()
            has_strong_match = False  # quoted / compound term hit

            for term in terms:
                term_lower = term.lower()
                if re.fullmatch(r"\w+", term_lower):
                    # Plain word — requires word-boundary match
                    if re.search(
                        r"\b" + re.escape(term_lower) + r"\b",
                        searchable_lower,
                    ):
                        distinct_matches.add(term_lower)
                else:
                    # Compound / special-char term — single match is enough
                    if term_lower in searchable_lower:
                        has_strong_match = True
                        break

            matched = has_strong_match or len(distinct_matches) >= min_word_matches

            if matched:
                violations.append(
                    Violation(
                        rule=rule,
                        error_record=error,
                        match_type="keyword",
                        confidence=1.0,
                    )
                )

    # Sort by frequency (most violated rules first), then by recency.
    # Count violations per rule text.
    rule_freq: Counter[str] = Counter()
    for v in violations:
        rule_freq[v.rule.text] += 1

    # Sort: highest frequency first, then most recent timestamp first.
    violations.sort(
        key=lambda v: (
            -rule_freq[v.rule.text],
            v.error_record.get("timestamp", ""),
        ),
        reverse=False,
    )
    # Within same frequency group, reverse timestamp order (most recent first).
    # Since Python sort is stable, we can do a two-pass sort:
    # First sort by timestamp descending, then by frequency descending.
    violations.sort(key=lambda v: v.error_record.get("timestamp", ""), reverse=True)
    violations.sort(key=lambda v: -rule_freq[v.rule.text])

    return violations


def get_violation_report(
    db: sqlite3.Connection,
    rule_file_paths: list[str],
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Generate a complete violation report.

    Parses all rule files, queries recent error records from the database,
    runs violation detection, and returns a summary dict.

    Parameters
    ----------
    db:
        Open SQLite connection to the SIO database.
    rule_file_paths:
        List of paths to instruction files to scan for rules.
    since:
        Optional ISO-8601 date string to filter error records.

    Returns
    -------
    dict
        Report with keys: violations (list of dicts), compliant_rules (int),
        total_rules (int), date_range (dict with start/end),
        violation_summary (list of dicts with rule_text, count, last_seen, sessions).
    """
    # 1. Parse all rule files.
    all_rules: list[Rule] = []
    for fp in rule_file_paths:
        all_rules.extend(parse_rules(fp))

    if not all_rules:
        return {
            "violations": [],
            "compliant_rules": 0,
            "total_rules": 0,
            "date_range": {"start": None, "end": None},
            "violation_summary": [],
        }

    # 2. Query recent error records.
    query = "SELECT * FROM error_records"
    params: list[Any] = []
    if since:
        query += " WHERE timestamp >= ?"
        params.append(since)
    query += " ORDER BY timestamp DESC"

    rows = db.execute(query, params).fetchall()
    error_records = [dict(row) for row in rows]

    # 3. Detect violations.
    violations = detect_violations(all_rules, error_records)

    # 4. Build summary.
    violated_rule_texts: set[str] = set()
    rule_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "last_seen": "",
            "sessions": set(),
            "rule": None,
        }
    )

    for v in violations:
        violated_rule_texts.add(v.rule.text)
        stats = rule_stats[v.rule.text]
        stats["count"] += 1
        stats["rule"] = v.rule
        ts = v.error_record.get("timestamp", "")
        if ts > stats["last_seen"]:
            stats["last_seen"] = ts
        sid = v.error_record.get("session_id")
        if sid:
            stats["sessions"].add(sid)

    # Compute compliant rules count.
    all_rule_texts = {r.text for r in all_rules}
    compliant_count = len(all_rule_texts - violated_rule_texts)

    # Build date range from error records.
    timestamps = [e.get("timestamp", "") for e in error_records if e.get("timestamp")]
    date_range = {
        "start": min(timestamps) if timestamps else None,
        "end": max(timestamps) if timestamps else None,
    }

    # Build violation summary sorted by count desc, then recency.
    summary_list: list[dict[str, Any]] = []
    for rule_text, stats in rule_stats.items():
        summary_list.append(
            {
                "rule_text": rule_text,
                "file_path": stats["rule"].file_path if stats["rule"] else "",
                "line_number": stats["rule"].line_number if stats["rule"] else 0,
                "count": stats["count"],
                "last_seen": stats["last_seen"],
                "sessions": len(stats["sessions"]),
            }
        )

    summary_list.sort(key=lambda s: (-s["count"], s["last_seen"]), reverse=False)
    # The above sorts by count desc (because of -count), then last_seen asc.
    # Re-sort properly:
    summary_list.sort(key=lambda s: (-s["count"], s.get("last_seen", "")))

    # Build violation dicts for JSON output.
    # tool_name + tool_input + tool_output are lifted from error_record so
    # downstream consumers (e.g. `sio promote-rule`) can sample the actual
    # violating tool calls without re-querying the DB row-by-row.
    violation_dicts: list[dict[str, Any]] = []
    for v in violations:
        violation_dicts.append(
            {
                "rule_text": v.rule.text,
                "rule_file": v.rule.file_path,
                "rule_line": v.rule.line_number,
                "error_text": v.error_record.get("error_text", ""),
                "error_type": v.error_record.get("error_type", ""),
                "session_id": v.error_record.get("session_id", ""),
                "timestamp": v.error_record.get("timestamp", ""),
                "tool_name": v.error_record.get("tool_name", ""),
                "tool_input": v.error_record.get("tool_input", ""),
                "tool_output": v.error_record.get("tool_output", ""),
                "match_type": v.match_type,
                "confidence": v.confidence,
            }
        )

    return {
        "violations": violation_dicts,
        "compliant_rules": compliant_count,
        "total_rules": len(all_rule_texts),
        "date_range": date_range,
        "violation_summary": summary_list,
    }
