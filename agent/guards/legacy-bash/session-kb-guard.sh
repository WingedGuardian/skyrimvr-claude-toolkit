#!/bin/bash
# SessionStart: snapshot the files nothing else can rebuild, and shout if one shrank.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# Deliberately THIN. The work lives in tools/kb-guard.sh, which explains itself and can
# be run by hand at any time (`bash tools/kb-guard.sh --verbose`). A hook that carries
# its own logic can only be tested by firing the hook; a hook that delegates can be
# tested where the logic is.
#
# It can NEVER fail the session. SessionStart output is injected into Claude's context,
# so a broken guard here would poison every session it ran in. Every failure path below
# prints what was NOT checked and exits 0 -- an unchecked install must not be reported
# as a checked one, and it must not be reported by refusing to start either.

HOOK_NAME="session-kb-guard"
JQ="{{JQ_PATH}}"

# `cat /dev/stdin` returns ZERO BYTES in the Claude Code hook environment while a bare
# `cat` returns the payload -- fd 0 is a Win32 pipe from a Node parent, which
# /proc/self/fd/0 cannot resolve. Every hook in this toolkit once had that defect and
# was inert for five weeks. See tools/hook-canary.sh for the measurement.
INPUT=$(cat)

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat"

mkdir -p "$HB_DIR" 2>/dev/null

if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/$HOOK_NAME" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/$HOOK_NAME.blind" 2>/dev/null
fi

# `compact` is the same session continuing, not a new one. The snapshot was already
# taken at its start, and re-announcing it would spend context on a non-event. Any
# other source -- startup, resume, clear -- gets the check. Without jq the source is
# unknown, and an unknown source is checked rather than skipped.
SOURCE=""
if "$JQ" --version >/dev/null 2>&1 && [ -n "$INPUT" ]; then
    SOURCE=$(printf '%s' "$INPUT" | "$JQ" -r '.source // empty' 2>/dev/null)
fi
[ "$SOURCE" = "compact" ] && exit 0

TOOL="$PROJECT_DIR/tools/kb-guard.sh"
if [ ! -f "$TOOL" ]; then
    echo "KB GUARD: NOT RUN -- $TOOL is missing, so your knowledgebase was NOT snapshotted this session."
    printf '[%s] %s NOT RUN: tools/kb-guard.sh missing\n' \
        "$(date +%Y%m%d_%H%M%S)" "$HOOK_NAME" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi

OUTPUT=$(bash "$TOOL" 2>&1)
RC=$?

[ -n "$OUTPUT" ] && printf '%s\n' "$OUTPUT"
if [ "$RC" -eq 2 ]; then
    echo "KB GUARD: the above means NOTHING was snapshotted this session -- unchecked, not clean."
fi

printf '[%s] %s rc=%s %s\n' "$(date +%Y%m%d_%H%M%S)" "$HOOK_NAME" "$RC" \
    "$(printf '%s' "$OUTPUT" | tr '\n' ' ' | head -c 200)" >> "$AUDIT_LOG" 2>/dev/null

exit 0
