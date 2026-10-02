#!/usr/bin/env bash
# Preserve the Spriggit ModKey basename, validate artifacts, then replace output transactionally.
set -euo pipefail
# Pure-bash dirname (no external tools before Python is located); accepts either separator.
case "${BASH_SOURCE[0]}" in *[/\\]*) SCRIPT_DIR="${BASH_SOURCE[0]%[/\\]*}";; *) SCRIPT_DIR=.;; esac
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && pwd)"
# The Microsoft Store "App Execution Alias" (...\WindowsApps\python.exe) is not Python: it opens
# the Store and exits 9009. It often precedes a real install on PATH, so skip it explicitly.
PY=(); STORE_STUB=""
if [ -n "${SKYRIM_PYTHON:-}" ]; then PY=("$SKYRIM_PYTHON")
else
    for name in python python3; do
        while IFS= read -r candidate; do
            case "$candidate" in
                *[/\\][Ww][Ii][Nn][Dd][Oo][Ww][Ss][Aa][Pp][Pp][Ss][/\\]*) STORE_STUB="$candidate";;
                *) PY=("$candidate"); break 2;;
            esac
        done < <(type -aP "$name" 2>/dev/null || true)
    done
    if [ "${#PY[@]}" -eq 0 ] && type -P py >/dev/null 2>&1; then PY=(py -3); fi
fi
if [ "${#PY[@]}" -eq 0 ]; then
    if [ -n "$STORE_STUB" ]; then
        echo "[spriggit-cli] Python 3 is required for validated publication; the only python on PATH is the Microsoft Store stub ($STORE_STUB). Install Python 3 or set SKYRIM_PYTHON." >&2
    else
        echo '[spriggit-cli] Python 3 is required for validated publication' >&2
    fi
    exit 1
fi
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
# A global tool install puts `spriggit` on PATH (Windows); the dev container restores it as a
# LOCAL tool from .config/dotnet-tools.json, which is only reachable via `dotnet tool run`.
if type -P spriggit >/dev/null 2>&1; then SPRIGGIT=(spriggit)
elif type -P dotnet >/dev/null 2>&1; then SPRIGGIT=(dotnet tool run spriggit --)
else echo '[spriggit-cli] spriggit not found: install the Spriggit.CLI dotnet tool' >&2; exit 1; fi
if [ -z "$REAL_IN" ] && [ -z "$REAL_OUT" ]; then exec "${SPRIGGIT[@]}" "$@"; fi
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
"${SPRIGGIT[@]}" "${ARGS[@]}"
"${PY[@]}" "$SCRIPT_DIR/spriggit_publish.py" publish "$NEW_OUT" "$REAL_OUT"
echo "[spriggit-cli] result -> $REAL_OUT" >&2
