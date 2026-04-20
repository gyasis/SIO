#!/usr/bin/env bash
# install_autoresearch_systemd.sh (T079 — US4)
#
# Installs a user systemd service + timer that runs the autoresearch pipeline
# daily at 04:00 local time.
#
# Usage:
#   bash scripts/install_autoresearch_systemd.sh
#
# Idempotent: re-running overwrites the unit files cleanly.
# Restart=no per economy-first principle (no runaway restarts on failure).

set -euo pipefail

UNIT_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${UNIT_DIR}/sio-autoresearch.service"
TIMER_FILE="${UNIT_DIR}/sio-autoresearch.timer"

# Resolve the Python interpreter (prefer project venv if present)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then
    PYTHON="$(command -v python3 || command -v python)"
fi

echo "[sio-autoresearch] Installing systemd user units..."
echo "  Python:  ${PYTHON}"
echo "  Project: ${PROJECT_ROOT}"
echo "  Units:   ${UNIT_DIR}"

mkdir -p "${UNIT_DIR}"

# Write the service unit
cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=SIO Autoresearch — daily suggestion evaluation
After=network.target

[Service]
Type=oneshot
WorkingDirectory=${PROJECT_ROOT}
ExecStart=${PYTHON} -m scripts.autoresearch_cron
StandardOutput=journal
StandardError=journal
Restart=no
EOF

# Write the timer unit
cat > "${TIMER_FILE}" << EOF
[Unit]
Description=SIO Autoresearch daily timer

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true
Unit=sio-autoresearch.service

[Install]
WantedBy=timers.target
EOF

# Reload systemd and enable the timer
systemctl --user daemon-reload
systemctl --user enable sio-autoresearch.timer
systemctl --user start sio-autoresearch.timer

echo "[sio-autoresearch] Timer installed and started."
systemctl --user status sio-autoresearch.timer --no-pager || true
