#!/usr/bin/env bash
# Build the Nexus upload pack into its ONE canonical home.
#
# WHY THIS EXISTS. Across eleven releases the pack landed in six different shapes:
# v3.2-3.4 wrote `nexus-vX-description.txt`, v3.5.1 switched to `UPLOAD-NOTES.txt`
# plus `mod-page-full-rewrite.txt`, v3.5.4 added a `PATCH-ONLY` file, v3.7 renamed
# the changelog to `CHANGELOG-for-nexus.txt`, **v3.5 and v3.5.3 shipped EMPTY
# directories**, and **v3.9 split in two: a complete pack (zip, changelog,
# description) landed on the //HUB/Downloads-Temp/Downloads share while the
# canonical home got only UPLOAD-NOTES.txt**.
# `nexus-v3.8/` contains a zip named 3.8.1. Prose could not hold the shape; this
# script is the shape.
#
# THE ZIP IS DOWNLOADED, NEVER REBUILT. The bundle users install is built by
# .github/workflows/release.yml from the TAG, with excludes and payload guards
# (tests/ must not leak, KNOWLEDGEBASE.local.md must not ship). Rebuilding it here
# would produce a second artifact that can silently differ from the one GitHub
# serves. We fetch the published asset, so the file the user uploads to Nexus is
# byte-identical to the file the release page hands out.
#
# Usage:  bash scripts/build-nexus-pack.sh v3.9 <changelog.txt> <description.txt>
# Exit:   0 pack complete and self-verified · 1 refused or incomplete
set -uo pipefail

TAG="${1:-}"
CHANGELOG_SRC="${2:-}"     # authored, one-issue-per-line changelog for the Nexus box
DESCRIPTION_SRC="${3:-}"   # authored Nexus mod-page text
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { printf 'REFUSED: %s\n' "$1" >&2; exit 1; }

# Anchored and dot-segment-free: `v[0-9]*` accepted `/` and `..`, and MEASURED,
# a tag of 'v9/../../../ESCAPED' created a directory as a SIBLING of the pack
# root before the download refused. Refuse the shape, not just the prefix.
if ! printf '%s' "$TAG" | grep -qE '^v[0-9]+(\.[0-9]+)*$'; then
    die "first argument must be a tag like v3.9 (got '${TAG:-<none>}')"
fi
VERSION="${TAG#v}"

# --- where the pack goes -----------------------------------------------------
# Canonical home, decided 2026-09-09: the game root's _CLAUDE_OUTPUT, because a
# Nexus pack is something the USER opens and pastes from, and that is where the
# user's own standing rule puts user-facing output. NEXUS_PACK_ROOT exists so the
# test suite can point this somewhere disposable -- it is NOT a way to improvise a
# new location by hand.
ROOT="${NEXUS_PACK_ROOT:-}"
if [ -z "$ROOT" ]; then
    # The installed copy is the one with real paths; the repo ships only the
    # .example. Prefer an explicit CLAUDE_PROJECT_DIR, then the repo's own file;
    # in each, the toolkit-root file (4.0) before the legacy .claude/ one.
    ENVF=""
    for f in "${CLAUDE_PROJECT_DIR:+$CLAUDE_PROJECT_DIR/skyrim-paths.env}" \
             "${CLAUDE_PROJECT_DIR:+$CLAUDE_PROJECT_DIR/.claude/skyrim-paths.env}" \
             "$REPO_DIR/skyrim-paths.env" "$REPO_DIR/.claude/skyrim-paths.env"; do
        [ -n "$f" ] && [ -f "$f" ] && { ENVF="$f"; break; }
    done
    [ -n "$ENVF" ] || die "no skyrim-paths.env found; set NEXUS_PACK_ROOT or run setup.sh"
    # shellcheck disable=SC1090
    . "$ENVF"
    ROOT="${SKYRIM_GAME_ROOT:-}"
fi
# A guard that cannot resolve its target must not invent one. Guessing a path here
# is exactly the failure this script was written to end.
[ -n "$ROOT" ] || die "SKYRIM_GAME_ROOT is empty -- refusing to guess where the pack goes"
[ -d "$ROOT" ] || die "resolved root does not exist: $ROOT"

DEST="$ROOT/_CLAUDE_OUTPUT/nexus-$TAG"
ZIP_NAME="skyrimvr-claude-toolkit-$VERSION.zip"

mkdir -p "$DEST" || die "cannot create $DEST"
printf 'pack home: %s\n' "$DEST"

# --- 1. the bundle, from the published release -------------------------------
if [ ! -f "$DEST/$ZIP_NAME" ]; then
    printf 'downloading %s from release %s...\n' "$ZIP_NAME" "$TAG"
    gh release download "$TAG" --repo WingedGuardian/skyrimvr-claude-toolkit \
        --pattern '*.zip' --dir "$DEST" --clobber \
        || die "gh release download failed for $TAG (is the release published?)"
fi
[ -f "$DEST/$ZIP_NAME" ] || die "expected asset $ZIP_NAME not present after download -- \
the release may name its asset differently, which would also mean the folder and the zip disagree"

SHA=$(sha256sum "$DEST/$ZIP_NAME" | cut -d' ' -f1)
SIZE=$(wc -c < "$DEST/$ZIP_NAME" | tr -d ' ')
# The completeness gate below checks that files are non-empty, not that their
# CONTENTS are. MEASURED with a failing sha256sum on PATH: the manifest shipped
# `sha256` blank and the run still reported PACK COMPLETE -- a blank hash in the
# one file the pack calls "evidence of what was published".
[ -n "$SHA" ] || die "could not hash $ZIP_NAME -- refusing to ship a manifest with no checksum"
[ -n "$SIZE" ] || die "could not size $ZIP_NAME -- refusing to ship a manifest with no size"

# --- 2. the changelog, AUTHORED not extracted --------------------------------
# MEASURED 2026-09-09: extracting the `## <tag>` block straight out of CHANGELOG.md
# produced 147 lines of markdown against the 63 hand-written lines that were
# actually fit to paste. The Nexus box renders a flat list, and the standing rule
# is ONE ISSUE PER LINE, never combined -- that is a rewrite, not an extract.
#
# So the script owns the SHAPE and the LOCATION, which is what drifted across
# eleven releases. It does not pretend to own the prose. Pass the authored file;
# with no argument it drops the raw block as a .DRAFT to edit, and REFUSES to call
# the pack complete.
CHANGELOG_OUT="$DEST/NEXUS-CHANGELOG-$TAG.txt"
if [ -n "$CHANGELOG_SRC" ]; then
    [ -f "$CHANGELOG_SRC" ] || die "changelog source not found: $CHANGELOG_SRC"
    cp "$CHANGELOG_SRC" "$CHANGELOG_OUT" || die "could not copy changelog into the pack"
elif [ ! -s "$CHANGELOG_OUT" ]; then
    awk -v ver="## $TAG" '
        $0 == ver          { inblock = 1; next }
        inblock && /^## /  { exit }
        inblock            { print }
    ' "$REPO_DIR/CHANGELOG.md" > "$DEST/NEXUS-CHANGELOG-$TAG.DRAFT.txt"
    printf 'wrote NEXUS-CHANGELOG-%s.DRAFT.txt -- raw markdown, NOT ready to paste\n' "$TAG"
fi

# --- 2b. the mod-page description -------------------------------------------
# This CANNOT be generated: it is the live Nexus page, re-read and edited by hand,
# and every count in it rots between releases. The script therefore refuses to call
# a pack complete without one, rather than quietly shipping a pack that looks whole.
DESC_OUT="$DEST/NEXUS-DESCRIPTION-$TAG.txt"
if [ -n "$DESCRIPTION_SRC" ]; then
    [ -f "$DESCRIPTION_SRC" ] || die "description source not found: $DESCRIPTION_SRC"
    cp "$DESCRIPTION_SRC" "$DESC_OUT" || die "could not copy description into the pack"
fi

# --- 3. the manifest: dated evidence of what was published -------------------
{
    printf 'tag           %s\n' "$TAG"
    printf 'version       %s\n' "$VERSION"
    printf 'asset         %s\n' "$ZIP_NAME"
    printf 'sha256        %s\n' "$SHA"
    printf 'size_bytes    %s\n' "$SIZE"
    printf 'source        https://github.com/WingedGuardian/skyrimvr-claude-toolkit/releases/tag/%s\n' "$TAG"
    printf 'packed_utc    %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$DEST/RELEASE-MANIFEST.txt"

# --- 4. what the user has to do by hand --------------------------------------
{
    printf '# Nexus upload — %s\n\n' "$TAG"
    printf 'Nexus has NO write API for files or the mod description. Everything below is\n'
    printf 'manual, by you. This pack contains everything you need to paste.\n\n'
    printf '## Upload\n\n'
    printf '1. Nexus mod page -> Files -> Manage files -> Add file\n'
    printf '2. Upload:  %s\n' "$ZIP_NAME"
    printf '   sha256:  %s\n' "$SHA"
    printf '   size:    %s bytes\n' "$SIZE"
    printf '3. Set file version to %s and mark it MAIN.\n' "$VERSION"
    printf '4. Mark the previous main file as OLD.\n'
    printf '5. Paste NEXUS-CHANGELOG-%s.txt into the changelog box.\n\n' "$TAG"
    printf '## Before you paste the changelog\n\n'
    printf 'Check the version the Nexus page is CURRENTLY on. If it is more than one\n'
    printf 'release behind, users jump the whole gap in a single update, so the changelog\n'
    printf 'must cover every version in between -- not just this one.\n\n'
    printf '## Files in this pack\n\n'
    printf '  %-40s the bundle, downloaded from the GitHub release (not rebuilt)\n' "$ZIP_NAME"
    printf '  %-40s this version block, one item per line\n' "NEXUS-CHANGELOG-$TAG.txt"
    printf '  %-40s sha256/size/source/date -- evidence of what was published\n' "RELEASE-MANIFEST.txt"
    printf '  %-40s the mod page text, ready to paste\n' "NEXUS-DESCRIPTION-$TAG.txt"
    printf '  %-40s this file\n' "UPLOAD-NOTES.md"
} > "$DEST/UPLOAD-NOTES.md"

# --- 5. self-verify: refuse to report success on an incomplete pack ----------
# v3.9 left only UPLOAD-NOTES.txt in the canonical home and nothing said so. This is the check
# that would have caught it, and the folder/zip version check is what would have
# caught nexus-v3.8 holding a 3.8.1 zip.
missing=0
for f in "$ZIP_NAME" "NEXUS-CHANGELOG-$TAG.txt" "NEXUS-DESCRIPTION-$TAG.txt" "RELEASE-MANIFEST.txt" "UPLOAD-NOTES.md"; do
    if [ ! -s "$DEST/$f" ]; then
        printf 'INCOMPLETE: missing or empty -- %s\n' "$f" >&2
        missing=$((missing + 1))
    fi
done
[ "$missing" -eq 0 ] || die "$missing required file(s) missing from $DEST -- the changelog and description are AUTHORED, not generated: build-nexus-pack.sh <tag> <changelog.txt> <description.txt>"

# The previous form tested ZIP_NAME against the VERSION it was constructed from,
# so it could never fail, while its comment claimed it would have caught
# `nexus-v3.8/` holding a 3.8.1 zip. Compare against what the release ACTUALLY
# put on disk, which is a check that can go red.
landed=$(ls -1 "$DEST"/*.zip 2>/dev/null | wc -l | tr -d ' ')
if [ "$landed" != "1" ]; then
    die "$landed zip(s) in $DEST -- expected exactly $ZIP_NAME; a stray asset would ship as part of the pack"
fi
actual=$(basename "$(ls -1 "$DEST"/*.zip)")
if [ "$actual" != "$ZIP_NAME" ]; then
    die "release $TAG published '$actual' but this pack is named for $VERSION -- folder and zip disagree"
fi

printf '\nPACK COMPLETE: %s\n' "$DEST"
ls -1 "$DEST" | sed 's/^/  /'
