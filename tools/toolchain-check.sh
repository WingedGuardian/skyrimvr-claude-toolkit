#!/usr/bin/env bash
# Does every tool this toolkit documents actually start?
#
# WHY THIS EXISTS. Spriggit was unable to run here for an unknown period -- it needs
# a .NET 9 SDK and the machine had only 6 and 8. Nothing reported it, because nothing
# asked. CLAUDE.md still called Spriggit the PREFERRED ESP editing workflow, and
# `esp-verify-wrapper.sh` -- the ESP corruption guard -- drives spriggit, so that
# guard was down too. **A documented tool that cannot start is a capability you
# believe you have and do not.**
#
# This checks STARTABILITY (and, for spriggit, one capability that startability does
# not imply). It never asks a tool to modify anything, so it is safe to run at any
# time and cannot touch your install.
#
# Exit: 0 = all present · 1 = at least one cannot start · 2 = could not check
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FAIL=0
MISSING=""
# Test seam: tests/test_toolchain_check.py points this at a fake `dotnet` (the harness shadows the real name).
DOTNET="${TOOLCHAIN_DOTNET:-dotnet}"
# The .NET SDK the toolkit was tested with (the devcontainer pins the same one: .devcontainer/Dockerfile
# DOTNET_SDK_VERSION; scripts/thirdparty-audit.py holds the two equal). Install hints name it exactly.
SDK_PIN=10.0.401

# check <label> <expect-substring> <command...>
#
# A tool "starts" when it runs AND its output looks like itself. A bare exit code is
# not enough: spriggit's launcher exits 0 while printing "You must install .NET",
# which is precisely the case that went unnoticed for weeks.
check() {
  local label="$1" expect="$2"; shift 2
  local out rc
  if ! command -v "$1" >/dev/null 2>&1 && [ ! -f "$1" ]; then
    printf '  %-22s NOT INSTALLED\n' "$label"
    FAIL=1; MISSING="$MISSING $label"; return 1
  fi
  out="$("$@" 2>&1)"; rc=$?
  if printf '%s' "$out" | grep -qiE 'you must install|to install missing framework|command not found|No such file'; then
    printf '  %-22s CANNOT START  -- %s\n' "$label" "$(printf '%s' "$out" | grep -iE 'you must install|framework|not found' | head -1 | cut -c1-70)"
    FAIL=1; MISSING="$MISSING $label"; return 1
  fi
  if [ -n "$expect" ] && ! printf '%s' "$out" | grep -qiE "$expect"; then
    printf '  %-22s SUSPECT       -- ran (rc=%s) but output did not look like %s: %s\n' "$label" "$rc" "$label" \
      "$(printf '%s' "$out" | grep -v '^[[:space:]]*$' | head -1 | cut -c1-60)"
    FAIL=1; MISSING="$MISSING $label"; return 1
  fi
  printf '  %-22s ok            %s\n' "$label" "$(printf '%s' "$out" | head -1 | cut -c1-52)"
}

optional() {
  local label="$1" path="$2"; shift 2
  if [ -f "$path" ]; then
    check "$label" "" "$path" "$@"
  else
    printf '  %-22s not installed (optional)\n' "$label"
  fi
}

echo "=============================================================="
echo "TOOLCHAIN CHECK -- can each documented tool actually start?"
echo "=============================================================="

# The bash running THIS script runs the wrappers too. A bare `bash` on PATH can be WSL's launcher
# (C:\Windows\System32\bash.exe), which exits 1 with no distro installed -- not Git's bash.exe.
{ [ -n "${BASH:-}" ] && [ -x "$BASH" ]; } || { echo "cannot tell which bash is running this check" >&2; exit 2; }

echo "runtimes:"
check "python"        "Python 3"       python --version
check "node"          "v[0-9]"         node --version
check "jq"            "jq-"            jq --version
check "java"          "version"        java -version
check "dotnet"        "[0-9]+\."       "$DOTNET" --version

echo "modding tools:"
# Two documented installs. The global tool puts `spriggit` on PATH; `dotnet tool restore` installs the
# version pinned in .config/dotnet-tools.json as a LOCAL tool, reachable only as `dotnet tool run spriggit`
# from inside the toolkit -- and an earlier version of this script called that healthy install "not
# installed" (review I3). Same order as tools/spriggit-cli.sh and esp-verify-wrapper.sh: global first.
spriggit_local() { (cd "$ROOT" && "$DOTNET" tool run spriggit -- "$@"); }
SPRIGGIT=""
if command -v spriggit >/dev/null 2>&1; then
  SPRIGGIT=spriggit
# Judge by OUTPUT: MEASURED 2026-10-05, `spriggit --version` prints its version and EXITS 1 (global or
# local), and a `| grep -q` under pipefail fails on the SIGPIPE it causes. Capture, then match.
elif SP_OUT=$(spriggit_local --version 2>&1); printf '%s' "$SP_OUT" | grep -qE 'Spriggit[^0-9]*[0-9]+\.[0-9]+'; then
  SPRIGGIT=spriggit_local
fi
if [ -n "$SPRIGGIT" ]; then
  [ "$SPRIGGIT" = spriggit_local ] && printf '  %-22s (local tool from .config/dotnet-tools.json: dotnet tool run spriggit)\n' "spriggit"
  check "spriggit"    "[0-9]"          "$SPRIGGIT" --version

  # Spriggit STARTING is not Spriggit WORKING. An earlier version of this script
  # reported "ok" for a spriggit that could not serialize anything -- presence
  # rather than capability, the exact confusion the whole script exists to catch,
  # committed inside the script itself.
  #
  # MEASURED: spriggit resolves its serializer at RUNTIME (it runs dotnet's own tool installer for
  # Spriggit.Yaml.Skyrim, at the CLI's own version). Its tool assets ship under tools/net9.0/ (CLI 0.40.0) or
  # tools/net10.0/ (0.41.0), and the SDK can only install a tool whose target
  # framework it supports. With only SDK 8 the install fails with a misleading
  # "DotnetToolSettings.xml was not found in the package" -- the file IS there,
  # under a framework that SDK will not select. A control proved the machine was
  # otherwise healthy: dotnetsay, whose assets are net8.0, installs fine.
  #
  # VERSION PIN. Every serialize/deserialize command in the toolkit was written against the
  # version pinned in .config/dotnet-tools.json (0.41.0, target net10.0). An install of
  # Spriggit.CLI without --version pulls whatever is newest, and an older one (0.40.0 =
  # net9.0) is just as untested, so the INSTALLED version must equal the pin, and the SDK
  # required follows the INSTALLED version.
  PIN=$(tr -d '\r\n ' < "$ROOT/.config/dotnet-tools.json" 2>/dev/null | sed -n 's/.*"spriggit\.cli":{"version":"\([^"]*\)".*/\1/p')
  INSTALLED=$("$SPRIGGIT" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  if [ -z "$PIN" ]; then
    printf '  %-22s unknown       no pin readable from .config/dotnet-tools.json\n' "spriggit version"
  elif [ "$INSTALLED" = "$PIN" ]; then
    printf '  %-22s ok            installed %s == pinned %s\n' "spriggit version" "$INSTALLED" "$PIN"
  else
    printf '  %-22s MISMATCH      installed %s, toolkit pinned to %s\n' "spriggit version" "${INSTALLED:-?}" "$PIN"
    if [ "$SPRIGGIT" = spriggit_local ]; then
      printf '  %-22s               fix: dotnet tool restore   (from the toolkit root)\n' ""
    else
      printf '  %-22s               fix: dotnet tool uninstall -g Spriggit.CLI; dotnet tool install -g Spriggit.CLI --version %s\n' "" "$PIN"
    fi
    FAIL=1; MISSING="$MISSING spriggit-version"
  fi
  # Target runtime (SDK major) the INSTALLED spriggit needs: 0.41+ ships net10.0, earlier net9.0.
  SP_MINOR=$(printf '%s' "$INSTALLED" | cut -d. -f2)
  SP_MAJOR_N=$(printf '%s' "$INSTALLED" | cut -d. -f1)
  NEED=9
  if [ "${SP_MAJOR_N:-0}" -gt 0 ] 2>/dev/null || [ "${SP_MINOR:-0}" -ge 41 ] 2>/dev/null; then NEED=10; fi
  SDK_MAJORS=$("$DOTNET" --list-sdks 2>/dev/null | sed 's/\..*//' | sort -un | tr '\n' ' ')
  if "$DOTNET" --list-sdks 2>/dev/null | sed 's/\..*//' | awk -v n="$NEED" '$1+0>=n{f=1} END{exit !f}'; then
    printf '  %-22s ok            SDK %s+ present, so it can fetch its serializer\n' "spriggit entry point" "$NEED"
  else
    printf '  %-22s CANNOT WORK   spriggit starts but cannot install its serializer\n' "spriggit entry point"
    printf '  %-22s               (SDK majors here: %s-- spriggit %s needs SDK %s+. Install the tested SDK:\n' "" "$SDK_MAJORS" "${INSTALLED:-?}" "$NEED"
    printf '  %-22s               winget install --id Microsoft.DotNet.SDK.10 --exact --version %s)\n' "" "$SDK_PIN"
    FAIL=1; MISSING="$MISSING spriggit-entry-point"
  fi
else
  printf '  %-22s not installed (optional)\n' "spriggit"
fi

optional "Champollion" "$ROOT/tools/Champollion/Champollion.exe" --help
optional "Caprica"     "$ROOT/tools/Caprica/Caprica.exe" --help

echo "our wrappers (do they reach their dependency?):"
# wrapper <name> <the dependency the user installs> <what a WORKING wrapper prints for --help>
#
# The identity is the point. Both wrappers START fine with their dependency missing -- they print
# "Built DLL not found" / {"ok":false,"error":"ReSaver.jar not found"} -- and an empty expectation
# reported that as "ok". An error pattern will not do either: a WORKING resaver-cli answers --help
# with {"ok":false,"error":"need <op> <save> [...]"} from our own driver (ResaverCLI.java).
#
# <also-needs> (optional): a glob that must match once the dependency is there. ReSaver's --help answers
# from our driver BEFORE any ReSaver class loads, so with lib/ deleted after the first compile it still
# printed its identity and read "ok", and the first real op then failed (review M1).
wrapper() {
  local w="$1" dep="$2" expect="$3" also="${4:-}"
  if [ ! -f "$ROOT/tools/$w.sh" ]; then
    printf '  %-22s not present (optional)\n' "$w"; return 1
  elif [ ! -e "$dep" ]; then
    printf '  %-22s not installed (optional)\n' "$w"; return 1
  elif [ -n "$also" ] && ! compgen -G "$also" >/dev/null; then
    printf '  %-22s INCOMPLETE    -- %s is installed but %s is missing\n' "$w" "${dep#"$ROOT"/}" "${also#"$ROOT"/}"
    FAIL=1; MISSING="$MISSING $w"; return 1
  fi
  check "$w" "$expect" "$BASH" "$ROOT/tools/$w.sh" --help
}
# AutoMod is pinned to one upstream commit; its wrapper records the commit each build came from.
if wrapper automod-cli "$ROOT/tools/automod" "Spooky's AutoMod Toolkit"; then
  if AM_BUILT=$("$BASH" "$ROOT/tools/automod-cli.sh" --built 2>&1); then
    printf '  %-22s ok            %s\n' "automod version" "$AM_BUILT"
  else
    printf '  %-22s MISMATCH      %s\n' "automod version" "$AM_BUILT"
    FAIL=1; MISSING="$MISSING automod-version"
  fi
fi
wrapper resaver-cli "$ROOT/tools/resaver-cli/ReSaver.jar" "need <op>" "$ROOT/tools/resaver-cli/lib/*.jar"

echo "safety:"
if [ -f "$ROOT/tools/hook-canary.sh" ]; then
  printf '  %-22s present       run it to prove the hooks receive their payload\n' "hook-canary"
else
  printf '  %-22s MISSING       nothing can confirm the hooks are alive\n' "hook-canary"
  FAIL=1; MISSING="$MISSING hook-canary"
fi

echo "--------------------------------------------------------------"
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: OK -- every documented tool starts"
else
  echo "RESULT: DEGRADED --$MISSING"
  echo "        A documented tool that cannot start -- or that starts and cannot do"
  echo "        its job -- is a capability you believe you have and do not. Fix it or"
  echo "        correct the docs; do not route around it silently."
fi
exit $FAIL
