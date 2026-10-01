#!/bin/bash
# Guard Bash against destructive or file-modifying commands in a Skyrim install.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# POLICY -- see the header of protect-files.sh. deny for what is never correct,
# advise for what is consequential but legitimate, ask only for what is genuinely
# the user's decision (exactly one rule here uses it).

HOOK_NAME="protect-bash"
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

# jq is how this hook SPEAKS. If it is missing, unset, or still the literal
# unsubstituted JQ placeholder (the user extracted the zip and never ran setup.sh), then
# deny() and the inert-guard below both emit nothing -- and nothing is read as
# ALLOW. So the one refusal that must not depend on jq is printed with printf.
if ! "$JQ" --version >/dev/null 2>&1; then
    # Backslashes out, forward slashes in: this string goes into JSON by printf, not
    # through jq, and a Windows jq path (`where jq` prints one) would otherwise emit
    # invalid escapes -- unparseable output is not a deny, it is an allow.
    JQ_SHOWN="${JQ//\\//}"
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"GUARD INERT: %s cannot run jq (%s), so it evaluated NO rules and cannot report a decision. Allowing silently would be indistinguishable from approving. Install jq (winget install jqlang.jq) and run setup.sh to configure its path."}}\n' \
        "$HOOK_NAME" "$JQ_SHOWN"
    exit 0
fi
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

# --- WHERE IS THE INSTALL, ACTUALLY? ----------------------------------------
#
# The old rule asked "does this command mention a path containing the word Skyrim".
# MEASURED 2026-09-08, every one of these matched and none is the live install:
#
#   C:/Users/Moona/Projects/skyrimvr-claude-toolkit   our own repo checkout
#   .../Temp/claude/C--GOG-Games-...-Skyrim-VR/...    the session scratchpad
#   C:/Temp/skyrim-notes.txt                          a note that happens to be named
#   .../Downloads/skyrimvr-claude-toolkit-3.8.3.zip   a downloaded zip
#
# So a scratchpad cleanup was refused as "deleting the Skyrim install" -- not because
# a game path appeared elsewhere in the command, but because the scratchpad IS a game
# path to that regex. Note what this means: a shell PARSER would not have fixed it. It
# would bind the verb to its target correctly and still deny, because the target still
# matches. The defect was path RECOGNITION, not scoping.
#
# Derived from THIS FILE's location, deliberately NOT from $CLAUDE_PROJECT_DIR: the
# hooks live at <install>/.claude/hooks/, so ../.. is the install root whichever folder
# the session is rooted in. CLAUDE_PROJECT_DIR would narrow the guard to a subfolder if
# someone opened Claude Code inside Data/Scripts, and a guard that silently protects
# less than it claims is the defect this replaces.
# ⚠ Normalise the separators FIRST. `dirname` splits on `/` only, so when this hook is
# invoked by a pure-backslash path -- `bash "C:\Games\Skyrim VR\.claude\hooks\..."`,
# which is what a Windows caller passing a native path produces -- dirname finds no
# separator, returns `.`, and INSTALL_ROOT silently becomes the CURRENT WORKING
# DIRECTORY. The guard then protects whatever folder the session happens to be in and
# reports nothing wrong: it does not fail closed, because GAME_PATH is still non-empty.
# Found by a test that invoked the hook the way Python's subprocess does.
_SELF="${BASH_SOURCE[0]//\\//}"
INSTALL_ROOT="$(cd "$(dirname "$_SELF")/../.." 2>/dev/null && pwd)"
# ...and the SAME directory in its Windows spelling. `pwd` can return an MSYS MOUNT
# ALIAS -- `/tmp/...` for `C:/Users/<you>/AppData/Local/Temp/...` -- and the alias
# shares no prefix with the drive-letter form a command actually names, so the guard
# derives a root that cannot match its own install and silently allows. MEASURED: an
# install under a `/tmp`-mapped directory produced NO hook output for
# `rm -rf "C:/Users/.../Program Files (x86)"`. Both spellings are added; a root is
# only ever ADDITIVE, so naming the same directory twice costs nothing and closes the
# gap wherever MSYS aliases a mount.
INSTALL_ROOT_WIN="$(cd "$(dirname "$_SELF")/../.." 2>/dev/null && pwd -W 2>/dev/null)"

# Turn an absolute path into an ERE matching it in EITHER dialect with EITHER
# separator. PROJECT_DIR is MSYS (/c/...) under Git Bash while every path this project
# documents is Windows (C:/...), so a plain compare reads two spellings of one
# directory as two directories. Metacharacters in the path are escaped: a real install
# at "C:/Games/Skyrim (VR)" must match literally, not as a regex group.
path_to_ere() {
    local p="${1//\\//}"
    local drive="" tail="" out c i
    case "$p" in
        [a-zA-Z]:/*)  drive="${p:0:1}"; tail="${p:3}" ;;   # C:/... (Windows)
        /[a-zA-Z]/*)  drive="${p:1:1}"; tail="${p:3}" ;;   # /c/... (MSYS)
        /?*)          tail="${p:1}" ;;                     # /home/... (plain POSIX)
        *) return 1 ;;                                     # relative: not a root
    esac
    [ -n "$tail" ] || return 1
    out=""
    for (( i=0; i<${#tail}; i++ )); do
        c="${tail:i:1}"
        case "$c" in
            # ONE OR MORE separators, not exactly one. A doubled separator names the
            # identical directory on every platform, and it is not exotic -- it is how
            # you WRITE a Windows path in Python or PowerShell source:
            #   shutil.rmtree('C:\\GOG Games\\The Elder Scrolls V Skyrim VR')
            # MEASURED: with a single-character class, that spelling and
            # `rm -rf "C://GOG Games//..."` both produced NO hook output at all, which
            # the runtime reads as allow -- while the single-separator spelling denied.
            # The pre-arc rule matched with `[^"']*` and was separator-agnostic, so
            # this was a regression introduced by resolving the root, and the replay
            # evidence could not see it: 7,641 historical commands contain no doubled
            # spelling, so "0 new refusals" was silent about an entire class.
            # Braced deliberately: `$out[` reads as an array expansion to shellcheck
            # (SC1087, error severity), and this is the one place a `[` follows a
            # variable. Latent since the root-resolution rework -- CI had never run on
            # it, because the commit was never pushed.
            /)   out="${out}[/\\\\]+" ;;
            '['|']'|'*'|'+'|'?'|'^'|'$'|'('|')'|'{'|'}'|'|'|'.'|'\') out="$out\\$c" ;;
            *)   out="$out$c" ;;
        esac
    done
    if [ -n "$drive" ]; then
        ERE_OUT="($drive:|[/\\\\]+$drive)[/\\\\]+$out"
    else
        ERE_OUT="[/\\\\]+$out"
    fi
    return 0
}

# Paths the user configured, written by setup.sh. ADDITIVE ONLY -- INSTALL_ROOT above
# always resolves, so a missing or unreadable paths file cannot make this guard inert.
# That is the whole reason the root is derived first and the file read second: it is
# not another unsubstituted-placeholder dependency that fails silently open. (Written
# WITHOUT naming that placeholder literally: setup.sh substitutes it with a global sed,
# so a second occurrence in a COMMENT would be rewritten too, destroying the
# one-per-hook property that makes an UNCONFIGURED hook detectable at all.)
PATHS_ENV="$INSTALL_ROOT/.claude/skyrim-paths.env"
# shellcheck source=/dev/null
[ -f "$PATHS_ENV" ] && . "$PATHS_ENV" 2>/dev/null

add_root() {   # add_root VAR_VALUE -> append its ERE to $1 if new
    local val="$2" e
    [ -n "$val" ] || return 0
    path_to_ere "$val" || return 0
    e="$ERE_OUT"
    case "|${!1}|" in *"|$e|"*) return 0 ;; esac
    printf -v "$1" '%s' "${!1:+${!1}|}$e"
}

GAME_PATH=""
add_root GAME_PATH "$INSTALL_ROOT"
add_root GAME_PATH "$INSTALL_ROOT_WIN"
add_root GAME_PATH "${SKYRIM_GAME_ROOT:-}"

# The config directory lives OUTSIDE the install, so it keeps its own rule and its own
# refusal text -- the INIs there are not recoverable from a mod manager, and a reason
# that named the wrong directory would be worse than none.
CONFIG_PATH='Documents[/\\]+My Games[/\\]+Skyrim'
add_root CONFIG_PATH "${SKYRIM_CONFIG_DIR:-}"

# Advisory-only widening. The deny rules use the RESOLVED roots and nothing else.
# These add a relative `Data/` -- `rm Data/Textures/x.dds`, run from the game folder --
# which no resolved root can match, because a hook is never told the caller's cwd. The
# old substring rule caught these as a side effect of being too broad; losing them
# outright would have been a real reduction in cover, so they are kept HERE, where the
# consequence is a note to Claude rather than a refusal.
ADVISE_PATH="$GAME_PATH|$CONFIG_PATH"

# A RELATIVE `Data/` gets its own rule rather than an alternative inside ADVISE_PATH,
# and the reason is a real bug caught by the suite: the rules above consume the space
# after the verb, so a boundary group inside ADVISE_PATH had nothing left to match
# against in `rm Data/Textures/x.dds` -- it silently stopped advising, while
# `rm ./Data/x.dds` still worked because the `/` survived as a boundary. Two rules that
# LOOK equivalent and differ only in what an earlier group already ate.
#
# This spelling carries its own boundary and tolerates flag tokens between the verb and
# the path. MEASURED on 10 inputs: `rm Data/x`, `rm ./Data/x`, `rm ../Data/x`,
# `rm -rf Data/x` and `rm -r -f Data/x` advise; `rm mydata/cache.bin`, `ls Data/`,
# `rm -rf node_modules`, a pytest run and `git status` do not.
RELATIVE_DATA_DELETE='(^|[;&|(`]|[[:space:]])(rm|rmdir|del|erase)([[:space:]]+-[^[:space:]]*)*[[:space:]]+([^[:space:]]*[/\\]+)?Data[/\\]+'

# FAIL CLOSED. If no root could be derived, this guard evaluates nothing, and a guard
# that evaluated nothing must not answer "fine" -- that conflation is what left every
# hook in this toolkit inert for five weeks.
if [ -z "$GAME_PATH" ]; then
    hooklog REFUSE "could not derive the install root -- the delete guard evaluated NOTHING"
    "$JQ" -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"GUARD INERT: protect-bash.sh could not work out which directory is the Skyrim install, so its delete rules checked NOTHING. Allowing silently would be indistinguishable from having no guard at all. Re-run setup.sh from your Skyrim folder."}}'
    exit 0
fi

# === HARD BLOCK ===
#
# ANCHOR ON THE PATH AND THE INTENT, NOT ON ONE SPELLING OF `rm`.
#
# The previous rule was `rm\s+(-[a-z]*f[a-z]*\s+)?<path>`: exactly one flag token,
# which had to contain an `f`. MEASURED -- every one of these reached the game
# directory unrefused while the README promised they could not:
#
#   rm -f -r <game>              two flag tokens
#   rm -r -f <game>              same
#   rm --recursive --force <game>  long flags
#   rm -rf -- <game>             the end-of-options marker
#   cd <game> && rm -rf .        the path is in the cd, not the rm
#   G=<game>; rm -rf "$G"        the path is in a variable
#   rmdir /s /q <game>           not rm at all
#   del /f /s /q <game>          nor this
#   Remove-Item -Recurse <game>  nor this, and powershell is allow-listed
#   find <game> -delete          nor this
#   python -c "shutil.rmtree(...)"  nor this
#
# So the test is now a CONJUNCTION of two independent things: does the command name
# a destroyer at all, and does it name a qualified path inside a Skyrim install.
# Neither half alone denies.
#
# The cost is honest and deliberate: a compound command that deletes something in
# /tmp while merely MENTIONING a game path is refused too. That is one rephrase into
# two commands, against a claim in the README that the game directory cannot be
# deleted -- and a claim like that has to be true or it should not be made. See
# tests/test_hooks.py, which pins both the catches and the accepted false positive.
DESTROYER='(^|[;&|(`]|[[:space:]])(rm|rmdir|del|erase)[[:space:]]|Remove-Item|shutil\.rmtree|rmtree[[:space:]]*\(|[[:space:]]-delete([[:space:]]|$)|Remove-ItemProperty'

if echo "$COMMAND" | grep -qiE "$DESTROYER"; then
    echo "$COMMAND" | grep -qiE "$CONFIG_PATH" && deny "BLOCKED: this command would delete inside the Skyrim config directory (Documents/My Games/Skyrim). Your INIs and controlmap live there and are not recoverable from a mod manager."
    echo "$COMMAND" | grep -qiE "$GAME_PATH" && deny "BLOCKED: this command names both a deletion and a path inside the Skyrim install. If you meant to delete something elsewhere, run it as a separate command that does not also mention the game directory."
fi

# `reg.exe delete` is the same command as `reg delete`; the old pattern required
# whitespace immediately after `reg` and missed the .exe form entirely.
echo "$COMMAND" | grep -qiE '(reg(\.exe)?[[:space:]]+delete|Remove-ItemProperty.*Bethesda)' && deny "BLOCKED: Cannot delete Bethesda registry keys."

# === HARD BLOCK -- tool output aimed straight at a .psc source file ===
# .psc sources are irreplaceable. Decompilers have been observed to leave the output
# file empty when their decompilation step fails, destroying what was there.
echo "$COMMAND" | grep -qiE -- '-(o|-output|-OutputPath)\s+["'"'"']?[^"'"'"' ]*\.psc(\b|"|'"'"')' && deny "BLOCKED: Tool output cannot target a .psc file directly. Use a separate output directory."

# === HARD BLOCK -- Champollion against a .pex inside Data/Scripts/ ===
# Champollion writes to Data/Scripts/Source/ regardless of -p/-a, and on a failed
# decompile can leave the output .psc empty. This has destroyed reconstructed
# sources twice. Copy the .pex to a temp directory and run it there.
if echo "$COMMAND" | grep -qiE 'Champollion\b'; then
    echo "$COMMAND" | grep -qiE "($GAME_PATH)[/\\\\]+Data[/\\\\]+Scripts[/\\\\]+[^\"' /\\\\]+[.]pex" \
        && deny "BLOCKED: Champollion against a PEX in Data/Scripts/ has destroyed a .psc twice. Copy the .pex to a temp directory first, then run Champollion there."
fi

# === ADVISE -- destructive or overwriting commands inside the install ===
# NOTE: the game-root deny above is broad enough to catch a drive-qualified rm
# anywhere under a Skyrim path, so this advisory is reached only by paths that
# are NOT drive-qualified (`rm Data/Textures/x.dds`). That is v3.8.2's behaviour
# and the live install's, kept deliberately: narrowing the deny to root-only
# deletions is a real design question and a NEW behaviour neither has been
# exercised with.
echo "$COMMAND" | grep -qiE "(^|[;&|(\`]|[[:space:]])(rm|rmdir|del|erase)[[:space:]].*($ADVISE_PATH)" && advise "Deleting files inside the live game install: $COMMAND. Deleting the game ROOT is denied outright; this is a delete further in, which a mod manager can usually redeploy but your own mod files cannot be. Check the path is what you meant."
echo "$COMMAND" | grep -qiE "$RELATIVE_DATA_DELETE" && advise "Deleting inside Data/ by a RELATIVE path: $COMMAND. The hook is never told the caller's working directory, so it cannot tell whether this resolves into the live install -- check the path is what you meant."
echo "$COMMAND" | grep -qiE "(^|[;&|(\`]|[[:space:]])(mv|cp|move|copy)[[:space:]].*($ADVISE_PATH)" && advise "Moving/copying inside the live game install: $COMMAND. A stray overwrite here is silent -- confirm the destination before relying on it."
echo "$COMMAND" | grep -qiE ">[[:space:]]*[\"']?($GAME_PATH|$CONFIG_PATH)" && advise "Redirecting output into the game/config directory: $COMMAND. A redirect TRUNCATES its target before anything is written."
echo "$COMMAND" | grep -qiE "sed[[:space:]]+-i.*($GAME_PATH|$CONFIG_PATH)" && advise "In-place sed edit in the game directory: $COMMAND. A Windows path in sed's REPLACEMENT is destroyed by escape handling -- normalise the path first."

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
    echo "$COMMAND" | grep -qiE -- "-(o|-output|-OutputPath|-p|-psc|-asm|-a)[[:space:]]+[\"']?($GAME_PATH|$CONFIG_PATH)" \
        && advise "External Papyrus/ESP tool writing into the game/data directory: $COMMAND. Verify the output landed where you intended -- several of these have destructive defaults."
fi

[ -n "$ADVICE" ] && "$JQ" -n --arg r "$ADVICE" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$r}}'
exit 0
