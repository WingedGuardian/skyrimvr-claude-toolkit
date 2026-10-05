#!/bin/bash
# List and restore the pre-edit backups in .claude/backups/
#
# Usage: bash scripts/restore-from-backup.sh [search-term]
#        bash scripts/restore-from-backup.sh --restore <backup-file> [--to <path>]
#
# Each backup's ORIGINAL PATH and sha256 are read from .claude/backups/MANIFEST.jsonl (written by skyguard's
# backup hook), so a file is restored where it came from -- `Skyrim_VR - Main.bsa` stays one name. A restore checks
# the backup against its recorded sha256 first and backs up the file it is about to replace.
#
# Backups taken before the manifest existed have no recorded path. They are listed with a path rebuilt from the
# flattened name, labelled `(legacy: path guessed)`, and restoring one needs an explicit --to <path>.
#
# The logic lives in tools/skyguard/backup.py (restore_main). Set SKYGUARD_PYTHON to choose the interpreter.

BACKUP_DIR="${GAME_DIR:-.}/.claude/backups"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "No backup directory found at $BACKUP_DIR"
    echo "Set GAME_DIR to your Skyrim install path, or run from the game directory."
    exit 1
fi

_SELF="${BASH_SOURCE[0]//\\//}"
case "$_SELF" in */*) HERE="${_SELF%/*}" ;; *) HERE="." ;; esac
TOOLS="$(CDPATH= cd -- "$HERE/../tools" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })"
if [ -z "$TOOLS" ] || [ ! -f "$TOOLS/skyguard/backup.py" ]; then
    echo "restore-from-backup: cannot find tools/skyguard next to this script."
    echo "The raw files are still there: ls -lt \"$BACKUP_DIR\""
    exit 1
fi

PY=""
for c in "${SKYGUARD_PYTHON:-}" python3 python py; do
    [ -n "$c" ] || continue
    if "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "restore-from-backup: no Python 3.7+ found (set SKYGUARD_PYTHON). The manifest cannot be read without it."
    echo "The raw files are still there: ls -lt \"$BACKUP_DIR\""
    exit 1
fi

PYTHONIOENCODING=utf-8 exec "$PY" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from skyguard import backup; sys.exit(backup.restore_main(sys.argv[2:]))' "$TOOLS" "$BACKUP_DIR" "$@"
