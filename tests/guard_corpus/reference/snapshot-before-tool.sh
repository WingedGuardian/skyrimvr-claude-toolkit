#!/bin/bash
# Snapshot .psc and .pex files before EVERY Bash command.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# Why before every command, rather than the risky ones. backup-before-edit.sh only
# fires for Edit/Write tool calls, so an external tool invoked through Bash bypasses
# it entirely -- AutoMod, PapyrusAssembler, Champollion, Caprica, spriggit, and
# whatever gets installed next. AutoMod's `papyrus decompile` was observed silently
# destroying its output when the decompile step failed, and it was missed precisely
# because it had been assumed read-only. Classifying commands as risky is the
# judgement that already failed once, so this does not attempt it. Scripts are
# small, the copy is fast, and a rate limit keeps bursts cheap.

HOOK_NAME="snapshot-before-tool"
JQ="{{JQ_PATH}}"

# MEASURED (X4 toolkit 2026-08-29, reproduced on a second machine 2026-09-06):
# `cat /dev/stdin` returns ZERO BYTES in the Claude Code hook environment, while a
# bare `cat` returns the payload. A hook that reads nothing falls through its first
# guard and exits 0 -- byte-identical to deciding "this is fine".
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
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/snapshot-before-tool" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/snapshot-before-tool.blind" 2>/dev/null
fi

COMMAND=$(echo "$INPUT" | "$JQ" -r '.tool_input.command // empty')

# An empty payload here means no snapshot was taken before whatever ran next. It
# cannot refuse the call, but it must not pass in silence -- silence is what let the
# stdin defect sit undetected for four months.
if [ -z "$INPUT" ]; then
    printf '[%s] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n' \
        "$(date +%Y%m%d_%H%M%S)" >> "$AUDIT_LOG" 2>/dev/null
    exit 0
fi

# Skip commands that obviously cannot write. A DENYLIST of things safe to skip, not
# an allowlist of things thought risky -- the allowlist is the judgement that failed.
echo "$COMMAND" | grep -qE '^\s*(ls|cat|head|tail|grep|find|wc|file|stat|pwd|echo|date|whoami|which|type|env|printenv)\s' && exit 0
echo "$COMMAND" | grep -qE '^\s*git\s+(status|log|diff|show|branch|remote|config\s+--get)' && exit 0

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SNAPSHOT_BASE="$BACKUP_DIR/auto_snapshots"
SOURCE_DIR="$PROJECT_DIR/Data/Scripts/Source"
DEPLOY_DIR="$PROJECT_DIR/Data/Scripts"
RATE_LIMIT_FILE="$SNAPSHOT_BASE/.last_snapshot"
mkdir -p "$SNAPSHOT_BASE" 2>/dev/null

# Nothing to snapshot on an install without Papyrus sources deployed.
[ -d "$SOURCE_DIR" ] || [ -d "$DEPLOY_DIR" ] || exit 0

# Rate limit: within 60s of the last snapshot, only take another if a .psc has been
# touched since -- so a burst of commands does not produce a burst of copies, but a
# destructive command firing repeatedly still gets a fresh snapshot each time.
NOW_EPOCH=$(date +%s)
if [ -f "$RATE_LIMIT_FILE" ]; then
    LAST_EPOCH=$(cat "$RATE_LIMIT_FILE" 2>/dev/null || echo 0)
    AGE=$((NOW_EPOCH - LAST_EPOCH))
    if [ "$AGE" -lt 60 ]; then
        NEWER=$(find "$SOURCE_DIR" -maxdepth 1 -name "*.psc" -newer "$RATE_LIMIT_FILE" -type f 2>/dev/null | head -1)
        [ -z "$NEWER" ] && exit 0
    fi
fi

SNAPSHOT_DIR="$SNAPSHOT_BASE/${TIMESTAMP}"
mkdir -p "$SNAPSHOT_DIR/source" "$SNAPSHOT_DIR/deploy" 2>/dev/null

# Only files touched in the last 7 days -- the active dev set. Copying every vanilla
# script on each command would make the hook slow enough to be worth disabling.
find "$SOURCE_DIR" -maxdepth 1 -name "*.psc" -mtime -7 -type f \
    -exec cp {} "$SNAPSHOT_DIR/source/" \; 2>/dev/null
find "$DEPLOY_DIR" -maxdepth 1 -name "*.pex" -mtime -7 -type f \
    -exec cp {} "$SNAPSHOT_DIR/deploy/" \; 2>/dev/null

PSC_COUNT=$(ls "$SNAPSHOT_DIR/source/" 2>/dev/null | wc -l)
PEX_COUNT=$(ls "$SNAPSHOT_DIR/deploy/" 2>/dev/null | wc -l)

# An empty snapshot directory is worse than none: `hook-canary` and any later audit
# would count it as a snapshot that exists.
if [ "$PSC_COUNT" -eq 0 ] && [ "$PEX_COUNT" -eq 0 ]; then
    rm -rf "$SNAPSHOT_DIR"
    exit 0
fi

date +%s > "$RATE_LIMIT_FILE"

SHORT_CMD=$(echo "$COMMAND" | head -c 120)
printf '[%s] AUTO-SNAPSHOT (psc:%s pex:%s) before: %s\n' \
    "$TIMESTAMP" "$PSC_COUNT" "$PEX_COUNT" "$SHORT_CMD" >> "$AUDIT_LOG"

# Prune AFTER the new snapshot is on disk, never before -- a prune that runs first
# can delete the last good copy and then fail to make a new one.
# -mindepth 1: the start directory is at depth 0 and matches `-type d`, so without
# it an aged base would delete ITSELF and every snapshot in it. Unreachable today
# (the mkdir above refreshes the base mtime first) but one word to make it so.
find "$SNAPSHOT_BASE" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} \; 2>/dev/null

exit 0
