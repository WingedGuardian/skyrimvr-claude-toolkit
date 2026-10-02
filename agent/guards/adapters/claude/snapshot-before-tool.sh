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
HB_DIR="$BACKUP_DIR/.hook-heartbeat"
[ -d "$HB_DIR" ] || mkdir -p "$HB_DIR" 2>/dev/null
printf -v NOW '%(%Y%m%d_%H%M%S)T' -1

if ! command -v "$PY" >/dev/null 2>&1; then
    printf '[%s] %s FAILED: Python not runnable (%s) -- NO backup/snapshot was taken\n' \
        "$NOW" "$HOOK_NAME" "$(printf '%s' "$PY" | tr '\134' '/')" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi

if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$NOW" "${#INPUT}" > "$HB_DIR/$HOOK_NAME" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$NOW" > "$HB_DIR/$HOOK_NAME.blind" 2>/dev/null
fi

printf '%s' "$INPUT" | "$PY" "${ROOT_WIN:-$ROOT}/tools/skyguard/run.py" claude "$HOOK_NAME" \
    --root "$ROOT" --root-win "$ROOT_WIN" --project "$PROJECT_DIR" >/dev/null 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    printf '[%s] %s FAILED: engine error (exit %s) -- NO backup/snapshot was taken\n' \
        "$NOW" "$HOOK_NAME" "$RC" >> "$AUDIT_LOG" 2>/dev/null
fi
exit 0
