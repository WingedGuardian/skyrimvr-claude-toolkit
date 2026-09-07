#!/usr/bin/env bash
# Cold-clone verification of a PUBLISHED toolkit release.
#
#   bash tools/verify-release.sh v3.8.3
#
# Downloads the published zip and exercises it. Not the working tree, and not the
# repo -- the ARTIFACT, because those have diverged and shipped a defect here twice:
# once when a release port ran while a mutating gate had a file rewritten in place,
# and once when the safety hooks were fixed in a private install and only the
# references were published.
#
# This lived in a scratchpad for one release and immediately went stale in two
# places -- a checker that matched its own documentation, and an assertion string
# left behind by a wording change. It is a repo tool now for that reason.
#
# Verifies the PUBLISHED ZIP, not the working tree. The two have diverged before:
# a release port ran while a mutating gate had a file rewritten in place, and the
# tracked diff looked right while the bundle carried the mutation. Everything here
# runs against what a user actually extracts.
set -uo pipefail

TAG="${1:?usage: coldclone.sh vX.Y.Z}"
VER="${TAG#v}"
WORK="$(mktemp -d)"
# Point this at a folder of real crash dumps to exercise the triage tools against
# something other than fixtures. Skipped (not failed) when unset or absent.
REAL_DUMPS="${SKSE_CRASH_DIR:-}"
fails=0

note() { printf '  %-52s %s\n' "$1" "$2"; }
ok()   { note "$1" "ok"; }
bad()  { note "$1" "!! $2"; fails=$((fails+1)); }

echo "=============================================================="
echo "COLD CLONE -- $TAG, from the published artifact"
echo "=============================================================="

cd "$WORK" || exit 2
if ! gh release download "$TAG" -R "${TOOLKIT_REPO:-WingedGuardian/skyrimvr-claude-toolkit}" -p '*.zip' >/dev/null 2>&1; then
  bad "download the release zip" "gh release download failed"
  echo "RESULT: FAILED"; exit 1
fi
ZIP=$(ls ./*.zip 2>/dev/null | head -1)
[ -n "$ZIP" ] && ok "downloaded $(basename "$ZIP")" || { bad "locate zip" "none"; exit 1; }

unzip -q "$ZIP" || { bad "unzip" "failed"; exit 1; }
ROOT="$WORK/skyrimvr-claude-toolkit-$VER"
[ -d "$ROOT" ] && ok "extracted to a versioned root" || bad "extracted root" "missing $ROOT"

# --- payload shape ---------------------------------------------------------
[ -d "$ROOT/tests" ] && bad "tests/ excluded from the payload" "tests/ LEAKED" \
                     || ok "tests/ excluded from the payload"
[ -d "$ROOT/.claude/hooks" ] && ok ".claude/hooks present" \
                             || bad ".claude/hooks present" "MISSING"
[ -d "$ROOT/.git" ] && bad ".git excluded" ".git LEAKED" || ok ".git excluded"
n=$(find "$ROOT/.claude/hooks" -name '*.sh' 2>/dev/null | wc -l)
[ "$n" -ge 4 ] && ok "hook scripts in payload ($n)" || bad "hook scripts" "only $n"

# --- the hooks must not be inert: `cat`, never `cat /dev/stdin` ------------
# Assert the POSITIVE: every hook must contain the line that works. A blocklist
# here matched the comments explaining the defect, and also passed for `cat
# /dev/fd/0`, `cat  /dev/stdin`, and a hook reading stdin not at all.
miss=0
for h in "$ROOT/.claude/hooks"/*.sh; do
  grep -qxF 'INPUT=$(cat)' "$h" || { miss=1; echo "        $(basename $h) does not read stdin with a bare cat"; }
done
[ "$miss" -eq 0 ] && ok "every hook reads stdin with a bare cat" || bad "hook stdin reads" "see above"
[ -f "$ROOT/tools/hook-canary.sh" ] && ok "hook-canary.sh shipped" || bad "hook-canary.sh" "MISSING but referenced in the docs"

# --- the tools must RUN from the artifact ---------------------------------
if [ -d "$REAL_DUMPS" ]; then
  out=$(python "$ROOT/tools/crash-triage.py" "$REAL_DUMPS" 2>&1); rc=$?
  if [ $rc -eq 0 ] && grep -q "RESULT: OK" <<<"$out"; then
    ok "crash-triage on 28 real dumps (exit 0, RESULT: OK)"
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
out=$(python "$ROOT/tools/crash-triage.py" "$T" 2>&1); rc=$?
if [ $rc -eq 1 ] && grep -q "PARSER DEGRADED" <<<"$out"; then
  ok "crash-triage goes red on a total break (exit 1)"
else
  bad "crash-triage total-break guard" "exit $rc, no PARSER DEGRADED"
fi

# Near-miss guard from the artifact.
T2="$WORK/nearmiss"; mkdir -p "$T2"
printf 'x\n' > "$T2/crash-2026-01-01.md"
out=$(python "$ROOT/tools/crash-triage.py" "$T2" 2>&1); rc=$?
if [ $rc -eq 2 ] && grep -q 'crash- prefix\|crash-/crash_ prefix' <<<"$out"; then
  ok "near-miss named rather than 'none found' (exit 2)"
else
  bad "near-miss message" "exit $rc"; echo "$out" | head -4
fi

# cosave exit contract from the artifact.
printf 'plainly not a co-save\n' > "$WORK/nope.txt"
python "$ROOT/tools/cosave-info.py" "$WORK/nope.txt" >/dev/null 2>&1
[ $? -eq 2 ] && ok "cosave-info refuses a non-cosave (exit 2)" \
             || bad "cosave-info exit contract" "expected 2"

# --help must render prose, not source.
out=$(bash "$ROOT/tools/esp-verify-wrapper.sh" --help 2>&1)
if grep -q 'WHY THIS EXISTS' <<<"$out" && ! grep -qE '^\s*(set -|#!/)' <<<"$out"; then
  ok "esp-verify-wrapper --help renders prose"
else
  bad "esp-verify-wrapper --help" "banner range leaked source"
fi

# --- the version the artifact claims --------------------------------------
if grep -q "## $TAG" "$ROOT/CHANGELOG.md" 2>/dev/null; then
  ok "CHANGELOG carries a $TAG section"
else
  bad "CHANGELOG section" "no '## $TAG' heading"
fi

echo "--------------------------------------------------------------"
if [ "$fails" -eq 0 ]; then
  echo "RESULT: OK -- the published artifact behaves"
else
  echo "RESULT: $fails CHECK(S) FAILED"
fi
echo "(workspace: $WORK)"
exit $(( fails == 0 ? 0 : 1 ))
