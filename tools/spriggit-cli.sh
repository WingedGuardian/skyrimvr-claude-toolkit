#!/usr/bin/env bash
# Preserve the Spriggit ModKey basename, validate artifacts, then replace output transactionally.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=()
if [ -n "${SKYRIM_PYTHON:-}" ]; then PY=("$SKYRIM_PYTHON")
elif command -v python >/dev/null 2>&1; then PY=(python)
elif command -v python3 >/dev/null 2>&1; then PY=(python3)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
else echo '[spriggit-cli] Python 3 is required for validated publication' >&2; exit 1; fi
REAL_IN=""; REAL_OUT=""; ARGS=(); argv=("$@"); i=0
while [ "$i" -lt "${#argv[@]}" ]; do
    a="${argv[$i]}"
    case "$a" in
        --InputPath|--OutputPath)
            [ "$((i+1))" -lt "${#argv[@]}" ] || { echo "Missing value for $a" >&2; exit 1; }
            p="${argv[$((i+1))]//\\//}"
            if [ "$a" = --InputPath ]; then REAL_IN="$p"; else REAL_OUT="$p"; fi
            i=$((i+2));;
        *) i=$((i+1));;
    esac
done
if [ -z "$REAL_IN" ] && [ -z "$REAL_OUT" ]; then exec spriggit "$@"; fi
[ -n "$REAL_IN" ] && [ -n "$REAL_OUT" ] || { echo 'InputPath and OutputPath are both required' >&2; exit 1; }
"${PY[@]}" "$SCRIPT_DIR/spriggit_publish.py" check "$REAL_IN" "$REAL_OUT"
DEFAULT_ROOT="${TMPDIR:-/tmp}/spriggit-work"
case "${OSTYPE:-}" in msys*|cygwin*) DEFAULT_ROOT='C:/Temp/spriggit-work';; esac
WORK_ROOT="${SPRIGGIT_WORK_ROOT:-$DEFAULT_ROOT}"
mkdir -p "$WORK_ROOT"
WORK=$(mktemp -d "$WORK_ROOT/run.XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
mkdir -p "$WORK/in" "$WORK/out"
NEW_IN="$WORK/in/$(basename "$REAL_IN")"; NEW_OUT="$WORK/out/$(basename "$REAL_OUT")"
if [ -d "$REAL_IN" ]; then cp -r -- "$REAL_IN" "$NEW_IN"; else cp -- "$REAL_IN" "$NEW_IN"; fi
i=0
while [ "$i" -lt "${#argv[@]}" ]; do
    a="${argv[$i]}"
    case "$a" in
        --InputPath) ARGS+=("$a" "$NEW_IN"); i=$((i+2));;
        --OutputPath) ARGS+=("$a" "$NEW_OUT"); i=$((i+2));;
        *) ARGS+=("$a"); i=$((i+1));;
    esac
done
echo "[spriggit-cli] workspace: $WORK" >&2
spriggit "${ARGS[@]}"
"${PY[@]}" "$SCRIPT_DIR/spriggit_publish.py" publish "$NEW_OUT" "$REAL_OUT"
echo "[spriggit-cli] result -> $REAL_OUT" >&2
