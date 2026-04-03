#!/bin/bash
# Protect against destructive or file-modifying bash commands in Skyrim modding environment
#
# SETUP: Update the JQ variable below to point to your jq installation.
#   Install jq: winget install jqlang.jq
#   Find path:  where jq

JQ="{{JQ_PATH}}"

INPUT=$(cat /dev/stdin)
COMMAND=$(echo "$INPUT" | "$JQ" -r '.tool_input.command // empty')

deny() { "$JQ" -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
ask()  { "$JQ" -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'; exit 0; }

# === HARD BLOCK ===
# Prevent deleting the game installation directory
echo "$COMMAND" | grep -qiE 'rm\s+(-[a-z]*f[a-z]*\s+)?["'"'"']?(C:/|/c/).*Skyrim' && deny "BLOCKED: Cannot delete the game installation directory."
# Prevent deleting Skyrim config directory
echo "$COMMAND" | grep -qiE 'rm\s+(-[a-z]*f[a-z]*\s+)?["'"'"']?(C:/|/c/).*Documents/My Games/Skyrim' && deny "BLOCKED: Cannot delete the Skyrim config directory."
# Prevent deleting Bethesda registry keys
echo "$COMMAND" | grep -qiE '(reg\s+delete|Remove-ItemProperty.*Bethesda)' && deny "BLOCKED: Cannot delete Bethesda registry keys."

# === CONFIRM -- destructive commands ===
echo "$COMMAND" | grep -qiE 'rm\s.*(Skyrim|/Data/)' && ask "Deleting files in game directory -- confirm: $COMMAND"

# === CONFIRM -- any command that modifies files in game/config directories ===
echo "$COMMAND" | grep -qiE '(mv|cp|move|copy)\s.*(Skyrim|/Data/|/My Games/Skyrim)' && ask "Moving/copying files in game directory -- confirm: $COMMAND"
echo "$COMMAND" | grep -qiE '>\s*["'"'"']?(C:/|/c/).*Skyrim' && ask "Redirecting output to game/config directory -- confirm: $COMMAND"
echo "$COMMAND" | grep -qiE 'sed\s+-i.*(Skyrim|/Data/|/My Games/Skyrim)' && ask "In-place edit in game directory -- confirm: $COMMAND"

# === CONFIRM -- plugin/archive/load order references ===
echo "$COMMAND" | grep -qiE '\.(esp|esm|esl|bsa|ba2)\b' && ask "Command references plugin/archive files -- confirm: $COMMAND"
echo "$COMMAND" | grep -qiE '(loadorder\.txt|plugins\.txt)' && ask "Command references load order -- confirm: $COMMAND"

# === CONFIRM -- AutoMod CLI write commands (require confirmation unless --dry-run) ===
# ESP write commands
if echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(add-weapon|add-spell|add-armor|add-npc|add-quest|add-perk|add-book|add-global|add-faction|add-leveled-item|add-form-list|add-encounter-zone|add-location|add-outfit|attach-script|set-property|auto-fill|merge|generate-seq)\b'; then
    echo "$COMMAND" | grep -qiE '\-\-dry-run' || ask "AutoMod ESP write command without --dry-run -- confirm: $COMMAND"
fi
# NIF write commands (always confirm -- these modify mesh files)
echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(replace-textures|rename-strings|fix-eyes|scale)\b' && ask "AutoMod NIF write command -- confirm: $COMMAND"
# Archive write commands (always confirm)
echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(archive\s+(create|add-files|remove-files|replace-files|update-file|merge|optimize))\b' && ask "AutoMod archive write command -- confirm: $COMMAND"

exit 0
