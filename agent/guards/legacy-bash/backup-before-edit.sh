#!/bin/bash
# Back up any file before it is edited, and record it in the audit trail.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# This hook never blocks. It is the record that makes a bad edit recoverable, so a
# failure here must be LOUD in the audit log rather than a silent no-op.

HOOK_NAME="backup-before-edit"
JQ="{{JQ_PATH}}"

# MEASURED (X4 toolkit 2026-08-29, reproduced on a second machine 2026-09-06):
# `cat /dev/stdin` returns ZERO BYTES in the Claude Code hook environment, while a
# bare `cat` returns the payload. This hook is the reason it mattered most: reading
# nothing meant FILE_PATH was empty, the guard below exited 0, and nothing was ever
# backed up -- while AUDIT_LOG.txt filled with 2,454 entries whose command field was
# blank and nothing looked wrong.
#
# WHY NO TEST CAUGHT IT, precisely. /dev/stdin is a symlink to /proc/self/fd/0:
# it resolves when fd 0 is a real file or an MSYS-shell pipe, and FAILS when fd 0
# is a Win32 pipe from a non-MSYS parent -- which is how Claude Code (Node) spawns
# a hook. Python's subprocess does the same, so tests/test_hooks.py DOES detect
# this on Windows and does NOT on Linux. The real reason it survived is simpler:
# nothing ran the hooks as processes at all.
INPUT=$(cat)

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat"

mkdir -p "$HB_DIR" 2>/dev/null

# Without jq this hook cannot parse the payload. It must not block -- that is not
# its job -- but it must not imply it did its work either.
if ! "$JQ" --version >/dev/null 2>&1; then
    printf '[%s] %s FAILED: jq not runnable (%s) -- NO backup/snapshot was taken\n' \
        "$(date +%Y%m%d_%H%M%S)" "$HOOK_NAME" "$JQ" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi
if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/backup-before-edit" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/backup-before-edit.blind" 2>/dev/null
fi

hooklog() { printf '[%s] backup-before-edit %s: %s\n' "$(date +%Y%m%d_%H%M%S)" "$1" "$2" >> "$AUDIT_LOG" 2>/dev/null; }

TOOL_NAME=$(echo "$INPUT" | "$JQ" -r '.tool_name // "unknown"')
FILE_PATH=$(echo "$INPUT" | "$JQ" -r '.tool_input.file_path // empty')

# A backup hook that reads nothing is indistinguishable from one with nothing to do.
# It cannot refuse the call -- that is not this hook's job -- but it must not pass
# in silence either, because silence is what hid the four-month outage.
if [ -z "$INPUT" ]; then
    hooklog BLIND "received an EMPTY payload -- no backup was taken for this call"
    exit 0
fi

[ -z "$FILE_PATH" ] && exit 0
[ ! -f "$FILE_PATH" ] && exit 0

# Skip only our own transient workspace. Note .claude/backups is skipped for the
# obvious reason: backing up the backups grows without bound.
echo "$FILE_PATH" | grep -qiE '[/\\]\.claude[/\\](backups|hooks|plans)[/\\]' && exit 0
echo "$FILE_PATH" | grep -qiE '[/\\]node_modules[/\\]' && exit 0

mkdir -p "$BACKUP_DIR" 2>/dev/null

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
# Flatten the path into a filename: / \ and : all become _
SAFE_NAME=$(echo "$FILE_PATH" | sed 's|[/\\:]|_|g' | sed 's|^_*||')
BACKUP_PATH="$BACKUP_DIR/${TIMESTAMP}__${SAFE_NAME}"

if cp "$FILE_PATH" "$BACKUP_PATH" 2>/dev/null; then
    printf '[%s] %s -> %s (backup: %s__%s)\n' \
        "$TIMESTAMP" "$TOOL_NAME" "$FILE_PATH" "$TIMESTAMP" "$SAFE_NAME" >> "$AUDIT_LOG"
else
    # The copy failing is exactly when the record matters, so say so rather than
    # letting the audit log imply a backup exists.
    hooklog FAILED "could not copy $FILE_PATH -- NO BACKUP EXISTS for this edit"
fi

exit 0
