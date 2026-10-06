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
# Test seam: tests/test_automod_cli.py names its own fixture commit (it cannot fabricate this SHA).
PIN_COMMIT="${AUTOMOD_TEST_PIN_COMMIT:-e1c34ea34cae4b9a1a547ea6a90c8a91412d689e}"
CLONE_HINT="git clone --branch $PIN_TAG https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod"
# Test seam: tests/test_automod_cli.py points this at a fake `dotnet` (the harness shadows the real name).
DOTNET="${AUTOMOD_DOTNET:-dotnet}"

g() { git -C "$AUTOMOD" "$@"; }

# True when Directory.Build.props differs from HEAD's ONLY by our net8.0 -> net10.0 line.
only_our_patch() {
    [ "$(g diff --name-only)" = "Directory.Build.props" ] || return 1
    [ "$(g show HEAD:Directory.Build.props | sed "s#$TFM_OLD#$TFM_NEW#" | tr -d '\r')" = "$(tr -d '\r' < "$PROPS")" ]
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
    g rev-parse HEAD > "$MARKER" || exit 1
    exit 0
fi

if [ "$1" == "--built" ]; then
    if [ ! -f "$DLL" ]; then echo "AutoMod is not built for net10.0. Run: bash tools/automod-cli.sh --rebuild"; exit 1; fi
    built="$(tr -d '\r\n ' < "$MARKER" 2>/dev/null)"
    if [ "$built" = "$PIN_COMMIT" ]; then
        echo "AutoMod $PIN_TAG (${PIN_COMMIT:0:12}) built for net10.0"
        exit 0
    fi
    echo "AutoMod built from ${built:+${built:0:12}}${built:-an unknown commit (no build marker)}, toolkit pinned to $PIN_TAG (${PIN_COMMIT:0:12}). Run: bash tools/automod-cli.sh --rebuild"
    exit 1
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
