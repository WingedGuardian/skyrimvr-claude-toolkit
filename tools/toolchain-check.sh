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
    FAIL=1; MISSING="$MISSING $label"; return
  fi
  out="$("$@" 2>&1)"; rc=$?
  if printf '%s' "$out" | grep -qiE 'you must install|to install missing framework|command not found|No such file'; then
    printf '  %-22s CANNOT START  -- %s\n' "$label" "$(printf '%s' "$out" | grep -iE 'you must install|framework|not found' | head -1 | cut -c1-70)"
    FAIL=1; MISSING="$MISSING $label"; return
  fi
  if [ -n "$expect" ] && ! printf '%s' "$out" | grep -qiE "$expect"; then
    printf '  %-22s SUSPECT       -- ran (rc=%s) but output did not look like %s\n' "$label" "$rc" "$label"
    FAIL=1; MISSING="$MISSING $label"; return
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

command -v bash >/dev/null 2>&1 || { echo "no bash?" >&2; exit 2; }

echo "runtimes:"
check "python"        "Python 3"       python --version
check "node"          "v[0-9]"         node --version
check "jq"            "jq-"            jq --version
check "java"          "version"        java -version
check "dotnet"        "[0-9]+\."       dotnet --version

echo "modding tools:"
if command -v spriggit >/dev/null 2>&1; then
  check "spriggit"    "[0-9]"          spriggit --version

  # Spriggit STARTING is not Spriggit WORKING. An earlier version of this script
  # reported "ok" for a spriggit that could not serialize anything -- presence
  # rather than capability, the exact confusion the whole script exists to catch,
  # committed inside the script itself.
  #
  # MEASURED: spriggit resolves its serializer at RUNTIME via `dotnet tool install
  # Spriggit.Yaml.Skyrim`. Its tool assets ship under tools/net9.0/ (CLI 0.40.0) or
  # tools/net10.0/ (0.41.0), and the SDK can only install a tool whose target
  # framework it supports. With only SDK 8 the install fails with a misleading
  # "DotnetToolSettings.xml was not found in the package" -- the file IS there,
  # under a framework that SDK will not select. A control proved the machine was
  # otherwise healthy: dotnetsay, whose assets are net8.0, installs fine.
  SDK_MAJORS=$(dotnet --list-sdks 2>/dev/null | sed 's/\..*//' | sort -un | tr '\n' ' ')
  if dotnet --list-sdks 2>/dev/null | grep -qE '^(9|1[0-9])\.'; then
    printf '  %-22s ok            SDK 9+ present, so it can fetch its serializer\n' "spriggit entry point"
  else
    printf '  %-22s CANNOT WORK   spriggit starts but cannot install its serializer\n' "spriggit entry point"
    printf '  %-22s               (SDK majors here: %s-- needs 9+. Install:\n' "" "$SDK_MAJORS"
    printf '  %-22s               winget install Microsoft.DotNet.SDK.9)\n' ""
    FAIL=1; MISSING="$MISSING spriggit-entry-point"
  fi
else
  printf '  %-22s not installed (optional)\n' "spriggit"
fi

optional "Champollion" "$ROOT/tools/Champollion/Champollion.exe" --help
optional "Caprica"     "$ROOT/tools/Caprica/Caprica.exe" --help

echo "our wrappers (do they reach their dependency?):"
for w in automod-cli resaver-cli; do
  if [ -f "$ROOT/tools/$w.sh" ]; then
    check "$w" "" bash "$ROOT/tools/$w.sh" --help
  else
    printf '  %-22s not present (optional)\n' "$w"
  fi
done

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
