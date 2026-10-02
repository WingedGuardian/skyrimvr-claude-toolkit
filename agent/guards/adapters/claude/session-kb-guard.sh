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

HOOK_NAME="session-kb-guard"
PY="{{PYTHON_PATH}}"
INPUT=$(cat)

_SELF="${BASH_SOURCE[0]//\//}"
HOOK_DIR="$(cd "$(dirname "$_SELF")" 2>/dev/null && pwd)"
ROOT="$(cd "$HOOK_DIR/../.." 2>/dev/null && pwd)"
ROOT_WIN="$(cd "$HOOK_DIR/../.." 2>/dev/null && pwd -W 2>/dev/null)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$ROOT}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat"
mkdir -p "$HB_DIR" 2>/dev/null

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "KB GUARD: NOT RUN -- Python is not runnable ($(printf '%s' "$PY" | tr '\134' '/')), so your knowledgebase was NOT snapshotted this session."
    printf '[%s] %s FAILED: Python not runnable (%s) -- knowledgebase NOT snapshotted\n' \
        "$(date +%Y%m%d_%H%M%S)" "$HOOK_NAME" "$(printf '%s' "$PY" | tr '\134' '/')" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi

if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/$HOOK_NAME" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/$HOOK_NAME.blind" 2>/dev/null
fi

OUT=$(printf '%s' "$INPUT" | "$PY" "${ROOT_WIN:-$ROOT}/tools/skyguard/run.py" claude "$HOOK_NAME" \
      --root "$ROOT" --root-win "$ROOT_WIN" --project "$PROJECT_DIR" --bash "$BASH" 2>/dev/null)
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "KB GUARD: NOT RUN -- the engine failed (exit $RC), so your knowledgebase was NOT snapshotted this session."
    printf '[%s] %s FAILED: engine error (exit %s) -- knowledgebase NOT snapshotted\n' \
        "$(date +%Y%m%d_%H%M%S)" "$HOOK_NAME" "$RC" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi
[ -n "$OUT" ] && printf '%s\n' "$OUT"
exit 0
