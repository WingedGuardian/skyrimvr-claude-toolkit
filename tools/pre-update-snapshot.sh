#!/usr/bin/env bash
# Copy the files a toolkit update will replace -- BEFORE you extract the new zip over your install.
#
# WHY. Updating is "extract the new zip over your install", which overwrites every file the toolkit ships:
# CLAUDE.md and AGENTS.md (your paths, and any instructions you added), .claude/settings.json (any hook or
# permission you added), KNOWLEDGEBASE.md (3.x told you to append notes to it), the hooks and skills. Once
# extracted, the old copies are gone; tools/migrate-3x.py can only keep what still exists somewhere. This
# takes that copy. It reads your install and writes ONE new folder; it never changes or deletes anything else.
#
# Run it from the NEW toolkit, unpacked anywhere OUTSIDE your install, against your install:
#     bash <unpacked-new-toolkit>/tools/pre-update-snapshot.sh "<your Skyrim folder>"
# (No argument = the current folder.) Then extract the zip over your install and run `bash setup.sh`.
#
# What it copies, into <install>/.claude/backups/pre-update-<stamp>/ with the same relative paths:
#   - always, if present: CLAUDE.md, AGENTS.md, KNOWLEDGEBASE.md, KNOWLEDGEBASE.local.md, skyrim-paths.env,
#     .claude/settings.json, .claude/settings.local.json, .claude/skyrim-paths.env,
#     .devcontainer/devcontainer.json, and everything under .claude/hooks, .claude/skills, .codex, .agents;
#   - plus every file of your install that the new toolkit (the folder this script came from) would
#     overwrite -- when that folder is not your install itself and is not a game folder.
# .claude/backups is never copied into itself. A MANIFEST.txt lists each file with its SHA-256.
#
# Exit: 0 = saved (or nothing to save) · 1 = some file could not be copied (named; the rest is saved)
#       2 = the folder is not a toolkit install

set -uo pipefail

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P) || SRC=""
TARGET_ARG=${1:-$PWD}
if [ ! -d "$TARGET_ARG" ]; then
    echo "pre-update-snapshot: '$TARGET_ARG' is not a folder" >&2
    exit 2
fi
TARGET=$(cd "$TARGET_ARG" && pwd -P)
if [ ! -f "$TARGET/CLAUDE.md" ] && [ ! -f "$TARGET/AGENTS.md" ] && [ ! -d "$TARGET/.claude" ]; then
    echo "pre-update-snapshot: $TARGET has no CLAUDE.md, AGENTS.md or .claude/ -- not a toolkit install." >&2
    echo "  Pass your Skyrim folder (the one you ran setup.sh in):  bash $0 \"<your Skyrim folder>\"" >&2
    exit 2
fi

BACKUPS="$TARGET/.claude/backups"
BASE_STAMP=$(date +%Y%m%d_%H%M%S)
STAMP="$BASE_STAMP"
_n=2
# Unique, not merely current: two runs in one second must not share (and overwrite) a folder.
while [ -e "$BACKUPS/pre-update-$STAMP" ]; do
    STAMP="${BASE_STAMP}_$_n"
    _n=$((_n + 1))
done
DEST="$BACKUPS/pre-update-$STAMP"

LIST=$(mktemp) || { echo "pre-update-snapshot: cannot create a temp file" >&2; exit 1; }
# The script's own temp file, removed by the script itself.
trap 'rm -f "$LIST"' EXIT

for rel in CLAUDE.md AGENTS.md KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md skyrim-paths.env \
           .claude/settings.json .claude/settings.local.json .claude/skyrim-paths.env \
           .devcontainer/devcontainer.json; do
    [ -f "$TARGET/$rel" ] && printf '%s\0' "$rel" >> "$LIST"
done
for dir in .claude/hooks .claude/skills .codex .agents; do
    [ -d "$TARGET/$dir" ] || continue
    ( cd "$TARGET" && find "$dir" -type f -print0 ) >> "$LIST"
done

# Every file the new toolkit would overwrite. Skipped when this script runs from the install itself (the
# tree would be the whole game folder) or from any game folder.
if [ -n "$SRC" ] && [ "$SRC" != "$TARGET" ] && [ ! -f "$SRC/SkyrimVR.exe" ] && [ ! -f "$SRC/SkyrimSE.exe" ]; then
    while IFS= read -r -d '' rel; do
        rel=${rel#./}
        [ -f "$TARGET/$rel" ] && printf '%s\0' "$rel" >> "$LIST"
    done < <(cd "$SRC" && find . \( -path ./.git -o -path ./node_modules -o -path ./.claude/backups \) -prune \
                                -o -type f -print0)
fi

count=0
failed=""
seen=$'\n'
while IFS= read -r -d '' rel; do
    case "$seen" in *$'\n'"$rel"$'\n'*) continue ;; esac
    seen="$seen$rel"$'\n'
    if mkdir -p "$DEST/$(dirname "$rel")" 2>/dev/null && cp -p "$TARGET/$rel" "$DEST/$rel" 2>/dev/null \
       && cmp -s "$TARGET/$rel" "$DEST/$rel"; then
        count=$((count + 1))
    else
        failed="$failed"$'\n'"  $rel"
    fi
done < "$LIST"

if [ "$count" -eq 0 ] && [ -z "$failed" ]; then
    echo "pre-update-snapshot: nothing in $TARGET that an update replaces -- nothing saved."
    exit 0
fi

if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$DEST" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum ) > "$DEST/MANIFEST.txt" 2>/dev/null
else
    ( cd "$DEST" && find . -type f ! -name MANIFEST.txt | sort ) > "$DEST/MANIFEST.txt"
fi

echo "pre-update-snapshot: saved $count file(s) from $TARGET"
echo "  to: $DEST"
if [ -n "$failed" ]; then
    echo "  !! NOT saved (copy failed or did not read back identical):$failed"
    echo "  !! Copy those by hand before you extract the update."
    exit 1
fi
echo "Next: extract the new toolkit zip over $TARGET, then run: bash setup.sh"
echo "(setup carries your own edits forward from this copy; nothing in it is ever deleted.)"
exit 0
