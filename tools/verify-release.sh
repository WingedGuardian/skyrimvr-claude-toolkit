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
# ⚠ It checks the PUBLISHED ARTIFACT against THIS CHECKOUT'S expectations -- the hook
# floor and the gate list are read from the working tree, not from the zip (.github is
# excluded from the payload, so the artifact cannot state its own contract). Run it from
# the tag you are verifying. Pointed at an older release from a newer checkout, gates
# added since that release will correctly, and uninterestingly, go red.
set -uo pipefail

TAG="${1:?usage: bash tools/verify-release.sh vX.Y.Z}"
VER="${TAG#v}"
WORK="$(mktemp -d)"
# Nothing removed these: 56 stale workspaces, ~1.2 MB each, had accumulated on the
# author's machine because every exit path left one behind.
trap 'rm -rf "$WORK"' EXIT
# Point this at a folder of real crash dumps to exercise the triage tools against
# something other than fixtures. Skipped (not failed) when unset or absent.
REAL_DUMPS="${SKSE_CRASH_DIR:-}"
fails=0

note() { printf '  %-52s %s\n' "$1" "$2"; }
ok()   { note "$1" "ok"; }
bad()  { note "$1" "!! $2"; fails=$((fails+1)); }
# A dependency this machine lacks is SKIPPED OUT LOUD, never silently passed: a check
# that cannot run has proven nothing, and reporting it as ok is the lie this file exists
# to avoid telling.
skip() { note "$1" "skipped ($2)"; }

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
# The FLOOR is derived from the workflow that built this zip, not retyped here. A
# hardcoded `-ge 4` was already one release stale at the moment it shipped: the arc
# that promoted this file into the repo also added a fifth hook and bumped release.yml
# to `-ge 5`, and nothing said so. Same defect as the E2E harness that asserted the
# literal string "4 hook(s) proven".
floor=$(sed -n 's/.*test "\$nhooks" -ge \([0-9][0-9]*\).*/\1/p' \
        "$ROOT/../.github/workflows/release.yml" 2>/dev/null | head -1)
case "$floor" in ''|*[!0-9]*) floor=5 ;; esac
n=$(find "$ROOT/.claude/hooks" -name '*.sh' 2>/dev/null | wc -l)
[ "$n" -ge "$floor" ] && ok "hook scripts in payload ($n, floor $floor)" \
                      || bad "hook scripts" "only $n, floor is $floor"

# --- the hooks must not be inert: `cat`, never `cat /dev/stdin` ------------
# Assert the POSITIVE: every hook must contain the line that works. A blocklist
# here matched the comments explaining the defect, and also passed for `cat
# /dev/fd/0`, `cat  /dev/stdin`, and a hook reading stdin not at all.
# `find`, not a glob. The count above is recursive and this loop was not, so a hook
# in a subdirectory was COUNTED and never INSPECTED -- the inert-stdin defect this
# check exists to catch, walking back in through a door the check does not look at.
# release.yml carries a comment saying exactly this; the lesson did not make the copy.
miss=0
while IFS= read -r h; do
  grep -qxF 'INPUT=$(cat)' "$h" || { miss=1; echo "        $(basename "$h") does not read stdin with a bare cat"; }
  grep -qE '^[^#]*hook-heartbeat' "$h" || { miss=1; echo "        $(basename "$h") writes no heartbeat outside a comment"; }
done < <(find "$ROOT/.claude/hooks" -name '*.sh' 2>/dev/null)
[ "$miss" -eq 0 ] && ok "every hook reads stdin bare AND writes a heartbeat" || bad "hook stdin/heartbeat" "see above"
[ -f "$ROOT/tools/hook-canary.sh" ] && ok "hook-canary.sh shipped" || bad "hook-canary.sh" "MISSING but referenced in the docs"
[ -f "$ROOT/tools/kb-guard.sh" ] && ok "kb-guard.sh shipped" || bad "kb-guard.sh" "MISSING; session-kb-guard.sh would print NOT RUN every session"
[ -f "$ROOT/tools/skyguard/run.py" ] && ok "skyguard engine shipped" || bad "tools/skyguard/run.py" "MISSING; every hook would deny with 'engine failed'"
[ -f "$ROOT/setup.sh" ] && ok "setup.sh shipped" || bad "setup.sh" "MISSING but every doc tells the user to run it"
# The user's own knowledgebase must not be IN the bundle, and ours must be.
[ -e "$ROOT/KNOWLEDGEBASE.local.md" ] && bad "KNOWLEDGEBASE.local.md excluded" "PRESENT -- this zip would overwrite the user's notes" \
                                      || ok "KNOWLEDGEBASE.local.md excluded"
[ -f "$ROOT/KNOWLEDGEBASE.md" ] && ok "the toolkit's KNOWLEDGEBASE.md shipped" || bad "KNOWLEDGEBASE.md" "MISSING"

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
# ⚠ Pair the exit code with the MESSAGE. CPython exits 2 when it cannot open the
# script file either, so `[ $? -eq 2 ]` alone reported "refuses a non-cosave" for a
# payload with tools/cosave-info.py DELETED -- green-lighting the very
# shipped-the-references-without-the-tool defect this script was promoted to catch.
[ -f "$ROOT/tools/cosave-info.py" ] && ok "cosave-info.py shipped" || bad "cosave-info.py" "MISSING from the payload"
printf 'plainly not a co-save\n' > "$WORK/nope.txt"
out=$(python "$ROOT/tools/cosave-info.py" "$WORK/nope.txt" 2>&1); rc=$?
if [ "$rc" -eq 2 ] && grep -qiE 'co-?save|magic|header' <<<"$out"; then
  ok "cosave-info refuses a non-cosave (exit 2, and says why)"
else
  bad "cosave-info exit contract" "expected exit 2 with a reason, got $rc"; echo "$out" | head -3
fi

# --help must render prose, not source.
# esp-verify-wrapper.sh dies on a missing spriggit BEFORE it reaches its --help case,
# so on any machine without spriggit this went red on a perfectly good artifact and
# blamed "banner range leaked source". A check that fails on correct payloads is the
# one that gets worked around. SKIP LOUDLY instead -- the same answer this file already
# gives for SKSE_CRASH_DIR.
out=$(bash "$ROOT/tools/esp-verify-wrapper.sh" --help 2>&1)
if grep -qi 'spriggit not found' <<<"$out"; then
  skip "esp-verify-wrapper --help" "spriggit not installed here; the banner is unreachable"
elif grep -q 'WHY THIS EXISTS' <<<"$out" && ! grep -qE '^\s*(set -|#!/)' <<<"$out"; then
  ok "esp-verify-wrapper --help renders prose"
else
  bad "esp-verify-wrapper --help" "banner range leaked source"
fi

# --- the version the artifact claims --------------------------------------
# Anchored, fixed-string, and end-of-heading: `grep -q "## $TAG"` matched `## v3.9.1`
# when the tag was v3.9, and `$TAG` was treated as a REGEX so `.` was a wildcard.
VER_RE=$(printf '%s' "${TAG#v}" | sed 's/[.]/[.]/g')
if grep -qE "^#{1,3} +\[?v?${VER_RE}\]?([^0-9.]|$)" "$ROOT/CHANGELOG.md" 2>/dev/null; then
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
if [ "$fails" -gt 0 ]; then
  trap - EXIT
  echo "(workspace kept for inspection: $WORK)"
fi
exit $(( fails == 0 ? 0 : 1 ))
