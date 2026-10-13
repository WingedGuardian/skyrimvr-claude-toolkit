#!/bin/bash
# vr-headless.sh -- boot Skyrim VR with no headset (SteamVR's null driver) so DevBench can drive the game.
# The logic, and everything it changes and restores, is documented in tools/vr-headless.py.
#
#   bash tools/vr-headless.sh status
#   bash tools/vr-headless.sh on [--force]
#   bash tools/vr-headless.sh off              # byte-exact restore; safe to repeat, safe after a crash
#   bash tools/vr-headless.sh emu on|off       # only after: python tools/vr-emu-driver/build.py
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# py -3 and python first: on Windows a bare python3 is often the Microsoft Store stub.
PY=""
for c in "py -3" "python" "python3"; do
  if $c -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "ERROR: Python 3 not found (tried py -3, python, python3). Install Python 3 and ensure it is on PATH." >&2
  exit 2
fi
exec $PY "$HERE/vr-headless.py" "$@"
