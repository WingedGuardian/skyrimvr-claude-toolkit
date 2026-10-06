#!/bin/bash
# Wrapper for SpookyPirate's AutoMod CLI
# Usage: bash tools/automod-cli.sh <module> <command> [args] --json
#        bash tools/automod-cli.sh --rebuild   build (or rebuild) it -- see below
#        bash tools/automod-cli.sh --built     which commit the DLL was built from, vs the pin
#
# Invokes the pre-built Release DLL directly (no per-call recompile) to avoid the
# MSB1025 / SDK-mismatch failures that `dotnet run` triggered.
#
# PINNED. The toolkit was tested with AutoMod v1.11.2 = commit e1c34ea; that is the only
# version `--rebuild` builds. A clone at another commit is moved to the pin when it is clean
# (only the wrapper's own one-line patch is undone first); a clone with local changes is
# never touched -- `--rebuild` stops and says what to do.
#
# .NET 10 ONLY. The .NET 10 SDK is the toolkit's single .NET requirement. Upstream targets
# net8.0 and ships no global.json, so `--rebuild` then:
#   1. retargets the clone to net10.0 -- a LOCAL PATCH of upstream's Directory.Build.props
#      (net8.0 -> net10.0; nothing else changes), applied idempotently, refused for a TFM we
#      never tested,
#   2. writes tools/automod/global.json selecting SDK 10.0.x (rollForward latestFeature),
#   3. builds the Cli project only (Setup is a WPF net8.0-windows app, never built headless),
#   4. records the commit it built in bin/Release/net10.0/toolkit-built-from.txt.
# Retarget, not roll-forward: a net8.0 build on a 10-only machine does not start without
# DOTNET_ROLL_FORWARD=Major on every launch (MEASURED 2026-10-05). The first build needs
# internet (NuGet restore).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOMOD="$SCRIPT_DIR/automod"
CLI_PROJ="$AUTOMOD/src/SpookysAutomod.Cli"
OUT="$CLI_PROJ/bin/Release/net10.0"
DLL="$OUT/spookys-automod.dll"
MARKER="$OUT/toolkit-built-from.txt"
OLD_DLL="$CLI_PROJ/bin/Release/net8.0/spookys-automod.dll"
PROPS="$AUTOMOD/Directory.Build.props"
TFM_OLD="<TargetFramework>net8.0</TargetFramework>"
TFM_NEW="<TargetFramework>net10.0</TargetFramework>"
PIN_TAG="v1.11.2"
PIN_COMMIT="e1c34ea34cae4b9a1a547ea6a90c8a91412d689e"
# Test seam: tests/test_automod_cli.py names its own fixture commit (it cannot fabricate this SHA). Honoured
# ONLY together with TOOLKIT_TEST_MODE=1, which the tests set and real users never do: an override someone
# merely has in their environment would otherwise make --built and toolchain-check call a wrong commit
# "pinned" -- the false green the pin exists to stop (re-review N1).
if [ -n "${AUTOMOD_TEST_PIN_COMMIT:-}" ]; then
    if [ "${TOOLKIT_TEST_MODE:-}" = 1 ]; then
        PIN_COMMIT="$AUTOMOD_TEST_PIN_COMMIT"
    else
        echo "note: AUTOMOD_TEST_PIN_COMMIT is a test-only setting and was ignored (pin: $PIN_TAG)" >&2
    fi
fi
CLONE_HINT="git clone --branch $PIN_TAG https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod"
# Test seam: tests/test_automod_cli.py points this at a fake `dotnet` (the harness shadows the real name).
DOTNET="${AUTOMOD_DOTNET:-dotnet}"

g() { git -C "$AUTOMOD" "$@"; }

# True when Directory.Build.props differs from HEAD's ONLY by our net8.0 -> net10.0 line.
only_our_patch() {
    [ "$(g diff --name-only)" = "Directory.Build.props" ] || return 1
    [ "$(g show HEAD:Directory.Build.props | sed "s#$TFM_OLD#$TFM_NEW#" | tr -d '\r')" = "$(tr -d '\r' < "$PROPS")" ]
}

# True when commit $1 stays reachable after HEAD moves: it is an ancestor of the pin, or a tag or a branch
# (local or remote) contains it.
held_elsewhere() {
    g merge-base --is-ancestor "$1" "$PIN_COMMIT" 2>/dev/null && return 0
    [ -n "$(g tag --contains "$1" 2>/dev/null)" ] && return 0
    [ -n "$(g branch -a --contains "$1" 2>/dev/null | grep -v 'HEAD detached')" ]
}

# Tracked changes OTHER than our own one-line TFM patch ("" when the tree is what was committed).
local_changes() {
    local files
    files="$(g diff --name-only HEAD)"
    if [ "$files" = "Directory.Build.props" ] && only_our_patch; then files=""; fi
    printf '%s' "$files"
}

# Put the clone on the pinned commit, or stop without changing anything.
to_pinned_commit() {
    local head dirty
    head="$(g rev-parse HEAD 2>/dev/null)" || {
        echo "tools/automod is not a git clone, so its version cannot be checked." >&2
        echo "Move it aside and run: $CLONE_HINT" >&2
        return 1
    }
    [ "$head" = "$PIN_COMMIT" ] && return 0
    echo "AutoMod is at ${head:0:7}; this toolkit was tested with $PIN_TAG (${PIN_COMMIT:0:7})."
    if only_our_patch; then g checkout -q -- Directory.Build.props || return 1; fi
    dirty="$(g status --porcelain --untracked-files=no)"
    if [ -n "$dirty" ]; then
        echo "tools/automod has local changes, so it was NOT moved to $PIN_TAG:" >&2
        printf '%s\n' "$dirty" >&2
        echo "Commit or stash them, then: git -C tools/automod checkout $PIN_TAG && bash tools/automod-cli.sh --rebuild" >&2
        return 1
    fi
    if ! g cat-file -e "$PIN_COMMIT^{commit}" 2>/dev/null; then
        echo "Fetching $PIN_TAG..."
        g fetch -q --tags origin
    fi
    g cat-file -e "$PIN_COMMIT^{commit}" 2>/dev/null || {
        echo "Commit $PIN_COMMIT ($PIN_TAG) is not in tools/automod and could not be fetched (internet?)." >&2
        return 1
    }
    # A detached HEAD carrying commits that no branch, no tag and not the pin contains would, once moved,
    # survive only in the reflog (re-review N2). A branch is never moved by `checkout --detach`.
    if ! g symbolic-ref -q HEAD >/dev/null && ! held_elsewhere "$head"; then
        echo "tools/automod is on a detached commit ${head:0:7} that no branch or tag holds, so it was NOT moved:" >&2
        echo "moving would leave that commit reachable only from git's reflog. To keep it, give it a branch:" >&2
        echo "  git -C tools/automod branch my-automod ${head:0:7}" >&2
        echo "then run: bash tools/automod-cli.sh --rebuild" >&2
        return 1
    fi
    g checkout -q --detach "$PIN_COMMIT" || return 1
    echo "Checked out $PIN_TAG (${PIN_COMMIT:0:7})."
}

if [ "$1" == "--rebuild" ]; then
    if [ ! -d "$CLI_PROJ" ] || [ ! -f "$PROPS" ]; then
        echo "AutoMod is not cloned at: $AUTOMOD" >&2
        echo "Run: $CLONE_HINT" >&2
        exit 1
    fi
    to_pinned_commit || exit 1
    # Validate before writing anything: a refused rebuild leaves global.json alone.
    if ! grep -qF "$TFM_OLD" "$PROPS" && ! grep -qF "$TFM_NEW" "$PROPS"; then
        echo "Cannot retarget AutoMod: Directory.Build.props has no $TFM_OLD or $TFM_NEW." >&2
        echo "Found: $(grep -o '<TargetFramework>[^<]*</TargetFramework>' "$PROPS" | head -1)" >&2
        echo "This toolkit was tested with AutoMod $PIN_TAG; check out that tag and rebuild." >&2
        exit 1
    fi
    if grep -qF "$TFM_OLD" "$PROPS"; then
        sed -b -i "s#$TFM_OLD#$TFM_NEW#" "$PROPS" || exit 1
        echo "Retargeted AutoMod to net10.0 (local patch of Directory.Build.props)."
    fi
    printf '{ "sdk": { "version": "10.0.100", "rollForward": "latestFeature" } }\n' > "$AUTOMOD/global.json" || exit 1
    echo "Rebuilding AutoMod CLI (Release, net10.0)..."
    "$DOTNET" build "$CLI_PROJ" -c Release
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "AutoMod build failed (rc=$rc). The first build needs internet for the NuGet restore," >&2
        echo "and the .NET 10 SDK (dotnet --list-sdks)." >&2
        exit "$rc"
    fi
    # The record: the commit built, plus "dirty" when tracked files differed from it (re-review N3).
    { printf '%s' "$(g rev-parse HEAD)"; [ -n "$(local_changes)" ] && printf ' dirty'; echo; } > "$MARKER" || exit 1
    exit 0
fi

if [ "$1" == "--built" ]; then
    if [ ! -f "$DLL" ]; then echo "AutoMod is not built for net10.0. Run: bash tools/automod-cli.sh --rebuild"; exit 1; fi
    want="toolkit pinned to $PIN_TAG (${PIN_COMMIT:0:12}). Run: bash tools/automod-cli.sh --rebuild"
    if [ ! -f "$MARKER" ]; then
        echo "AutoMod built from an unknown commit (no build record: not built by --rebuild); $want"; exit 1
    fi
    if [ "$DLL" -nt "$MARKER" ]; then
        echo "AutoMod's DLL changed after its last --rebuild (unknown source); $want"; exit 1
    fi
    read -r built state < "$MARKER"
    built="${built%$'\r'}"; state="${state%$'\r'}"   # a record written with CRLF
    if [ "$built" != "$PIN_COMMIT" ]; then
        echo "AutoMod built from ${built:0:12}, $want"; exit 1
    fi
    if [ -n "$state" ]; then
        echo "AutoMod built from $PIN_TAG (${PIN_COMMIT:0:12}) WITH local changes, not what the toolkit tested. Revert them (git -C tools/automod status), then: bash tools/automod-cli.sh --rebuild"
        exit 1
    fi
    echo "AutoMod $PIN_TAG (${PIN_COMMIT:0:12}) built for net10.0"
    exit 0
fi

if [ ! -f "$DLL" ]; then
    if [ -f "$OLD_DLL" ]; then
        echo "AutoMod was built for net8.0 by an older toolkit: $OLD_DLL" >&2
        echo "This toolkit needs only the .NET 10 SDK, so rebuild it for net10.0." >&2
    else
        echo "Built DLL not found at: $DLL" >&2
    fi
    echo "Run: bash tools/automod-cli.sh --rebuild" >&2
    exit 1
fi

"$DOTNET" "$DLL" "$@"
