#!/usr/bin/env bash
# Regenerate docs/CLI_REFERENCE.md from `sio --help` + every subcommand's --help.
# Usage: docs/gen_cli_reference.sh   (run from repo root)
#
# The output is committed to a PUBLIC repo, so every --help capture goes through
# `scrub`: local wrapper banners (lines starting "[sio-wrapper]", which printed a
# LAN Ollama address into 62 sections before 0.5.2) are dropped, $HOME is
# rewritten to ~, and the run FAILS if a private IPv4 address or the local
# username survives. Prefer a plain console script: SIO_BIN=.venv/bin/sio.
set -euo pipefail
SIO="${SIO_BIN:-sio}"
OUT="docs/CLI_REFERENCE.md"
HOME_RE=$(printf '%s' "$HOME" | sed 's/[][\.*^$/]/\\&/g')

scrub() {
  grep -v '^\[sio-wrapper\]' \
    | sed -e "s/${HOME_RE}/~/g" -e 's/python -m sio/sio/g'
}

{
  echo "# SIO CLI Reference"; echo
  echo "> Auto-generated from \`sio --help\` and each subcommand's \`--help\`."
  echo "> Regenerate with \`docs/gen_cli_reference.sh\`. SIO version:$("$SIO" --version 2>/dev/null | scrub | sed 's/^/ /')"
  echo; echo '## Top-level'; echo; echo '```'; "$SIO" --help 2>&1 | scrub; echo '```'; echo
  echo '## Commands'; echo
} > "$OUT"
cmds=$("$SIO" --help 2>&1 | scrub | awk '/^Commands:/{f=1;next} f&&/^  [a-z]/{print $1}')
for c in $cmds; do
  { echo "### \`sio $c\`"; echo; echo '```'; "$SIO" "$c" --help 2>&1 | scrub; echo '```'; echo; } >> "$OUT"
done

# Leak gate: refuse to leave a file that names this machine.
leaks=$(grep -nE '\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+' "$OUT" || true)
user=$(id -un)
leaks="$leaks$(grep -nw -- "$user" "$OUT" || true)"
if [ -n "$leaks" ]; then
  echo "REFUSING: $OUT still contains local addresses or the username '$user':" >&2
  echo "$leaks" | head -20 >&2
  exit 1
fi
echo "generated $OUT ($(wc -l < "$OUT") lines, $(echo "$cmds" | wc -w) subcommands)"
