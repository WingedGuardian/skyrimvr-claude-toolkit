#!/bin/bash
# Claude Code adapter for skyguard's SessionStart knowledgebase guard. The logic lives in
# tools/skyguard/session.py (which runs tools/kb-guard.sh); this wrapper reads the payload, records
# the heartbeat and prints the engine's text.
#
# SETUP: setup.sh fills in the Python path below (it writes sys.executable of the Python it found).
#
# It can NEVER fail the session. SessionStart output is injected into Claude's context, so a broken
# guard here would poison every session it ran in. Every failure path prints what was NOT checked and
# exits 0 -- an unchecked install must not be reported as a checked one, and it must not be reported
# by refusing to start either.
#
# A bare `cat`, never `cat /dev/stdin`: the latter returns ZERO BYTES when Claude Code (Node) spawns a
# hook, which made every hook here inert for five weeks. tools/hook-canary.sh reads the heartbeat.
#
# Hot path = bash BUILTINS ONLY: a fork costs 10-70 ms on Git Bash (MEASURED 2026-10-02). See
# protect-bash.sh for the full note; the location/heartbeat block below is the same in every wrapper.

HOOK_NAME="session-kb-guard"
PY="{{PYTHON_PATH}}"
T0="${EPOCHREALTIME:-}"; T0="${T0/,/.}"; [ -n "$T0" ] || printf -v T0 '%(%s)T' -1
NOPAY=""
INPUT=$(cat)

_SELF="${BASH_SOURCE[0]//\\//}"
case "$_SELF" in */*) HOOK_DIR="${_SELF%/*}" ;; *) HOOK_DIR="." ;; esac
_CWD="$PWD"
ROOT=""
CDPATH= cd -- "$HOOK_DIR/../.." >/dev/null 2>&1 && ROOT="$PWD"
CDPATH= cd -- "$_CWD" >/dev/null 2>&1
ROOT_WIN=""
case "$OSTYPE:$ROOT" in
    msys*:/[a-zA-Z]/*) ROOT_WIN="${ROOT:1:1}"; ROOT_WIN="${ROOT_WIN^}:${ROOT:2}" ;;
    msys*:/[a-zA-Z])   ROOT_WIN="${ROOT:1:1}"; ROOT_WIN="${ROOT_WIN^}:/" ;;
    msys*:/*)          ROOT_WIN="$(CDPATH= cd -- "$ROOT" 2>/dev/null && pwd -W 2>/dev/null)" ;;
esac
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$ROOT}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat/claude"
[ -d "$HB_DIR" ] || mkdir -p "$HB_DIR" 2>/dev/null
printf -v NOW '%(%Y%m%d_%H%M%S)T' -1

# Stage records (see tools/skyguard/health.py): invoked -> received -> evaluated (engine) -> emitted, and
# `failed` from any stage. `_done` also writes the latency line; a payload-less call keeps a `failed`
# record NEWER than its own evaluation, so one empty payload cannot clear itself.
_stamp() { T1="${EPOCHREALTIME:-$T0}"; T1="${T1/,/.}"; }
_fail() { _stamp; printf '%s %s %s %s\n' "$T1" "$INV" "$1" "$2" > "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null; }
_done() {
    _stamp
    printf '%s %s ok\n' "$T1" "$INV" > "$HB_DIR/$HOOK_NAME.emitted" 2>/dev/null
    printf '%s %s %s\n' "$INV" "$T0" "$T1" >> "$HB_DIR/$HOOK_NAME.latency" 2>/dev/null
    [ -z "$NOPAY" ] || _fail receipt "NO PAYLOAD"
}
# Invocation sequence and killed-run detection, from the PREVIOUS records, with the `read` builtin only.
# The id is `<seq>.<pid>-<rand>`: health.py orders failures against completions by <seq>, so a clock that
# steps back cannot hide a failure. A previous `invoked` that never emitted, never recorded a failure, and
# is older than the grace below was killed (the agent's timeout); that is recorded under THIS id, so this
# call's own completion is not "newer" and the report stays DEGRADED until the next one.
OT=""; OINV=""; EINV=""; FINV=""
[ -r "$HB_DIR/$HOOK_NAME.invoked" ] && read -r OT OINV < "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
[ -r "$HB_DIR/$HOOK_NAME.emitted" ] && read -r _ EINV _ < "$HB_DIR/$HOOK_NAME.emitted" 2>/dev/null
[ -r "$HB_DIR/$HOOK_NAME.failed" ] && read -r _ FINV _ < "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null
# a record rewritten by a Windows-native writer may end in CR; a CR would make every id look different
OINV="${OINV%$'\r'}"; EINV="${EINV%$'\r'}"; FINV="${FINV%$'\r'}"
SEQ="${OINV%%.*}"; case "$SEQ" in ''|*[!0-9]*) SEQ=0 ;; esac
INV="$((SEQ + 1)).$$-$RANDOM"
O_S="${OT%%.*}"; T_S="${T0%%.*}"
case "$O_S$T_S" in
    ''|*[!0-9]*) ;;
    *) if [ -n "$OINV" ] && [ "$OINV" != "$EINV" ] && [ "$OINV" != "$FINV" ] && [ $((T_S - O_S)) -gt 60 ]; then
           _fail killed "run $OINV invoked at $OT never finished"
       fi ;;
esac
printf '%s %s\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
if [ -n "$INPUT" ]; then
    printf '%s %s bytes=%s\n' "$T0" "$INV" "${#INPUT}" > "$HB_DIR/$HOOK_NAME.received" 2>/dev/null
else
    NOPAY=1
    printf '%s %s receipt NO PAYLOAD\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null
fi

# PY-CHAIN-BEGIN
# Interpreter fallback chain (identical in every wrapper). The configured $PY is the only thing the hot
# path touches; everything below runs ONLY once it has failed, so it adds no fork to a healthy call.
# Order: py -3 / python / python3 that resolve and are not a Store alias, then the usual install
# locations, then a Store alias, last. Every candidate is capability-probed (Python >= 3.8, the engine's
# floor). Multi-word candidates are arrays/word-split, never eval'd. A fallback that is taken writes a
# `failed` record BEFORE the engine runs, so the health report says DEGRADED until a call through the
# configured interpreter completes again.
PYC=("$PY")
py_ok() { "$@" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1; }
py_use() { PYC=("$@"); _fail python "configured $PY unusable; used $* -- re-run setup.sh"; return 0; }
py_fallback() {
    local c p g PY_ALIAS=""
    for c in "py -3" python python3; do
        p=$(command -v ${c%% *} 2>/dev/null) || continue
        case "$p" in
            */[Ww]indows[Aa]pps/*) [ -z "$PY_ALIAS" ] && PY_ALIAS="$c"; continue ;;
        esac
        if py_ok $c; then py_use $c; return 0; fi
    done
    for g in "${LOCALAPPDATA//\\//}"/Programs/Python/Python3*/python.exe /c/Python3*/python.exe \
             "${PROGRAMFILES//\\//}"/Python3*/python.exe /usr/bin/python3 /usr/local/bin/python3; do
        if [ -x "$g" ] && py_ok "$g"; then py_use "$g"; return 0; fi
    done
    if [ -n "$PY_ALIAS" ] && py_ok $PY_ALIAS; then py_use $PY_ALIAS; return 0; fi
    return 1
}
# PY-CHAIN-END

if ! command -v "$PY" >/dev/null 2>&1 && ! py_fallback; then
    _fail python "cannot run Python"
    echo "KB GUARD: NOT RUN -- Python is not runnable ($(printf '%s' "$PY" | tr '\134' '/')), so your knowledgebase was NOT snapshotted this session."
    printf '[%s] %s FAILED: Python not runnable (%s) -- knowledgebase NOT snapshotted\n' \
        "$NOW" "$HOOK_NAME" "$(printf '%s' "$PY" | tr '\134' '/')" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi


run_engine() { printf '%s' "$INPUT" | "${PYC[@]}" "${ROOT_WIN:-$ROOT}/tools/skyguard/run.py" claude "$HOOK_NAME" \
      --root "$ROOT" --root-win "$ROOT_WIN" --project "$PROJECT_DIR" --invocation "$INV" --telemetry "$HB_DIR" --bash "$BASH" 2>/dev/null; }
OUT=$(run_engine)
RC=$?
if [ "$RC" -ne 0 ] && ! py_ok "${PYC[@]}" && py_fallback; then
    OUT=$(run_engine); RC=$?
fi
if [ "$RC" -ne 0 ]; then
    _fail engine "exit $RC"
    echo "KB GUARD: NOT RUN -- the engine failed (exit $RC), so your knowledgebase was NOT snapshotted this session."
    printf '[%s] %s FAILED: engine error (exit %s) -- knowledgebase NOT snapshotted\n' \
        "$NOW" "$HOOK_NAME" "$RC" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi
[ -n "$OUT" ] && printf '%s\n' "$OUT"
_done
exit 0
