#!/bin/bash
# Generate SHA256 checksums for critical game files
# Run manually to create a baseline snapshot for drift detection
#
# Usage: bash scripts/generate-baseline.sh            writes .claude/baseline_checksums.txt
#        bash scripts/generate-baseline.sh --stdout   prints the same text and leaves the baseline alone
#
# SETUP: Set GAME_DIR and CONFIG_DIR below to match your installation.

GAME_DIR="${GAME_DIR:-$(pwd)}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/Documents/My Games/Skyrim VR}"
OUTPUT="$GAME_DIR/.claude/baseline_checksums.txt"
STDOUT=
if [ "${1:-}" = "--stdout" ]; then
    STDOUT=1
    OUTPUT=$(mktemp) || exit 1       # /dev/stdout cannot be reopened for writing when stdout is a Windows pipe
    trap 'rm -f "$OUTPUT"' EXIT
fi

{
    echo "Generating baseline checksums..."
    echo "Game dir:   $GAME_DIR"
    echo "Config dir: $CONFIG_DIR"
    echo ""
} >&2

echo "# Baseline checksums generated $(date '+%Y-%m-%d %H:%M:%S')" > "$OUTPUT"
echo "# Compare with: diff <(bash scripts/generate-baseline.sh --stdout) .claude/baseline_checksums.txt" >> "$OUTPUT"
echo "" >> "$OUTPUT"

# INI configs
echo "## INI Configs" >> "$OUTPUT"
for f in "$CONFIG_DIR"/*.ini; do
    [ -f "$f" ] && sha256sum "$f" >> "$OUTPUT"
done

# SKSE plugin configs
echo "" >> "$OUTPUT"
echo "## SKSE Plugin Configs" >> "$OUTPUT"
find "$GAME_DIR/Data/SKSE/Plugins" -maxdepth 1 -name "*.ini" -exec sha256sum {} \; >> "$OUTPUT" 2>/dev/null

# ESP/ESM files (just the ones in Data/ root, not BSA contents)
echo "" >> "$OUTPUT"
echo "## Plugin Files (ESP/ESM/ESL)" >> "$OUTPUT"
find "$GAME_DIR/Data" -maxdepth 1 \( -name "*.esp" -o -name "*.esm" -o -name "*.esl" \) -exec sha256sum {} \; >> "$OUTPUT" 2>/dev/null

# Load order files
echo "" >> "$OUTPUT"
echo "## Load Order Files" >> "$OUTPUT"
LO_DIR="$HOME/AppData/Local/Skyrim VR"
[ -f "$LO_DIR/loadorder.txt" ] && sha256sum "$LO_DIR/loadorder.txt" >> "$OUTPUT"
[ -f "$LO_DIR/plugins.txt" ] && sha256sum "$LO_DIR/plugins.txt" >> "$OUTPUT"

# Project files
echo "" >> "$OUTPUT"
echo "## Project Files" >> "$OUTPUT"
sha256sum "$GAME_DIR/CLAUDE.md" >> "$OUTPUT" 2>/dev/null
sha256sum "$GAME_DIR/KNOWLEDGEBASE.md" >> "$OUTPUT" 2>/dev/null

if [ -n "$STDOUT" ]; then
    cat "$OUTPUT"
    exit 0
fi
LINES=$(wc -l < "$OUTPUT")
echo "Baseline written to $OUTPUT ($LINES lines)"
echo "To compare against the current state, use --stdout: running without it replaces this baseline."
