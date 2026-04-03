#!/bin/bash
# Interactive restore from .claude/backups/
# Lists available backups and shows how to restore them
#
# Usage: bash scripts/restore-from-backup.sh [search-term]

BACKUP_DIR="${GAME_DIR:-.}/.claude/backups"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "No backup directory found at $BACKUP_DIR"
    echo "Set GAME_DIR to your Skyrim install path, or run from the game directory."
    exit 1
fi

SEARCH="$1"

echo "=== Available Backups ==="
echo ""

if [ -n "$SEARCH" ]; then
    echo "Filtering for: $SEARCH"
    echo ""
    BACKUPS=$(ls -1t "$BACKUP_DIR" | grep -v "AUDIT_LOG" | grep -i "$SEARCH")
else
    BACKUPS=$(ls -1t "$BACKUP_DIR" | grep -v "AUDIT_LOG")
fi

if [ -z "$BACKUPS" ]; then
    echo "No backups found."
    exit 0
fi

# Number the backups
i=1
while IFS= read -r backup; do
    TS=$(echo "$backup" | cut -d'_' -f1-2)
    ORIG=$(echo "$backup" | sed 's/^[0-9]*_[0-9]*__//' | sed 's/_/\//g')
    SIZE=$(stat -c%s "$BACKUP_DIR/$backup" 2>/dev/null || echo "?")
    echo "  [$i] $TS  $ORIG  ($SIZE bytes)"
    i=$((i + 1))
done <<< "$BACKUPS"

echo ""
echo "Total: $((i - 1)) backup(s)"
echo ""
echo "To restore, run:"
echo "  cp \"$BACKUP_DIR/<backup-filename>\" \"<original-path>\""
echo ""
echo "To view the audit log:"
echo "  cat \"$BACKUP_DIR/AUDIT_LOG.txt\""
