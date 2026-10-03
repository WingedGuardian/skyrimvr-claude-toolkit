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
# this script is for: each hook records, per invocation, that it ran, that it received a
# payload, that the engine reached a verdict and that the wrapper delivered it -- all written from inside a REAL
# invocation. This reads them back, through tools/skyguard/health.py (the states and the
# recovery rule live there: a failure clears only on a NEWER COMPLETED evaluation, never on a
# later payload). The old 3.x heartbeat files are ignored.
#
# Exit: 0 = nothing degraded, nothing unmeasurable · 1 = at least one hook is DEGRADED ·
#       2 = cannot check (no Python, no hooks installed, unreadable or malformed telemetry)
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# The interpreter the hooks were configured with (setup.sh writes it as `PY="..."`), else whatever runs.
PYBIN=""
if [ -f "$ROOT/.claude/hooks/protect-bash.sh" ]; then
  while IFS= read -r line; do
    case "$line" in
      PY=\"*\") cand="${line#PY=\"}"; cand="${cand%\"}"
                case "$cand" in *"{{"*) ;; *) command -v "$cand" >/dev/null 2>&1 && PYBIN="$cand" ;; esac
                break ;;
    esac
  done < "$ROOT/.claude/hooks/protect-bash.sh"
fi
PYCMD=()
if [ -n "$PYBIN" ]; then
  PYCMD=("$PYBIN")
else
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      PYCMD=("$cand"); break
    fi
  done
  if [ "${#PYCMD[@]}" -eq 0 ] && command -v py >/dev/null 2>&1 && py -3 -c 'pass' >/dev/null 2>&1; then
    PYCMD=(py -3)
  fi
fi
if [ "${#PYCMD[@]}" -eq 0 ]; then
  echo "UNMEASURABLE: no Python 3 -- and without it every blocking hook is refusing every command (run setup.sh)"
  exit 2
fi

HEALTH="$ROOT/tools/skyguard/health.py"
[ -f "$HEALTH" ] || { echo "UNMEASURABLE: $HEALTH is missing -- cannot read the telemetry" >&2; exit 2; }
if command -v cygpath >/dev/null 2>&1; then HEALTH="$(cygpath -m "$HEALTH")"; ROOT="$(cygpath -m "$ROOT")"; fi

exec "${PYCMD[@]}" "$HEALTH" report --root "$ROOT" "$@"
