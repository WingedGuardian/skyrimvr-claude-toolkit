#!/bin/bash
# Guard Edit/Write against damaging a live Skyrim install.
#
# SETUP: run setup.sh, which fills in the JQ path below. By hand: install jq
# (winget install jqlang.jq), run `where jq`, and paste the path into JQ.
#
# POLICY. A hook exists to stop the ASSISTANT, not to interrupt you. Every rule is
# therefore one of:
#   deny   - refuse the call and say why. Reserved for what is NEVER correct.
#   advise - inject a note into the assistant's context; the call proceeds and you
#            are never prompted.
# `ask` is reserved for what is GENUINELY your decision, and is unused in this file.
# A guard that prompts on routine work gets approved by reflex and then protects
# nothing -- the X4 toolkit measured 40 such prompts across 13,282 commands, every
# one of them noise.
#
# Deny is deliberately a SHORT list. "The mod manager owns it" or "this is risky" is
# a reason to advise, not to refuse: there are legitimate uses for nearly everything
# here, and a guard that blocks legitimate work is a guard that gets deleted.

JQ="{{JQ_PATH}}"

# MEASURED (X4 toolkit 2026-08-29, reproduced on a second machine 2026-09-06):
# `cat /dev/stdin` returns ZERO BYTES in the Claude Code hook environment, while a
# bare `cat` returns the payload. Seven consecutive probes: 0 bytes via /dev/stdin,
# 641-2840 bytes via bare cat, PreToolUse and PostToolUse alike.
#
# A hook that reads nothing falls through its first guard and exits 0, which is
# BYTE-IDENTICAL to deciding "this is fine". Every hook here was inert that way for
# months while the audit log recorded an empty command field in 2,454 of 2,454
# entries. A TEST SUITE CANNOT CATCH IT: a suite pipes stdin, so /dev/stdin resolves
# fine there. Green in the harness, dead in production.
INPUT=$(cat)

# Hooks live at <project>/.claude/hooks/, so this file's own location is a reliable
# fallback when CLAUDE_PROJECT_DIR is not set.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"
BACKUP_DIR="$PROJECT_DIR/.claude/backups"
AUDIT_LOG="$BACKUP_DIR/AUDIT_LOG.txt"
HB_DIR="$BACKUP_DIR/.hook-heartbeat"

# --- liveness heartbeat ------------------------------------------------------
# Records that this hook RAN **AND SAW ITS PAYLOAD**. Two different facts, and only
# the second was ever in doubt. `tools/hook-canary.sh` reads these back. The signal
# has to be written from inside a real invocation precisely because a suite cannot
# reproduce the condition that breaks it.
mkdir -p "$HB_DIR" 2>/dev/null
if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/protect-files" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/protect-files.blind" 2>/dev/null
fi

hooklog() { printf '[%s] protect-files %s: %s\n' "$(date +%Y%m%d_%H%M%S)" "$1" "$2" >> "$AUDIT_LOG" 2>/dev/null; }

FILE_PATH=$(echo "$INPUT" | "$JQ" -r '.tool_input.file_path // empty')

# A guard that cannot see its input must not stay SILENT -- silence IS allow, and
# that is exactly how the defect above survived undetected. Refuse loudly instead,
# and say that the rules were never evaluated.
if [ -z "$FILE_PATH" ]; then
    hooklog REFUSE "no file_path in payload -- hook could not evaluate its rules"
    "$JQ" -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"GUARD INERT: protect-files.sh received no file_path and therefore checked NOTHING. Allowing silently would be indistinguishable from deciding this is fine. Investigate the hook payload before retrying."}}'
    exit 0
fi

deny() { hooklog DENY "$1"; "$JQ" -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }

# An advisory is a NOTE, not a decision, so it must never make the rules below it
# unreachable: advisories accumulate and are emitted once, at the end.
ADVICE=""
advise() { hooklog ADVISE "$1"; ADVICE="${ADVICE:+$ADVICE | }$1"; }

# === HARD BLOCK -- binary plugin/archive files ===
# The only deny here. A text write into a binary plugin corrupts it, and there is a
# correct tool for every legitimate case (xelib, Spriggit, AutoMod).
echo "$FILE_PATH" | grep -qiE '\.(esp|esm|esl|bsa|ba2)$' && deny "BLOCKED: Cannot directly write to plugin/archive files. Use xelib, Spriggit or AutoMod."

# === WHITELIST -- our own workspace (silent, no note) ===
echo "$FILE_PATH" | grep -qiE '[/\\]\.claude[/\\](hooks|plans|backups|memory|projects)[/\\]' && exit 0
echo "$FILE_PATH" | grep -qiE '[/\\]node_modules[/\\]' && exit 0

# === ADVISE -- legitimate but consequential ===
echo "$FILE_PATH" | grep -qiE '(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)$' \
    && advise "SKYRIM CONFIG: $FILE_PATH. Load order is Skyrim.ini then SkyrimVR.ini then SkyrimPrefs.ini (last wins), so a setting here can be overridden by a later file. Diff before and after rather than trusting the edit."
echo "$FILE_PATH" | grep -qiE '[/\\]Data[/\\]SKSE[/\\]Plugins[/\\].*\.ini$' \
    && advise "SKSE PLUGIN CONFIG: $FILE_PATH. Not captured by any snapshot hook -- copy it yourself before changing it."
echo "$FILE_PATH" | grep -qiE '(loadorder\.txt|plugins\.txt)$' \
    && advise "LOAD ORDER: $FILE_PATH. Your mod manager owns these and may overwrite a direct edit on its next deploy, so make sure a direct edit is really what is wanted. Note the Special Edition copy under AppData is a DIFFERENT file from the VR one and is routinely stale."
echo "$FILE_PATH" | grep -qiE '\.(pex|psc)$' \
    && advise "PAPYRUS SCRIPT: $FILE_PATH. A .pex loads at game startup ONLY, so a running session will not see this change. Snapshot to .claude/backups/<name>/ before experimenting."

# === ADVISE -- catch-all, only if nothing more specific already fired ===
if [ -z "$ADVICE" ]; then
    echo "$FILE_PATH" | grep -qiE "([/\\\\]Data[/\\\\]|Skyrim|My Games[/\\\\]Skyrim)" \
        && advise "Editing inside the live game/config install: $FILE_PATH"
fi

[ -n "$ADVICE" ] && "$JQ" -n --arg r "$ADVICE" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$r}}'
exit 0
