#!/usr/bin/env bash
# Verify a toolkit release ARTIFACT -- the zip a user extracts, never the working tree.
#
#   bash tools/verify-release.sh vX.Y.Z                         the PUBLISHED zip (gh release download)
#   bash tools/verify-release.sh --local <zip> [--scrubbed] [--expect-commit SHA] [--tag vX.Y.Z]
#
# --local       a zip built here by scripts/build-release-zip.sh (the release workflow runs this on the zip it
#               is about to publish; a local pre-release build runs it too).
# --scrubbed    also install the zip into a fresh folder with a fake SkyrimVR.exe and run setup.sh, the
#               canary, the generator drift check, the engine selftest, a payload through every hook entry
#               and the instruction-size check -- all with the environment CLEARED (env -i): HOME/USERPROFILE/
#               APPDATA point at an empty folder, PATH holds only the interpreters, and nothing of the author's
#               machine (X4_NEXUS_KEY, PYTHONPATH, CLAUDE_*/CODEX_*, the repo checkout) is visible. A canary
#               variable (PLAN8_LEAK_CANARY) is set out here and must be INVISIBLE in there: that is the
#               tripwire for the scrub itself. Windows keeps SystemRoot/SystemDrive/ComSpec/PATHEXT/WINDIR,
#               without which Python and the harness create stray folders (deferred item 24).
# --expect-commit  BUILD-INFO in the zip must name this commit.
# --tag         the version the CHANGELOG must carry a section for (default: the published tag, or BUILD-INFO).
#
# Leak greps (both modes): every file of the payload, and with --scrubbed every file setup.sh generated,
# for the home folder and user name of whoever runs this, in every spelling (tools/check-release-payload.py
# --leaks; the name comes from the environment, never from this repo), for the VALUE of every secret this
# machine holds (X4_NEXUS_KEY, NEXUS_API_KEY, GH_TOKEN, GITHUB_TOKEN, tools/.nexus_api_key -- never printed),
# and for API-key shapes (Nexus, GitHub, OpenAI-style, AWS). LEAK_TOKENS (newline-separated names and paths)
# adds the maintainer's identity where the environment cannot supply it: the release workflow fills it from
# the repository secret LEAK_TOKENS and sets VERIFY_REQUIRE_LEAK_TOKENS=1, so an empty secret FAILS CLOSED
# (a runner's own name is `runner`, which says nothing about the maintainer). The authoritative identity gate
# is this script run LOCALLY by the maintainer before any tag. RELEASE_LEAK_PATHS='p1|p2' adds paths,
# e.g. this machine's real Skyrim folder.
#
# Why the artifact: the zip and the repo have diverged and shipped a defect twice -- once when a release port
# ran while a mutating gate had a file rewritten in place, and once when the safety hooks were fixed in a
# private install and only the references were published.
# ⚠ It checks the artifact against THIS CHECKOUT's expectations (the payload checker is read from here, not
# from the zip). Run it from the tag you are verifying. The root folder's expected name comes from the zip's own
# package.json; a zip of another product name (a 3.x skyrimvr-claude-toolkit-* zip checked from a 4.x checkout) is
# refused on a line that names both and says to verify it from a checkout of its own tag.
set -uo pipefail

usage() { echo "usage: bash tools/verify-release.sh vX.Y.Z | --local <zip> [--scrubbed] [--expect-commit SHA] [--tag vX.Y.Z]" >&2; exit 2; }

TAG="" LOCAL_ZIP="" SCRUBBED=0 EXPECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --local)         [ $# -ge 2 ] || usage; LOCAL_ZIP="$2"; shift 2 ;;
    --scrubbed)      SCRUBBED=1; shift ;;
    --expect-commit) [ $# -ge 2 ] || usage; EXPECT="$2"; shift 2 ;;
    --tag)           [ $# -ge 2 ] || usage; TAG="$2"; shift 2 ;;
    -*)              usage ;;
    *)               [ -z "$TAG" ] && [ -z "$LOCAL_ZIP" ] || usage; TAG="$1"; shift ;;
  esac
done
[ -n "$LOCAL_ZIP" ] || [ -n "$TAG" ] || usage
case "$TAG" in ''|v[0-9]*) ;; *) echo "verify-release: --tag must look like vX.Y.Z, got '$TAG'" >&2; exit 2 ;; esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # this checkout's tools/
REAL_HOME="${USERPROFILE:-${HOME:-}}"                  # before any scrub: the config folder must sit below it
CHECKOUT="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python3 python; do
    # Skip the Windows Store launchers (WindowsApps): one prints an advert instead of running, and the Store
    # Python reports a sys.executable under LOCALAPPDATA, which a scrubbed environment moves (MEASURED: setup.sh
    # then recorded an interpreter that did not exist and every hook fell back).
    p=$(command -v "$cand" 2>/dev/null) || continue
    case "$p" in *WindowsApps*) continue ;; esac
    if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
      PY="$p"; break
    fi
  done
fi
[ -n "$PY" ] || { echo "verify-release: no Python 3 found (set PYTHON)" >&2; exit 2; }
PY="$(command -v "$PY")"
CHECKOUT_SLUG=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["name"])' "$CHECKOUT/package.json") || exit 2

# The workspace goes under VERIFY_WORK_ROOT when given (tests point it at their tmp folder).
WORK="$(mktemp -d "${VERIFY_WORK_ROOT:-${TMPDIR:-/tmp}}/verify-release.XXXXXX")" || exit 2
# This script's own temporary workspace, removed by the script itself (the stated exception to the
# Recycle-Bin rule); kept when a check fails, for inspection.
trap 'rm -rf "$WORK"' EXIT
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
# The product name an ARTIFACT carries: its own package.json, validated as the builder validates it (a lowercase,
# dash-separated slug). Empty when the artifact has none.
slug_of() {
  "$PY" -c 'import json,re,sys
try:
    n = json.load(open(sys.argv[1], encoding="utf-8"))["name"]
except Exception:
    sys.exit(1)
if not isinstance(n, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", n) is None:
    sys.exit(1)
print(n)' "$(winpath "$1")" 2>/dev/null
}
REAL_DUMPS="${SKSE_CRASH_DIR:-}"
fails=0

note() { printf '  %-58s %s\n' "$1" "$2"; }
ok()   { note "$1" "ok"; }
bad()  { note "$1" "!! $2"; fails=$((fails+1)); }
# A dependency this machine lacks is SKIPPED OUT LOUD, never silently passed: a check that cannot run has
# proven nothing, and reporting it as ok is the lie this file exists to avoid telling.
skip() { note "$1" "skipped ($2)"; }

echo "=============================================================="
if [ -n "$LOCAL_ZIP" ]; then echo "VERIFY -- local artifact $(basename "$LOCAL_ZIP")"; else echo "VERIFY -- $TAG, from the published artifact"; fi
echo "=============================================================="

cd "$WORK" || exit 2
if [ -n "$LOCAL_ZIP" ]; then
  [ -f "$LOCAL_ZIP" ] || { bad "local zip" "no such file: $LOCAL_ZIP"; echo "RESULT: FAILED"; exit 1; }
  ZIP="$LOCAL_ZIP"
  ok "local zip $(basename "$ZIP")"
else
  if ! gh release download "$TAG" -R "${TOOLKIT_REPO:-WingedGuardian/skyrimvr-claude-toolkit}" -p '*.zip' >/dev/null 2>&1; then
    bad "download the release zip" "gh release download failed"
    echo "RESULT: FAILED"; exit 1
  fi
  ZIP=$(ls ./*.zip 2>/dev/null | head -1)
  [ -n "$ZIP" ] && ok "downloaded $(basename "$ZIP")" || { bad "locate zip" "none"; exit 1; }
fi

mkdir -p "$WORK/x"
if ! "$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$(winpath "$ZIP")" "$(winpath "$WORK/x")"; then
  bad "unzip" "failed"; echo "RESULT: FAILED"; exit 1
fi
tops=$(ls -A "$WORK/x")
if [ "$(printf '%s\n' "$tops" | grep -c .)" -ne 1 ]; then
  bad "the zip has one root folder" "found: $(printf '%s ' $tops)"; echo "RESULT: FAILED"; exit 1
fi
ROOT="$WORK/x/$tops"
# Review T13 M5: the expected root comes from the artifact being verified, not from this checkout -- a renamed
# product (v3.x shipped as skyrimvr-claude-toolkit-*) then fails on the line that says why, not as a bare
# "versioned root" mismatch. Every other check here is still THIS checkout's (header): an artifact of another
# product name is refused by name, with the remedy.
RELEASE_SLUG=$(slug_of "$ROOT/package.json")
if [ -z "$RELEASE_SLUG" ]; then
  bad "the artifact names its product" "no valid package.json name in $tops"
elif [ "$RELEASE_SLUG" != "$CHECKOUT_SLUG" ]; then
  bad "the artifact is this checkout's product" "the artifact is '$RELEASE_SLUG', this checkout '$CHECKOUT_SLUG': verify it from a checkout of its own release tag"
fi
case "$tops" in "$RELEASE_SLUG"-*) ok "extracted to a versioned root ($tops)" ;; *) bad "versioned root" "$tops" ;; esac
BUILT_COMMIT=$(sed -n 's/^commit=//p' "$ROOT/BUILD-INFO" 2>/dev/null | head -1)
BUILT_VERSION=$(sed -n 's/^version=//p' "$ROOT/BUILD-INFO" 2>/dev/null | head -1)
if [ -n "$TAG" ]; then
  # A tag names the version: the root folder and BUILD-INFO must both say it, and (published mode) BUILD-INFO
  # must name the tag's commit when this checkout has the tag.
  [ "$tops" = "$RELEASE_SLUG-${TAG#v}" ] && ok "root folder names $TAG" || bad "root folder names $TAG" "$tops"
  [ "$BUILT_VERSION" = "${TAG#v}" ] && ok "BUILD-INFO version is ${TAG#v}" || bad "BUILD-INFO version" "'$BUILT_VERSION', expected '${TAG#v}'"
  if [ -z "$EXPECT" ] && [ -z "$LOCAL_ZIP" ]; then
    EXPECT=$(git -C "$CHECKOUT" rev-parse --verify --quiet "refs/tags/$TAG^{commit}" 2>/dev/null || true)
    [ -n "$EXPECT" ] || skip "BUILD-INFO commit = the tag's" "this checkout has no tag $TAG"
  fi
else
  case "$BUILT_VERSION" in [0-9]*) TAG="v$BUILT_VERSION" ;; esac
fi
REQ=(); [ "${VERIFY_REQUIRE_LEAK_TOKENS:-0}" = 1 ] && REQ=(--require-tokens)

# --- payload shape, hooks, Codex half, adapter kit: ONE checker, the same one release.yml runs ---------------
# The checker imports the payload's hasher: do not write local bytecode into what we scan as shipped.
if "$PY" -B "$HERE/check-release-payload.py" "$(winpath "$ROOT")" ${EXPECT:+--expect-commit "$EXPECT"}; then
  ok "payload whole (check-release-payload.py)"
else
  bad "payload (check-release-payload.py)" "see the !! lines above"
fi

# The machine's real Skyrim install (Windows registry) joins RELEASE_LEAK_PATHS: setup in the scrubbed folder must
# use the fake install, never the real one, and no shipped file should name it.
# RELEASE_LEAK_REGISTRY=0 turns the lookup off (the tests do: they must not depend on this machine's install).
if [ "${RELEASE_LEAK_REGISTRY:-1}" != 0 ] && [ "${OS:-}" = "Windows_NT" ] && command -v reg >/dev/null 2>&1; then
  for key in 'HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim VR' 'HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition'; do
    real=$(MSYS_NO_PATHCONV=1 reg query "$key" /v "Installed Path" 2>/dev/null | sed -n 's/.*REG_SZ[[:space:]]*//p' | tr -d '\r' | sed 's#[\\/]*$##')
    # A stock Steam library path (.../steamapps/common/...) names nobody, and the docs quote the default one.
    case "$real" in ''|*steamapps[\\/]common*) continue ;; esac
    RELEASE_LEAK_PATHS="${RELEASE_LEAK_PATHS:+$RELEASE_LEAK_PATHS|}$real"
  done
fi
export RELEASE_LEAK_PATHS="${RELEASE_LEAK_PATHS:-}"

# --- leak grep on what ships ------------------------------------------------------------------------------
"$PY" "$HERE/check-release-payload.py" --leaks "$(winpath "$ROOT")" "${REQ[@]}"; rc=$?
case $rc in
  0) ok "no home path, user name or key in the payload" ;;
  1) bad "leak grep (payload)" "see the !! lines above" ;;
  *) bad "leak grep (payload)" "COULD NOT CHECK (see above): a grep that cannot see the maintainer is a fail" ;;
esac

# --- the tools must RUN from the artifact -----------------------------------------------------------------
if [ -n "$REAL_DUMPS" ] && [ -d "$REAL_DUMPS" ]; then
  out=$("$PY" "$ROOT/tools/crash-triage.py" "$REAL_DUMPS" 2>&1); rc=$?
  if [ $rc -eq 0 ] && grep -q "RESULT: OK" <<<"$out"; then
    ok "crash-triage on real dumps (exit 0, RESULT: OK)"
  else
    bad "crash-triage on real dumps" "exit $rc"; echo "$out" | tail -6
  fi
  # The annotation control: a clean run must NOT carry an arrow on the unparsed line.
  if grep -E '^\s+unparsed' <<<"$out" | grep -q '<--'; then
    bad "clean run leaves the unparsed line unannotated" "arrow present on a 0-unparsed run"
  else
    ok "clean run leaves the unparsed line unannotated"
  fi
else
  note "real crash dumps" "skipped (set SKSE_CRASH_DIR to exercise these)"
fi

# Guards must go red from the artifact, not just in the repo.
T="$WORK/degraded"; mkdir -p "$T"
for i in 1 2 3; do printf 'not a dump at all\n' > "$T/crash-junk-$i.log"; done
out=$("$PY" "$ROOT/tools/crash-triage.py" "$T" 2>&1); rc=$?
if [ $rc -eq 1 ] && grep -q "PARSER DEGRADED" <<<"$out"; then
  ok "crash-triage goes red on a total break (exit 1)"
else
  bad "crash-triage total-break guard" "exit $rc, no PARSER DEGRADED"
fi

# Near-miss guard from the artifact.
T2="$WORK/nearmiss"; mkdir -p "$T2"
printf 'x\n' > "$T2/crash-2026-01-01.md"
out=$("$PY" "$ROOT/tools/crash-triage.py" "$T2" 2>&1); rc=$?
if [ $rc -eq 2 ] && grep -q 'crash- prefix\|crash-/crash_ prefix' <<<"$out"; then
  ok "near-miss named rather than 'none found' (exit 2)"
else
  bad "near-miss message" "exit $rc"; echo "$out" | head -4
fi

# cosave exit contract from the artifact.
# ⚠ Pair the exit code with the MESSAGE. CPython exits 2 when it cannot open the script file either, so
# `[ $? -eq 2 ]` alone reported "refuses a non-cosave" for a payload with tools/cosave-info.py DELETED.
[ -f "$ROOT/tools/cosave-info.py" ] && ok "cosave-info.py shipped" || bad "cosave-info.py" "MISSING from the payload"
printf 'plainly not a co-save\n' > "$WORK/nope.txt"
out=$("$PY" "$ROOT/tools/cosave-info.py" "$WORK/nope.txt" 2>&1); rc=$?
if [ "$rc" -eq 2 ] && grep -qiE 'co-?save|magic|header' <<<"$out"; then
  ok "cosave-info refuses a non-cosave (exit 2, and says why)"
else
  bad "cosave-info exit contract" "expected exit 2 with a reason, got $rc"; echo "$out" | head -3
fi

# --help must render prose, not source. esp-verify-wrapper.sh dies on a missing spriggit BEFORE its --help
# case, so SKIP LOUDLY on a machine without it rather than blame a good artifact.
out=$(bash "$ROOT/tools/esp-verify-wrapper.sh" --help 2>&1)
if grep -qi 'spriggit not found' <<<"$out"; then
  skip "esp-verify-wrapper --help" "spriggit not installed here; the banner is unreachable"
elif grep -q 'WHY THIS EXISTS' <<<"$out" && ! grep -qE '^\s*(set -|#!/)' <<<"$out"; then
  ok "esp-verify-wrapper --help renders prose"
else
  bad "esp-verify-wrapper --help" "banner range leaked source"
fi

# --- the version the artifact claims --------------------------------------------------------------------
# Anchored, fixed-string, and end-of-heading: `grep -q "## $TAG"` matched `## v3.9.1` when the tag was v3.9.
if [ -n "$TAG" ]; then
  VER_RE=$(printf '%s' "${TAG#v}" | sed 's/[.]/[.]/g')
  if grep -qE "^#{1,3} +\[?v?${VER_RE}\]?([^0-9.]|$)" "$ROOT/CHANGELOG.md" 2>/dev/null; then
    ok "CHANGELOG carries a $TAG section"
  else
    bad "CHANGELOG section" "no '## $TAG' heading"
  fi
else
  skip "CHANGELOG section" "a dev build ($BUILT_VERSION) names no version; pass --tag"
fi

# --- scrubbed environment ----------------------------------------------------------------------------------
if [ "$SCRUBBED" -eq 1 ]; then
  echo "--- scrubbed environment ---"
  SCR="$WORK/scrubbed"; GAME="$SCR/game"; FAKEHOME="$SCR/home"; STMP="$SCR/tmp"
  mkdir -p "$FAKEHOME/AppData/Roaming" "$FAKEHOME/AppData/Local" "$STMP"
  cp -R "$ROOT" "$GAME" && : > "$GAME/SkyrimVR.exe"
  # PATH: the interpreters and coreutils, nothing else -- in particular not this checkout's folders.
  # bash's folder FIRST, and Python's REAL folder (sys.executable), never the folder of a launcher alias: the
  # Windows Store aliases live in WindowsApps beside WSL's bash.exe, which then shadows Git Bash (MEASURED here:
  # setup.sh died in WSL with "execvpe(/bin/bash) failed").
  PYDIR="$(dirname "$(cygpath -u "$("$PY" -c 'import sys; print(sys.executable)')" 2>/dev/null || "$PY" -c 'import sys; print(sys.executable)')")"
  MINPATH="$(dirname "$(command -v bash)"):$(dirname "$(command -v cat)"):/usr/bin:/bin:$PYDIR"
  for tool in git jq node; do
    p=$(command -v "$tool" 2>/dev/null) && MINPATH="$MINPATH:$(dirname "$p")"
  done
  SCRUB_ENV=(HOME="$FAKEHOME" USERPROFILE="$(winpath "$FAKEHOME")" APPDATA="$(winpath "$FAKEHOME/AppData/Roaming")"
             LOCALAPPDATA="$(winpath "$FAKEHOME/AppData/Local")" TMPDIR="$STMP" TEMP="$(winpath "$STMP")"
             TMP="$(winpath "$STMP")" LANG=C.UTF-8)
  if [ "${OS:-}" = "Windows_NT" ]; then
    SYSDIR="$(cygpath -u "${SYSTEMROOT:-C:/Windows}")/System32"
    MINPATH="$MINPATH:$SYSDIR:$SYSDIR/WindowsPowerShell/v1.0"
    SCRUB_ENV+=(SYSTEMROOT="${SYSTEMROOT:-C:\\Windows}" SYSTEMDRIVE="${SYSTEMDRIVE:-C:}" WINDIR="${WINDIR:-C:\\Windows}"
                COMSPEC="${COMSPEC:-C:\\Windows\\system32\\cmd.exe}" PATHEXT="${PATHEXT:-.COM;.EXE;.BAT;.CMD}")
  fi
  SCRUB_ENV+=(PATH="$MINPATH")
  scrub() { env -i "${SCRUB_ENV[@]}" "$@"; }

  # 1. the tripwire for the scrub itself. The positive control first: the canary must be visible OUT here,
  #    or "invisible in there" proves nothing.
  export PLAN8_LEAK_CANARY=1
  if env | grep -q '^PLAN8_LEAK_CANARY=1$'; then ok "leak canary set in the parent (control)"; else bad "leak canary control" "not visible in the parent"; fi
  inner=$(scrub bash -c 'env' 2>&1)
  if grep -q 'PLAN8_LEAK_CANARY' <<<"$inner"; then
    bad "leak canary invisible inside the scrubbed env" "PLAN8_LEAK_CANARY reached the child: the scrub is not a scrub"
  else
    ok "leak canary invisible inside the scrubbed env"
  fi
  leaked=$(grep -oE '^(X4_NEXUS_KEY|PYTHONPATH|CLAUDE_[A-Z_]*|CODEX_[A-Z_]*|SKYRIM_[A-Z_]*)=' <<<"$inner" | tr '\n' ' ')
  [ -z "$leaked" ] && ok "no author variables inside (X4_NEXUS_KEY, PYTHONPATH, CLAUDE_*, CODEX_*, SKYRIM_*)" \
                   || bad "author variables inside the scrubbed env" "$leaked"
  inner_path=$(sed -n 's/^PATH=//p' <<<"$inner")
  case ":$inner_path:" in
    *":$CHECKOUT:"*|*":$CHECKOUT/tools:"*|*":$CHECKOUT/scripts:"*) bad "repo checkout off the scrubbed PATH" "$CHECKOUT is on it" ;;
    *) ok "repo checkout off the scrubbed PATH" ;;
  esac

  # 2. setup.sh, twice (the second run must be a clean no-op): `--agent all` unless VERIFY_SETUP_ARGS says otherwise
  #    (set it to an empty string for a plain `bash setup.sh`).
  # shellcheck disable=SC2086
  out=$(cd "$GAME" && scrub bash setup.sh ${VERIFY_SETUP_ARGS---agent all} </dev/null 2>&1); rc=$?
  [ $rc -eq 0 ] && ok "setup.sh in the scrubbed env (exit 0)" || { bad "setup.sh in the scrubbed env" "exit $rc"; echo "$out" | tail -15; }
  # shellcheck disable=SC2086
  out=$(cd "$GAME" && scrub bash setup.sh ${VERIFY_SETUP_ARGS---agent all} </dev/null 2>&1); rc=$?
  [ $rc -eq 0 ] && ok "setup.sh again: idempotent (exit 0)" || { bad "setup.sh re-run" "exit $rc"; echo "$out" | tail -15; }
  # setup.sh renders .codex/hooks.json only for a drive-letter root (its CODEX_WIN test on `pwd -W`): the toolkit's
  # Codex support is Windows-only. Elsewhere (Linux, the CI runner) setup leaves it as shipped -- byte-identical,
  # placeholders and all -- and that, not a rendered file, is what is checked there.
  codex_defs="$GAME/.codex/hooks.json"
  case "$(cd "$GAME" && { pwd -W 2>/dev/null || pwd; } | tr '\134' '/')" in
    [A-Za-z]:/*) ;;
    *) if cmp -s "$GAME/.codex/hooks.json" "$ROOT/.codex/hooks.json"; then
         ok "Codex hooks left as shipped off Windows (Codex support is Windows-only)"
       else
         bad "Codex hooks off Windows" "setup changed .codex/hooks.json on a root that is not a drive letter"
       fi
       codex_defs="" ;;
  esac
  left=$(grep -lE '\{\{[A-Z_]+\}\}' "$GAME/CLAUDE.md" "$GAME/AGENTS.md" "$GAME"/.claude/hooks/*.sh "$GAME"/.codex/hooks/* ${codex_defs:+"$codex_defs"} 2>/dev/null | tr '\n' ' ')
  [ -z "$left" ] && ok "no unrendered placeholder left by setup" || bad "placeholders left by setup" "$left"
  "$PY" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$(winpath "$GAME/.claude/settings.json")" 2>/dev/null \
    && ok ".claude/settings.json parses after setup" || bad ".claude/settings.json after setup" "does not parse"

  # 3. instruction size on the INSTALLED files (setup fills placeholders, so this is the size an agent reads).
  for f in CLAUDE.md AGENTS.md; do
    n=$(wc -c < "$GAME/$f" 2>/dev/null || echo missing)
    case "$n" in missing) bad "$f size after setup" "missing" ;;
      *) [ "$n" -le 32768 ] && ok "$f after setup: $n bytes (<= 32768)" || bad "$f after setup" "$n bytes: over the 32768 an agent reads untruncated" ;;
    esac
  done

  # 4. the hook canary runs from the install (a fresh install has no heartbeats yet: it must REPORT, not crash).
  out=$(cd "$GAME" && scrub env CLAUDE_PROJECT_DIR="$(winpath "$GAME")" bash tools/hook-canary.sh 2>&1); rc=$?
  # Its contract: 0 healthy / 1 blind / 2 unmeasurable -- a fresh install, never exercised, is the last. A crash
  # (127, a Python traceback) prints no RESULT line.
  if [ $rc -le 2 ] && grep -q '^RESULT:' <<<"$out"; then ok "hook-canary reports from the install (exit $rc)"; else bad "hook-canary" "exit $rc, no RESULT line"; echo "$out" | tail -5; fi

  # 5. the shipped generated files match agent/ (the extracted payload, before setup fills its copies).
  out=$(cd "$ROOT" && scrub "$PY" -B scripts/gen_agent_trees.py --check 2>&1); rc=$?
  [ $rc -eq 0 ] && ok "gen_agent_trees --check on the payload" || { bad "gen_agent_trees --check" "exit $rc"; echo "$out" | tail -8; }

  # 6. the engine answers.
  out=$(cd "$GAME" && scrub "$PY" -B tools/skyguard/run.py --selftest verify-release 2>&1); rc=$?
  grep -q 'skyguard-selftest:verify-release' <<<"$out" && ok "skyguard engine selftest" || bad "skyguard engine selftest" "exit $rc: $out"

  # 7. a payload through every entry wrapper, the way the agent sends it.
  GW="$(winpath "$GAME")"
  verdict() {   # <hook path relative to GAME> <payload> [arg] -> prints the permissionDecision, or "none"
    local o
    o=$(cd "$GAME" && printf '%s' "$2" | scrub env CLAUDE_PROJECT_DIR="$GW" bash "$1" ${3:+"$3"} 2>/dev/null)
    "$PY" -c 'import json,sys
t=sys.stdin.read().strip()
try:
    print(json.loads(t).get("hookSpecificOutput", {}).get("permissionDecision") or "none")
except Exception:
    print("none" if not t else "unparsed")' <<<"$o"
  }
  rmgame=$("$PY" -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf \""+sys.argv[1]+"\""}}))' "$GW")
  esp=$("$PY" -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":sys.argv[1]+"/Data/x.esp","content":"x"}}))' "$GW")
  benign='{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo hello"}}'
  mcp='{"hook_event_name":"PreToolUse","tool_name":"mcp__devbench-vr__console","tool_input":{"command":"smp reset"}}'
  session='{"hook_event_name":"SessionStart","source":"startup"}'
  for spec in "protect-bash.sh|$rmgame|deny" "protect-bash.sh|$benign|none" "protect-files.sh|$esp|deny" \
              "protect-mcp.sh|$mcp|deny"; do
    h=${spec%%|*}; rest=${spec#*|}; want=${rest##*|}; p=${rest%|*}
    got=$(verdict ".claude/hooks/$h" "$p")
    [ "$got" = "$want" ] && ok "$h answers $want through the entry" || bad "$h through the entry" "expected $want, got $got"
  done
  for h in backup-before-edit.sh snapshot-before-tool.sh; do
    (cd "$GAME" && printf '%s' "$benign" | scrub env CLAUDE_PROJECT_DIR="$GW" bash ".claude/hooks/$h" >/dev/null 2>&1); rc=$?
    [ $rc -eq 0 ] && ok "$h exits 0 on an ordinary call" || bad "$h" "exit $rc on an ordinary call"
  done
  (cd "$GAME" && printf '%s' "$session" | scrub env CLAUDE_PROJECT_DIR="$GW" bash .claude/hooks/session-kb-guard.sh >/dev/null 2>&1); rc=$?
  [ $rc -eq 0 ] && ok "session-kb-guard.sh exits 0 at session start" || bad "session-kb-guard.sh" "exit $rc"
  got=$(verdict ".codex/hooks/skyguard-entry.sh" "$rmgame" pre-tool)
  [ "$got" = deny ] && ok "Codex skyguard-entry.sh denies deleting the install" || bad "Codex skyguard-entry.sh" "expected deny, got $got"

  # 8. leak grep on everything setup GENERATED or changed (files identical to the payload were grepped above).
  #    The install's own folder is expected in there (setup writes
  #    it), so the workspace is the one allowed prefix; any OTHER home path or the user name is a leak.
  #    Two more prefixes come from the MACHINE, not the payload, and are printed so a reviewer sees them: the
  #    Python install the hooks were configured with, and the config folder setup found through the Windows
  #    shell-folder API (which reads the profile, not USERPROFILE -- the one thing env -i cannot hide).
  pybase=$("$PY" -c 'import sys; print(sys.base_prefix)')
  envf="$GAME/skyrim-paths.env"                       # 4.0: the toolkit root; 3.x: .claude/
  [ -f "$envf" ] || envf="$GAME/.claude/skyrim-paths.env"
  cfg=$(sed -n "s/^SKYRIM_CONFIG_DIR=['\"]\{0,1\}\([^'\"]*\).*/\1/p" "$envf" 2>/dev/null | head -1)
  # A venv's actual executable directory can be outside sys.base_prefix; PYDIR was measured above.
  allow=(--allow-prefix "$(winpath "$WORK")" --allow-prefix "$pybase" --allow-prefix "$(winpath "$PYDIR")")
  # The config folder is NOT taken on trust from setup's own output: the checker allow-lists it only if it is
  # <home>/.../Documents/My Games/Skyrim*, strictly below the REAL home (the parent's, not the scrubbed one).
  # A second home, the same validated shape below it: where setup cannot ask Windows for Documents (no PowerShell --
  # Linux, the CI runner) it writes its fallback C:/Users/$(whoami)/Documents (setup.sh), which is not below a
  # /home/<login> REAL_HOME. Never an --allow-prefix: only a config folder of that exact shape is allowed.
  cfg_homes=(--home "$(winpath "${REAL_HOME}")")
  login=$(whoami 2>/dev/null) && [ -n "$login" ] && cfg_homes+=(--home "C:/Users/$login")
  [ -n "$cfg" ] && allow+=(--allow-config "$cfg" "${cfg_homes[@]}")
  # Named by role, never printed: on this machine both sit under the maintainer's home (review N1).
  note "allowed in generated files (machine discovery)" "the Python install${cfg:+, the config folder if shaped right}"
  "$PY" "$HERE/check-release-payload.py" --leaks "$(winpath "$GAME")" "${allow[@]}" --changed-from "$(winpath "$ROOT")" "${REQ[@]}"; rc=$?
  case $rc in
    0) ok "no home path or user name in the installed files" ;;
    1) bad "leak grep (installed files)" "see the !! lines above" ;;
    *) bad "leak grep (installed files)" "COULD NOT CHECK (see above)" ;;
  esac
  unset PLAN8_LEAK_CANARY
fi

echo "--------------------------------------------------------------"
if [ "$fails" -eq 0 ]; then
  echo "RESULT: OK -- the artifact behaves"
else
  echo "RESULT: $fails CHECK(S) FAILED"
  trap - EXIT
  echo "(workspace kept for inspection: $WORK)"
fi
exit $(( fails == 0 ? 0 : 1 ))
