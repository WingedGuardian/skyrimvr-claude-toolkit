#!/bin/bash
# Guard Bash against destructive or file-modifying commands in a Skyrim install.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# POLICY -- see the header of protect-files.sh. deny for what is never correct,
# advise for what is consequential but legitimate, ask only for what is genuinely
# the user's decision (exactly one rule here uses it).

JQ="{{JQ_PATH}}"

# MEASURED (X4 toolkit 2026-08-29, reproduced on a second machine 2026-09-06):
# `cat /dev/stdin` returns ZERO BYTES in the Claude Code hook environment, while a
# bare `cat` returns the payload. A hook that reads nothing falls through its first
# guard and exits 0 -- byte-identical to deciding "this is fine". A test suite
# CANNOT catch it: a suite pipes stdin, so /dev/stdin resolves fine there.
INPUT=$(cat)

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat"

mkdir -p "$HB_DIR" 2>/dev/null
if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/protect-bash" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/protect-bash.blind" 2>/dev/null
fi

hooklog() { printf '[%s] protect-bash %s: %s\n' "$(date +%Y%m%d_%H%M%S)" "$1" "$2" >> "$AUDIT_LOG" 2>/dev/null; }

COMMAND=$(echo "$INPUT" | "$JQ" -r '.tool_input.command // empty')

if [ -z "$COMMAND" ]; then
    hooklog REFUSE "no command in payload -- hook could not evaluate its rules"
    "$JQ" -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"GUARD INERT: protect-bash.sh received no command and therefore checked NOTHING. Allowing silently would be indistinguishable from deciding this is fine. Investigate the hook payload before retrying."}}'
    exit 0
fi

deny() { hooklog DENY "$1"; "$JQ" -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
ask()  { hooklog ASK  "$1"; "$JQ" -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'; exit 0; }

ADVICE=""
advise() { hooklog ADVISE "$1"; ADVICE="${ADVICE:+$ADVICE | }$1"; }

# Every path pattern below accepts BOTH separators via [/\\]; see the note on the deny
# rules for why.

# === HARD BLOCK ===
# These are v3.8.2's patterns with ONE change: both separators are accepted. The
# matching is deliberately broad -- an rm naming a Skyrim path is refused outright
# -- because the cost of a false positive is one rephrased command and the cost of
# a false negative is the install. A guard written with only forward slashes matched
# C:/Games/Skyrim and missed C:\Games\Skyrim, while cmd.exe and powershell are both
# callable, so the backslash form was the one that got through. Do not tighten these
# without a matrix of real commands in BOTH directions.
echo "$COMMAND" | grep -qiE 'rm\s+(-[a-z]*f[a-z]*\s+)?["'"'"']?([A-Za-z]:|[/\\][a-z])[/\\].*Skyrim' && deny "BLOCKED: Cannot delete the game installation directory."
echo "$COMMAND" | grep -qiE 'rm\s+(-[a-z]*f[a-z]*\s+)?["'"'"']?([A-Za-z]:|[/\\][a-z])[/\\].*Documents[/\\]My Games[/\\]Skyrim' && deny "BLOCKED: Cannot delete the Skyrim config directory."
echo "$COMMAND" | grep -qiE '(reg\s+delete|Remove-ItemProperty.*Bethesda)' && deny "BLOCKED: Cannot delete Bethesda registry keys."

# === HARD BLOCK -- tool output aimed straight at a .psc source file ===
# .psc sources are irreplaceable. Decompilers have been observed to leave the output
# file empty when their decompilation step fails, destroying what was there.
echo "$COMMAND" | grep -qiE -- '-(o|-output|-OutputPath)\s+["'"'"']?[^"'"'"' ]*\.psc(\b|"|'"'"')' && deny "BLOCKED: Tool output cannot target a .psc file directly. Use a separate output directory."

# === HARD BLOCK -- Champollion against a .pex inside Data/Scripts/ ===
# Champollion writes to Data/Scripts/Source/ regardless of -p/-a, and on a failed
# decompile can leave the output .psc empty. This has destroyed reconstructed
# sources twice. Copy the .pex to a temp directory and run it there.
if echo "$COMMAND" | grep -qiE 'Champollion\b'; then
    echo "$COMMAND" | grep -qiE '[/\\]Data[/\\]Scripts[/\\][^"'"'"' /\\]+\.pex' \
        && deny "BLOCKED: Champollion against a PEX in Data/Scripts/ has destroyed a .psc twice. Copy the .pex to a temp directory first, then run Champollion there."
fi

# === ADVISE -- destructive or overwriting commands inside the install ===
# NOTE: the game-root deny above is broad enough to catch a drive-qualified rm
# anywhere under a Skyrim path, so this advisory is reached only by paths that
# are NOT drive-qualified (`rm Data/Textures/x.dds`). That is v3.8.2's behaviour
# and the live install's, kept deliberately: narrowing the deny to root-only
# deletions is a real design question and a NEW behaviour neither has been
# exercised with.
echo "$COMMAND" | grep -qiE 'rm\s.*(\bData[/\\]|Skyrim)' && advise "Deleting files inside the live game install: $COMMAND. Deleting the game ROOT is denied outright; this is a delete further in, which a mod manager can usually redeploy but your own mod files cannot be. Check the path is what you meant."
echo "$COMMAND" | grep -qiE '(mv|cp|move|copy)\s.*(\bData[/\\]|Skyrim|My Games[/\\]Skyrim)' && advise "Moving/copying inside the live game install: $COMMAND. A stray overwrite here is silent -- confirm the destination before relying on it."
echo "$COMMAND" | grep -qiE '>\s*["'"'"']?([A-Za-z]:)?[/\\]?[^"'"'"']*(Skyrim|\bData[/\\])' && advise "Redirecting output into the game/config directory: $COMMAND. A redirect TRUNCATES its target before anything is written."
echo "$COMMAND" | grep -qiE 'sed\s+-i.*(\bData[/\\]|Skyrim|My Games[/\\]Skyrim)' && advise "In-place sed edit in the game directory: $COMMAND. A Windows path in sed's REPLACEMENT is destroyed by escape handling -- normalise the path first."

# === ADVISE -- plugin/archive/load order references ===
echo "$COMMAND" | grep -qiE '\.(esp|esm|esl|bsa|ba2)\b' && advise "Command references plugin/archive files: $COMMAND. Reading one is fine; writing one directly corrupts it -- use xelib, Spriggit or AutoMod."
echo "$COMMAND" | grep -qiE '(loadorder\.txt|plugins\.txt)' && advise "Command references load order: $COMMAND. Your mod manager owns these files, and the Special Edition copy under AppData is a DIFFERENT file from the VR one and is routinely stale."

# === ADVISE -- AutoMod CLI write commands ===
if echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(add-weapon|add-spell|add-armor|add-npc|add-quest|add-perk|add-book|add-global|add-faction|add-leveled-item|add-form-list|add-encounter-zone|add-location|add-outfit|attach-script|set-property|auto-fill|merge|generate-seq)\b'; then
    echo "$COMMAND" | grep -qiE -- '--dry-run' || advise "AutoMod ESP write WITHOUT --dry-run: $COMMAND. The standing rule is dry-run first, review, then write."
fi
echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(replace-textures|rename-strings|fix-eyes|scale)\b' && advise "AutoMod NIF write: $COMMAND. Cross-read the result with an independent parser before handing it to the engine -- same-tool readback misses malformed files."
echo "$COMMAND" | grep -qiE '(automod|SpookysAutomod).*\b(archive\s+(create|add-files|remove-files|replace-files|update-file|merge|optimize))\b' && advise "AutoMod archive write: $COMMAND. Loose files always override a BSA, so check for loose copies before assuming this took effect."

# === ASK -- the one decision that is genuinely the user's ===
# These MUTATE a save file through ReSaver's write path. They are not part of any
# normal workflow, and an unintended run is not recoverable from the save itself.
# Dry-runs (no --apply) and every read op pass through untouched.
if echo "$COMMAND" | grep -qiE '(resaver-cli|ResaverCLI)\b.*\b(reset-havok|cleanse-formlists|remove-created)\b'; then
    echo "$COMMAND" | grep -qiE -- '--apply' && ask "DESTRUCTIVE ReSaver save-write with --apply (reset-havok/cleanse-formlists/remove-created) MUTATES a .ess save and is never part of normal work. Explicit approval required: $COMMAND"
fi

# === ADVISE -- any external Papyrus/ESP tool writing into the game dirs ===
if echo "$COMMAND" | grep -qiE '\b(automod|SpookysAutomod|spookys-automod|automod-cli|PapyrusAssembler|PapyrusCompiler|Champollion|Caprica|spriggit)\b'; then
    echo "$COMMAND" | grep -qiE -- '-(o|-output|-OutputPath|-p|-psc|-asm|-a)\s+["'"'"']?[^"'"'"' ]*([/\\]Data[/\\]|Skyrim)' \
        && advise "External Papyrus/ESP tool writing into the game/data directory: $COMMAND. Verify the output landed where you intended -- several of these have destructive defaults."
fi

[ -n "$ADVICE" ] && "$JQ" -n --arg r "$ADVICE" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$r}}'
exit 0
