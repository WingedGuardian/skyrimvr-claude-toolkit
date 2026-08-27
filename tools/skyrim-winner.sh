#!/usr/bin/env bash
# Wrapper for skyrim-winner.js that turns its output into a trustworthy exit code.
#
# Why this exists: every koffi/xelib Node helper here exits non-zero (often 127,
# sometimes with a stray "Failed to sync '<stdout>': Incorrect function") under
# Git Bash even on complete success -- node's stdio flush fails during shutdown
# while XEditLib.dll is still resident. It is not reachable from JS. So the
# script prints a RESULT: line and this wrapper judges THAT, never $?.
# See CLAUDE.md #12.
#
# Usage:
#   bash tools/skyrim-winner.sh winner    <formid> [plugin]
#   bash tools/skyrim-winner.sh chain     <formid> [plugin]
#   bash tools/skyrim-winner.sh conflicts <plugin>

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JS="$HERE/skyrim-winner.js"

command -v node >/dev/null 2>&1 || { echo "ERROR: node not found." >&2; exit 1; }
[ -f "$JS" ] || { echo "ERROR: $JS not found." >&2; exit 1; }

if [ $# -eq 0 ]; then
    sed -n '11,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

out="$(node "$JS" "$@" 2>&1)"

# Print everything except the machine-readable marker.
printf '%s\n' "$out" | grep -v '^RESULT:'

result="$(printf '%s\n' "$out" | grep '^RESULT:' | tail -1)"
case "$result" in
    "RESULT: OK")  exit 0 ;;
    RESULT:*)      printf '%s\n' "$result" >&2; exit 2 ;;
    *)
        # No marker at all: the script died before finishing. That is a real
        # failure and must not be reported as success just because the output
        # looked plausible.
        echo "ERROR: skyrim-winner produced no RESULT line -- it did not complete." >&2
        exit 3
        ;;
esac
