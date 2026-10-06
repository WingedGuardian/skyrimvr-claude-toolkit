#!/bin/bash
# Wrapper for SpookyPirate's AutoMod CLI
# Usage: bash tools/automod-cli.sh <module> <command> [args] --json
#
# Invokes the pre-built Release DLL directly (no per-call recompile) to avoid the
# MSB1025 / SDK-mismatch failures that `dotnet run` triggered.
#
# .NET 10 ONLY. The .NET 10 SDK is the toolkit's single .NET requirement. Upstream
# AutoMod (tested: v1.11.2) targets net8.0 and ships no global.json, so `--rebuild`:
#   1. writes tools/automod/global.json selecting SDK 10.0.x (rollForward latestFeature),
#   2. retargets the clone to net10.0 -- a LOCAL PATCH of upstream's Directory.Build.props
#      (net8.0 -> net10.0; nothing else changes), applied idempotently,
#   3. builds the Cli project only (Setup is a WPF net8.0-windows app, never built headless).
# Retarget, not roll-forward: a net8.0 build on a 10-only machine does not start without
# DOTNET_ROLL_FORWARD=Major on every launch (MEASURED 2026-10-05). The first build needs
# internet (NuGet restore).
#
# To build, or rebuild after a source update:  bash tools/automod-cli.sh --rebuild
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOMOD="$SCRIPT_DIR/automod"
CLI_PROJ="$AUTOMOD/src/SpookysAutomod.Cli"
DLL="$CLI_PROJ/bin/Release/net10.0/spookys-automod.dll"
OLD_DLL="$CLI_PROJ/bin/Release/net8.0/spookys-automod.dll"
PROPS="$AUTOMOD/Directory.Build.props"
TFM_OLD="<TargetFramework>net8.0</TargetFramework>"
TFM_NEW="<TargetFramework>net10.0</TargetFramework>"
# Test seam: tests/test_automod_cli.py points this at a fake `dotnet` (the harness shadows the real name).
DOTNET="${AUTOMOD_DOTNET:-dotnet}"

if [ "$1" == "--rebuild" ]; then
    if [ ! -d "$CLI_PROJ" ] || [ ! -f "$PROPS" ]; then
        echo "AutoMod is not cloned at: $AUTOMOD" >&2
        echo "Run: git clone --branch v1.11.2 https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod" >&2
        exit 1
    fi
    printf '{ "sdk": { "version": "10.0.100", "rollForward": "latestFeature" } }\n' > "$AUTOMOD/global.json" || exit 1
    if grep -qF "$TFM_OLD" "$PROPS"; then
        sed -b -i "s#$TFM_OLD#$TFM_NEW#" "$PROPS" || exit 1
        echo "Retargeted AutoMod to net10.0 (local patch of Directory.Build.props)."
    fi
    if ! grep -qF "$TFM_NEW" "$PROPS"; then
        echo "Cannot retarget AutoMod: Directory.Build.props has no $TFM_OLD or $TFM_NEW." >&2
        echo "Found: $(grep -o '<TargetFramework>[^<]*</TargetFramework>' "$PROPS" | head -1)" >&2
        echo "This toolkit was tested with AutoMod v1.11.2; check out that tag and rebuild." >&2
        exit 1
    fi
    echo "Rebuilding AutoMod CLI (Release, net10.0)..."
    "$DOTNET" build "$CLI_PROJ" -c Release
    exit $?
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
