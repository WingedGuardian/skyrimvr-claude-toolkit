#!/bin/bash
# skyguard-entry.sh -- the fail-closed POSIX entry for the toolkit's Codex hooks (Linux/macOS).
#
#   bash "<root>/.codex/hooks/skyguard-entry.sh" <role>       (.codex/hooks.json `command`; payload on stdin)
#
# <role> is pre-tool or session-start. Windows uses skyguard-entry.ps1: under cmd.exe `bash` can resolve
# to the WSL stub, which fails and lets the command run (MEASURED). POSIX support is BEST EFFORT.
#
# Codex blocks a tool call on exactly ONE thing, a parsed JSON permissionDecision "deny" with exit 0
# (MEASURED, codex-cli 0.160.0); ask, exit 1/2, a crash, bad JSON, a timeout all let it RUN. So every path
# out of this file is the engine's own validated answer or a deny (pre-tool) / a NOT LIVE line
# (session-start), and it always exits 0 -- an EXIT trap emits the deny if nothing else did.
#
# Hot path = bash builtins, as in the Claude wrappers (a fork costs 10-70 ms on Git Bash); the remaining
# forks are `$(cat)` and the engine. Never logs the environment (hooks inherit secrets).
# SETUP: setup.sh fills in the two paths below.
# Test-only knobs: SKYGUARD_WRAPPER_TIMEOUT_S (engine deadline; default 40 pre-tool, 95 session-start,
# 20+ s below the definition timeouts 60/120), SKYGUARD_NO_PYTHON_FALLBACK=1 (configured interpreter only),
# SKYGUARD_HOOK_TIMEOUT_S (a SHORTER definition timeout for the outer deadline; see PY-CHAIN below).

ROLE="${1:-}"
PY='{{PYTHON_PATH}}'
BASH_EXE='{{BASH_PATH}}'
HOOK_NAME="$ROLE"
# the FROZEN definition timeouts in .codex/hooks.json (tests/test_hook_deadline.py checks them)
HOOK_TIMEOUT_S=60; [ "$ROLE" = "session-start" ] && HOOK_TIMEOUT_S=120
EMITTED=""
INV=""
HB_DIR=""
T0="${EPOCHREALTIME:-}"; T0="${T0/,/.}"; [ -n "$T0" ] || printf -v T0 '%(%s)T' -1

_emit() {
    [ -z "$EMITTED" ] || return 0
    EMITTED=1
    [ -z "$1" ] || printf '%s\n' "$1"
}
_stamp() { T1="${EPOCHREALTIME:-$T0}"; T1="${T1/,/.}"; }
_fail() { [ -n "$INV" ] && [ -n "$HB_DIR" ] || return 0; _stamp; printf '%s %s %s %s\n' "$T1" "$INV" "$1" "$2" > "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null; }
# deny <stage> <cause>: the failure answer, then exit 0. The cause goes into a JSON string literal, so it
# is reduced to an allowlist (spelled out: a range like A-Z can match other letters in some locales).
deny() {
    local safe="${2//[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._:\/()=,+-]/}"
    safe="${safe:0:300}"
    _fail "$1" "$safe"
    if [ "$ROLE" = "session-start" ]; then
        _emit "SKYGUARD: NOT LIVE -- the Codex session hook could not get a result from the toolkit engine ($safe). The toolkit guards may not be running: tell the user, and ask them to run: bash tools/hook-canary.sh"
    else
        _emit '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"GUARD INERT: the Codex hook could not get a verdict from the toolkit engine ('"$safe"'). NOTHING was checked: this is a refusal, not a decision. Tell the user to run: bash tools/hook-canary.sh"}}'
    fi
    exit 0
}
_finally() {
    [ -n "$EMITTED" ] || deny wrapper "the wrapper ended without a verdict"
    exit 0
}
# UTF-8 validity in builtins (byte-wise in the C locale): Codex decodes stdout as UTF-8, and a body it
# cannot decode lets the command RUN. ASCII-only text (the usual case) returns at the first test.
_utf8_ok() {
    local LC_ALL=C
    local hi=$'[\x80-\xff]' t=$'[\x80-\xbf]' u
    [[ $1 =~ $hi ]] || return 0
    u="^([^\x80-\xff]|[\xc2-\xdf]$t|\xe0[\xa0-\xbf]$t|[\xe1-\xec\xee\xef]$t$t|\xed[\x80-\x9f]$t|\xf0[\x90-\xbf]$t$t|[\xf1-\xf3]$t$t$t|\xf4[\x80-\x8f]$t$t)*\$"
    printf -v u '%b' "$u"
    [[ $1 =~ $u ]]
}
trap _finally EXIT

INPUT=$(cat)

case "$ROLE" in pre-tool|session-start) ;; *) deny role "unknown role" ;; esac

# Where the install is: two levels above this file (.codex/hooks/), however it was launched.
_SELF="${BASH_SOURCE[0]//\\//}"
case "$_SELF" in */*) HOOK_DIR="${_SELF%/*}" ;; *) HOOK_DIR="." ;; esac
_CWD="$PWD"
ROOT=""
CDPATH= cd -- "$HOOK_DIR/../.." >/dev/null 2>&1 && ROOT="$PWD"
CDPATH= cd -- "$_CWD" >/dev/null 2>&1
[ -n "$ROOT" ] || deny wrapper "cannot locate the install from the hook path"
# The Windows spelling under Git Bash (a native python.exe cannot open /c/... or /tmp/... paths).
ROOT_WIN=""
case "$OSTYPE:$ROOT" in
    msys*:/[a-zA-Z]/*) ROOT_WIN="${ROOT:1:1}"; ROOT_WIN="${ROOT_WIN^}:${ROOT:2}" ;;
    msys*:/[a-zA-Z])   ROOT_WIN="${ROOT:1:1}"; ROOT_WIN="${ROOT_WIN^}:/" ;;
    msys*:/*)          ROOT_WIN="$(CDPATH= cd -- "$ROOT" 2>/dev/null && pwd -W 2>/dev/null)" ;;
esac
HB_DIR="$ROOT/.claude/backups/.hook-heartbeat/codex"
[ -d "$HB_DIR" ] || mkdir -p "$HB_DIR" 2>/dev/null

# Telemetry (tools/skyguard/health.py): invoked -> received -> (engine: evaluated) -> emitted; failed from
# any stage. The id is `<seq>.<pid>-<rand>`; a previous call that never emitted or failed and is older than
# any hook timeout was killed, and is recorded under THIS id.
OT=""; OINV=""; EINV=""; FINV=""
[ -r "$HB_DIR/$HOOK_NAME.invoked" ] && read -r OT OINV < "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
[ -r "$HB_DIR/$HOOK_NAME.emitted" ] && read -r _ EINV _ < "$HB_DIR/$HOOK_NAME.emitted" 2>/dev/null
[ -r "$HB_DIR/$HOOK_NAME.failed" ] && read -r _ FINV _ < "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null
OINV="${OINV%$'\r'}"; EINV="${EINV%$'\r'}"; FINV="${FINV%$'\r'}"
SEQ="${OINV%%.*}"; case "$SEQ" in ''|*[!0-9]*) SEQ=0 ;; esac
INV="$((SEQ + 1)).$$-$RANDOM"
GRACE=65; [ "$ROLE" = "session-start" ] && GRACE=125
O_S="${OT%%.*}"; T_S="${T0%%.*}"
case "$O_S$T_S" in
    ''|*[!0-9]*) ;;
    *) if [ -n "$OINV" ] && [ "$OINV" != "$EINV" ] && [ "$OINV" != "$FINV" ] && [ $((T_S - O_S)) -gt "$GRACE" ]; then
           _fail killed "run $OINV invoked at $OT never finished"
       fi ;;
esac
printf '%s %s\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
[ -n "$INPUT" ] || deny receipt "NO PAYLOAD on stdin"
printf '%s %s bytes=%s\n' "$T0" "$INV" "${#INPUT}" > "$HB_DIR/$HOOK_NAME.received" 2>/dev/null

RUNPY="${ROOT_WIN:-$ROOT}/tools/skyguard/run.py"
[ -f "$ROOT/tools/skyguard/run.py" ] || deny engine "engine missing (tools/skyguard/run.py)"
LIMIT=40; [ "$ROLE" = "session-start" ] && LIMIT=95
[ -z "${SKYGUARD_WRAPPER_TIMEOUT_S:-}" ] || LIMIT="$SKYGUARD_WRAPPER_TIMEOUT_S"
case "$LIMIT" in ''|*[!0-9]*) deny wrapper "SKYGUARD_WRAPPER_TIMEOUT_S is not a whole number" ;; esac
case "$BASH_EXE" in '{{'*) BASH_EXE="${BASH:-bash}" ;; esac

# PY-CHAIN-BEGIN
# Interpreter fallback chain and time limits (identical in every bash entry). Needs from the wrapper: T0,
# ROOT, ROOT_WIN, HB_DIR, INV, PY, INPUT, _fail, and HOOK_TIMEOUT_S = this hook's FROZEN definition timeout.
#
# Time limits. An agent DISCARDS an answer that arrives after its hook timeout and runs the command (Claude
# Code: MEASURED; Codex: a timeout is not a deny), so the whole call has an OUTER deadline DL = start +
# HOOK_TIMEOUT_S - 15 s, and every native child (probe, engine) gets a slice of it. A child is read through
# a pipe in 0.2 s `read -t` slices (a builtin: no sleep/timeout fork) and, at its limit, killed by its
# WINDOWS pid (`taskkill /F /T` on /proc/<pid>/winpid; elsewhere `kill -KILL`). The kill is started in the
# background; the wrapper waits for the child to go only while time is left before DL, so the answer is on
# time even while a child is still dying. coreutils `timeout` is not used: its signal reaches a native
# python.exe late or never once the CPU is oversubscribed (MEASURED 2026-10-05, 2x load: a 3 s bound took
# 18-227 s; taskkill by pid 5-6 s). The engine's own in-process watchdog (run.py, faulthandler,
# SKYGUARD_ENGINE_TIMEOUT, default 20 s, counted from run.py's start) normally ends a stuck engine first;
# the wrapper's kill comes 10 s after that budget, counted from the spawn, which leaves room for a slow
# interpreter start under load (a hang BEFORE the watchdog is armed is the case only the wrapper sees).
#
# The chain runs ONLY once the configured $PY has failed, so it adds no fork to a healthy call. Order:
# a remembered fallback (below), then py -3 / python / python3 that resolve and are not a Store alias,
# then the usual install locations (newest version first), then a Store alias, last. A candidate is
# accepted only if it runs the engine's own `run.py --selftest <nonce>` (ONE process: run.py itself
# refuses Python < 3.8, and an older one cannot even parse it) and echoes the nonce: a stub that merely
# exits 0 and prints `{}` would otherwise read as ALLOW. Each probe gets min(SKYGUARD_PROBE_TIMEOUT, the
# time left), sub-second allowed, and no probe starts in the last 10 s before DL (the engine needs them).
# Default 15 s: a HEALTHY selftest took up to 3.8 s native and ~9 s through a bash shim on 4 cores at 2x
# load (MEASURED 2026-10-05; 0.35 s idle), so the old 3 s rejected working interpreters exactly when the
# machine was busiest. A rejected candidate and a hanging one both end fail-closed (GUARD INERT); the
# slow-but-healthy one is the common case under load, so it gets the room.
# Multi-word candidates are word-split, never eval'd. A fallback that is taken writes a `failed` record
# BEFORE the engine runs, so the health report says DEGRADED until the configured interpreter works again.
#
# A verified fallback is remembered in $HB_DIR/.python-fallback (tab-separated: version tag, absolute
# path, optional -3), so a sustained outage probes once, not on every call. The file is never trusted:
# any read or format problem is "no cache"; the path must be absolute, named like a Python and executable;
# it is dropped when that file or run.py is newer than the cache; and EVERY fallback engine run must echo
# a fresh nonce (--nonce) ahead of its answer -- the identity check rides on the run itself, so writing
# that file cannot substitute an interpreter. A remembered fallback that fails is forgotten.
PYC=("$PY")
FB=""             # "" configured interpreter / 1 probed this call / cache remembered
WD=""             # the last child was ended by run.py's own watchdog (its report is on stdout)
NUL=""            # the last child's stdout held a NUL byte (dropped from OUT)
OUT=""; RC=0; NOCACHE=""
NONCE="$RANDOM$RANDOM$RANDOM"
RUNPY="${ROOT_WIN:-$ROOT}/tools/skyguard/run.py"
PYFB_CACHE="$HB_DIR/.python-fallback"
_to_ms() {
    local s="${1%%[.,]*}" f=""
    [ "$s" = "$1" ] || f="${1#*[.,]}"
    f="${f}000"; f="${f:0:3}"
    case "$s" in ''|*[!0-9]*) return 1 ;; esac
    case "$f" in *[!0-9]*) return 1 ;; esac
    MS=$(( 10#$s * 1000 + 10#$f ))
}
_now() { local n="${EPOCHREALTIME:-}"; [ -n "$n" ] || printf -v n '%(%s)T' -1; _to_ms "$n"; }
_secs_ms() { { _to_ms "$1" && [ "$MS" -gt 0 ]; } || _to_ms "$2"; }
_to_ms "$T0" || _now
T0_MS=$MS
H_S="$HOOK_TIMEOUT_S"
# test-only: a SHORTER deadline (never a longer one than the definition's)
case "${SKYGUARD_HOOK_TIMEOUT_S:-}" in ''|*[!0-9]*) ;; *) [ "$SKYGUARD_HOOK_TIMEOUT_S" -ge "$H_S" ] || H_S="$SKYGUARD_HOOK_TIMEOUT_S" ;; esac
DL=$(( T0_MS + (H_S - 15) * 1000 ))
PROBE_END=$(( DL - 10000 ))
_secs_ms "${SKYGUARD_PROBE_TIMEOUT:-}" 15; PROBE_MS=$MS
_secs_ms "${SKYGUARD_ENGINE_TIMEOUT:-}" 20; ENGINE_MS=$(( MS + 10000 ))
_kill_tree() {
    local w="" tk=taskkill
    if [ -r "/proc/$1/winpid" ] && read -r w < "/proc/$1/winpid" 2>/dev/null && [ -n "$w" ]; then
        hash taskkill 2>/dev/null || tk="${SYSTEMROOT//\\//}/System32/taskkill.exe"
        "$tk" //F //T //PID "$w" >/dev/null 2>&1 </dev/null &
    else
        kill -KILL "$1" 2>/dev/null
    fi
}
# _bounded <limit ms> <in|none> <command...>: run a native child with $INPUT (in) or nothing on stdin,
# never past DL. Sets OUT (stdout), RC (124 = the limit ended it), NUL, WD.
_bounded() {
    local end fd cpid part r eof="" in="$2"
    _now; end=$(( MS + $1 )); [ "$end" -le "$DL" ] || end=$DL
    shift 2
    OUT=""; RC=124; NUL=""; WD=""
    [ $(( end - MS )) -ge 200 ] || return 0
    # bash < 4.4 has no {fd} redirection / wait on a process substitution: an unbounded child is not an
    # option, so that is a failure (126) -- and `exec {fd}<` there would REPLACE this shell, never run it
    (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )) || { RC=126; return 0; }
    if [ "$in" = in ] && [ -n "$INPUT" ]; then
        exec {fd}< <(exec "$@" 2>/dev/null <<<"$INPUT") || { RC=126; return 0; }
    else
        exec {fd}< <(exec "$@" 2>/dev/null </dev/null) || { RC=126; return 0; }
    fi
    cpid=$!
    while :; do
        if [ -z "$eof" ]; then
            part=""; IFS= read -r -d '' -t 0.2 -u "$fd" part; r=$?; OUT+="$part"
            if [ "$r" -eq 0 ]; then NUL=1; continue; fi
            [ "$r" -gt 128 ] || eof=1
        fi
        if ! kill -0 "$cpid" 2>/dev/null; then
            # it exited: take what it wrote first (a grandchild may still hold the pipe, so no read to EOF)
            while [ -z "$eof" ]; do
                part=""; IFS= read -r -d '' -t 0.05 -u "$fd" part; r=$?; OUT+="$part"
                if [ "$r" -eq 0 ]; then NUL=1; elif [ "$r" -le 128 ]; then eof=1; elif [ -z "$part" ]; then break; fi
                _now; [ "$MS" -lt "$end" ] || break
            done
            wait "$cpid"; RC=$?
            break
        fi
        _now
        if [ "$MS" -ge "$end" ]; then
            _kill_tree "$cpid"; RC=124
            # let the kill land while there is time (no stray process), but never wait past DL for it
            while kill -0 "$cpid" 2>/dev/null; do
                _now; [ "$MS" -lt "$DL" ] && [ "$MS" -lt $(( end + 10000 )) ] || break
                IFS= read -r -d '' -t 0.1 -u "$fd" part; [ $? -gt 128 ] || sleep 0.05 2>/dev/null
            done
            break
        fi
        [ -z "$eof" ] || sleep 0.05 2>/dev/null
    done
    exec {fd}<&-
    case "$OUT" in "Timeout ("*) [ "$RC" -eq 0 ] || WD=1 ;; esac
}
py_good() {
    local n o b
    _now; b=$(( PROBE_END - MS )); [ "$b" -le "$PROBE_MS" ] || b=$PROBE_MS
    [ "$b" -ge 200 ] || return 1
    n="$RANDOM$RANDOM"
    _bounded "$b" none "$@" "$RUNPY" --selftest "$n"
    o="${OUT%$'\n'}"
    [ "$RC" -eq 0 ] && [ "${o%$'\r'}" = "skyguard-selftest:$n" ]
}
_cache_put() {
    local p="$1" w
    case "$p" in */*) ;; *) hash "$p" 2>/dev/null && p="${BASH_CMDS[$p]:-}" ;; esac
    case "$p" in /*|[A-Za-z]:/*) ;; *) return 0 ;; esac
    shift
    { printf 'skyguard-pyfb-v1\t%s' "$p"; for w in "$@"; do printf '\t%s' "$w"; done; printf '\n'; } \
        > "$PYFB_CACHE" 2>/dev/null
    return 0
}
py_cached() {
    local c=() f="$PYFB_CACHE"
    [ -z "$NOCACHE" ] && [ -n "$HB_DIR" ] && [ -f "$f" ] && [ -r "$f" ] || return 1
    IFS=$'\t' read -r -a c < "$f" 2>/dev/null || return 1
    [ "${c[0]:-}" = "skyguard-pyfb-v1" ] && [ "${#c[@]}" -ge 2 ] && [ "${#c[@]}" -le 3 ] || return 1
    c=("${c[@]:1}")
    [ "${#c[@]}" -eq 1 ] || [ "${c[1]}" = "-3" ] || return 1
    case "${c[0]}" in /*|[A-Za-z]:/*) ;; *) return 1 ;; esac
    case "${c[0]##*/}" in python|python3|py|python.exe|python3.exe|py.exe) ;; *) return 1 ;; esac
    [ -x "${c[0]}" ] && [ ! "${c[0]}" -nt "$f" ] && [ ! "$ROOT/tools/skyguard/run.py" -nt "$f" ] || return 1
    PYC=("${c[@]}"); FB=cache
    _fail python "configured $PY unusable; used ${c[*]} (verified earlier) -- re-run setup.sh"
}
py_use() { PYC=("$@"); FB=1; _fail python "configured $PY unusable; used $* -- re-run setup.sh"; _cache_put "$@"; }
py_fallback() {
    local c p d m PY_ALIAS="" bases=()
    py_cached && return 0
    for c in "py -3" python python3; do
        hash "${c%% *}" 2>/dev/null || continue
        p="${BASH_CMDS[${c%% *}]:-}"
        case "$p" in
            */[Ww]indows[Aa]pps/*) [ -z "$PY_ALIAS" ] && PY_ALIAS="$c"; continue ;;
        esac
        if py_good $c; then py_use $c; return 0; fi
    done
    [ -z "${LOCALAPPDATA:-}" ] || bases+=("${LOCALAPPDATA//\\//}/Programs/Python")
    [ -z "${PROGRAMFILES:-}" ] || bases+=("${PROGRAMFILES//\\//}")
    bases+=(/c)
    for ((m = 30; m >= 8; m--)); do
        for d in "${bases[@]}"; do
            if [ -x "$d/Python3$m/python.exe" ] && py_good "$d/Python3$m/python.exe"; then
                py_use "$d/Python3$m/python.exe"; return 0
            fi
        done
    done
    for d in /usr/bin/python3 /usr/local/bin/python3; do
        if [ -x "$d" ] && py_good "$d"; then py_use "$d"; return 0; fi
    done
    if [ -n "$PY_ALIAS" ] && py_good $PY_ALIAS; then py_use $PY_ALIAS; return 0; fi
    return 1
}
# _engine <limit ms>: run.py with ENGINE_ARGS (the wrapper's) through PYC. A fallback must echo NONCE
# first; OUT is the answer without it, trailing newlines removed.
_engine() {
    if [ -n "$FB" ]; then
        _bounded "$1" in "${PYC[@]}" "$RUNPY" "${ENGINE_ARGS[@]}" --nonce "$NONCE"
        if [ "$RC" -eq 0 ]; then
            case "$OUT" in
                "skyguard-nonce:$NONCE"$'\n'*) OUT="${OUT#*$'\n'}" ;;
                *) RC=1; _fail python "fallback ${PYC[*]} did not echo the engine nonce -- not used" ;;
            esac
        fi
    else
        _bounded "$1" in "${PYC[@]}" "$RUNPY" "${ENGINE_ARGS[@]}"
    fi
    while [ "${OUT%$'\n'}" != "$OUT" ]; do OUT="${OUT%$'\n'}"; done
}
# After a failed engine run, is the INTERPRETER to blame? Not when run.py's own watchdog ended it (that
# proves the interpreter runs the engine), not at the limit (no time left), and a fallback probed in this
# call is not probed again. A remembered fallback that failed is forgotten and the chain probes afresh.
_retry() {
    [ "$RC" -ne 0 ] && [ "$RC" -ne 124 ] && [ -z "$WD" ] && [ "$FB" != 1 ] || return 1
    if [ "$FB" = cache ]; then
        NOCACHE=1; : > "$PYFB_CACHE" 2>/dev/null; FB=""
    elif py_good "${PYC[@]}"; then
        return 1
    fi
    py_fallback
}
# PY-CHAIN-END

# The engine's limit: LIMIT s from the start of the call on EVERY path (Codex runs the command when a
# hook outlives its timeout), the fallback's shorter budget on a fallback, never past the outer deadline.
# run.py's own watchdog (SKYGUARD_ENGINE_TIMEOUT, default 20 s) normally ends a stuck engine first.
# The output is read by _bounded, which stops at the engine's EXIT, not at EOF: a grandchild that inherited
# the pipe cannot hold this file past Codex's timeout.
_engine_limit() {
    _now; ELIM=$(( T0_MS + LIMIT * 1000 - MS ))
    [ -z "$FB" ] || [ "$ENGINE_MS" -ge "$ELIM" ] || ELIM=$ENGINE_MS
    [ "$ELIM" -ge 1000 ] || ELIM=1000
}
ENGINE_ARGS=(codex "$ROLE" --root "$ROOT" --root-win "$ROOT_WIN" --project "${ROOT_WIN:-$ROOT}" --bash "$BASH_EXE"
             --invocation "$INV" --telemetry "${ROOT_WIN:-$ROOT}/.claude/backups/.hook-heartbeat/codex")

if ! command -v "$PY" >/dev/null 2>&1 || [ "${PY:0:2}" = "{{" ]; then
    [ "${SKYGUARD_NO_PYTHON_FALLBACK:-}" != 1 ] && py_fallback || deny python "cannot run Python -- install Python 3 and run setup.sh"
fi
_engine_limit; _engine "$ELIM"
if [ "${SKYGUARD_NO_PYTHON_FALLBACK:-}" != 1 ] && _retry; then
    _engine_limit; _engine "$ELIM"
fi
case "$RC" in
    0) ;;
    124) deny engine "engine did not answer within $LIMIT s" ;;
    *) deny engine "engine exited $RC" ;;
esac

# A NUL byte: no engine answer contains one, and everything after it would otherwise vanish.
[ -z "$NUL" ] || deny emission "engine output has a NUL byte"
OUT="${OUT//$'\r'/}"
while [ "${OUT%$'\n'}" != "$OUT" ]; do OUT="${OUT%$'\n'}"; done
_utf8_ok "$OUT" || deny emission "engine output is not valid UTF-8"
case "$OUT" in
    SKYGUARD-OK) BODY="" ;;
    SKYGUARD-OK$'\n'*) BODY="${OUT#SKYGUARD-OK$'\n'}" ;;
    *) deny emission "engine output lacks the SKYGUARD-OK line" ;;
esac

if [ "$ROLE" = "session-start" ]; then
    case "$BODY" in "SKYGUARD: "*) ;; *) deny emission "engine session text lacks the SKYGUARD banner" ;; esac
elif [ -n "$BODY" ]; then
    case "$BODY" in *$'\n'*) deny emission "engine printed more than one body line" ;; esac
    # The same strict grammar as the PowerShell entry: one object, one key hookSpecificOutput, an inner
    # object of string pairs; known keys only, once each; permissionDecision only "deny". Codex parses
    # strictly and a body it cannot parse lets the command RUN.
    # Control characters as an explicit byte range: [:cntrl:] in the C locale (a hook's usual one) also
    # covers the bytes 0x80-0x9F, i.e. the middle of ordinary UTF-8 text such as an em dash.
    S=$'"([^"\\\\\x01-\x1f]|\\\\["\\\\/bfnrt]|\\\\u[0-9a-fA-F]{4})*"'
    W='[ 	]*'
    P="$S$W:$W$S"
    RE="^[{]$W\"hookSpecificOutput\"$W:$W[{]$W($P($W,$W$P)*)$W[}]$W[}]\$"
    PAIR="^($S)$W:$W($S)$W,?$W(.*)\$"
    [[ $BODY =~ $RE ]] || deny emission "engine output is not a verdict JSON"
    REST="${BASH_REMATCH[1]}"
    # An escape starts at an ODD run of backslashes: `\\ud83d` is an escaped backslash and the text "ud83d".
    # (After REST: a match here would overwrite BASH_REMATCH.)
    SUR='(^|[^\\])(\\\\)*\\u[dD][89abAB]'
    [[ $BODY =~ $SUR ]] && deny emission "engine output has a surrogate escape"
    SEEN=" "
    while [ -n "$REST" ]; do
        [[ $REST =~ $PAIR ]] || deny emission "engine output is not a verdict JSON"
        K="${BASH_REMATCH[1]}"; V="${BASH_REMATCH[3]}"; REST="${BASH_REMATCH[5]}"
        case "$K" in
            '"hookEventName"'|'"permissionDecision"'|'"permissionDecisionReason"'|'"additionalContext"') ;;
            *) deny emission "engine output has an unknown key" ;;
        esac
        case "$SEEN" in *" $K "*) deny emission "engine output repeats a key" ;; esac
        SEEN="$SEEN$K "
        [ "$K" != '"permissionDecision"' ] || [ "$V" = '"deny"' ] || deny emission "engine answered a permissionDecision other than deny"
        [ "$K" != '"hookEventName"' ] || [ "$V" = '"PreToolUse"' ] || deny emission "engine output names another event"
    done
    case "$SEEN" in *' "hookEventName" '*) ;; *) deny emission "engine output names no event" ;; esac
fi

_emit "$BODY"
_stamp
printf '%s %s ok\n' "$T1" "$INV" > "$HB_DIR/$HOOK_NAME.emitted" 2>/dev/null
printf '%s %s %s\n' "$INV" "$T0" "$T1" >> "$HB_DIR/$HOOK_NAME.latency" 2>/dev/null
exit 0
