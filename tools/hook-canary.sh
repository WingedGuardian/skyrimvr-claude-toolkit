#!/usr/bin/env bash
# Are the safety hooks actually alive -- firing AND receiving their payload?
#
# WHY THIS EXISTS. Every hook in this toolkit once read stdin with
# `cat /dev/stdin` and was INERT: it fired on every tool call, read nothing, fell
# through its first guard clause and exited 0 -- byte-identical to deciding "this is
# fine". Nothing was protected, and nothing looked wrong.
#
# The mechanism, MEASURED here 2026-09-06. `/dev/stdin` is a symlink to
# `/proc/self/fd/0`, so it resolves when fd 0 is a real file or a pipe created by an
# MSYS shell -- but it fails with "No such file or directory" when fd 0 is a **Win32
# anonymous pipe from a non-MSYS parent**, which is exactly how Claude Code (a Node
# process) spawns a hook. Hence: fine by hand, dead in production.
#
#   stdin source                          /dev/stdin
#   ------------------------------------  -----------------
#   pipe from an MSYS shell               resolves (31 bytes)
#   a real file handle                    resolves (31 bytes)
#   Win32 pipe from Node / subprocess     FAILS  (0 bytes)
#
# `tests/test_hooks.py` does catch this on Windows, because Python's subprocess
# hands bash a Win32 pipe too -- but it CANNOT catch it on Linux, where /dev/stdin
# resolves for any pipe. So the suite is a real detector on one platform and blind on
# the other, and neither tells you about the install in front of you. That is what
# this script is for: each hook records a heartbeat when it parses a payload and a
# `.blind` marker when it runs and sees nothing, both written from inside a REAL
# invocation. This reads them back.
#
# Exit: 0 = every exercised hook is alive · 1 = at least one is BLIND · 2 = cannot check
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HB="$ROOT/.claude/backups/.hook-heartbeat"
HOOKS="protect-files protect-bash backup-before-edit snapshot-before-tool"
STALE_MIN="${HOOK_CANARY_STALE_MIN:-120}"

[ -d "$ROOT/.claude/hooks" ] || { echo "no .claude/hooks under $ROOT -- cannot check" >&2; exit 2; }

echo "=============================================================="
echo "HOOK CANARY -- did each guard fire AND see its payload?"
echo "=============================================================="
mkdir -p "$HB" 2>/dev/null

now=$(date +%s)
blind_count=0
alive_count=0
idle_count=0

for h in $HOOKS; do
  # A blind marker is the loud case: the hook RAN and got nothing. That is the
  # original defect, and it must never read as "fine".
  if [ -f "$HB/$h.blind" ]; then
    printf '  %-22s BLIND        ran but received NO payload (%s)\n' "$h" "$(cut -d' ' -f1 < "$HB/$h.blind")"
    blind_count=$((blind_count + 1))
    continue
  fi
  if [ ! -f "$HB/$h" ]; then
    # NOT the same as broken, and not the same as fine: the Edit|Write hooks never
    # fire in a Bash-only session. Saying "not exercised" rather than "OK" is the
    # whole point -- a hook that has not run has proven nothing, and reporting that
    # as healthy is the lie this tool exists to avoid telling.
    printf '  %-22s NOT EXERCISED  no heartbeat yet -- proves nothing either way\n' "$h"
    idle_count=$((idle_count + 1))
    continue
  fi
  ts=$(cut -d' ' -f1 < "$HB/$h")
  bytes=$(sed 's/.*payload_bytes=//' "$HB/$h")
  age=$(( (now - $(date -d "$(echo "$ts" | sed 's/\(....\)\(..\)\(..\)_\(..\)\(..\)\(..\)/\1-\2-\3 \4:\5:\6/')" +%s 2>/dev/null || echo "$now")) / 60 ))
  if [ "$bytes" = "0" ]; then
    # A heartbeat recording zero bytes is a blind hook that wrote the wrong file.
    printf '  %-22s BLIND          heartbeat records 0 bytes\n' "$h"
    blind_count=$((blind_count + 1))
  elif [ "$age" -gt "$STALE_MIN" ]; then
    printf '  %-22s STALE          last payload %s min ago (%s bytes)\n' "$h" "$age" "$bytes"
    idle_count=$((idle_count + 1))
  else
    printf '  %-22s ALIVE          %s bytes, %s min ago\n' "$h" "$bytes" "$age"
    alive_count=$((alive_count + 1))
  fi
done

echo "--------------------------------------------------------------"
echo "alive: $alive_count   blind: $blind_count   not-exercised/stale: $idle_count"
if [ "$blind_count" -gt 0 ]; then
  echo
  echo "RESULT: BLIND HOOK(S) -- a guard is running and seeing nothing, which is"
  echo "        indistinguishable from allowing. Check how the hook reads stdin:"
  echo "        \`INPUT=\$(cat /dev/stdin)\` returns zero bytes when Claude Code spawns"
  echo "        the hook; \`INPUT=\$(cat)\` works. Also check that jq is installed and"
  echo "        that setup.sh has filled in its path."
  exit 1
fi
if [ "$alive_count" -eq 0 ]; then
  echo
  echo "RESULT: UNPROVEN -- no hook has recorded a payload yet. Trigger one tool call"
  echo "        of each kind (a Bash command and a file edit) and re-run."
  exit 0
fi
echo
echo "RESULT: OK -- $alive_count hook(s) proven to receive their payload"
exit 0
