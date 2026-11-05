#!/bin/bash
# UserPromptSubmit: the session charter (records a session's first prompt as its origin) and the per-prompt
# ledger line, straight through the shared core (tools/modding_harness/charter.py). SessionStart prints the
# whole charter and open ledger through skyguard (tools/skyguard/session.py, with_charter).
#
# NOT a guard, and deliberately outside skyguard and .claude/hooks/: it decides nothing, it only adds context,
# so it carries no heartbeat, no interpreter fallback chain and no fail-closed path. A crash prints nothing and an
# unreadable ledger prints one line, and both exit 0; a session without its ledger line is still a working session. Routing it through the skyguard
# engine would cost every prompt what a guarded Bash call costs (MEASURED on a Windows install: 3.1 s mean
# over 10 calls).
#
# Hot path = bash BUILTINS ONLY (a fork costs 10-70 ms on Git Bash; see .claude/hooks/protect-bash.sh). Once a
# session's charter exists and it has no ledger, there is nothing to record or print, so it returns without
# starting Python. The interpreter is setup.sh's, read as data from skyrim-paths.env (never sourced).

# The read builtin reads a pipe a byte per syscall, but still beats a `cat` fork on Git Bash for any payload up to
# 50 KB (MEASURED on a Windows install, 8 runs each: 300 B 192 vs 304 ms, 50 KB 266 vs 285 ms, bash start ~195 ms).
IFS= read -r -d '' INPUT
SID_RE='"session_id"[[:space:]]*:[[:space:]]*"([A-Za-z0-9_-][A-Za-z0-9._-]{0,127})"'
[[ $INPUT =~ $SID_RE ]] || exit 0
SID="${BASH_REMATCH[1]}"

_SELF="${BASH_SOURCE[0]//\\//}"
case "$_SELF" in */*) TOOLS_DIR="${_SELF%/*}" ;; *) TOOLS_DIR="." ;; esac
_CWD="$PWD"
ROOT=""
CDPATH= cd -- "$TOOLS_DIR/.." >/dev/null 2>&1 && ROOT="$PWD"
CDPATH= cd -- "$_CWD" >/dev/null 2>&1
[ -n "$ROOT" ] || exit 0

[ -f "$ROOT/.agent-sessions/$SID.md" ] && [ ! -e "$ROOT/.agent-sessions/$SID.ledger.jsonl" ] && exit 0

PY=""
for _PATHS in "$ROOT/.claude/skyrim-paths.env" "$ROOT/skyrim-paths.env"; do     # the 3.x location first, so the root file wins
    [ -r "$_PATHS" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in SKYGUARD_PYTHON=*) PY="${line#SKYGUARD_PYTHON=}" ;; esac
    done < "$_PATHS"
done
case "$PY" in \'*\') PY="${PY:1:${#PY}-2}"; PY="${PY//\'\\\'\'/\'}" ;; esac
[ -n "$PY" ] || exit 0

# A native Windows Python needs the Windows spelling of the install (same conversion as the guard wrappers).
WS="$ROOT"
_MSYS=""
case "$OSTYPE" in msys*) _MSYS=1 ;; cygwin*) [ -e /usr/bin/msys-2.0.dll ] && _MSYS=1 ;; esac
case "$_MSYS:$ROOT" in
    1:/[a-zA-Z]/*) WS="${ROOT:1:1}"; WS="${WS^}:${ROOT:2}" ;;
    1:/[a-zA-Z])   WS="${ROOT:1:1}"; WS="${WS^}:/" ;;
    1:/*)          WS="$(CDPATH= cd -- "$ROOT" 2>/dev/null && pwd -W 2>/dev/null)" ;;
esac
[ -n "$WS" ] || exit 0

"$PY" -B "$WS/tools/modding_harness/charter.py" prompt --workspace "$WS" 2>/dev/null <<<"$INPUT"
exit 0
