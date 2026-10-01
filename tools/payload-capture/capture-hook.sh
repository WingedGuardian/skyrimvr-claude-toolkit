#!/usr/bin/env bash
# tools/payload-capture/capture-hook.sh -- FIXTURE CAPTURE ONLY, never a guard.
# Wire it as a hook in a SCRATCH project, launch the agent with PAYLOAD_CAPTURE_DIR set, and
# every payload the agent sends is saved verbatim. It never blocks: it prints nothing and
# exits 0. (On Codex a failing hook fails OPEN anyway; spec E17.)
dir="${PAYLOAD_CAPTURE_DIR:-}"
[ -n "$dir" ] || exit 0
mkdir -p "$dir" 2>/dev/null || exit 0
cat > "$dir/$(date +%Y%m%d_%H%M%S)_$$_${RANDOM}.json" 2>/dev/null
exit 0
