#!/bin/bash
# Skyrim Claude Code Toolkit -- Setup Script
#
# This script is designed to be run FROM your Skyrim folder, after
# extracting the toolkit zip into it. It configures everything in-place.
#
# Usage: bash setup.sh

set -e

GAME_DIR="$(pwd)"
USERNAME="$(whoami)"
# Native-Windows form of the game dir (C:/...), for paths we WRITE into CLAUDE.md.
# `pwd` under Git Bash/MSYS returns an MSYS path (/c/...) that Claude's file tools and
# PowerShell can't open; `pwd -W` returns the Windows form. Keep $GAME_DIR (MSYS) for the
# filesystem work below -- both forms work there -- and use this one only for substitution.
GAME_ROOT_WIN=$(pwd -W 2>/dev/null || pwd)
GAME_ROOT_WIN=$(printf '%s' "$GAME_ROOT_WIN" | tr '\134' '/')

echo "============================================"
echo " Skyrim Claude Code Toolkit -- Setup"
echo "============================================"
echo ""
echo "Game directory: $GAME_ROOT_WIN"
echo ""

# --- Verify this looks like a Skyrim install ---
if [ ! -f "$GAME_DIR/SkyrimVR.exe" ] && [ ! -f "$GAME_DIR/SkyrimSE.exe" ]; then
    echo "WARNING: No SkyrimVR.exe or SkyrimSE.exe found here."
    echo "This script should be run from your Skyrim installation folder."
    echo ""
    echo "Are you sure this is the right directory?"
    read -p "Continue anyway? (y/n) " CONTINUE
    [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ] && exit 1
fi

# --- Verify toolkit files are present ---
if [ ! -f "$GAME_DIR/KNOWLEDGEBASE.md" ] || [ ! -f "$GAME_DIR/.claude/hooks/protect-bash.sh" ]; then
    echo "ERROR: Toolkit files not found in this directory."
    echo "Make sure you extracted the toolkit zip into your Skyrim folder first."
    exit 1
fi

# --- jq is optional ---
#
# The safety hooks no longer use jq (they run on Python -- see below). The only shipped tools that
# still call it are tools/devbench-cli.sh and tools/nexus.sh, for pretty-printing, and both fall
# back to plain output without it. So this is informational: no install, no failure.
echo "Checking for jq (optional)..."
if which jq >/dev/null 2>&1; then
    echo "  Found jq: $(which jq) -- used for pretty output by devbench-cli.sh and nexus.sh"
else
    echo "  jq not found. That is fine: it is optional (devbench-cli.sh and nexus.sh print plain output without it)."
fi

# --- Detect Node.js (needed for xeditlib; NOT auto-installed) ---
#
# Setup writes configuration. It does not run a package manager: installing software the user did
# not ask for from a config script is a side effect this toolkit refuses (the contained test harness
# blocks winget/npm/pip for the same reason). So a missing Node is reported with how to get it.
echo ""
echo "Checking for Node.js..."
if which node >/dev/null 2>&1; then
    echo "  Found Node.js: $(node --version 2>/dev/null)"
else
    echo "  Node.js not found. It's needed for xeditlib (ESP read/write via XEditLib.dll)."
    echo "  Setup does not install it. When you need xeditlib, install it yourself:"
    echo "    winget install OpenJS.NodeJS.LTS   (or download from nodejs.org)"
fi

# --- Detect Python 3 (needed for cosave-info, save analysis, PyFFI/PyNifly; do NOT auto-install) ---
echo ""
echo "Checking for Python 3..."
PY_FOUND=""
for c in "py -3" "python" "python3"; do
    # >= 3.8 is the safety engine's floor: a 3.6 that passed a bare "is it 3?" probe made every hook
    # deny "engine failed" with nothing saying why.
    if $c -c 'import sys; sys.exit(0 if sys.version_info[:2]>=(3,8) else 1)' >/dev/null 2>&1; then
        PY_FOUND="$c"; echo "  Found Python 3: '$c' -> $($c --version 2>&1)"; break
    fi
done
if [ -z "$PY_FOUND" ]; then
    for c in "py -3" "python" "python3"; do
        if $c -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' >/dev/null 2>&1; then
            echo "  Python 3 found ('$c' -> $($c --version 2>&1)) but it is too old: the safety hooks need Python 3.8 or newer."
            echo "  Install a current Python (python.org, or 'winget install Python.Python.3.12') and re-run."
            break
        fi
    done
fi
if [ -z "$PY_FOUND" ]; then
    echo "  Python 3.8+ not found. It's needed for cosave-info and the save-analysis scripts (and PyFFI/PyNifly if you use them)."
    echo "  Install it from python.org (or 'winget install Python.Python.3.12') and re-run — the bundled tools try 'py -3', 'python', then 'python3'."
fi

# The safety hooks run on this interpreter. Record the ABSOLUTE path of the one just found, so a hook
# does not depend on PATH (Claude Code launches hooks with whatever PATH it happens to have).
# sys.executable is a native Windows path under `py -3`; GNU sed reads backslash sequences in replacement
# text as escapes (\U upper-cases the rest, \t becomes a tab), so a backslash path silently corrupts every
# hook -- the defect fixed for LOCALAPPDATA/Documents in v3.2.1 and for jq after it. Normalized to
# forward slashes (and CR stripped) here, where every branch has converged.
#
# Which interpreter: a REGULAR install beats the Microsoft Store alias (...\WindowsApps\...). Every hook
# call starts Python, and the alias costs ~135 ms more per start (MEASURED 2026-10-02: `python -c pass`
# median 156 ms via the alias vs 21 ms for a python.org install) -- on every Bash, Write and Edit call.
# `py -3` can resolve to the alias even when a regular install exists, so ask every candidate and take
# the first that is not the alias; the alias is used only when nothing else runs.
PYTHON_PATH=""
PY_STORE=""
if [ -n "$PY_FOUND" ]; then
    for c in "py -3" "python" "python3"; do
        $c -c 'import sys; sys.exit(0 if sys.version_info[:2]>=(3,8) else 1)' >/dev/null 2>&1 || continue
        exe=$($c -c 'import sys; print(sys.executable)' 2>/dev/null | tr -d '\r' | tr '\134' '/') || exe=""
        [ -n "$exe" ] || continue
        case "$exe" in
            */[Ww]indows[Aa]pps/*) if [ -z "$PY_STORE" ]; then PY_STORE="$exe"; fi ;;
            *) PYTHON_PATH="$exe"; break ;;
        esac
    done
    if [ -z "$PYTHON_PATH" ] && [ -n "$PY_STORE" ]; then
        PYTHON_PATH="$PY_STORE"
        echo "  Hooks will use the Microsoft Store Python ($PY_STORE). It works, but each hook call starts"
        echo "  ~0.1 s slower than a python.org install would; install one and re-run setup.sh to switch."
    elif [ -n "$PYTHON_PATH" ]; then
        echo "  Hooks will use: $PYTHON_PATH"
    fi
fi
# The form that goes into sed's REPLACEMENT: `&` (the whole match), `|` (our delimiter) and a backslash
# are all special there, and a Windows folder name may legally contain `&`.
# The hooks hold it as PY='...' (single quotes: a `$` or backtick in a folder name must not expand at every
# call), so a single quote inside the path is closed, escaped and reopened first: ' -> '\''
PY_Q="${PYTHON_PATH//\'/\'\\\'\'}"
PY_SED=$(printf '%s' "$PY_Q" | sed -e 's/[\\|&]/\\&/g')
# The Codex PowerShell entry holds it as $PY = '...'. PowerShell escapes a quote inside single quotes by
# DOUBLING it (' -> ''); the bash '\'' form above would end the string there and break the script.
PY_PS="${PYTHON_PATH//\'/\'\'}"
PY_PS_SED=$(printf '%s' "$PY_PS" | sed -e 's/[\\|&]/\\&/g')
if [ -z "$PYTHON_PATH" ]; then
    echo ""
    echo "  WARNING: the safety hooks need Python 3 and will REFUSE every command until it is installed"
    echo "  and setup.sh is re-run. (They fail closed on purpose: a guard that cannot run must not allow.)"
    echo "  Install Python 3 from python.org (or 'winget install Python.Python.3.12'), then: bash setup.sh"
fi

# --- Detect .NET (needed for Spriggit / AutoMod; do NOT auto-install) ---
#
# This checks whether the SDK Spriggit needs is present, not merely that some
# dotnet exists. MEASURED 2026-09-06 on the dev machine: this block previously
# recommended SDK 8 and then printed "Found .NET SDK: 8.0.424", which reads as
# success. `dotnet tool install Spriggit.CLI` also succeeded. But Spriggit.CLI
# 0.40.x targets net9.0, so every invocation died with "You must install or update
# .NET" -- and because tools/esp-verify-wrapper.sh drives spriggit, the ESP
# cross-reference guard was silently unusable too. Reporting presence when the
# question is capability is how an install ends up broken while looking configured.
echo ""
echo "Checking for .NET..."
if which dotnet >/dev/null 2>&1; then
    echo "  Found .NET SDK: $(dotnet --version 2>/dev/null)"
    # SDK, not runtime. MEASURED 2026-09-06, and v3.8.2 shipped this check asking the
    # WRONG question: with only the .NET 9 RUNTIME installed, spriggit starts and
    # prints its version -- and still cannot serialize anything. It resolves its
    # serializer at runtime via `dotnet tool install Spriggit.Yaml.Skyrim`, whose
    # tool assets live under tools/net9.0/, and an SDK can only install a tool whose
    # TFM it supports. With SDK 8 that fails with a misleading "DotnetToolSettings.xml
    # was not found in the package" -- the file is there, under a framework the SDK
    # will not select. Control: dotnetsay (net8.0 assets) installs fine.
    #
    # So a runtime check passes in a state where Spriggit is unusable. Ask for the SDK.
    if dotnet --list-sdks 2>/dev/null | grep -qE "^(9|1[0-9])\."; then
        echo "  .NET 9+ SDK present -- Spriggit can run AND fetch its serializer."
    else
        echo "  !! .NET 9 SDK NOT found."
        echo "     Spriggit (ESP <-> YAML) targets net9.0 and installs its serializer as a"
        echo "     dotnet tool, which needs a matching SDK -- the RUNTIME alone is not"
        echo "     enough. Without it spriggit starts, prints a version, and cannot"
        echo "     serialize, which also disables tools/esp-verify-wrapper.sh."
        echo "     Install:  winget install Microsoft.DotNet.SDK.9"
        echo "     (AutoMod needs an 8.0.x SDK specifically -- it pins one via global.json"
        echo "      with rollForward: latestFeature, which does not roll 8 -> 9.)"
    fi
else
    echo "  .NET not found. Spriggit (ESP <-> YAML) and AutoMod CLI need it."
    echo "    Spriggit needs SDK 9:   winget install Microsoft.DotNet.SDK.9"
    echo "    AutoMod ALSO needs 8:   winget install Microsoft.DotNet.SDK.8"
    echo "    (AutoMod pins SDK 8.0.x via tools/automod/global.json with"
    echo "     rollForward: latestFeature, which does not roll 8 -> 9. Installing"
    echo "     only 9 leaves its build failing on SDK resolution.)"
fi

# --- Detect a JDK (needed for ReSaver CLI; do NOT auto-install) ---
echo ""
echo "Checking for a JDK..."
if which java >/dev/null 2>&1; then
    echo "  Found Java: $(java -version 2>&1 | head -n1)"
else
    echo "  No JDK found. ReSaver CLI (headless .ess save parse/clean) needs JDK 17+."
    echo "    Install when you want it:  winget install Microsoft.OpenJDK.21"
fi

# --- Detect user paths ---
# Query Windows for the actual Documents folder instead of assuming the default location --
# it may be redirected (OneDrive "Back up your folders", a manual Properties > Location move,
# or a GPO folder redirect all update the same Known Folder, none of which live under
# C:/Users/$USERNAME/Documents in that case).
DOCUMENTS_DIR=$(powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')" 2>/dev/null | tr -d '\r')
[ -z "$DOCUMENTS_DIR" ] && DOCUMENTS_DIR="C:/Users/$USERNAME/Documents"
# Normalize backslashes to forward slashes -- via tr, not bash's ${var//\\//}, which was
# unreliable across the bash builds we tested (Cygwin bash silently no-ops on it).
# $LOCALAPPDATA is backslash-delimited on every Windows install, and feeding a raw backslash
# path straight into sed's replacement text lets GNU sed interpret \U, \a, etc. as escapes,
# corrupting the substituted path -- this bit LOCALAPPDATA_DIR specifically, but we normalize
# DOCUMENTS_DIR here too for consistency.
DOCUMENTS_DIR=$(printf '%s' "$DOCUMENTS_DIR" | tr '\134' '/')
LOCALAPPDATA_DIR="${LOCALAPPDATA:-C:/Users/$USERNAME/AppData/Local}"
LOCALAPPDATA_DIR=$(printf '%s' "$LOCALAPPDATA_DIR" | tr '\134' '/')
# Which Skyrim variant is this? The .exe sitting in the game folder is the ground truth --
# My Games/<variant>/ only exists once the game has been launched at least once, so probing
# Documents alone silently mis-detects a freshly-installed copy. Fall back to the config-folder
# probe only when the exe check is ambiguous (both present, or neither).
if [ -f "$GAME_DIR/SkyrimVR.exe" ] && [ ! -f "$GAME_DIR/SkyrimSE.exe" ]; then
    SKYRIM_FOLDER="Skyrim VR"
elif [ -f "$GAME_DIR/SkyrimSE.exe" ] && [ ! -f "$GAME_DIR/SkyrimVR.exe" ]; then
    SKYRIM_FOLDER="Skyrim Special Edition"
elif [ -d "$DOCUMENTS_DIR/My Games/Skyrim Special Edition" ] && [ ! -d "$DOCUMENTS_DIR/My Games/Skyrim VR" ]; then
    SKYRIM_FOLDER="Skyrim Special Edition"
else
    SKYRIM_FOLDER="Skyrim VR"
fi
CONFIG_DIR="$DOCUMENTS_DIR/My Games/$SKYRIM_FOLDER"
LOADORDER_DIR="$LOCALAPPDATA_DIR/$SKYRIM_FOLDER"

# --- Detect Mod Organizer 2 -------------------------------------------------
# MO2 has NO flat Data/ folder on disk. It builds a virtual one at launch by merging each enabled
# mod's own folder. So on an MO2 setup the game's Data/ holds the stock game and almost none of the
# user's mods, and the profile -- not Documents -- is where the INIs and load order live. Getting
# this wrong means every path we write into CLAUDE.md points somewhere real but nearly empty.
MO2_INSTANCE=""; MO2_MODS=""; MO2_PROFILE=""; MO2_PROFILE_DIR=""; MO2_OVERWRITE=""

ini_get() { # $1=file $2=section $3=key  -> value, QSettings-unescaped, forward-slashed
    awk -v sec="[$2]" -v key="$3" '
        /^[ \t]*\[/ { insec = ($0 ~ "^[ \t]*\\" sec); next }
        insec && index($0, key) == 1 {
            eq = index($0, "="); if (eq == 0) next
            v = substr($0, eq + 1); sub(/^[ \t]+/, "", v); sub(/[ \t\r]+$/, "", v)
            gsub(/^"|"$/, "", v); print v; exit
        }' "$1" 2>/dev/null | sed 's/\\\\/\\/g' | tr '\134' '/' \
      | sed 's/^@ByteArray(\(.*\))$/\1/'
    # QSettings writes any value it considers binary as @ByteArray(...) -- MO2 does this routinely
    # for gamePath and selected_profile. Unwrap it, or every comparison below is against a wrapper.
}

find_mo2_instance() {
    local ini gp
    # Global instances live one folder deep under %LOCALAPPDATA%\ModOrganizer\<InstanceName>\.
    # A portable instance keeps its ini in the MO2 install folder -- point MO2_INSTANCE_INI at it.
    # Also probe the game root's siblings: a Nolvus/Wabbajack-style install parks the portable MO2
    # next to the game folder (<instance>/MO2/ModOrganizer.ini beside <instance>/STOCK GAME/).
    for ini in "${MO2_INSTANCE_INI:-}" "$LOCALAPPDATA_DIR/ModOrganizer"/*/ModOrganizer.ini \
               "$GAME_DIR"/../*/ModOrganizer.ini; do
        [ -f "$ini" ] || continue
        gp="$(ini_get "$ini" General gamePath)"
        [ -n "$gp" ] || continue
        # Match this game folder case-insensitively, ignoring any trailing slash.
        if [ "$(echo "${gp%/}" | tr 'A-Z' 'a-z')" = "$(echo "${GAME_ROOT_WIN%/}" | tr 'A-Z' 'a-z')" ]; then
            echo "$ini"; return 0
        fi
    done
    return 1
}

if MO2_INI="$(find_mo2_instance)"; then
    MO2_INSTANCE="$(dirname "$MO2_INI")"
    # Normalize to a real Windows-style path -- the sibling probe above resolves through `..`, and
    # a `STOCK GAME/../MO2` instance path written into CLAUDE.md is correct but unreadable.
    # `pwd -W` gives the Windows form but does not exist off MSYS, so fall back to
    # plain `pwd` (matching line 17) before giving up on the raw value. Without
    # that fallback the `..` survives everywhere `pwd -W` is unavailable -- which
    # now includes the shipped Linux devcontainer -- and CLAUDE.md gets an
    # instance path like `STOCK GAME/../MO2`: correct, but unreadable.
    MO2_INSTANCE="$( { cd "$MO2_INSTANCE" 2>/dev/null && { pwd -W 2>/dev/null || pwd; } ; } || echo "$MO2_INSTANCE")"
    # base_directory is optional. It can also be written as the literal %BASE_DIR% token, which is
    # self-referential -- in both cases the base IS the instance folder.
    MO2_BASE="$(ini_get "$MO2_INI" Settings base_directory)"
    MO2_BASE="${MO2_BASE//\%BASE_DIR\%/}"
    [ -n "$MO2_BASE" ] || MO2_BASE="$MO2_INSTANCE"
    # The per-directory overrides are optional too, and commonly embed %BASE_DIR%.
    resolve_dir() { # $1=ini key  $2=default subfolder
        local v; v="$(ini_get "$MO2_INI" Settings "$1")"
        [ -n "$v" ] || { echo "$MO2_BASE/$2"; return; }
        v="${v//\%BASE_DIR\%/$MO2_BASE}"
        # A bare/relative override is relative to the base directory.
        case "$v" in [A-Za-z]:/*|/*) echo "$v" ;; *) echo "$MO2_BASE/$v" ;; esac
    }
    MO2_MODS="$(resolve_dir mod_directory mods)"
    MO2_OVERWRITE="$(resolve_dir overwrite_directory overwrite)"
    MO2_PROFILES="$(resolve_dir profiles_directory profiles)"
    MO2_PROFILE="$(ini_get "$MO2_INI" General selected_profile)"
    [ -n "$MO2_PROFILE" ] || MO2_PROFILE="Default"
    MO2_PROFILE_DIR="$MO2_PROFILES/$MO2_PROFILE"
    # MO2 keeps loadorder.txt/plugins.txt in the profile, and the INIs too when that profile has
    # "profile-specific INI files" enabled (the default, but it IS a per-profile toggle). Only move
    # the paths when the files are genuinely there -- don't assume.
    if [ -f "$MO2_PROFILE_DIR/loadorder.txt" ]; then
        LOADORDER_DIR="$MO2_PROFILE_DIR"
    fi
    if [ -f "$MO2_PROFILE_DIR/SkyrimPrefs.ini" ] || [ -f "$MO2_PROFILE_DIR/skyrimprefs.ini" ]; then
        CONFIG_DIR="$MO2_PROFILE_DIR"
    fi
fi

echo ""
if [ -n "$MO2_INSTANCE" ]; then
    echo "  Mod manager: Mod Organizer 2"
    echo "    Instance: $MO2_INSTANCE"
    echo "    Profile:  $MO2_PROFILE"
    echo "    Mods:     $MO2_MODS"
    echo "  NOTE: MO2 builds a VIRTUAL Data/ folder at launch -- there is no merged Data/ on disk."
    echo "        Your mods' real files live under the mods folder above, one folder per mod."
elif [ -d "$CONFIG_DIR" ]; then
    echo "  Mod manager: not MO2 (stock layout / Vortex -- mods deploy into the game's Data/)"
fi
if [ -d "$CONFIG_DIR" ]; then
    echo "  Found Skyrim configs in: $CONFIG_DIR/"
else
    echo "  WARNING: Skyrim config not found at $CONFIG_DIR"
    echo "  You may need to update paths in CLAUDE.md manually."
fi

# --- Configure hook scripts (replace the Python placeholder) ---
echo ""
echo "Configuring safety hooks..."
HOOKS_MISSING=0
# The Codex entries (.codex/hooks/) take the same interpreter. The .sh holds it as PY='...' like the Claude
# hooks; the .ps1 as $PY = '...' with PowerShell's own quote escaping (PY_PS above).
for hook in .claude/hooks/protect-bash.sh .claude/hooks/protect-files.sh .claude/hooks/protect-mcp.sh \
            .claude/hooks/backup-before-edit.sh .claude/hooks/snapshot-before-tool.sh .claude/hooks/session-kb-guard.sh \
            .codex/hooks/skyguard-entry.ps1 .codex/hooks/skyguard-entry.sh; do
    case "$hook" in
        *.ps1) H_SED="$PY_PS_SED"; H_LINE="\$PY = '$PY_PS'"; H_RE='^[$]PY = '; H_NEW="\$PY = '$PY_PS_SED'" ;;
        *)     H_SED="$PY_SED";    H_LINE="PY='$PY_Q'";       H_RE='^PY=';       H_NEW="PY='$PY_SED'" ;;
    esac
    # Check it EXISTS first. `grep -q` on a missing file exits 2, which is not 0, so
    # control fell to the else branch and a hook that had been quarantined by antivirus
    # or lost to a partial unzip was reported as "Already configured".
    if [ ! -f "$GAME_DIR/$hook" ]; then
        echo "  MISSING: $hook -- this hook is NOT installed and will never run"
        HOOKS_MISSING=$((HOOKS_MISSING + 1))
    elif grep -q '{{PYTHON_PATH}}' "$GAME_DIR/$hook"; then
        if [ -n "$PYTHON_PATH" ]; then
            sed -i "s|{{PYTHON_PATH}}|$H_SED|g" "$GAME_DIR/$hook"
            echo "  Configured: $hook"
        else
            echo "  NOT CONFIGURED: $hook -- no Python 3 found, so this hook will refuse until setup.sh is re-run"
        fi
    elif [ -n "$PYTHON_PATH" ] && ! grep -qxF "$H_LINE" "$GAME_DIR/$hook"; then
        # Already configured, but for a different interpreter (Python upgraded or moved). Left alone,
        # the hook would refuse every command while this script said "Already configured".
        OLD_PY=$(grep -m1 "$H_RE" "$GAME_DIR/$hook" || true)
        sed -i "s|$H_RE.*|$H_NEW|" "$GAME_DIR/$hook"
        echo "  Updated interpreter: $hook ($OLD_PY -> PY=$PYTHON_PATH)"
    else
        echo "  Already configured: $hook"
    fi
done

# The Codex entries also pass the engine a bash for the rules that run shell snippets. A Windows path on
# Git Bash (the PowerShell entry cannot use /usr/bin/bash), else bash's own path.
BASH_FOR_HOOKS="${BASH:-bash}"
if command -v cygpath >/dev/null 2>&1; then
    BASH_FOR_HOOKS=$(cygpath -m "$BASH_FOR_HOOKS" 2>/dev/null | tr -d '\r') || BASH_FOR_HOOKS="${BASH:-bash}"
fi
B_SH="${BASH_FOR_HOOKS//\'/\'\\\'\'}"
B_SH_SED=$(printf '%s' "$B_SH" | sed -e 's/[\\|&]/\\&/g')
B_PS="${BASH_FOR_HOOKS//\'/\'\'}"
B_PS_SED=$(printf '%s' "$B_PS" | sed -e 's/[\\|&]/\\&/g')
for hook in .codex/hooks/skyguard-entry.ps1 .codex/hooks/skyguard-entry.sh; do
    [ -f "$GAME_DIR/$hook" ] && grep -q '{{BASH_PATH}}' "$GAME_DIR/$hook" || continue
    case "$hook" in *.ps1) H_SED="$B_PS_SED" ;; *) H_SED="$B_SH_SED" ;; esac
    sed -i "s|{{BASH_PATH}}|$H_SED|g" "$GAME_DIR/$hook"
done

# --- Record the resolved paths where the hooks can read them ---
#
# protect-bash.sh decides "is this path inside the install" by resolving a real root,
# not by looking for the word Skyrim in the command. It derives one root on its own --
# the hooks live at <install>/.claude/hooks/, so ../.. is the install -- and reads this
# file for anything else. That ordering is deliberate: the self-derived root ALWAYS
# resolves, so a missing or unreadable file here can never leave the guard with nothing
# to match and, via its fail-closed branch, refusing everything.
#
# Written with printf per line rather than a heredoc: a quoted heredoc still eats a
# backslash level through some tool boundaries, and these are Windows paths.
echo ""
echo "Recording resolved paths for the safety hooks..."
# SINGLE quotes, with any embedded quote escaped. protect-bash.sh SOURCES this file,
# and `$` and backtick are both legal characters in an NTFS folder name. Written
# double-quoted, MEASURED: a real folder `C:/Games/My$Stuff/Skyrim VR` arrived at the
# guard as `C:/Games/My/Skyrim VR` -- matching nothing, so the install was silently
# unguarded -- and a backtick in the path EXECUTED on every single Bash tool call.
sq() {
    local s=$1
    s=${s//\'/\'\\\'\'}          # ' -> '\''
    printf "'%s'" "$s"
}
{
    printf '# Written by setup.sh. Machine-local: not tracked, not shipped.\n'
    printf '# protect-bash.sh reads these to tell YOUR install from a path that merely\n'
    printf '# contains the word "Skyrim" -- a scratchpad, a checkout, a downloaded zip.\n'
    printf 'SKYRIM_GAME_ROOT=%s\n' "$(sq "$GAME_ROOT_WIN")"
    printf 'SKYRIM_CONFIG_DIR=%s\n' "$(sq "$CONFIG_DIR")"
    printf 'SKYRIM_LOADORDER_DIR=%s\n' "$(sq "$LOADORDER_DIR")"
    # MO2 keeps every mod's real files in mods/ and overwrite/, outside the install, so the guard can only
    # protect them if it is told where they are. Empty ('') on a non-MO2 install: declares nothing.
    printf 'SKYRIM_MO2_MODS=%s\n' "$(sq "$MO2_MODS")"
    printf 'SKYRIM_MO2_OVERWRITE=%s\n' "$(sq "$MO2_OVERWRITE")"
} > "$GAME_DIR/.claude/skyrim-paths.env"

# It must SOURCE, or the hooks silently fall back to the self-derived root alone. A
# config bash cannot read is the failure this check exists to catch, and it costs
# nothing to run here where the user can still see the message.
# ⚠ Sourcing cleanly proves the file PARSES, which is not the same as proving it
# still says what we wrote. The previous version of this check asked only the first
# question, so a path whose `$` had been expanded away passed it and printed success.
# Read the values back and compare them.
_want=$(printf '%s\n%s\n%s\n%s\n%s\n' "$GAME_ROOT_WIN" "$CONFIG_DIR" "$LOADORDER_DIR" "$MO2_MODS" "$MO2_OVERWRITE")
_got=$( set -a; . "$GAME_DIR/.claude/skyrim-paths.env" 2>/dev/null
        printf '%s\n%s\n%s\n%s\n%s\n' "${SKYRIM_GAME_ROOT:-}" "${SKYRIM_CONFIG_DIR:-}" "${SKYRIM_LOADORDER_DIR:-}" \
            "${SKYRIM_MO2_MODS:-}" "${SKYRIM_MO2_OVERWRITE:-}" )
if [ "$_want" = "$_got" ]; then
    echo "  Wrote: .claude/skyrim-paths.env"
else
    echo "  WARNING: .claude/skyrim-paths.env does not parse as shell."
    echo "           The hooks will still guard this folder, but not the config"
    echo "           directory. Check for unusual characters in your paths."
fi

# --- Configure the Codex hook definitions (.codex/hooks.json) ---
#
# Codex runs a project hook only after the user approves it, and the approval is a hash of the rendered
# definition -- install path included. So the rendering is part of the frozen surface: it is done by
# tools/skyguard/codex_definitions.py, the ONE renderer (pinned by tests against hashes Codex itself
# reported), never re-implemented here with sed. It refuses a root holding % ! $ ` or " (cmd.exe or bash
# would expand or cut the hook's path, the hook could not start, and a hook that cannot start lets every
# command RUN); then the Codex side is left unconfigured and this says so.
echo ""
echo "Configuring the Codex hooks (.codex/hooks.json)..."
CODEX_DEFS="$GAME_DIR/.codex/hooks.json"
CODEX_STATE="none"
case "$GAME_ROOT_WIN" in [A-Za-z]:/*) CODEX_WIN=1 ;; *) CODEX_WIN="" ;; esac
# "Is this the folder the definitions were rendered for?" Windows paths are case-insensitive and pwd keeps
# the case the user typed, so the comparison folds case there (ASCII, via tr -- this Git Bash's grep -i
# aborts with -F). Only the comparison: the rendered path above is never case-folded.
codex_same_root() {
    if [ -n "$CODEX_WIN" ]; then
        tr 'A-Z' 'a-z' < "$CODEX_DEFS" | grep -qF "$(printf '%s' "$GAME_ROOT_WIN/.codex/hooks/skyguard-entry.ps1" | tr 'A-Z' 'a-z')"
    else
        grep -qF "$GAME_ROOT_WIN/.codex/hooks/skyguard-entry.ps1" "$CODEX_DEFS"
    fi
}
if [ ! -f "$CODEX_DEFS" ]; then
    echo "  MISSING: .codex/hooks.json -- Codex has no toolkit hooks in this folder."
elif grep -q '{{CODEX_ROOT' "$CODEX_DEFS"; then
    if [ -z "$CODEX_WIN" ]; then
        echo "  NOT CONFIGURED: the toolkit's Codex support is Windows-only (Codex on Linux or WSL is not"
        echo "  supported). Claude Code is configured as usual."
    elif [ -z "$PY_FOUND" ]; then
        echo "  NOT CONFIGURED: rendering the Codex hooks needs Python 3.8+. Install it and re-run setup.sh."
    else
        # Windows-form paths and no MSYS argument conversion: Git Bash would otherwise rewrite the POSIX
        # root (/c/...) into C:/... on its way to a native python, and the definition would change.
        CODEX_ERR=$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND \
            "$GAME_ROOT_WIN/tools/skyguard/codex_definitions.py" render \
            --template "$GAME_ROOT_WIN/.codex/hooks.json" \
            --root-win "$GAME_ROOT_WIN" --root-posix "$GAME_DIR" \
            --out "$GAME_ROOT_WIN/.codex/hooks.json.new" 2>&1) && CODEX_RC=0 || CODEX_RC=$?
        if [ "$CODEX_RC" = 0 ] && [ -s "$CODEX_DEFS.new" ]; then
            mv -f "$CODEX_DEFS.new" "$CODEX_DEFS"
            CODEX_STATE="configured"
            echo "  Configured: .codex/hooks.json (hooks run from $GAME_ROOT_WIN)"
        else
            rm -f "$CODEX_DEFS.new"
            echo ""
            echo "  !! CODEX HOOKS NOT CONFIGURED -- Codex will run WITHOUT the toolkit's safety hooks here."
            echo "  !! ${CODEX_ERR:-the renderer failed (exit $CODEX_RC)}"
            echo "  !! Claude Code is configured as usual; only the Codex side is affected."
        fi
    fi
elif codex_same_root; then
    CODEX_STATE="configured"
    echo "  Already configured: .codex/hooks.json"
else
    # Rendered for another folder: the toolkit was moved or copied after setup. Not rewritten here --
    # re-extracting gives the pristine definitions, and the new path needs a fresh approval anyway.
    CODEX_OLD=$(sed -n 's/.*-File \\"\(.*\)\/\.codex\/hooks\/skyguard-entry\.ps1\\".*/\1/p' "$CODEX_DEFS" | head -n1)
    CODEX_STATE="elsewhere"
    echo ""
    echo "  WARNING: .codex/hooks.json points at ${CODEX_OLD:-another folder}, not this one ($GAME_ROOT_WIN)."
    echo "  Codex would run the hooks of that other folder, or none. To fix:"
    echo "    1. Re-extract the toolkit zip into this folder (restores the unconfigured .codex/hooks.json)."
    echo "    2. Run: bash setup.sh"
    echo "    3. Re-approve the hooks: open codex here, type /hooks, approve the 2 Skyrim toolkit hooks."
fi
if [ "$CODEX_STATE" = "configured" ]; then
    echo ""
    echo "Codex users: Codex runs these hooks only after you approve them. Open \`codex\` in this folder, type"
    echo "/hooks, and approve the 2 Skyrim toolkit hooks. Until then NOTHING is checked and Codex does not say so."
    echo "Start Codex in the game folder itself, never a subfolder: from a subfolder no hook runs, and Codex does not tell you."
    echo "Check any time with: bash tools/hook-canary.sh"
fi

# --- Configure CLAUDE.md and AGENTS.md (replace path placeholders) ---
# Both carry the same shared instructions (agent/instructions/core.md) and so the same placeholders:
# CLAUDE.md for Claude Code, AGENTS.md for Codex and other agents.
for DOC in CLAUDE.md AGENTS.md; do
echo ""
echo "Configuring $DOC..."
if [ ! -f "$GAME_DIR/$DOC" ]; then
    echo "  MISSING: $DOC"
elif grep -q '{{GAME_ROOT}}' "$GAME_DIR/$DOC"; then
    sed -i "s|{{GAME_ROOT}}|$GAME_ROOT_WIN|g" "$GAME_DIR/$DOC"
    sed -i "s|{{CONFIG_DIR}}|$CONFIG_DIR|g" "$GAME_DIR/$DOC"
    sed -i "s|{{LOADORDER_DIR}}|$LOADORDER_DIR|g" "$GAME_DIR/$DOC"
    sed -i "s|{{SKYRIM_FOLDER}}|$SKYRIM_FOLDER|g" "$GAME_DIR/$DOC"

    # Mod-manager block. Built in a temp file and spliced in with sed's `r`, so no amount of
    # punctuation in a path can break the substitution.
    MM_BLOCK="$(mktemp)"
    if [ -n "$MO2_INSTANCE" ]; then
        cat > "$MM_BLOCK" <<MMEOF
- **Mod manager**: **Mod Organizer 2** — instance \`$MO2_INSTANCE/\`, profile \`$MO2_PROFILE\`
- **MO2 mods**: \`$MO2_MODS/<mod-name>/\` — the REAL location of every installed mod's files
- **MO2 overwrite**: \`$MO2_OVERWRITE/\` — catches files written to Data/ during a session
- **MO2 profile**: \`$MO2_PROFILE_DIR/\` — this profile's INIs, \`loadorder.txt\`, \`plugins.txt\`

> **⚠ MO2 has no real \`Data/\` folder.** It builds a *virtual* one at launch by merging the stock
> game folder, each enabled mod's folder, and \`overwrite/\` (highest priority). The \`Data/\` path
> above is the **stock game only** — it does NOT contain the user's mods. To inspect an installed
> mod's files, read \`$MO2_MODS/<mod-name>/\`, not \`Data/\`.
>
> **This breaks load-order-aware tooling.** xelib/XEditLib resolves plugins from the game path, so
> launched outside MO2 it sees only the plugins physically present in the stock \`Data/\` — it will
> silently return a *wrong but plausible* answer for anything involving the override chain or the
> full load order. For those, run the script through MO2 (add it to MO2's executables list) so the
> virtual filesystem is mounted. Single-file reads (Spriggit on a specific \`.esp\` by path) work
> fine outside MO2, because they never consult the load order.
MMEOF
    else
        cat > "$MM_BLOCK" <<'MMEOF'
- **Mod manager**: stock layout (Vortex or manual) — mods deploy their files directly into `Data/`,
  so `Data/` is the real, merged view of everything installed.
MMEOF
    fi
    sed -i -e "/{{MOD_MANAGER_PATHS}}/{r $MM_BLOCK" -e "d}" "$GAME_DIR/$DOC"
    rm -f "$MM_BLOCK"

    echo "  Configured with your paths (Skyrim folder: $SKYRIM_FOLDER)."
else
    echo "  Already configured."
fi
done

# --- Configure the devcontainer (optional; only if the user has .devcontainer/) ---
if [ -f "$GAME_DIR/.devcontainer/devcontainer.json" ] && grep -q '{{DEVCONTAINER_MODS_MOUNT}}' "$GAME_DIR/.devcontainer/devcontainer.json"; then
    echo ""
    echo "Configuring .devcontainer/ (Docker dev environment for the container-side tools)..."
    if [ -n "$MO2_INSTANCE" ]; then
        DC_MODS="$MO2_MODS"; DC_PROFILE="$MO2_PROFILE_DIR"; DC_OVERWRITE="$MO2_OVERWRITE"
    else
        # Stock/Vortex: Data/ already IS the merged view, and there's no separate profile folder
        # or overwrite catcher -- point all three at what actually exists.
        DC_MODS="$GAME_ROOT_WIN/Data"; DC_PROFILE="$CONFIG_DIR"; DC_OVERWRITE="$GAME_ROOT_WIN/Data"
    fi
    sed -i "s|{{DEVCONTAINER_MODS_MOUNT}}|$DC_MODS|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    sed -i "s|{{DEVCONTAINER_PROFILE_MOUNT}}|$DC_PROFILE|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    sed -i "s|{{DEVCONTAINER_OVERWRITE_MOUNT}}|$DC_OVERWRITE|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    echo "  Configured .devcontainer/devcontainer.json with your mod paths."
    echo "  Run ./devshell-docker.sh (Docker only) or ./devshell.sh (needs the devcontainer CLI)."
fi

# --- Ensure backup directory exists ---
mkdir -p "$GAME_DIR/.claude/backups"

# --- Copy settings.local.json.example if no settings.local.json exists ---
if [ ! -f "$GAME_DIR/.claude/settings.local.json" ] && [ -f "$GAME_DIR/.claude/settings.local.json.example" ]; then
    cp "$GAME_DIR/.claude/settings.local.json.example" "$GAME_DIR/.claude/settings.local.json"
    echo ""
    echo "  Copied settings.local.json.example -> settings.local.json (customize allowed commands later)"
fi

# --- Optional: Nexus API integration status (non-blocking) ---
echo ""
echo "Checking optional Nexus API integration..."
NEXUS_KEY_FILE="$GAME_DIR/tools/.nexus_api_key"
if [ -s "$NEXUS_KEY_FILE" ]; then
    chmod 600 "$NEXUS_KEY_FILE" 2>/dev/null || true
    echo "  Nexus API key: configured (tools/.nexus_api_key) -- update detection enabled"
elif [ -n "$NEXUS_API_KEY" ]; then
    echo "  Nexus API key: found in \$NEXUS_API_KEY -- update detection enabled"
else
    echo "  Nexus API key: not set (optional)."
    echo "    Unlocks mod version/update/changelog/dependency lookups (update detection & triage)."
    echo "    To enable: get a free Personal API Key at"
    echo "      https://www.nexusmods.com/users/myaccount?tab=api"
    echo "    then save it (one line) to tools/.nexus_api_key  (already gitignored),"
    echo "    or set the NEXUS_API_KEY environment variable. Claude can do this for you on request."
fi

echo ""
echo "============================================"
echo " Setup Complete!"
echo "============================================"
echo ""
echo "Installed and configured:"
echo "  CLAUDE.md                        -- Project instructions (paths filled in)"
echo "  AGENTS.md                        -- The same instructions for Codex (paths filled in)"
echo "  .codex/                          -- Codex hook definitions + entries (approve them in Codex /hooks)"
echo "  KNOWLEDGEBASE.md                 -- 1,300+ lines of Skyrim modding knowledge"
echo "  .claude/settings.json            -- Hook configuration"
echo "  .claude/hooks/protect-bash.sh    -- Guards dangerous commands"
echo "  .claude/hooks/protect-files.sh   -- Guards file edits"
echo "  .claude/hooks/protect-mcp.sh     -- Guards the DevBench MCP tools (smp reset denied, save overwrites asked)"
echo "  .claude/hooks/backup-before-edit.sh -- Auto-backups (Edit/Write) with audit trail"
echo "  .claude/hooks/snapshot-before-tool.sh -- Auto-snapshots .psc/.pex before Bash commands"
echo "  .claude/hooks/session-kb-guard.sh -- Snapshots KNOWLEDGEBASE/CLAUDE.md/AGENTS.md once per session"
echo "  tools/                           -- Helper scripts (AutoMod wrapper, esp-verify, NIF tools, nexus.sh, resaver-cli.sh)"
echo "  .claude/backups/                 -- Backup storage (empty for now)"
echo ""
echo "The safety hooks are now active. Claude Code will:"
echo "  - Ask permission before editing any game file"
echo "  - Block direct writes to ESP/ESM/BSA files"
echo "  - Automatically back up files before modifying them"
echo "  - Snapshot your active scripts before running external tools"
echo ""
echo "--------------------------------------------"
echo " Optional modding tools (install as needed)"
echo "--------------------------------------------"
echo "These are NOT bundled. Ask Claude to set up any you want, or install yourself:"
echo "  xeditlib     -- programmatic ESP read/write:  npm install github:WingedGuardian/xeditlib"
echo "                  (run from THIS toolkit root so the bundled tools/ + examples/ scripts resolve it)"
echo "  Champollion  -- Papyrus .pex -> .psc:         github.com/Orvid/Champollion/releases"
echo "  Caprica      -- Papyrus .psc -> .pex:         github.com/Orvid/Caprica/releases"
echo "  Spriggit     -- ESP <-> YAML editing:         dotnet tool install Spriggit.CLI   (needs the .NET 9 runtime)"
echo "                  (deep output paths: use tools/spriggit-cli.sh)"
echo "  AutoMod CLI  -- NIF / BSA / audio / MCM / ESP:"
echo "                  git clone https://github.com/SpookyPirate/spookys-automod-toolkit into tools/automod"
echo "                  pin SDK 8.0.x via tools/automod/global.json (rollForward: latestFeature),"
echo "                  build the Cli project ONLY (dotnet build tools/automod/src/SpookysAutomod.Cli -c Release)"
echo "                  -- never build the WPF Setup project headless -- then use tools/automod-cli.sh"
echo "  PyFFI        -- LE NiTriShape geometry (any modern Python + setuptools):  pip install pyffi setuptools"
echo "  PyNifly      -- SSE BSTriShape + animation authoring:"
echo "                  download io_scene_nifly.zip from github.com/BadDogSkyrim/PyNifly/releases,"
echo "                  extract into tools/pynifly/ (DLL -> tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll; no build)."
echo "                  NOTE: a git clone does NOT include the compiled DLL -- use the release zip"
echo "  Blender      -- NIF repair + render-to-PNG (headless): blender.org (+ PyNifly Blender addon)"
echo "  NifSkope     -- independent visual NIF render gate:    github.com/niftools/nifskope/releases"
echo "  ReSaver CLI  -- headless .ess parse/cross-ref/clean:   download ReSaver from Nexus mod 5031"
echo "                  (FallrimTools); drop ReSaver.jar + lib/ into tools/resaver-cli/; needs JDK 17+;"
echo "                  the wrapper auto-compiles its driver on first run (tools/resaver-cli.sh)"
echo "  cosave-info  -- read-only SKSE .skse co-save survey (bundled): bash tools/cosave-cli.sh <save.skse>"
echo "  DevBench     -- LIVE in-game inspect/console/Papyrus while you play:"
echo "                  Nexus mod 181326 (alandtse). This one is a MOD, not a tools/ utility --"
echo "                  install it with your mod manager (Vortex/MO2), not by hand into Data/."
echo "                  dev-only, no gameplay change, no save data. Drive it via tools/devbench-cli.sh"
echo "                  (bundled wrapper). Lets Claude test its own fixes in the running game instead"
echo "                  of asking you to launch, trigger, and report back."
echo ""
echo "You're ready to go! Start asking Claude about your mods."
