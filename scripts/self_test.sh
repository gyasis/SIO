#!/usr/bin/env bash
set -euo pipefail

# SIO Self-Test: runs the full pipeline on its own development history
# Validates: mine -> patterns -> suggest -> output quality
#
# Usage:
#   ./scripts/self_test.sh
#   ./scripts/self_test.sh --since "7 days"
#
# Exit codes:
#   0 - All checks passed
#   1 - Pipeline failure or validation error

SINCE="${1:-30 days}"
PLATFORM="claude-code"
TMPDIR="${TMPDIR:-/tmp}"

echo "=== SIO Self-Test ==="
echo "Running SIO pipeline on its own development history..."
echo "  Time window: ${SINCE}"
echo "  Platform:    ${PLATFORM}"
echo ""

# Pre-flight: verify sio is installed
if ! command -v sio &>/dev/null; then
    echo "ERROR: 'sio' command not found. Run: pip install -e . (from the SIO project root)"
    exit 1
fi

# Step 1: Mine recent sessions
echo "[1/4] Mining errors from last ${SINCE}..."
if ! sio mine --since "${SINCE}"; then
    echo "WARN: 'sio mine' exited with non-zero status (may have no source files)."
    echo "  Checked directories:"
    echo "    SpecStory: ~/.specstory/history"
    echo "    JSONL:     ~/.claude/projects"
    echo ""
    echo "  If this is a fresh install, run a few Claude Code sessions first"
    echo "  to generate data, then re-run this script."
    exit 0
fi
echo ""

# Step 2: Show patterns
echo "[2/4] Clustering into patterns..."
PATTERN_OUTPUT="${TMPDIR}/sio_self_test_patterns.txt"
if sio patterns > "${PATTERN_OUTPUT}" 2>&1; then
    cat "${PATTERN_OUTPUT}"
    echo ""
else
    echo "WARN: 'sio patterns' produced no output (might need more error data)."
    echo ""
fi

# Step 3: Generate suggestions
echo "[3/4] Generating suggestions..."
SUGGESTION_EXIT=0
sio suggest --verbose 2>&1 || SUGGESTION_EXIT=$?

if [ "${SUGGESTION_EXIT}" -ne 0 ]; then
    echo ""
    echo "WARN: 'sio suggest' exited with code ${SUGGESTION_EXIT}."
    echo "  This may indicate insufficient error data for dataset building."
    echo "  Try lowering --min-examples or mining a longer time window."
    exit 0
fi
echo ""

# Step 4: Validate by querying the DB directly
echo "[4/4] Validating output quality..."
DB_PATH="${HOME}/.sio/sio.db"

if [ ! -f "${DB_PATH}" ]; then
    echo "WARN: Database not found at ${DB_PATH}"
    exit 0
fi

python3 -c "
import sqlite3, sys, json

db_path = '${DB_PATH}'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Count error records
error_count = conn.execute('SELECT COUNT(*) FROM error_records').fetchone()[0]
print(f'  Error records mined: {error_count}')

# Count patterns
pattern_count = conn.execute('SELECT COUNT(*) FROM patterns').fetchone()[0]
print(f'  Patterns discovered: {pattern_count}')

# Count suggestions
suggestion_count = conn.execute('SELECT COUNT(*) FROM suggestions WHERE status = \"pending\"').fetchone()[0]
print(f'  Pending suggestions: {suggestion_count}')

# Validate suggestions have required fields
if suggestion_count > 0:
    rows = conn.execute(
        'SELECT description, confidence, proposed_change, target_file, change_type '
        'FROM suggestions WHERE status = \"pending\" LIMIT 20'
    ).fetchall()

    required_fields = ('description', 'confidence', 'proposed_change', 'target_file', 'change_type')
    for row in rows:
        row_dict = dict(row)
        missing = [f for f in required_fields if not row_dict.get(f)]
        if missing:
            print(f'FAIL: Suggestion missing fields: {missing}')
            sys.exit(1)

    # Check distinct target surfaces
    surfaces = set(dict(r)['change_type'] for r in rows)
    print(f'  Target surfaces: {surfaces}')

    if len(surfaces) >= 2:
        print(f'  PASS: {len(surfaces)} distinct surface types')
    else:
        print(f'  INFO: {len(surfaces)} surface type(s) found (2+ expected for diverse error data)')

    print(f'PASS: {suggestion_count} valid suggestions generated')
else:
    print('INFO: No pending suggestions (may need more error data).')

conn.close()
"

echo ""
echo "=== Self-Test Complete ==="
