#!/usr/bin/env bash
# Regenerate docs/CLI_REFERENCE.md from `sio --help` + every subcommand's --help.
# Usage: docs/gen_cli_reference.sh   (run from repo root)
set -euo pipefail
SIO="${SIO_BIN:-sio}"
OUT="docs/CLI_REFERENCE.md"
{
  echo "# SIO CLI Reference"; echo
  echo "> Auto-generated from \`sio --help\` and each subcommand's \`--help\`."
  echo "> Regenerate with \`docs/gen_cli_reference.sh\`. SIO version:$("$SIO" --version 2>/dev/null | sed 's/^/ /')"
  echo; echo '## Top-level'; echo; echo '```'; "$SIO" --help 2>&1; echo '```'; echo
  echo '## Commands'; echo
} > "$OUT"
cmds=$("$SIO" --help 2>&1 | awk '/^Commands:/{f=1;next} f&&/^  [a-z]/{print $1}')
for c in $cmds; do
  { echo "### \`sio $c\`"; echo; echo '```'; "$SIO" "$c" --help 2>&1; echo '```'; echo; } >> "$OUT"
done
echo "generated $OUT ($(wc -l < "$OUT") lines, $(echo "$cmds" | wc -w) subcommands)"
