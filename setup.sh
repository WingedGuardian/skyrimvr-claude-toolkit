#!/bin/bash
# Skyrim Claude Code Toolkit -- Setup Script
#
# This script is designed to be run FROM your Skyrim folder, after
# extracting the toolkit zip into it. It configures everything in-place.
#
# Usage: bash setup.sh

set -e

GAME_DIR="$(pwd)"
USERNAME="$(whoami)"

echo "============================================"
echo " Skyrim Claude Code Toolkit -- Setup"
echo "============================================"
echo ""
echo "Game directory: $GAME_DIR"
echo ""

# --- Verify this looks like a Skyrim install ---
if [ ! -f "$GAME_DIR/SkyrimVR.exe" ] && [ ! -f "$GAME_DIR/SkyrimSE.exe" ]; then
    echo "WARNING: No SkyrimVR.exe or SkyrimSE.exe found here."
    echo "This script should be run from your Skyrim installation folder."
    echo ""
    echo "Are you sure this is the right directory?"
    read -p "Continue anyway? (y/n) " CONTINUE
    [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ] && exit 1
fi

# --- Verify toolkit files are present ---
if [ ! -f "$GAME_DIR/KNOWLEDGEBASE.md" ] || [ ! -f "$GAME_DIR/.claude/hooks/protect-bash.sh" ]; then
    echo "ERROR: Toolkit files not found in this directory."
    echo "Make sure you extracted the toolkit zip into your Skyrim folder first."
    exit 1
fi

# --- Detect jq ---
echo "Checking for jq..."
JQ_PATH=$(which jq 2>/dev/null || echo "")
if [ -z "$JQ_PATH" ]; then
    # Try common Windows locations
    for p in \
        "/c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Links/jq.exe" \
        "/c/ProgramData/chocolatey/bin/jq.exe" \
        "/usr/bin/jq"; do
        if [ -f "$p" ]; then
            JQ_PATH="$p"
            break
        fi
    done
fi

if [ -z "$JQ_PATH" ]; then
    echo ""
    echo "jq not found. It's needed for the safety hooks."
    echo "Installing jq via winget..."
    winget install jqlang.jq --accept-source-agreements --accept-package-agreements 2>/dev/null || {
        echo ""
        echo "ERROR: Could not auto-install jq."
        echo "Please install it manually: winget install jqlang.jq"
        echo "Then re-run: bash setup.sh"
        exit 1
    }
    # Re-detect after install
    JQ_PATH=$(which jq 2>/dev/null || echo "/c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Links/jq.exe")
fi
echo "  Found jq: $JQ_PATH"

# --- Detect user paths ---
DOCUMENTS_DIR="C:/Users/$USERNAME/Documents"
echo ""
if [ -d "$DOCUMENTS_DIR/My Games/Skyrim VR" ] || [ -d "$DOCUMENTS_DIR/My Games/Skyrim Special Edition" ]; then
    echo "  Found Skyrim configs in: $DOCUMENTS_DIR/My Games/"
else
    echo "  WARNING: Skyrim config not found at $DOCUMENTS_DIR/My Games/"
    echo "  You may need to update paths in CLAUDE.md manually."
fi

# --- Configure hook scripts (replace jq placeholder) ---
echo ""
echo "Configuring safety hooks..."
for hook in protect-bash.sh protect-files.sh backup-before-edit.sh; do
    if grep -q '{{JQ_PATH}}' "$GAME_DIR/.claude/hooks/$hook"; then
        sed -i "s|{{JQ_PATH}}|$JQ_PATH|g" "$GAME_DIR/.claude/hooks/$hook"
        echo "  Configured: .claude/hooks/$hook"
    else
        echo "  Already configured: .claude/hooks/$hook"
    fi
done

# --- Configure CLAUDE.md (replace path placeholders) ---
echo ""
echo "Configuring CLAUDE.md..."
if grep -q '{{GAME_ROOT}}' "$GAME_DIR/CLAUDE.md"; then
    sed -i "s|{{GAME_ROOT}}|$GAME_DIR|g" "$GAME_DIR/CLAUDE.md"
    sed -i "s|{{USERNAME}}|$USERNAME|g" "$GAME_DIR/CLAUDE.md"
    sed -i "s|{{DOCUMENTS_DIR}}|$DOCUMENTS_DIR|g" "$GAME_DIR/CLAUDE.md"
    echo "  Configured with your paths."
else
    echo "  Already configured."
fi

# --- Ensure backup directory exists ---
mkdir -p "$GAME_DIR/.claude/backups"

# --- Copy settings.local.json.example if no settings.local.json exists ---
if [ ! -f "$GAME_DIR/.claude/settings.local.json" ] && [ -f "$GAME_DIR/.claude/settings.local.json.example" ]; then
    echo ""
    echo "  Copied settings.local.json.example (you can customize allowed commands later)"
fi

echo ""
echo "============================================"
echo " Setup Complete!"
echo "============================================"
echo ""
echo "Installed and configured:"
echo "  CLAUDE.md                        -- Project instructions (paths filled in)"
echo "  KNOWLEDGEBASE.md                 -- 600+ lines of Skyrim modding knowledge"
echo "  .claude/settings.json            -- Hook configuration"
echo "  .claude/hooks/protect-bash.sh    -- Guards dangerous commands"
echo "  .claude/hooks/protect-files.sh   -- Guards file edits"
echo "  .claude/hooks/backup-before-edit.sh -- Auto-backups with audit trail"
echo "  .claude/backups/                 -- Backup storage (empty for now)"
echo ""
echo "The safety hooks are now active. Claude Code will:"
echo "  - Ask permission before editing any game file"
echo "  - Block direct writes to ESP/ESM/BSA files"
echo "  - Automatically back up files before modifying them"
echo ""
echo "You're ready to go! Start asking Claude about your mods."
