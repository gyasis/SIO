#!/usr/bin/env bash
# =============================================================================
# SIO Skills Installer
# Copies SIO skills to ~/.claude/skills/ (Claude Code) and, for each of pi,
# codex and opencode that is set up on this machine, into that harness's own
# user skills dir, for slash command access.
# Run after `pip install -e .` or `pip install sio`
#
# This is the plain-copy fallback. `sio init` (or `sio init --harness <name>`)
# is the manifest-tracked path: drift detection, backups, clean uninstall.
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
    # NOT ((installed++)): under `set -e` that returns 1 when installed is 0
    # and killed the script after the first skill.
    installed=$((installed + 1))
done

echo ""
echo "Installed ${installed} skills."

# pi reads user skills from <agentDir>/skills/<name>/SKILL.md, where agentDir
# is $PI_CODING_AGENT_DIR or ~/.pi/agent (pi's config.js getAgentDir). Only
# SIO's own skill names are ever written; anything else in that dir is left
# alone, and a symlinked entry (some other tool's skill) is never followed.
PI_AGENT_DIR="${PI_CODING_AGENT_DIR:-${HOME}/.pi/agent}"
if [[ -d "$PI_AGENT_DIR" ]]; then
    PI_SKILLS_DST="${PI_AGENT_DIR}/skills"
    echo ""
    echo "pi detected — installing SIO skills to ${PI_SKILLS_DST}..."
    pi_installed=0
    for skill_dir in "$SKILLS_SRC"/*/; do
        skill_name=$(basename "$skill_dir")
        dest="${PI_SKILLS_DST}/${skill_name}"
        if [[ -L "$dest" || -L "$dest/SKILL.md" ]]; then
            echo "  - ${skill_name} (symlink not managed by SIO — left alone)"
            continue
        fi
        mkdir -p "$dest"
        cp -R "${skill_dir}." "$dest/"     # SKILL.md + any sibling files (scripts/)
        echo "  ✓ ${skill_name}"
        pi_installed=$((pi_installed + 1))
    done
    echo "Installed ${pi_installed} skills for pi (restart pi to pick them up)."
fi

# Same plain copy for a harness whose user skills live at <dir>/skills/<name>/
# SKILL.md. Only SIO's own skill names are written; symlinked entries are
# never followed. Usage: copy_skills_into <harness> <skills-dst>
copy_skills_into() {
    local harness="$1" dst="$2" n=0 skill_dir skill_name dest
    echo ""
    echo "${harness} detected — installing SIO skills to ${dst}..."
    for skill_dir in "$SKILLS_SRC"/*/; do
        skill_name=$(basename "$skill_dir")
        dest="${dst}/${skill_name}"
        if [[ -L "$dest" || -L "$dest/SKILL.md" ]]; then
            echo "  - ${skill_name} (symlink not managed by SIO — left alone)"
            continue
        fi
        mkdir -p "$dest"
        cp -R "${skill_dir}." "$dest/"
        echo "  ✓ ${skill_name}"
        n=$((n + 1))
    done
    echo "Installed ${n} skills for ${harness} (restart ${harness} to pick them up)."
}

# codex reads user skills from $CODEX_HOME/skills/<name>/SKILL.md, where
# CODEX_HOME defaults to ~/.codex (the binary resolves the env var itself).
CODEX_DIR="${CODEX_HOME:-${HOME}/.codex}"
if [[ -d "$CODEX_DIR" ]]; then
    copy_skills_into codex "${CODEX_DIR}/skills"
fi

# opencode reads global skills from $XDG_CONFIG_HOME/opencode/skills/<name>/
# SKILL.md (default ~/.config/opencode). OPENCODE_CONFIG_DIR is an extra
# scanned root, not a relocation, so it is deliberately not consulted here.
OPENCODE_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/opencode"
if [[ -d "$OPENCODE_DIR" ]]; then
    copy_skills_into opencode "${OPENCODE_DIR}/skills"
fi
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
