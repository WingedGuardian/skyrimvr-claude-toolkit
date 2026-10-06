#!/bin/bash
# setup-smoke.sh -- install the toolkit into a fake game folder with `setup.sh --agent <value>` and check what it
# WROTE, then that a second run changes nothing and that a bad value writes nothing. CI runs it once per value and
# OS (.github/workflows/ci.yml, job setup-smoke); tests/test_setup_smoke.py runs it locally.
#
#   bash scripts/setup-smoke.sh <claude|codex|all|both|auto> [work-dir]
#
# work-dir (default: a new folder under ${TMPDIR:-/tmp}) must not exist or be empty; nothing in it is deleted.
# Exit 0 = every check passed, 1 = a check failed (each failure is printed), 2 = usage.
set -u
AGENT="${1:-}"
case "$AGENT" in claude|codex|all|both|auto) ;; *) echo "usage: setup-smoke.sh <claude|codex|all|both|auto> [work-dir]" >&2; exit 2 ;; esac
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${2:-$(mktemp -d "${TMPDIR:-/tmp}/setup-smoke-XXXXXX")}"
GAME="$WORK/Skyrim VR"
mkdir -p "$GAME" || exit 2
GAME="$(cd "$GAME" && pwd)"          # tar reads `C:\...` as host:path; the shell's own spelling is safe
[ -z "$(ls -A "$GAME")" ] || { echo "setup-smoke: $GAME is not empty" >&2; exit 2; }
tar -C "$REPO" --exclude=./.git --exclude=./node_modules --exclude=./.pytest_cache -cf - . | tar -C "$GAME" -xf - || exit 2
: > "$GAME/SkyrimVR.exe"
FAILS=0
fail() { echo "::error::setup-smoke [$AGENT]: $*"; FAILS=$((FAILS + 1)); }
ok() { echo "  ok: $*"; }
# Everything setup may write, by content. The backups folder is excluded (records, not configuration).
snapshot() { (cd "$GAME" && find . -type f ! -path './.claude/backups/*' ! -name '*.pyc' -print0 | sort -z \
              | xargs -0 cksum) ; }
has_marks() { grep -qE '\{\{(GAME_ROOT|CONFIG_DIR|LOADORDER_DIR|MOD_MANAGER_PATHS)\}\}' "$GAME/$1"; }
case "$(cd "$GAME" && { pwd -W 2>/dev/null || pwd; })" in [A-Za-z]:/*) WIN=1 ;; *) WIN="" ;; esac

# 1. A bad value is a usage error and writes nothing.
before=$(snapshot)
(cd "$GAME" && bash setup.sh --agent nonsense </dev/null >/dev/null 2>&1); rc=$?
[ "$rc" = 2 ] && ok "--agent nonsense exits 2" || fail "--agent nonsense exited $rc, not 2"
[ "$before" = "$(snapshot)" ] && ok "--agent nonsense wrote nothing" || fail "--agent nonsense changed files"

# 2. The run itself.
out=$(cd "$GAME" && bash setup.sh --agent "$AGENT" </dev/null 2>&1); rc=$?
if [ "$AGENT" = codex ] && [ -z "$WIN" ]; then
    [ "$rc" = 3 ] && ok "--agent codex off Windows exits 3" || fail "--agent codex off Windows exited $rc, not 3"
    [ "$before" = "$(snapshot)" ] && ok "and wrote nothing" || fail "--agent codex off Windows changed files"
    echo "setup-smoke [$AGENT]: $FAILS failure(s)"
    [ "$FAILS" = 0 ]; exit $?
fi
[ "$rc" = 0 ] || { fail "setup.sh --agent $AGENT exited $rc"; echo "$out" | tail -25; }
sel=$(printf '%s\n' "$out" | sed -n 's/.*-> configuring: \([a-z]*\) (--agent auto).*/\1/p; s/^Agents: configuring \([a-z]*\) (--agent .*/\1/p' | head -n1)
case "$AGENT" in auto) ;; both) [ "$sel" = all ] || fail "--agent both configured '$sel', not all" ;;
    *) [ "$sel" = "$AGENT" ] || fail "--agent $AGENT configured '$sel'" ;; esac
case "$sel" in claude) WANT_C=1; WANT_X="" ;; codex) WANT_C=""; WANT_X=1 ;; all) WANT_C=1; WANT_X=1 ;;
    *) fail "no 'configuring:' line in the output"; WANT_C=""; WANT_X="" ;; esac
[ -n "$WIN" ] || WANT_X=""          # Codex is configured on Windows only
if has_marks CLAUDE.md; then [ -z "$WANT_C" ] && ok "CLAUDE.md left as extracted" || fail "CLAUDE.md not filled in"
else [ -n "$WANT_C" ] && ok "CLAUDE.md filled in" || fail "CLAUDE.md filled in for an unselected agent"; fi
if has_marks AGENTS.md; then [ -z "$WANT_X" ] && ok "AGENTS.md left as extracted" || fail "AGENTS.md not filled in"
else [ -n "$WANT_X" ] && ok "AGENTS.md filled in" || fail "AGENTS.md filled in for an unselected agent"; fi
if grep -q '{{CODEX_ROOT' "$GAME/.codex/hooks.json"; then
    [ -z "$WANT_X" ] && ok ".codex/hooks.json left unrendered" || fail ".codex/hooks.json not rendered"
    printf '%s\n' "$out" | grep -q "Codex:       NOT CONFIGURED" && ok "says Codex is not configured" \
        || fail "Codex is unconfigured and setup does not say so"
else
    [ -n "$WANT_X" ] && ok ".codex/hooks.json rendered" || fail ".codex/hooks.json rendered for an unselected agent"
fi
for h in "$GAME"/.claude/hooks/*.sh "$GAME"/.codex/hooks/skyguard-entry.*; do
    grep -q '{{PYTHON_PATH}}' "$h" && fail "${h#"$GAME"/}: interpreter placeholder left"
done
[ -s "$GAME/skyrim-paths.env" ] && grep -q "^SKYRIM_GAME_ROOT='" "$GAME/skyrim-paths.env" \
    && ok "skyrim-paths.env written at the toolkit root" || fail "no usable skyrim-paths.env at the toolkit root"
[ ! -e "$GAME/.skyguard/lock.json" ] && ok "no lock without --lock" || fail "the lock was applied without --lock"

# 3. Idempotent: a second run changes nothing.
first=$(snapshot)
(cd "$GAME" && bash setup.sh --agent "$AGENT" </dev/null >/dev/null 2>&1); rc=$?
[ "$rc" = 0 ] || fail "second run exited $rc"
[ "$first" = "$(snapshot)" ] && ok "second run changed nothing" || fail "second run changed files"

echo "setup-smoke [$AGENT]: $FAILS failure(s)"
[ "$FAILS" = 0 ]
