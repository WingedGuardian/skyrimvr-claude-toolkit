#!/bin/bash
# skyguard.sh -- the entry for the skyguard CLI:  bash tools/skyguard.sh lock status
# (tools/skyguard is the package folder, so the executable wrapper is skyguard.sh; skyguard.cmd is the twin.)
# Python: $SKYGUARD_PYTHON, else the SKYGUARD_PYTHON setup.sh wrote into skyrim-paths.env, else the first of
# python3 / python / py -3 that really runs (a Store alias does not).
src="${BASH_SOURCE[0]//\\//}"          # a Windows caller may pass C:\...\tools\skyguard.sh: split on either slash
here="${src%/*}"
[ "$here" = "$src" ] && here="."
# No $SKYGUARD_PYTHON: the interpreter setup.sh recorded in <toolkit root>/skyrim-paths.env (Plan 8 T7), read as
# data -- only the exact SKYGUARD_PYTHON='...' line setup writes, never sourced -- and used only if it exists.
file_py=""
if [ -z "${SKYGUARD_PYTHON:-}" ] && [ -f "$here/../skyrim-paths.env" ]; then
    line=$(grep -m1 "^SKYGUARD_PYTHON='" "$here/../skyrim-paths.env" 2>/dev/null); line="${line%$'\r'}"
    case "$line" in
        "SKYGUARD_PYTHON='"*"'") v="${line#SKYGUARD_PYTHON=\'}"; v="${v%\'}"; v="${v//\'\\\'\'/\'}"
                                 [ -n "$v" ] && [ -e "$v" ] && file_py="$v" ;;
    esac
fi
for cand in "${SKYGUARD_PYTHON:-}" "$file_py" python3 python "py -3"; do
    [ -n "$cand" ] || continue
    # An existing path is ONE word, spaces and all (C:/Program Files/...); anything else (`py -3`) is split into words.
    if [ -e "$cand" ]; then run=("$cand"); else IFS=' ' read -r -a run <<< "$cand"; fi
    if "${run[@]}" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1; then
        exec "${run[@]}" -B "$here/skyguard" "$@"
    fi
done
echo "skyguard: no working Python 3.8+ found; for the lock, run tools/skyguard-unlock.cmd" >&2
exit 2
