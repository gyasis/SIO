#!/usr/bin/env bash
# =============================================================================
# SIO Skills Installer
# Copies SIO Claude Code skills to ~/.claude/skills/ for slash command access.
# Run after `pip install -e .` or `pip install sio`
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SKILLS_SRC="${PROJECT_DIR}/skills"
SKILLS_DST="${HOME}/.claude/skills"

if [[ ! -d "$SKILLS_SRC" ]]; then
    echo "ERROR: skills/ directory not found at ${SKILLS_SRC}"
    exit 1
fi

echo "Installing SIO skills to ${SKILLS_DST}..."

installed=0
for skill_dir in "$SKILLS_SRC"/*/; do
    skill_name=$(basename "$skill_dir")
    dest="${SKILLS_DST}/${skill_name}"
    mkdir -p "$dest"
    cp "$skill_dir/SKILL.md" "$dest/SKILL.md"
    echo "  ✓ ${skill_name}"
    ((installed++))
done

echo ""
echo "Installed ${installed} skills."
echo ""
echo "Available slash commands:"
echo "  /sio              — Master router (routes to sub-commands)"
echo "  /sio-scan         — Mine errors from sessions"
echo "  /sio-suggest      — Generate improvement rules"
echo "  /sio-review       — Review pending suggestions"
echo "  /sio-apply        — Apply approved suggestions"
echo "  /sio-status       — Pipeline status"
echo "  /sio-flows        — Discover positive patterns"
echo "  /sio-distill      — Distill session into playbook"
echo "  /sio-recall       — Recall how a task was solved"
echo "  /sio-export       — Export ML training datasets"
echo ""
echo "CLI commands (also available directly):"
echo "  sio mine, sio errors, sio flows, sio distill,"
echo "  sio recall, sio train, sio collect-recall, sio export-dataset"
