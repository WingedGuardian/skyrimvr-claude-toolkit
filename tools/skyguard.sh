#!/bin/bash
# skyguard.sh -- the entry for the skyguard CLI:  bash tools/skyguard.sh lock status
# (tools/skyguard is the package folder, so the executable wrapper is skyguard.sh; skyguard.cmd is the twin.)
# Python: $SKYGUARD_PYTHON, else the first of python3 / python / py -3 that really runs (a Store alias does not).
src="${BASH_SOURCE[0]//\\//}"          # a Windows caller may pass C:\...\tools\skyguard.sh: split on either slash
here="${src%/*}"
[ "$here" = "$src" ] && here="."
for cand in "${SKYGUARD_PYTHON:-}" python3 python "py -3"; do
    [ -n "$cand" ] || continue
    # An existing path is ONE word, spaces and all (C:/Program Files/...); anything else (`py -3`) is split into words.
    if [ -e "$cand" ]; then run=("$cand"); else IFS=' ' read -r -a run <<< "$cand"; fi
    if "${run[@]}" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1; then
        exec "${run[@]}" -B "$here/skyguard" "$@"
    fi
done
echo "skyguard: no working Python 3.8+ found; for the lock, run tools/skyguard-unlock.cmd" >&2
exit 2
