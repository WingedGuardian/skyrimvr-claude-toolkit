#!/bin/bash
# Skyrim Claude Code Toolkit -- Setup Script
#
# This script is designed to be run FROM your Skyrim folder, after
# extracting the toolkit zip into it. It configures everything in-place.
#
# Usage: bash setup.sh [--agent claude|codex|all|auto] [--lock]
#   --agent auto    (the default) every agent found on PATH; when none is found, all of them
#   --agent claude  Claude Code only      --agent codex  Codex only (Windows only)
#   --agent all     both (`both` is accepted as another name for it)
#   --lock          also apply the Layer 2 read-only lock to the vanilla masters and archives.
#                   OFF unless you pass it (bash tools/skyguard.sh lock unlock <file> lifts it again).
# Setup never installs software. Every install command it prints is for you to run.

set -e

# --- Options: checked before anything is written ----------------------------------------------------------
# An unknown option or --agent value is a usage error (exit 2) and nothing is touched.
usage() {
    echo "usage: bash setup.sh [--agent claude|codex|all|auto] [--lock]"
    echo "  --agent auto    the default: every agent found on PATH (claude, codex); all of them when none is found"
    echo "  --agent claude  configure Claude Code only"
    echo "  --agent codex   configure Codex only (Windows only)"
    echo "  --agent all     configure both (\`both\` means the same)"
    echo "  --lock          also lock the vanilla masters and archives read-only (off unless you ask)"
}
AGENT_ARG="auto"
WANT_LOCK=""
while [ $# -gt 0 ]; do
    case "$1" in
        --agent)
            if [ $# -lt 2 ]; then
                echo "setup.sh: --agent needs a value. Nothing was changed." >&2; usage >&2; exit 2
            fi
            AGENT_ARG="$2"; shift 2 ;;
        --agent=*) AGENT_ARG="${1#--agent=}"; shift ;;
        --lock) WANT_LOCK=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "setup.sh: unknown option '$1'. Nothing was changed." >&2; usage >&2; exit 2 ;;
    esac
done
case "$AGENT_ARG" in
    claude|codex|all|auto) ;;
    both) AGENT_ARG="all" ;;
    *) echo "setup.sh: --agent must be claude, codex, all (or both) or auto, not '$AGENT_ARG'. Nothing was changed." >&2
       usage >&2; exit 2 ;;
esac

GAME_DIR="$(pwd)"
USERNAME="$(whoami)"
# Native-Windows form of the game dir (C:/...), for paths we WRITE into CLAUDE.md.
# `pwd` under Git Bash/MSYS returns an MSYS path (/c/...) that Claude's file tools and
# PowerShell can't open; `pwd -W` returns the Windows form. Keep $GAME_DIR (MSYS) for the
# filesystem work below -- both forms work there -- and use this one only for substitution.
GAME_ROOT_WIN=$(pwd -W 2>/dev/null || pwd)
GAME_ROOT_WIN=$(printf '%s' "$GAME_ROOT_WIN" | tr '\134' '/')
# The toolkit's Codex support is Windows-only: its hooks are rendered for a drive-letter root.
case "$GAME_ROOT_WIN" in [A-Za-z]:/*) CODEX_WIN=1 ;; *) CODEX_WIN="" ;; esac
# The root as a native Python opens it (every tools/*.py call below): the C:/ form wherever Git Bash's cygpath
# exists -- the same as `pwd -W` normally -- else the shell's own path (Linux).
PY_ROOT="$GAME_ROOT_WIN"
if type cygpath >/dev/null 2>&1; then
    PY_ROOT=$(cygpath -m "$GAME_DIR" 2>/dev/null | tr -d '\r') || PY_ROOT="$GAME_ROOT_WIN"
    [ -n "$PY_ROOT" ] || PY_ROOT="$GAME_ROOT_WIN"
fi

# --- Which agents this run configures --------------------------------------------------------------------
# auto = every agent found on PATH. When none is found: all of them -- an agent installed later, or installed
# but not on this shell's PATH, must not find the folder unconfigured. Codex is never an auto candidate off
# Windows (unsupported there); asking for it by name off Windows is refused here, before anything is written.
AGENTS_FOUND=""
if [ "$AGENT_ARG" = "auto" ]; then
    which claude >/dev/null 2>&1 && AGENTS_FOUND="claude"
    if [ -n "$CODEX_WIN" ] && which codex >/dev/null 2>&1; then AGENTS_FOUND="${AGENTS_FOUND:+$AGENTS_FOUND }codex"; fi
    case "$AGENTS_FOUND" in
        claude) AGENT_SEL="claude" ;;
        codex) AGENT_SEL="codex" ;;
        *) AGENT_SEL="all" ;;
    esac
else
    AGENT_SEL="$AGENT_ARG"
fi
DO_CLAUDE=""; DO_CODEX=""
case "$AGENT_SEL" in
    claude) DO_CLAUDE=1 ;;
    codex) DO_CODEX=1 ;;
    all) DO_CLAUDE=1; DO_CODEX=1 ;;
esac
if [ "$AGENT_SEL" = "codex" ] && [ -z "$CODEX_WIN" ]; then
    echo "setup.sh: --agent codex: the toolkit's Codex support is Windows-only (Codex on Linux, macOS or WSL is" >&2
    echo "not supported), and this folder is not a Windows path ($GAME_ROOT_WIN). Nothing was changed." >&2
    echo "Use --agent claude here." >&2
    exit 3
fi

echo "============================================"
echo " Skyrim Claude Code Toolkit -- Setup"
echo "============================================"
echo ""
echo "Game directory: $GAME_ROOT_WIN"
case "$AGENT_ARG" in
    auto) echo "Agents: ${AGENTS_FOUND:-none found on PATH} -> configuring: $AGENT_SEL (--agent auto)" ;;
    *) echo "Agents: configuring $AGENT_SEL (--agent $AGENT_ARG)" ;;
esac
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

# --- Install hints: setup never runs an installer ----------------------------------------------------------
# Every command below is printed for the USER. Two forms only (tests/test_install_hints.py):
#   * a runtime an agent may install when the user's task authorizes exactly that winget id and version
#     (Python.Python.3.<minor>, Git.Git, OpenJS.NodeJS[.LTS] -- tools/inventory-runtimes.py ALLOWED_IDS):
#     "for you to run", in the exact form `--id <id> --exact --version <v> --no-upgrade`;
#   * anything else (the .NET SDK, a JDK), and every upgrade of a runtime that exists: a "manual step",
#     which an agent may not install.
# The versions are the tested ones (user ruling: every user gets exactly what was tested).
NODE_PIN="24.19.0"
PYTHON_PIN="3.12.10"
DOTNET_SDK_PIN="10.0.401"
JDK_PIN="21.0.12.8"
hint_node() {
    echo "    For you to run (an agent may run it only when your task authorizes exactly this id and version):"
    echo "      winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade"
}
hint_python() {
    echo "    For you to run (an agent may run it only when your task authorizes exactly this id and version):"
    echo "      winget install --id Python.Python.3.12 --exact --version 3.12.10 --no-upgrade"
}
hint_dotnet() {
    echo "    Manual step, for you to run (an agent may not install this):"
    echo "      winget install --id Microsoft.DotNet.SDK.10 --exact --version 10.0.401 --no-upgrade"
}
hint_jdk() {
    echo "    Manual step, for you to run (an agent may not install this):"
    echo "      winget install --id Microsoft.OpenJDK.21 --exact --version 21.0.12.8 --no-upgrade"
}

# --- The shell: the hooks run under Git Bash on Windows ---
echo "Checking the shell..."
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*)
        echo "  Git Bash: $(git --version 2>/dev/null | tr -d '\r') (tested with Git for Windows 2.48.1)" ;;
    *)
        case "$GAME_DIR" in
            /mnt/[a-zA-Z]/*)
                echo "  WARNING: this looks like WSL bash on a Windows drive. The toolkit's hooks run under Git Bash on"
                echo "  Windows; run setup.sh from Git Bash (Git for Windows), not WSL." ;;
            *) echo "  bash $BASH_VERSION ($(uname -s 2>/dev/null))" ;;
        esac ;;
esac

# --- jq is optional ---
#
# The safety hooks no longer use jq (they run on Python -- see below). The only shipped tools that
# still call it are tools/devbench-cli.sh and tools/nexus.sh, for pretty-printing, and both fall
# back to plain output without it. So this is informational: no install, no failure.
echo ""
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
    NODE_V=$(node --version 2>/dev/null | tr -d '\r')
    echo "  Found Node.js: $NODE_V"
    NODE_MAJOR="${NODE_V#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
    case "$NODE_MAJOR" in
        ''|*[!0-9]*) echo "  (could not read its version; the xelib tools were tested with Node.js $NODE_PIN)" ;;
        *)
            if [ "$NODE_MAJOR" -lt 24 ]; then
                echo "  Node.js $NODE_V found; v4 needs Node 24 LTS for the xelib tools (xeditlib, skyrim-winner)."
                echo "    Upgrading it is a manual step for you (an agent may not install this: it never upgrades a runtime):"
                echo "      download node-v24.19.0-x64.msi from https://nodejs.org/dist/v24.19.0/, check that its SHA-256 is"
                echo "      f0f66c2a80c08a30a5ab5179ee9ea9e45f9b46289436a8cc87ff833b852db351, and run it."
            elif [ "${NODE_V#v}" != "$NODE_PIN" ]; then
                echo "  (the xelib tools were tested with Node.js $NODE_PIN)"
            fi ;;
    esac
else
    echo "  Node.js not found. It's needed for xeditlib (ESP read/write via XEditLib.dll)."
    echo "  Setup does not install it. When you need xeditlib, install it yourself:"
    hint_node
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
            echo "  Install a current Python and re-run (it installs beside the old one; the tested range is 3.10-3.13):"
            hint_python
            break
        fi
    done
fi
if [ -z "$PY_FOUND" ]; then
    echo "  Python 3.8+ not found. It's needed for cosave-info and the save-analysis scripts (and PyFFI/PyNifly if you use them)."
    echo "  Install it and re-run -- the bundled tools try 'py -3', 'python', then 'python3':"
    hint_python
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
PY_HOOK_CAND=""; PY_STORE_CAND=""
if [ -n "$PY_FOUND" ]; then
    for c in "py -3" "python" "python3"; do
        $c -c 'import sys; sys.exit(0 if sys.version_info[:2]>=(3,8) else 1)' >/dev/null 2>&1 || continue
        exe=$($c -c 'import sys; print(sys.executable)' 2>/dev/null | tr -d '\r' | tr '\134' '/') || exe=""
        [ -n "$exe" ] || continue
        case "$exe" in
            */[Ww]indows[Aa]pps/*) if [ -z "$PY_STORE" ]; then PY_STORE="$exe"; PY_STORE_CAND="$c"; fi ;;
            *) PYTHON_PATH="$exe"; PY_HOOK_CAND="$c"; break ;;
        esac
    done
    if [ -z "$PYTHON_PATH" ] && [ -n "$PY_STORE" ]; then
        PYTHON_PATH="$PY_STORE"
        PY_HOOK_CAND="$PY_STORE_CAND"
        echo "  Hooks will use the Microsoft Store Python ($PY_STORE). It works, but each hook call starts"
        echo "  ~0.1 s slower than a python.org install would; install one and re-run setup.sh to switch."
    elif [ -n "$PYTHON_PATH" ]; then
        echo "  Hooks will use: $PYTHON_PATH"
    fi
fi
# The TESTED range (user ruling 2026-10-05): CI runs 3.10-3.13. 3.8 and 3.9 still run the engine, and a newer
# Python usually does too, so outside the range is a clear note, never a refusal.
if [ -n "$PY_HOOK_CAND" ]; then
    PY_VER=$($PY_HOOK_CAND --version 2>&1 | tr -d '\r' | sed -n 's/^Python \([0-9][0-9]*\.[0-9][0-9]*[.0-9]*\).*/\1/p' | head -n1)
    PY_MAJ="${PY_VER%%.*}"; PY_MIN="${PY_VER#*.}"; PY_MIN="${PY_MIN%%.*}"
    case "$PY_MAJ.$PY_MIN" in
        3.10|3.11|3.12|3.13) echo "  Python $PY_VER is inside the tested range 3.10-3.13." ;;
        3.8|3.9)
            echo "  NOTE: Python $PY_VER runs the safety hooks (their floor is 3.8) but is OLDER than the tested range"
            echo "  3.10-3.13. If anything misbehaves, install a tested Python beside it and re-run setup.sh:"
            hint_python ;;
        *)
            if [ -n "$PY_VER" ]; then
                echo "  NOTE: Python $PY_VER is outside the tested range 3.10-3.13 (newer than tested). The hooks will"
                echo "  use it; if anything misbehaves, install a tested Python ($PYTHON_PIN) beside it and re-run setup.sh."
            else
                echo "  (could not read the version of the hooks' Python; the tested range is 3.10-3.13)"
            fi ;;
    esac
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
    echo "  Install Python 3, then run: bash setup.sh"
    hint_python
fi

# --- Detect the .NET SDK (needed for Spriggit / AutoMod; do NOT auto-install) ---
#
# This checks whether the SDK Spriggit needs is present, not merely that some
# dotnet exists. MEASURED 2026-09-06 on the dev machine: this block previously
# recommended SDK 8 and then printed "Found .NET SDK: 8.0.424", which reads as
# success, while the Spriggit of that day targeted another framework, so every
# invocation died with "You must install or update .NET" -- and because
# tools/esp-verify-wrapper.sh drives spriggit, the ESP cross-reference guard was
# silently unusable too. Reporting presence when the question is capability is how
# an install ends up broken while looking configured.
#
# SDK, not runtime, and exactly major 10. With a runtime alone spriggit starts and
# prints its version, and still cannot serialize: it fetches its serializer at
# runtime as a dotnet tool package (Spriggit.Yaml.Skyrim), whose assets target one
# framework, and only an SDK of that major installs it. Spriggit 0.41.0 and AutoMod
# (its global.json pins 10.0.x, rollForward latestFeature, which never crosses a
# major) both need 10 -- so another major, older OR newer, is not "present".
echo ""
echo "Checking for the .NET SDK..."
if which dotnet >/dev/null 2>&1; then
    DOTNET_SDKS=$(dotnet --list-sdks 2>/dev/null | tr -d '\r' | sed 's/ .*//')
    if printf '%s\n' "$DOTNET_SDKS" | grep -qE '^10\.'; then
        SDK10=$(printf '%s\n' "$DOTNET_SDKS" | grep -E '^10\.' | tail -n1)
        echo "  .NET 10 SDK present ($SDK10) -- Spriggit 0.41.0 and AutoMod both run on it."
        if ! printf '%s\n' "$DOTNET_SDKS" | grep -qxF "$DOTNET_SDK_PIN"; then
            echo "  (the toolkit was tested with SDK $DOTNET_SDK_PIN)"
        fi
    else
        SDK_NEWEST=$(printf '%s\n' "$DOTNET_SDKS" | sed -n '$p')
        if [ -n "$SDK_NEWEST" ]; then
            echo "  .NET SDK $SDK_NEWEST found; Spriggit 0.41.0 and AutoMod need the .NET 10 SDK (installs beside it):"
        else
            echo "  dotnet runs but lists no SDK (a runtime alone); Spriggit 0.41.0 and AutoMod need the .NET 10 SDK:"
        fi
        hint_dotnet
        echo "     (the RUNTIME alone, or an SDK of another major, is not enough: spriggit starts, prints a"
        echo "      version, and cannot serialize, which also disables tools/esp-verify-wrapper.sh)"
    fi
else
    echo "  .NET not found. Spriggit (ESP <-> YAML) and AutoMod CLI need the .NET 10 SDK -- one SDK, nothing else:"
    hint_dotnet
fi

# --- Detect a JDK (needed for ReSaver CLI; do NOT auto-install) ---
# ReSaver CLI compiles its small driver on first run, so it needs a JDK (javac), 17 or newer; 21 is tested.
echo ""
echo "Checking for a JDK..."
if which java >/dev/null 2>&1; then
    JAVA_LINE=$(java -version 2>&1 | tr -d '\r' | head -n1)
    echo "  Found Java: $JAVA_LINE"
    JAVA_MAJOR=$(printf '%s' "$JAVA_LINE" | sed -n 's/.*version "\(1\.\)\{0,1\}\([0-9][0-9]*\).*/\2/p')
    case "$JAVA_MAJOR" in
        ''|*[!0-9]*) echo "  (could not read its version; ReSaver CLI needs JDK 17+, tested with $JDK_PIN)" ;;
        *)
            if [ "$JAVA_MAJOR" -lt 17 ]; then
                echo "  Java $JAVA_MAJOR is too old for ReSaver CLI, which needs JDK 17+ (installs beside it):"
                hint_jdk
            elif ! which javac >/dev/null 2>&1; then
                echo "  No javac: this is a runtime (JRE), and ReSaver CLI compiles its driver, so it needs a JDK:"
                hint_jdk
            fi ;;
    esac
else
    echo "  No JDK found. ReSaver CLI (headless .ess save parse/clean) needs JDK 17+ (tested: $JDK_PIN)."
    hint_jdk
fi

# --- 3.x -> 4.0: carry a 3.x user's own edits forward (tools/migrate-3x.py; it deletes nothing) ---
# Run BEFORE anything that renders CLAUDE.md/AGENTS.md or can run kb-guard, so the 3.x copies are read
# first. A fresh 4.x install prints "nothing to do". Exit 1 = a copy or write failed and nothing was
# half-written: said loudly, and setup carries on. Exit 2 = not an install.
echo ""
echo "Checking for a 3.x install to carry forward..."
MIG_ENV="$GAME_DIR/.claude/backups/migrate-3x/carried-paths.env"
if [ ! -f "$GAME_DIR/tools/migrate-3x.py" ]; then
    echo "  tools/migrate-3x.py is missing -- skipped (re-extract the toolkit zip to restore it)."
elif [ -z "$PY_FOUND" ]; then
    echo "  Migration skipped: it needs Python 3.8+. Your files from before the update are in"
    echo "  .claude/backups/ (pre-update-* if you ran tools/pre-update-snapshot.sh). Re-run setup.sh after installing Python."
else
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND -B "$PY_ROOT/tools/migrate-3x.py" \
        --root "$PY_ROOT" && MIG_RC=0 || MIG_RC=$?
    if [ "$MIG_RC" != 0 ]; then
        echo "  !! The 3.x migration reported a problem (exit $MIG_RC, above). Your 3.x files are in .claude/backups/;"
        echo "  !! setup continues."
    fi
fi

# --- Detect user paths ---
# Query Windows for the actual Documents folder instead of assuming the default location --
# it may be redirected (OneDrive "Back up your folders", a manual Properties > Location move,
# or a GPO folder redirect all update the same Known Folder, none of which live under
# C:/Users/$USERNAME/Documents in that case).
# UTF-8, set INSIDE the probe: piped, Windows PowerShell answers in the OEM code page (cp437 here), so a
# "Dokumente – Müller" folder arrived as cp437 bytes, landed in skyrim-paths.env, and the guard's UTF-8 reader
# refused every command (MEASURED, Plan 8 T7 review C1). This spelling emits UTF-8 with no BOM (MEASURED).
DOCUMENTS_DIR=$(powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('MyDocuments')" 2>/dev/null | tr -d '\r')
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

# --- Paths a 3.x CLAUDE.md held (tools/migrate-3x.py wrote them as data) ---
# A path the user corrected by hand in 3.x wins over the detected one -- but only when it names a real
# folder here: an annotated value ("Survival (NOT Default!)") is printed and the detected one kept.
# SKYRIM_PREFER_DETECTED=1 always keeps the detected paths. Read in a subshell, so nothing in that file can
# set a variable of setup's own; only a fixed key name is ever looked up, never a value evaluated.
pick_3x() {  # $1 = placeholder name, $2 = detected value -> prints the value to use; notes go to stderr
    local v
    v=$( set -a; . "$MIG_ENV" >/dev/null 2>&1; n="SKYRIM_3X_$1"; printf '%s' "${!n:-}" )
    if [ -n "$v" ] && [ "$v" != "$2" ] && [ -d "$v" ] && [ "${SKYRIM_PREFER_DETECTED:-}" != 1 ]; then
        echo "  $1: your 3.x CLAUDE.md said $v (detected: ${2:-nothing}) -- keeping yours" >&2
        printf '%s' "$v"
    else
        if [ -n "$v" ] && [ "$v" != "$2" ]; then
            echo "  $1: your 3.x CLAUDE.md said $v -- not used (not a folder here, or SKYRIM_PREFER_DETECTED=1); using ${2:-nothing}" >&2
        fi
        printf '%s' "$2"
    fi
}
if [ -f "$MIG_ENV" ]; then
    echo ""
    echo "Paths carried from your 3.x install ($MIG_ENV):"
    CONFIG_DIR=$(pick_3x CONFIG_DIR "$CONFIG_DIR")
    LOADORDER_DIR=$(pick_3x LOADORDER_DIR "$LOADORDER_DIR")
    if [ -n "$MO2_INSTANCE" ]; then
        MO2_MODS=$(pick_3x MO2_MODS "$MO2_MODS")
        MO2_OVERWRITE=$(pick_3x MO2_OVERWRITE "$MO2_OVERWRITE")
        MO2_PROFILE_DIR=$(pick_3x MO2_PROFILE_DIR "$MO2_PROFILE_DIR")
    fi
    # GAME_ROOT is never taken from it: setup runs IN the install, so the detected root is the truth.
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
# EVERY guard file, whichever agents this run configures: they all run the one shared engine, and the
# session-start drift check compares the interpreter across all eight (a file left at the placeholder would
# read as edited). An agent this run does not configure still gets a hook that runs on this interpreter.
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

# --- Record the resolved paths where the guard can read them ---
#
# protect-bash.sh decides "is this path inside the install" by resolving a real root,
# not by looking for the word Skyrim in the command. It derives one root on its own --
# the hooks live at <install>/.claude/hooks/, so ../.. is the install -- and reads this
# file for anything else. That ordering is deliberate: the self-derived root ALWAYS
# resolves, so a missing or unreadable file here can never leave the guard with nothing
# to match and, via its fail-closed branch, refusing everything.
#
# v4: the file lives at the toolkit ROOT (<root>/skyrim-paths.env), shared by every agent. A 3.x file at
# .claude/skyrim-paths.env is never rewritten: the reader adds its protection roots to the root file's (see
# retire_3x_paths below), so it is either retired by a verified move or kept and named. The root file is never
# written empty or half-written.
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
PATHS_ENV="$GAME_DIR/skyrim-paths.env"
PATHS_ENV_3X="$GAME_DIR/.claude/skyrim-paths.env"
PATHS_BAD=""
# The guard's OWN reader (tools/skyguard/paths.py), run on the files as data -- never by sourcing them -- so what
# setup checks is what the guard will read. Output is UTF-8 bytes, whatever the console's code page.
#   skypaths notice   the session-start notice for this install, verbatim (paths.legacy_notice), or nothing
#   skypaths readback the six values the guard reads from <root>/skyrim-paths.env, one per line (exit 3: malformed)
#   skypaths legacy   each protected folder the 3.x file still ADDS over the root file, as KEY=value (the reader's
#                     own union, read_install); nothing printed = it adds nothing
skypaths() {
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND -B -c '
import os, sys
mode, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "tools"))
from skyguard import paths
lines = []
if mode == "notice":
    lines = [x for x in [paths.legacy_notice(root)] if x]
elif mode == "readback":
    cfg = paths.read_config(os.path.join(root, paths.PATHS_FILE), {})
    if cfg.malformed:
        sys.exit(3)
    for k in ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR", "SKYRIM_MO2_MODS",
              "SKYRIM_MO2_OVERWRITE", "SKYGUARD_PYTHON"):
        lines.append((cfg.file.get(k) or ("",))[0])
else:
    cfg = paths.read_install(root, {})
    if cfg.legacy:
        own = paths.read_config(os.path.join(root, paths.PATHS_FILE), {})
        for k in paths.PROTECTION_KEYS:
            lines += ["%s=%s" % (k, v) for v in cfg.file.get(k, ()) if v not in own.file.get(k, ())]
sys.stdout.buffer.write("".join(x + "\n" for x in lines).encode("utf-8"))
' "$1" "$PY_ROOT"
}
if [ -f "$PATHS_ENV_3X" ] && [ ! -f "$PATHS_ENV" ] && [ -n "$PY_FOUND" ]; then
    # The state the session-start notice describes, in the reader's own words; this run writes the root file.
    _notice=$(skypaths notice 2>/dev/null) || _notice=""
    if [ -n "$_notice" ]; then
        printf '  %s\n' "$_notice"
        echo "  (this run writes skyrim-paths.env in the toolkit folder now)"
    fi
fi
# The 3.x file under the 4.0 reader: its well-formed PROTECTED folders are added to the root file's (a union -- a
# stale file can add a folder, never hide one); nothing else of it is read. So it must not be left to drift: when
# the reader says it adds nothing, it is retired -- MOVED (not deleted) into .claude/backups/skyrim-paths-3x/<stamp>/
# and the move verified. When it adds a folder it is kept, and the folders are named. It is kept as well until the
# 3.x migration has run here (tools/migrate-3x.py reads it as the 3.9 record), and when no Python can ask the reader.
retire_3x_paths() {
    local extra rc stamp dest
    [ -f "$PATHS_ENV_3X" ] || return 0
    if [ -z "$PY_FOUND" ] || [ "${MIG_RC-unset}" != 0 ]; then
        echo "  .claude/skyrim-paths.env (the 3.x location) is kept until the 3.x migration has run here (it reads"
        echo "  that file). Meanwhile the guard still adds any protected folder it lists that skyrim-paths.env does not."
        return 0
    fi
    extra=$(skypaths legacy 2>/dev/null) && rc=0 || rc=$?
    if [ "$rc" != 0 ]; then
        echo "  .claude/skyrim-paths.env (the 3.x location) could not be compared by the guard's reader, so it is kept."
        return 0
    fi
    if [ -n "$extra" ]; then
        echo "  .claude/skyrim-paths.env (the 3.x location) lists protected folders skyrim-paths.env does not, and the"
        echo "  guard still protects them (it combines the two files) until you remove it:"
        printf '%s\n' "$extra" | sed 's/^/    /'
        echo "  Keep it if those folders should stay protected. Otherwise retire it (moved, not deleted):"
        echo "    mkdir -p .claude/backups/skyrim-paths-3x && mv .claude/skyrim-paths.env .claude/backups/skyrim-paths-3x/"
        return 0
    fi
    stamp=$(date +%Y%m%d_%H%M%S)
    dest="$GAME_DIR/.claude/backups/skyrim-paths-3x/$stamp"
    # A rename keeps every byte, and -n never replaces an earlier copy. Verified: the old path gone, the copy there.
    if mkdir -p "$dest" && mv -n "$PATHS_ENV_3X" "$dest/skyrim-paths.env" && [ ! -e "$PATHS_ENV_3X" ] \
       && [ -f "$dest/skyrim-paths.env" ]; then
        echo "  Retired .claude/skyrim-paths.env (the 3.x location): the guard reads nothing from it that"
        echo "  skyrim-paths.env does not already say. Moved, not deleted: .claude/backups/skyrim-paths-3x/$stamp/skyrim-paths.env"
    else
        echo "  .claude/skyrim-paths.env (the 3.x location) could not be moved into .claude/backups/, so it is kept"
        echo "  (the guard reads nothing from it that skyrim-paths.env does not already say)."
    fi
}
# Written beside it, then renamed: a crash never leaves half a file, or an empty one, in place.
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
    # The interpreter the hooks were configured with ('' when no Python was found).
    printf 'SKYGUARD_PYTHON=%s\n' "$(sq "$PYTHON_PATH")"
} > "$PATHS_ENV.new" 2>/dev/null && mv -f "$PATHS_ENV.new" "$PATHS_ENV" && PATHS_OK=1 || PATHS_OK=""
# It must SOURCE, or the hooks silently fall back to the self-derived root alone. A
# config bash cannot read is the failure this check exists to catch, and it costs
# nothing to run here where the user can still see the message.
# ⚠ Sourcing cleanly proves the file PARSES, which is not the same as proving it
# still says what we wrote. The previous version of this check asked only the first
# question, so a path whose `$` had been expanded away passed it and printed success.
# Read the values back and compare them.
if [ -z "$PATHS_OK" ]; then
    rm -f "$PATHS_ENV.new"
    echo "  WARNING: could not write skyrim-paths.env. The hooks still guard this folder, but not the config directory."
else
    _want=$(printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$GAME_ROOT_WIN" "$CONFIG_DIR" "$LOADORDER_DIR" "$MO2_MODS" \
            "$MO2_OVERWRITE" "$PYTHON_PATH")
    # Cleared first: a value exported by the caller's own shell must not stand in for one the file lacks.
    _got=$( unset SKYRIM_GAME_ROOT SKYRIM_CONFIG_DIR SKYRIM_LOADORDER_DIR SKYRIM_MO2_MODS SKYRIM_MO2_OVERWRITE SKYGUARD_PYTHON
            set -a; . "$PATHS_ENV" 2>/dev/null
            printf '%s\n%s\n%s\n%s\n%s\n%s\n' "${SKYRIM_GAME_ROOT:-}" "${SKYRIM_CONFIG_DIR:-}" "${SKYRIM_LOADORDER_DIR:-}" \
                "${SKYRIM_MO2_MODS:-}" "${SKYRIM_MO2_OVERWRITE:-}" "${SKYGUARD_PYTHON:-}" )
    # ...and what the GUARD reads (its own parser, UTF-8). Sourcing alone passed a file of cp437 bytes that the
    # guard cannot decode, which makes it refuse every command (T7 review C1). No Python = no hook runs anyway.
    _guard="$_want"; _grc=0
    if [ -n "$PY_FOUND" ]; then
        _guard=$(skypaths readback 2>/dev/null) && _grc=0 || _grc=$?
    fi
    if [ "$_want" = "$_got" ] && [ "$_grc" = 0 ] && [ "$_want" = "$_guard" ]; then
        echo "  Wrote: skyrim-paths.env"
        retire_3x_paths
    else
        PATHS_BAD=1
        echo ""
        echo "  !! skyrim-paths.env does NOT read back as written ($( [ "$_grc" != 0 ] && echo "the guard's reader failed, exit $_grc" \
            || echo "a value came back different")). While the guard cannot read it, it REFUSES EVERY COMMAND."
        echo "  !! The cause is a character in one of the paths above (an encoding, or one the guard refuses)."
        echo "  !! Fix that path (or set the folder by hand in skyrim-paths.env) and re-run: bash setup.sh"
    fi
fi

# --- Configure the Codex hook definitions (.codex/hooks.json) ---
#
# Codex runs a project hook only after the user approves it, and the approval is a hash of the rendered
# definition -- install path included. So the rendering is part of the frozen surface: it is done by
# tools/skyguard/codex_definitions.py, the ONE renderer (pinned by tests against hashes Codex itself
# reported), never re-implemented here with sed. It refuses a root holding % ! $ ` or " (cmd.exe or bash
# would expand or cut the hook's path, the hook could not start, and a hook that cannot start lets every
# command RUN); then the Codex side is left unconfigured and this says so.
# Setup never marks a Codex project trusted and never approves a hook: both are the user's, in Codex.
echo ""
CODEX_DEFS="$GAME_DIR/.codex/hooks.json"
CODEX_STATE="none"
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
if [ -z "$DO_CODEX" ]; then
    # Not selected. Nothing under .codex/ is touched or deleted; the state is reported below.
    if [ -f "$CODEX_DEFS" ] && ! grep -q '{{CODEX_ROOT' "$CODEX_DEFS" && codex_same_root; then
        CODEX_STATE="configured"
    elif [ -f "$CODEX_DEFS" ] && ! grep -q '{{CODEX_ROOT' "$CODEX_DEFS"; then
        CODEX_STATE="elsewhere"
    fi
    echo "Codex hooks (.codex/hooks.json): not configured by this run (--agent $AGENT_SEL) -- left as they are."
else
echo "Configuring the Codex hooks (.codex/hooks.json)..."
# A leftover from an interrupted run is never taken for the result: the renderer writes a fresh one.
rm -f "$CODEX_DEFS.new"
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
fi

# --- Configure CLAUDE.md and AGENTS.md (replace path placeholders) ---
# Both carry the same shared instructions (agent/instructions/core.md) and so the same placeholders:
# CLAUDE.md for Claude Code, AGENTS.md for Codex and other agents. Only the selected agents' files are
# filled; the other is kept exactly as extracted. Every placeholder is filled separately, so a file an
# interrupted run left half-done (root filled, config dir not) is completed by the next run.
# A value spliced into sed's REPLACEMENT is escaped first: `&` means "the whole match" there (a root holding
# "Skyrim & Mods" re-inserted the placeholder, so CLAUDE.md grew on every run), and `\` and the `|` delimiter are
# special too. The same escape the interpreter path gets above.
sed_repl() { printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'; }
DOC_PLACEHOLDERS='{{GAME_ROOT}}\|{{CONFIG_DIR}}\|{{LOADORDER_DIR}}\|{{SKYRIM_FOLDER}}\|{{MOD_MANAGER_PATHS}}'
for DOC in CLAUDE.md AGENTS.md; do
case "$DOC" in
    CLAUDE.md) DOC_ON="$DO_CLAUDE" ;;
    *) DOC_ON="$DO_CODEX" ;;
esac
echo ""
if [ -z "$DOC_ON" ]; then
    echo "$DOC: not configured by this run (--agent $AGENT_SEL) -- left as it is."
    continue
fi
echo "Configuring $DOC..."
if [ ! -f "$GAME_DIR/$DOC" ]; then
    echo "  MISSING: $DOC"
elif grep -q "$DOC_PLACEHOLDERS" "$GAME_DIR/$DOC"; then
    sed -i "s|{{GAME_ROOT}}|$(sed_repl "$GAME_ROOT_WIN")|g" "$GAME_DIR/$DOC"
    sed -i "s|{{CONFIG_DIR}}|$(sed_repl "$CONFIG_DIR")|g" "$GAME_DIR/$DOC"
    sed -i "s|{{LOADORDER_DIR}}|$(sed_repl "$LOADORDER_DIR")|g" "$GAME_DIR/$DOC"
    sed -i "s|{{SKYRIM_FOLDER}}|$(sed_repl "$SKYRIM_FOLDER")|g" "$GAME_DIR/$DOC"

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

# --- The instruction budget, measured on the files just written ---
# Codex reads at most 32,768 bytes of AGENTS.md (project_doc_max_bytes) and silently drops the rest; the
# toolkit holds core + addendum + a 2,560-byte install reserve under that (scripts/gen_agent_trees.py
# INSTRUCTION_BUDGET). A very long install root or MO2 paths can still push the INSTALLED copy past it.
INSTRUCTION_BUDGET=32768
for BDOC in CLAUDE.md AGENTS.md; do
    [ -f "$GAME_DIR/$BDOC" ] || continue
    grep -q "$DOC_PLACEHOLDERS" "$GAME_DIR/$BDOC" && continue      # not configured: nothing installed to measure
    DOC_BYTES=$(wc -c < "$GAME_DIR/$BDOC" | tr -d ' \r')
    [ "$DOC_BYTES" -gt "$INSTRUCTION_BUDGET" ] || continue
    echo ""
    echo "  WARNING: $BDOC is $DOC_BYTES bytes, over the $INSTRUCTION_BUDGET-byte instruction budget (by $((DOC_BYTES - INSTRUCTION_BUDGET)))."
    if [ "$BDOC" = "AGENTS.md" ]; then
        echo "  Codex reads only the first $INSTRUCTION_BUDGET bytes of AGENTS.md in a project it does not trust (65,536 in a"
        echo "  trusted one, set by .codex/config.toml): past that, the END of its instructions is cut off, silently."
    else
        echo "  Claude Code reads all of it; the same text in AGENTS.md is what Codex would cut off."
    fi
    echo "  The cause is the length of your install paths (filled in above). A shorter game folder path fixes it."
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
    sed -i "s|{{DEVCONTAINER_MODS_MOUNT}}|$(sed_repl "$DC_MODS")|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    sed -i "s|{{DEVCONTAINER_PROFILE_MOUNT}}|$(sed_repl "$DC_PROFILE")|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    sed -i "s|{{DEVCONTAINER_OVERWRITE_MOUNT}}|$(sed_repl "$DC_OVERWRITE")|g" "$GAME_DIR/.devcontainer/devcontainer.json"
    echo "  Configured .devcontainer/devcontainer.json with your mod paths."
    echo "  Run ./devshell-docker.sh (Docker only) or ./devshell.sh (needs the devcontainer CLI)."
fi

# --- Ensure backup directory exists (both agents' hooks write their records under it) ---
mkdir -p "$GAME_DIR/.claude/backups"

# --- Copy settings.local.json.example if no settings.local.json exists (Claude Code only) ---
if [ -n "$DO_CLAUDE" ] && [ ! -f "$GAME_DIR/.claude/settings.local.json" ] && [ -f "$GAME_DIR/.claude/settings.local.json.example" ]; then
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
    echo "    or set the NEXUS_API_KEY environment variable. Your agent can do this for you on request."
fi

# --- Layer 2: the read-only lock on the vanilla masters and archives -- ONLY with --lock ---
# Off by default (Plan 6 R3: layer2.default = "off" until the Final Manual Batch decides). Setup never
# applies it unasked; --lock runs the same command a user would (tools/skyguard lock lock).
LOCK_RC=""
echo ""
if [ -n "$WANT_LOCK" ]; then
    echo "Applying the Layer 2 lock (--lock)..."
    if [ -z "$PY_FOUND" ]; then
        LOCK_RC=2
        echo "  !! LOCK NOT APPLIED: it needs Python 3.8+. Install it and re-run: bash setup.sh --lock"
    else
        MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $PY_FOUND -B "$PY_ROOT/tools/skyguard" lock lock \
            --root "$PY_ROOT" && LOCK_RC=0 || LOCK_RC=$?
        if [ "$LOCK_RC" = 0 ]; then
            echo "  The lock is on. Unlock one file with: bash tools/skyguard.sh lock unlock <file>"
        else
            echo "  !! LOCK NOT (fully) APPLIED (exit $LOCK_RC, above). Check with: bash tools/skyguard.sh lock status"
        fi
    fi
else
    echo "Layer 2 lock: OFF (the default). bash setup.sh --lock makes the vanilla masters and archives read-only."
fi

# --- Which agent ends up configured, said plainly ---
CLAUDE_STATE="not configured"
if [ -f "$GAME_DIR/CLAUDE.md" ] && ! grep -q "$DOC_PLACEHOLDERS" "$GAME_DIR/CLAUDE.md"; then CLAUDE_STATE="configured"; fi
AGENTS_DOC_STATE="not configured"
if [ -f "$GAME_DIR/AGENTS.md" ] && ! grep -q "$DOC_PLACEHOLDERS" "$GAME_DIR/AGENTS.md"; then AGENTS_DOC_STATE="configured"; fi

echo ""
echo "============================================"
echo " Setup Complete!"
echo "============================================"
echo ""
echo "Agents in this folder:"
if [ "$CLAUDE_STATE" = "configured" ]; then
    echo "  Claude Code: CONFIGURED -- CLAUDE.md has your paths; the hooks in .claude/settings.json guard every call."
else
    echo "  Claude Code: NOT CONFIGURED -- CLAUDE.md has no paths filled in (this run: --agent $AGENT_SEL)."
    echo "               Its hooks still guard (they run on the interpreter above). To set it up: bash setup.sh --agent claude"
fi
case "$CODEX_STATE/$AGENTS_DOC_STATE" in
    configured/configured) CODEX_SUMMARY="both" ;;
    configured/*) CODEX_SUMMARY="hooks-only" ;;
    *) CODEX_SUMMARY="" ;;
esac
if [ "$CODEX_SUMMARY" = "both" ]; then
    echo "  Codex:       CONFIGURED -- approve the 2 hooks once in Codex's /hooks; until then Codex runs UNGUARDED."
elif [ "$CODEX_SUMMARY" = "hooks-only" ]; then
    echo "  Codex:       hooks configured, but AGENTS.md has no paths filled in. To finish: bash setup.sh --agent codex"
elif [ -z "$CODEX_WIN" ]; then
    echo "  Codex:       NOT CONFIGURED -- the toolkit's Codex support is Windows-only."
else
    echo "  Codex:       NOT CONFIGURED -- .codex/hooks.json is not rendered for this folder, so Codex runs here"
    echo "               WITHOUT the toolkit's hooks, and AGENTS.md may have no paths. To set it up: bash setup.sh --agent codex"
    echo "               (bash tools/skydoctor reports it as 'Codex is NOT CONFIGURED' until then.)"
fi
echo ""
echo "Installed:"
echo "  CLAUDE.md                        -- Project instructions for Claude Code"
echo "  AGENTS.md                        -- The same instructions for Codex and other agents"
echo "  .codex/                          -- Codex hook definitions + entries (approve them in Codex /hooks)"
echo "  KNOWLEDGEBASE.md                 -- 1,300+ lines of Skyrim modding knowledge"
echo "  skyrim-paths.env                 -- The paths the safety hooks guard (machine-local)"
echo "  .claude/settings.json            -- Hook configuration"
echo "  .claude/hooks/protect-bash.sh    -- Guards dangerous commands"
echo "  .claude/hooks/protect-files.sh   -- Guards file edits"
echo "  .claude/hooks/protect-mcp.sh     -- Guards the DevBench MCP tools (smp reset denied, save overwrites asked)"
echo "  .claude/hooks/backup-before-edit.sh -- Auto-backups (Edit/Write) with audit trail"
echo "  .claude/hooks/snapshot-before-tool.sh -- Auto-snapshots .psc/.pex before Bash commands"
echo "  .claude/hooks/session-kb-guard.sh -- Snapshots KNOWLEDGEBASE/CLAUDE.md/AGENTS.md once per session"
echo "  tools/                           -- Helper scripts (AutoMod wrapper, esp-verify, NIF tools, nexus.sh, resaver-cli.sh)"
echo "  .claude/backups/                 -- Backup storage"
echo ""
echo "The safety hooks are now active. They will:"
echo "  - Ask permission before editing any game file"
echo "  - Block direct writes to ESP/ESM/BSA files"
echo "  - Automatically back up files before modifying them"
echo "  - Snapshot your active scripts before running external tools"
echo ""
echo "--------------------------------------------"
echo " Optional modding tools (install as needed)"
echo "--------------------------------------------"
echo "These are NOT bundled. Ask your agent to set up any you want, or install them yourself -- each at exactly the"
echo "version docs/installing-tools.md names, every download checked against its SHA-256 in *Pinned downloads*:"
echo "  xeditlib     -- programmatic ESP read/write (v1.0.1 = commit 19bef09 + koffi 2.15.1, integrity-checked by"
echo "                  package-lock.json), from THIS toolkit root:  npm ci --ignore-scripts --no-audit --no-fund"
echo "  Champollion  -- Papyrus .pex -> .psc:  v1.3.2 (Champollion.v1.3.2.zip; SHA-256 in Pinned downloads)"
echo "  Caprica      -- Papyrus .psc -> .pex:  v0.3.0 (Caprica.v0.3.0.7z; SHA-256 in Pinned downloads)"
echo "  Spriggit     -- ESP <-> YAML editing, 0.41.0 (needs the .NET 10 SDK), from THIS toolkit root:"
echo "                  dotnet tool restore   (installs exactly .config/dotnet-tools.json's 0.41.0 as a local tool)"
echo "                  or: dotnet tool install --global Spriggit.CLI --version 0.41.0"
echo "                  (never install it unpinned: you get whatever is newest, not what was tested)"
echo "                  (deep output paths: use tools/spriggit-cli.sh)"
echo "  AutoMod CLI  -- NIF / BSA / audio / MCM / ESP (needs the .NET 10 SDK):"
echo "                  git clone --branch v1.11.2 https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod"
echo "                  then: bash tools/automod-cli.sh --rebuild   (checks out the tested commit e1c34ea,"
echo "                  retargets it to the .NET 10 SDK, builds the Cli project only; first build needs internet)"
echo "                  -- never build it with a bare dotnet build -- then use tools/automod-cli.sh"
echo "  PyFFI        -- LE NiTriShape geometry (PyFFI 2.2.3 + setuptools 84.0.0, hash-locked), from THIS toolkit root:"
echo "                  python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock"
echo "  PyNifly      -- SSE BSTriShape + animation authoring: release V27.2.0 io_scene_nifly.zip (SHA-256 in Pinned"
echo "                  downloads), extracted into tools/pynifly/. Verified with 27.2.0: the DLL it loads is"
echo "                  tools/pynifly/io_scene_nifly/NiflyDLL.dll (the zip's pyn/NiflyDLL.dll is unused); no build."
echo "                  NOTE: a git clone does NOT include the compiled DLL -- use the release zip"
echo "  Blender      -- NIF repair + render-to-PNG (headless): 4.2.9 LTS (SHA-256 in Pinned downloads)"
echo "                  + the PyNifly Blender add-on, the same 27.2.0 io_scene_nifly.zip"
echo "  NifSkope     -- independent visual NIF render gate: v2.0.dev9 from the hexabits fork (SHA-256 in Pinned downloads)"
echo "  ReSaver CLI  -- headless .ess parse/cross-ref/clean: ReSaver 6.0.643 (FallrimTools, Nexus mod 5031); drop"
echo "                  ReSaver.jar + lib/ into tools/resaver-cli/ and check ReSaver.jar's SHA-256 in Pinned downloads;"
echo "                  needs JDK 17+ (21 tested); the wrapper auto-compiles its driver on first run (tools/resaver-cli.sh)"
echo "  cosave-info  -- read-only SKSE .skse co-save survey (bundled): bash tools/cosave-cli.sh <save.skse>"
echo "  DevBench     -- LIVE in-game inspect/console/Papyrus while you play:"
echo "                  Nexus mod 181326 (alandtse). This one is a MOD, not a tools/ utility --"
echo "                  install it with your mod manager (Vortex/MO2), not by hand into Data/."
echo "                  Needs DevBench 1.11.0+ (1.24.0 newest seen). Update the VR Address Library"
echo "                  FIRST (0.276.1 newest seen): DevBench 1.19+ is built on CommonLibSSE-NG 8.3.0."
echo "                  dev-only, no gameplay change, no save data. Drive it via tools/devbench-cli.sh"
echo "                  (bundled wrapper). Lets your agent test its own fixes in the running game instead"
echo "                  of asking you to launch, trigger, and report back."
echo ""
echo "You're ready to go! Start your agent in this folder and ask it about your mods."

# --- Exit status: 0, or 1 when something this run was explicitly asked to do did not happen ---
# --agent codex names Codex alone: Codex left unconfigured means the run did nothing it was asked for.
# --lock asked for the lock: a lock that did not apply is said above and reported here too.
SETUP_RC=0
if [ "$AGENT_SEL" = "codex" ] && { [ "$CODEX_STATE" != "configured" ] || [ "$AGENTS_DOC_STATE" != "configured" ]; }; then
    echo ""
    echo "!! --agent codex: Codex is NOT configured (see above), so this run did not do what it was asked."
    SETUP_RC=1
fi
if [ -n "$PATHS_BAD" ]; then
    echo ""
    echo "!! skyrim-paths.env could not be read back as the guard reads it (see above): the guard refuses every command."
    SETUP_RC=1
fi
if [ -n "$WANT_LOCK" ] && [ "$LOCK_RC" != 0 ]; then
    SETUP_RC=1
fi
exit "$SETUP_RC"
