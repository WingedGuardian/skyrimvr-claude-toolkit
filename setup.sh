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

# --- Detect jq ---
echo "Checking for jq..."
JQ_PATH=$(which jq 2>/dev/null || echo "")
if [ -z "$JQ_PATH" ]; then
    # Try common Windows locations
    for p in \
        "/c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Links/jq.exe" \
        "/c/ProgramData/chocolatey/bin/jq.exe" \
        "/usr/bin/jq"; do
        if [ -f "$p" ]; then
            JQ_PATH="$p"
            break
        fi
    done
fi

if [ -z "$JQ_PATH" ]; then
    echo ""
    echo "jq not found. It's needed for the safety hooks."
    echo "Installing jq via winget..."
    winget install jqlang.jq --accept-source-agreements --accept-package-agreements 2>/dev/null || {
        echo ""
        echo "ERROR: Could not auto-install jq."
        echo "Please install it manually: winget install jqlang.jq"
        echo "Then re-run: bash setup.sh"
        exit 1
    }
    # Re-detect after install
    JQ_PATH=$(which jq 2>/dev/null || echo "/c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Links/jq.exe")
fi
echo "  Found jq: $JQ_PATH"

# --- Detect Node.js (needed for xeditlib; auto-install) ---
echo ""
echo "Checking for Node.js..."
if which node >/dev/null 2>&1; then
    echo "  Found Node.js: $(node --version 2>/dev/null)"
else
    echo "  Node.js not found. It's needed for xeditlib (ESP read/write via XEditLib.dll)."
    echo "  Installing Node.js LTS via winget..."
    winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements 2>/dev/null || {
        echo "  Could not auto-install Node.js. Install it manually when you need xeditlib:"
        echo "    winget install OpenJS.NodeJS.LTS   (or download from nodejs.org)"
    }
    if which node >/dev/null 2>&1; then
        echo "  Node.js installed: $(node --version 2>/dev/null)"
    fi
fi

# --- Detect Python 3 (needed for cosave-info, save analysis, PyFFI/PyNifly; do NOT auto-install) ---
echo ""
echo "Checking for Python 3..."
PY_FOUND=""
for c in "py -3" "python" "python3"; do
    if $c -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' >/dev/null 2>&1; then
        PY_FOUND="$c"; echo "  Found Python 3: '$c' -> $($c --version 2>&1)"; break
    fi
done
if [ -z "$PY_FOUND" ]; then
    echo "  Python 3 not found. It's needed for cosave-info and the save-analysis scripts (and PyFFI/PyNifly if you use them)."
    echo "  Install it from python.org (or 'winget install Python.Python.3.12') and re-run — the bundled tools try 'py -3', 'python', then 'python3'."
fi

# --- Detect .NET SDK (needed for Spriggit / AutoMod; do NOT auto-install) ---
echo ""
echo "Checking for the .NET SDK..."
if which dotnet >/dev/null 2>&1; then
    echo "  Found .NET SDK: $(dotnet --version 2>/dev/null)"
else
    echo "  .NET SDK not found. Spriggit (ESP <-> YAML) and AutoMod CLI need it."
    echo "    Install when you want those tools:  winget install Microsoft.DotNet.SDK.8"
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
        }' "$1" 2>/dev/null | sed 's/\\\\/\\/g' | tr '\134' '/'
}

find_mo2_instance() {
    local ini gp
    # Global instances live one folder deep under %LOCALAPPDATA%\ModOrganizer\<InstanceName>\.
    # A portable instance keeps its ini in the MO2 install folder -- point MO2_INSTANCE_INI at it.
    for ini in "${MO2_INSTANCE_INI:-}" "$LOCALAPPDATA_DIR/ModOrganizer"/*/ModOrganizer.ini; do
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

# --- Configure hook scripts (replace jq placeholder) ---
echo ""
echo "Configuring safety hooks..."
for hook in protect-bash.sh protect-files.sh backup-before-edit.sh snapshot-before-tool.sh; do
    if grep -q '{{JQ_PATH}}' "$GAME_DIR/.claude/hooks/$hook"; then
        sed -i "s|{{JQ_PATH}}|$JQ_PATH|g" "$GAME_DIR/.claude/hooks/$hook"
        echo "  Configured: .claude/hooks/$hook"
    else
        echo "  Already configured: .claude/hooks/$hook"
    fi
done

# --- Configure CLAUDE.md (replace path placeholders) ---
echo ""
echo "Configuring CLAUDE.md..."
if grep -q '{{GAME_ROOT}}' "$GAME_DIR/CLAUDE.md"; then
    sed -i "s|{{GAME_ROOT}}|$GAME_ROOT_WIN|g" "$GAME_DIR/CLAUDE.md"
    sed -i "s|{{CONFIG_DIR}}|$CONFIG_DIR|g" "$GAME_DIR/CLAUDE.md"
    sed -i "s|{{LOADORDER_DIR}}|$LOADORDER_DIR|g" "$GAME_DIR/CLAUDE.md"
    sed -i "s|{{SKYRIM_FOLDER}}|$SKYRIM_FOLDER|g" "$GAME_DIR/CLAUDE.md"

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
    sed -i -e "/{{MOD_MANAGER_PATHS}}/{r $MM_BLOCK" -e "d}" "$GAME_DIR/CLAUDE.md"
    rm -f "$MM_BLOCK"

    echo "  Configured with your paths (Skyrim folder: $SKYRIM_FOLDER)."
else
    echo "  Already configured."
fi

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
echo "  KNOWLEDGEBASE.md                 -- 1,300+ lines of Skyrim modding knowledge"
echo "  .claude/settings.json            -- Hook configuration"
echo "  .claude/hooks/protect-bash.sh    -- Guards dangerous commands"
echo "  .claude/hooks/protect-files.sh   -- Guards file edits"
echo "  .claude/hooks/backup-before-edit.sh -- Auto-backups (Edit/Write) with audit trail"
echo "  .claude/hooks/snapshot-before-tool.sh -- Auto-snapshots .psc/.pex before Bash commands"
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
echo "  Spriggit     -- ESP <-> YAML editing:         dotnet tool install Spriggit.CLI"
echo "                  (deep output paths: use tools/spriggit-cli.sh)"
echo "  AutoMod CLI  -- NIF / BSA / audio / MCM / ESP:"
echo "                  git clone https://github.com/SpookyPirate/spookys-automod-toolkit into tools/automod"
echo "                  pin SDK 8.0.x via tools/automod/global.json (rollForward: latestFeature),"
echo "                  build the Cli project ONLY (dotnet build tools/automod/src/SpookysAutomod.Cli -c Release)"
echo "                  -- never build the WPF Setup project headless -- then use tools/automod-cli.sh"
echo "  PyFFI        -- LE NiTriShape geometry (Python 3.10):  pip install pyffi"
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
