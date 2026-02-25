"""Secret scrubber — regex-based sensitive data redaction.

Replaces AWS keys, API tokens, bearer tokens, passwords, connection strings,
and generic secrets with [REDACTED].
"""

from __future__ import annotations

import re

# Patterns that match key=value assignments (only redact when value is present)
_KEY_VALUE_PATTERNS = [
    # AWS access key IDs (AKIA prefix, 20 chars)
    re.compile(r"((?:aws_access_key_id|AKIA)\s*[=:]\s*)(\S+)", re.IGNORECASE),
    # AWS secret access keys
    re.compile(r"(aws_secret_access_key\s*[=:]\s*)(\S+)", re.IGNORECASE),
    # API keys/tokens with assignment
    re.compile(
        r"((?:api[_-]?key|api[_-]?token|x-api-key)\s*[=:]\s*\"?)(\S+?)\"?(?=\s|$|\")",
        re.IGNORECASE,
    ),
    # Passwords with assignment
    re.compile(r"((?:password|passwd|pwd)\s*[=:]\s*\"?)(\S+?)\"?(?=\s|$|\"|})", re.IGNORECASE),
    # JSON password fields: "password": "value"
    re.compile(r'("password"\s*:\s*")([^"]+)(")', re.IGNORECASE),
    # Generic secrets (SECRET_KEY, PRIVATE_KEY, etc.)
    re.compile(r"((?:SECRET_KEY|PRIVATE_KEY|ACCESS_TOKEN)\s*[=:]\s*)(\S+)", re.IGNORECASE),
]

# Bearer tokens
_BEARER_PATTERN = re.compile(
    r"(Bearer\s+)(\S+)", re.IGNORECASE
)

# Connection strings with credentials: scheme://user:pass@host
_CONN_STRING_PATTERN = re.compile(
    r"((?:postgresql|mysql|mongodb|redis|amqp)://)([^@]+)(@\S+)"
)

# Standalone AWS access key IDs (AKIA followed by 16 alphanumeric chars)
_AWS_AKIA_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def scrub(text: str) -> str:
    """Replace sensitive patterns in text with [REDACTED].

    Args:
        text: Input text that may contain secrets.

    Returns:
        Text with all detected secrets replaced by [REDACTED].
    """
    if not text:
        return text

    result = text

    # Key-value patterns (redact value, keep key prefix)
    for pattern in _KEY_VALUE_PATTERNS:
        if pattern.groups == 3:
            # JSON-style pattern with closing quote group
            result = pattern.sub(r"\1[REDACTED]\3", result)
        else:
            result = pattern.sub(r"\1[REDACTED]", result)

    # Bearer tokens
    result = _BEARER_PATTERN.sub(r"\1[REDACTED]", result)

    # Connection strings (redact user:pass, keep scheme and host)
    result = _CONN_STRING_PATTERN.sub(r"\1[REDACTED]\3", result)

    # Standalone AWS access key IDs
    result = _AWS_AKIA_PATTERN.sub("[REDACTED]", result)

    return result
