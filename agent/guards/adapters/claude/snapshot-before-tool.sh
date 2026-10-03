#!/bin/bash
# Claude Code adapter for skyguard's pre-command snapshot. The logic lives in tools/skyguard/snapshot.py;
# this wrapper reads the payload, records the heartbeat and runs the engine.
#
# SETUP: setup.sh fills in the Python path below (it writes sys.executable of the Python it found).
#
# This hook never blocks. It is the record that makes a bad command recoverable, so a failure here must
# be LOUD in the audit log rather than a silent no-op.
#
# A bare `cat`, never `cat /dev/stdin`: the latter returns ZERO BYTES when Claude Code (Node) spawns a
# hook, which made every hook here inert for five weeks. tools/hook-canary.sh reads the heartbeat.
#
# Hot path = bash BUILTINS ONLY: a fork costs 10-70 ms on Git Bash (MEASURED 2026-10-02). See
# protect-bash.sh for the full note; the location/heartbeat block below is the same in every wrapper.

HOOK_NAME="snapshot-before-tool"
PY="{{PYTHON_PATH}}"
T0="${EPOCHREALTIME:-}"; T0="${T0/,/.}"; [ -n "$T0" ] || printf -v T0 '%(%s)T' -1
INV="$$-$RANDOM"
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
printf '%s %s\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
if [ -n "$INPUT" ]; then
    printf '%s %s bytes=%s\n' "$T0" "$INV" "${#INPUT}" > "$HB_DIR/$HOOK_NAME.received" 2>/dev/null
else
    NOPAY=1
    printf '%s %s receipt NO PAYLOAD\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null
fi

if ! command -v "$PY" >/dev/null 2>&1; then
    _fail python "cannot run Python"
    printf '[%s] %s FAILED: Python not runnable (%s) -- NO backup/snapshot was taken\n' \
        "$NOW" "$HOOK_NAME" "$(printf '%s' "$PY" | tr '\134' '/')" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi


printf '%s' "$INPUT" | "$PY" "${ROOT_WIN:-$ROOT}/tools/skyguard/run.py" claude "$HOOK_NAME" \
    --root "$ROOT" --root-win "$ROOT_WIN" --project "$PROJECT_DIR" --invocation "$INV" --telemetry "$HB_DIR" >/dev/null 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    _fail engine "exit $RC"
    printf '[%s] %s FAILED: engine error (exit %s) -- NO backup/snapshot was taken\n' \
        "$NOW" "$HOOK_NAME" "$RC" >> "$AUDIT_LOG" 2>/dev/null
else
    _done
fi
exit 0
