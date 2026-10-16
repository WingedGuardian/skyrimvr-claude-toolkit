#!/usr/bin/env bash
# Keep a copy of the files nothing else can rebuild -- and say so when one shrinks.
#
# WHY THIS EXISTS. The documented update path for this toolkit is "extract the new zip
# over your install", so every file the toolkit ships is REPLACED on update. Two of
# them are files you are told to edit: KNOWLEDGEBASE.md (the standing instruction in
# CLAUDE.md appends findings to it after every session) and CLAUDE.md itself (which
# carries your install's own paths). v3.9 moved future accumulation to
# KNOWLEDGEBASE.local.md, which is untracked and gated out of the release payload --
# but that protects only what has not been written yet. An install whose notes are
# ALREADY in the shipped file is covered by exactly one thing: a copy taken BEFORE the
# extract. This takes that copy, once per session, from SessionStart.
#
# ⚠ MEASURED on the author's install 2026-09-08: the backup store held ZERO copies of a
# 177,459-byte KNOWLEDGEBASE.md across seven months. Not because backup-before-edit.sh is
# broken -- it works, and it has no .md or size filter. The cause is the write CHANNEL:
# the knowledgebase is edited through Python invoked via Bash, which an Edit/Write hook
# structurally cannot see. A file can be edited hundreds of times, backed up never, and
# nothing anywhere reports it. That is the gap this closes.
#
# ⚠ WHAT IT DOES NOT DO. The alarm fires on shrinkage; an update that replaces your
# 100 KB knowledgebase with a 140 KB shipped one is a total loss that shrinks nothing
# and will NOT alarm. The SNAPSHOT is the protection here; the alarm is a convenience
# on top of it. Recovery is always "the previous copy is in the store".
#
# Usage:  bash tools/kb-guard.sh [--verbose]
# Exit:   0 = checked · 1 = completed ALARM · 2 = cannot check or preserve every watched file

set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
STORE="$ROOT/.claude/backups/kb"

# The irreplaceable set: files a user writes into that this toolkit also ships.
# Anything the toolkit can regenerate (skyrim-paths.env, settings.json) is NOT here --
# a store full of reproducible files makes the copies that matter harder to find.
# AGENTS.md (Plan 4): the same installed instructions, with the same filled-in paths, for Codex.
WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md AGENTS.md"

KEEP="${KB_GUARD_KEEP:-30}"
# A non-numeric KEEP used to kill the script inside the rotation arithmetic under
# `set -u`, and exit 1 is this tool's ALARM code -- so a successful snapshot was
# reported to the user as an irreplaceable file having lost content, with two raw bash
# errors injected into the session. Validate rather than trust.
case "$KEEP" in
    ''|*[!0-9]*) KEEP=30 ;;
esac
[ "$KEEP" -lt 2 ] && KEEP=2
VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
    echo "KB GUARD: NOT RUN -- could not resolve the install root, so nothing was checked" >&2
    exit 2
fi

bytes() {
    local n
    n=$(wc -c < "$1" 2>/dev/null) || n=0
    echo $((n))
}

# The newest stored copy of $1, or empty. The glob expands in sorted order and the
# directories are timestamp-named, so the LAST match is the most recent.
prior_copy() {
    local name="$1" d found=""
    for d in "$STORE"/*/; do
        [ -f "$d$name" ] && found="$d$name"
    done
    printf '%s' "$found"
}

# Every stored file's size, read in ONE pass. MEASURED before this existed: a full
# store (30 directories x 3 files) with three alarms took 21.7 s idle and 39.3 s under
# load -- against a 30 s hook budget in which a late answer is DISCARDED, so the tool
# was slowest in precisely the state it exists for, and the ALARM was the thing most
# likely never to arrive. ~92% of that cost was one `wc` fork per file per comparison
# inside the rotation. `wc -c` takes many files at once, so this is one fork, not ~190.
declare -A STORE_SIZE=()
scan_store() {
    STORE_SIZE=()
    local sz path
    while read -r sz path; do
        case "$path" in ""|total) continue ;; esac
        STORE_SIZE["$path"]=$sz
    done < <(find "$STORE" -mindepth 2 -maxdepth 2 -type f -exec wc -c {} + 2>/dev/null)
}

# The BIGGEST stored copy of $1, which is not always the newest and is what someone
# recovering actually wants. If an extract-over went unnoticed for two sessions, the
# newest stored copy is the truncated one; pointing a user at that would hand them the
# damage back and call it a backup. Returns via globals: a command substitution here
# would fork once per stored directory, which is the cost this rewrite removes.
largest_copy_of() {
    local name="$1" d sz
    LARGEST_PATH=""; LARGEST_SIZE=0
    for d in "$STORE"/*/; do
        [ -f "$d$name" ] || continue
        sz=${STORE_SIZE["$d$name"]:-0}
        if [ "$sz" -gt "$LARGEST_SIZE" ]; then LARGEST_SIZE=$sz; LARGEST_PATH="$d$name"; fi
    done
}

# The two lines every alarm ends with: where the last copy is, and -- when they differ
# -- where the biggest one is.
where_to_recover() {
    local name="$1" prior="$2"
    largest_copy_of "$name"
    printf '    previous copy: %s' "$prior"
    if [ -n "$LARGEST_PATH" ] && [ "$LARGEST_PATH" != "$prior" ]; then
        printf '\n    LARGEST stored copy: %s (%s bytes)' "$LARGEST_PATH" "$LARGEST_SIZE"
    fi
}

if ! mkdir -p "$STORE" 2>/dev/null; then
    echo "KB GUARD: NOT RUN -- cannot create $STORE, so nothing was checked" >&2
    exit 2
fi

# ⚠ The stamp must be UNIQUE, not merely current. Two runs inside the same second
# otherwise share a directory and the second `cp` overwrites the first -- which is the
# exact loss this tool exists to prevent, committed by the tool itself. MEASURED while
# building it: a probe that emptied a 9000-byte file one second after snapshotting it
# left a 0-byte copy in the store where the 9000-byte one had been, and the rotation's
# "never prune the largest" rule then had nothing left to protect. Sessions rarely
# start twice in a second; `bash tools/kb-guard.sh` run twice by hand does.
# The suffix sorts AFTER the bare stamp, so newest-last ordering survives it.
BASE_STAMP=$(date +%Y%m%d_%H%M%S)
STAMP="$BASE_STAMP"
_n=2
while [ -e "$STORE/$STAMP" ]; do
    STAMP="${BASE_STAMP}_$_n"
    _n=$((_n + 1))
done
DEST="$STORE/$STAMP"
scan_store

alarms=""
copied=""
failed=""
checked=0
notes=""

# Copy, and say so when it does not work. `mkdir -p ... 2>/dev/null` and
# `cp ... 2>/dev/null && copied=...` discarded every failure, so a run that stored
# NOTHING -- disk full, a file lock from antivirus or OneDrive, an ACL, or a stray
# regular file sitting where the stamp directory belongs -- printed "checked,
# unchanged" and counted the empty directory it had just made as "1 kept".
store_copy() {
    local live="$1" name="$2" label="$3"
    if ! mkdir -p "$DEST" 2>/dev/null; then
        failed="$failed $name(mkdir)"
        return 1
    fi
    if ! cp -p "$live" "$DEST/$name" 2>/dev/null; then
        failed="$failed $name(copy)"
        return 1
    fi
    copied="$copied $name($label)"
    return 0
}

for name in $WATCHED; do
    live="$ROOT/$name"
    prior=$(prior_copy "$name")

    if [ ! -f "$live" ]; then
        # Absent and never seen is normal -- KNOWLEDGEBASE.local.md does not exist
        # until the first finding is written. Absent after we HAD one is not.
        #
        # Gate on the LARGEST stored copy, not the newest. Gating on the newest meant
        # that a file emptied in one session and deleted in the next reported "checked,
        # unchanged" and exit 0 -- because by then the newest stored copy was the 0-byte
        # one -- while a full copy sat in the store, unnamed. The loss went from loud to
        # silent by getting worse.
        largest_copy_of "$name"
        if [ "$LARGEST_SIZE" -gt 0 ]; then
            checked=$((checked + 1))
            alarms="$alarms
  $name is GONE. Its largest stored copy is $LARGEST_SIZE bytes.
$(where_to_recover "$name" "${prior:-$LARGEST_PATH}")"
        fi
        continue
    fi

    checked=$((checked + 1))
    cur=$(bytes "$live")

    if [ -z "$prior" ]; then
        store_copy "$live" "$name" "first,${cur}b"
        continue
    fi

    # cmp, not a hash: exact, and it needs no sha256sum/md5sum to exist.
    if cmp -s "$live" "$prior"; then
        # An alarm fires on the TRANSITION, so a user who scrolls past one never sees
        # it again -- every later session compares the truncated file against the
        # equally truncated newest copy and reports a clean install. This is the quiet
        # standing reminder for that state: not a second alarm (a deliberate trim would
        # then nag forever), but not silence either.
        largest_copy_of "$name"
        if [ "$LARGEST_SIZE" -gt 0 ] && [ $((cur * 2)) -lt "$LARGEST_SIZE" ]; then
            notes="$notes
  note: $name is $cur bytes; the largest stored copy is $LARGEST_SIZE bytes ($LARGEST_PATH)"
        fi
        continue
    fi

    prev=$(bytes "$prior")
    if [ "$cur" -eq 0 ] && [ "$prev" -gt 0 ]; then
        alarms="$alarms
  $name is EMPTY. It was $prev bytes at the last snapshot.
$(where_to_recover "$name" "$prior")"
    elif [ "$prev" -gt 0 ] && [ $((cur * 2)) -lt "$prev" ]; then
        alarms="$alarms
  $name lost more than half its bytes: $prev -> $cur since the last snapshot.
    That is what extracting a toolkit update over your install looks like.
$(where_to_recover "$name" "$prior")"
    fi

    # Snapshot regardless -- including when it alarmed. The alarm names the PREVIOUS
    # copy, which the store already holds and the rotation below is forbidden to eat;
    # skipping the new copy would only mean losing the shrunken state as well.
    store_copy "$live" "$name" "${prev}->${cur}b"
done

# The rotation reads sizes of files this run has just written, so the table has to be
# refreshed. Still one fork, not one per file.
scan_store

# ---------------------------------------------------------------- rotation
#
# AFTER the new copy is on disk, never before: a prune that runs first can delete the
# last good copy and then fail to write a new one.
#
# ⚠ Three kinds of directory are never eligible, whatever the limit says: the OLDEST,
# the NEWEST, and whichever holds the LARGEST copy of each watched file. Without the
# largest rule the store eats its own good copy in the sessions following a loss --
# every session after an extract-over snapshots the truncated file, and a plain
# oldest-first rotation walks the full-size copy off the end. That is worse than having
# no store at all, because it looks like protection the whole time it is destroying the
# thing it protects. (The newest is pinned because at a small KEEP the oldest-first
# walk would otherwise delete every unprotected directory, including the one written
# seconds earlier -- correct arithmetic, useless result.)
prune_store() {
    local dirs=() d p name best bestsize sz skip removed target
    for d in "$STORE"/*/; do
        [ -d "$d" ] && dirs+=("$d")
    done
    [ "${#dirs[@]}" -le "$KEEP" ] && return 0

    local protected=("${dirs[0]}" "${dirs[$(( ${#dirs[@]} - 1 ))]}")
    for name in $WATCHED; do
        best=""; bestsize=0
        for d in "${dirs[@]}"; do
            [ -f "$d$name" ] || continue
            sz=${STORE_SIZE["$d$name"]:-0}
            if [ "$sz" -ge "$bestsize" ]; then bestsize=$sz; best="$d"; fi
        done
        [ -n "$best" ] && protected+=("$best")
    done

    removed=0
    target=$(( ${#dirs[@]} - KEEP ))
    for d in "${dirs[@]}"; do
        [ "$removed" -ge "$target" ] && break
        skip=0
        for p in "${protected[@]}"; do
            [ "$d" = "$p" ] && skip=1
        done
        [ "$skip" -eq 1 ] && continue
        rm -rf "$d" 2>/dev/null && removed=$((removed + 1))
    done
}
prune_store

# ---------------------------------------------------------------- report
kept=0
for d in "$STORE"/*/; do
    [ -d "$d" ] && kept=$((kept + 1))
done

if [ -n "$alarms" ]; then
    echo "=============================================================="
    echo "KB GUARD -- ALARM: an irreplaceable file lost content"
    echo "=============================================================="
    printf '%s\n' "$alarms"
    echo
    echo "  Compare before writing anything else into it, then merge by hand:"
    echo "    diff \"<previous copy>\" \"$ROOT/<file>\""
    echo "  Extracting a toolkit update over an install replaces every file the"
    echo "  toolkit ships. Keep your own findings in KNOWLEDGEBASE.local.md, which"
    echo "  is never shipped."
    [ -z "$failed" ] && exit 1
fi

if [ "$checked" -eq 0 ]; then
    echo "KB GUARD: NOT RUN -- none of the watched files exist under $ROOT ($WATCHED)" >&2
    exit 2
fi

if [ -n "$failed" ]; then
    echo "KB GUARD: FAILED to store --$failed ($kept kept). Nothing was copied for those"
    echo "  files this session, so they are UNPROTECTED, not unchanged. Check free space"
    echo "  and whether $STORE is writable."
    [ -n "$copied" ] && echo "KB GUARD: did store --$copied"
    printf '%s' "$notes"
    [ -n "$notes" ] && echo
    exit 2
fi

if [ -n "$copied" ]; then
    echo "KB GUARD: snapshot $STAMP --$copied ($kept kept)"
    printf '%s' "$notes"
    [ -n "$notes" ] && echo
else
    echo "KB GUARD: $checked file(s) checked, unchanged ($kept kept)"
    printf '%s' "$notes"
    [ -n "$notes" ] && echo
fi

# --verbose had quietly become a no-op once the plain report started naming the
# unchanged count, while the README and CLAUDE.md both tell users to pass it. A
# documented flag that does nothing is a small lie in a permanent record.
if [ "$VERBOSE" -eq 1 ]; then
    echo "  store: $STORE"
    for name in $WATCHED; do
        if [ -f "$ROOT/$name" ]; then
            largest_copy_of "$name"
            printf '  %-24s live %s b, largest stored %s b\n' \
                "$name" "$(bytes "$ROOT/$name")" "$LARGEST_SIZE"
        else
            printf '  %-24s not present\n' "$name"
        fi
    done
fi
exit 0
