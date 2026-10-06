#!/bin/bash
# Wrapper for ResaverCLI — headless driver for ReSaver's (FallrimTools) save library.
# Parses/queries/edits Skyrim SE/VR .ess saves and emits JSON on stdout.
#
# Usage: bash tools/resaver-cli.sh <op> <save.ess> [args...]
#   info       <save>
#   dump       <save> <subsystem> [--limit N] [--undefined-only] [--script <name>] [--type <T>]
#                                 (scriptinstances|activescripts|references|structinstances|scripts|globals|changeforms)
#   find-refs  <save> <eidHex>    who references this element (labels direct vs secondary)
#   find       <save> <query>     query = <Plugin.esp:formid> | <formidHex> | <script-name substring>
#   worries    <save>             ReSaver's Worrier problem report (fatal/performance/potential)
#   recon      <save>             READ: sync-aware parse-coverage scan of ALL changeform body types —
#                                 perType ok/fail, failure categories, unknown extra-data types WITH
#                                 predecessor histograms (the phantom test). Uses the read-only overlay.
#   set-global <save> <target> <value> [<out.ess>] [--apply]      target = formidHex | Plugin.esp:formid
#   set-var    <save> <eidHex> [<index> <value> <out.ess>] [--type int|float|bool|str] [--apply]
#   clean      <save> <out.ess> [--undefined] [--unattached] [--terminate-threads] [--apply]
#                                 dry-run unless --apply; writes to a NEW file, auto-backs-up + handles .skse
#   changeform <save> <refidHex> [--raw] [--depth N]   parse ONE changeform body; --raw adds changeflags+hex/base64
#   --- ReSaver-DELEGATED write ops (dry-run unless --apply; VERIFY-GATED: re-read==model or delete+fail) ---
#   reset-havok       <save> [<out.ess>] [--apply]     ESS.resetHavok — clear REFR havok-move data
#   cleanse-formlists <save> [<out.ess>] [--apply]     ESS.cleanseFormLists — strip null FLST entries
#   remove-created    <save> [<out.ess>] [--apply]     ESS.removeNonexistentCreated — drop orphaned created-form instances
#   verify-roundtrip  <save>                           safety self-test: write+re-read+compare, report identical (temp discarded)
#   --- READ diagnostics ---
#   extradata-scan    <save> [--limit N] [--type ACHR|REFR]   tally unknown extra-data types (REFR=trustworthy)
#   changeform-diff   <saveA> <saveB> [refidHex|--quests]     structured+raw-tail diff; noise-labeled; refid candidates
#   freeze-report     <save>                                  active-script / suspended-stack survey (freeze-diagnosis aid)
#   globaldata        <save>                                  survey the save's global-data tables
#   globaldata-diff   <saveA> <saveB>                         cross-save global-data table diff
#
# Notes: writes NEVER overwrite the input save. Always review dry-run output before --apply.
#   Every --apply is VERIFY-GATED: the output is re-read and compared to the written model; on ANY
#   unintended divergence the output is DELETED and the op fails (fail-to-write beats silent corruption).
# Names: ResaverCLI emits Plugin:FORMID; resolve to EditorIDs on demand via tools/resaver-resolve-names.js.
#
# Requires ReSaver 6.0.643 (FallrimTools, Nexus mod 5031; ReSaver.jar's SHA-256 is in docs/installing-tools.md
# *Pinned downloads*) with its lib/ in tools/resaver-cli/, and JDK 17+ (JDK 21 LTS recommended). The wrapper
# compiles its small driver on first run, and again whenever the JDK's major version changes.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"
[ -z "$HERE" ] && HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL="$HERE/resaver-cli"
# Java's classpath separator is the PLATFORM's: ';' on Windows (Git Bash passes Windows paths, which contain ':'),
# ':' everywhere else -- with ';' on Linux java reads the whole string as one path and finds no class (review P1).
# Test seam: RESAVER_CLI_UNAME stands in for `uname -s`, honoured only under TOOLKIT_TEST_MODE=1.
UNAME="$(uname -s 2>/dev/null)"
[ "${TOOLKIT_TEST_MODE:-}" = 1 ] && [ -n "${RESAVER_CLI_UNAME:-}" ] && UNAME="$RESAVER_CLI_UNAME"
case "$UNAME" in
  MINGW*|MSYS*|CYGWIN*|Windows*) S=';' ;;
  *) S=':' ;;
esac
CP="$TOOL/ReSaver.jar$S$TOOL/lib/*"
OVERLAY="$TOOL/analysis-overlay"   # read-only analysis overlay (modified ReSaver source; see analysis-overlay/NOTICE.md)

if [ ! -f "$TOOL/ReSaver.jar" ]; then
  echo '{"ok":false,"error":"ReSaver.jar not found in tools/resaver-cli/ — get ReSaver 6.0.643 (FallrimTools, Nexus mod 5031), check ReSaver.jar against the SHA-256 in docs/installing-tools.md Pinned downloads, and drop ReSaver.jar + its lib/ here."}'
  exit 1
fi

# JDK-version-conditional JVM flags — older JDKs REJECT these options (they only silence harmless
# lz4/Unsafe warnings on newer ones). --enable-native-access is JDK 17+; --sun-misc-unsafe-memory-access
# is JDK 23+. Hardcoding them makes the tool fail to start on JDK 17-22, so gate by version.
JV=$(java -version 2>&1 | awk -F'"' '/version/{print $2}' | awk -F'[._]' '{if($1=="1")print $2; else print $1}')
FLAGS=""
[ "${JV:-0}" -ge 17 ] 2>/dev/null && FLAGS="$FLAGS --enable-native-access=ALL-UNNAMED"
[ "${JV:-0}" -ge 23 ] 2>/dev/null && FLAGS="$FLAGS --sun-misc-unsafe-memory-access=allow"

# Compile the driver once, when the source is newer than the class, or when the JDK's major version differs from
# the one that compiled it (a class built by JDK 24 does not load on the pinned JDK 21: UnsupportedClassVersionError).
# The stamp also stands for the overlay's classes, which the same JDK compiled.
STAMP="$TOOL/.compiled-by-jdk"
JDK_CHANGED=0
[ "$(cat "$STAMP" 2>/dev/null)" = "${JV:-?}" ] || JDK_CHANGED=1
if [ ! -f "$TOOL/ResaverCLI.class" ] || [ "$TOOL/ResaverCLI.java" -nt "$TOOL/ResaverCLI.class" ]; then
  javac -cp "$CP" -d "$TOOL" "$TOOL/ResaverCLI.java" >&2 || { echo '{"ok":false,"error":"compile failed"}'; exit 1; }
  printf '%s\n' "${JV:-?}" > "$STAMP" 2>/dev/null || true
elif [ "$JDK_CHANGED" = 1 ]; then
  # The class exists and may still load; a failed rebuild (no javac on PATH, say) warns instead of stopping.
  if javac -cp "$CP" -d "$TOOL" "$TOOL/ResaverCLI.java" >&2; then
    printf '%s\n' "${JV:-?}" > "$STAMP" 2>/dev/null || true
  else
    echo "[resaver-cli] WARN: could not recompile the driver for JDK ${JV:-?}; trying the existing class." >&2
  fi
fi

# Op-aware classpath (corruption safety). The analysis overlay carries modified-ReSaver parser fixes
# (ChangeFormExtraDataData cases 16/135 + RECENT, ChangeFormQust A1, ChangeFormRefr extra-data). It is
# prepended ONLY for read/diagnostic ops. Every WRITE-capable op (and any unknown op) runs the STOCK jar
# so ChangeForm.write() emits rawData verbatim — an authored fix can never reach updateRawData/writeESS.
# Default = stock (safe).
OP="${1:-}"
case "$OP" in
  info|dump|changeform|find-refs|find|worries|extradata-scan|recon|changeform-diff|freeze-report|globaldata|globaldata-diff)
    EDD_SRC="$OVERLAY/resaver/ess/ChangeFormExtraDataData.java"
    QUST_SRC="$OVERLAY/resaver/ess/ChangeFormQust.java"
    REFR_SRC="$OVERLAY/resaver/ess/ChangeFormRefr.java"
    EDD_CLS="$OVERLAY/resaver/ess/ChangeFormExtraDataData.class"
    QUST_CLS="$OVERLAY/resaver/ess/ChangeFormQust.class"
    REFR_CLS="$OVERLAY/resaver/ess/ChangeFormRefr.class"
    OVERLAY_OK=1
    if [ -f "$EDD_SRC" ]; then
      if [ ! -f "$EDD_CLS" ] || [ ! -f "$QUST_CLS" ] || [ ! -f "$REFR_CLS" ] \
         || [ "$EDD_SRC" -nt "$EDD_CLS" ] || [ "$QUST_SRC" -nt "$QUST_CLS" ] || [ "$REFR_SRC" -nt "$REFR_CLS" ] \
         || [ "$JDK_CHANGED" = 1 ]; then
        if ! javac -cp "$CP" -d "$OVERLAY" "$EDD_SRC" "$QUST_SRC" "$REFR_SRC" >&2; then
          # Almost always a ReSaver.jar version mismatch. Degrade gracefully to stock parsing rather
          # than hard-failing basic reads — enhanced changeform coverage is disabled, everything else works.
          echo "[resaver-cli] WARN: analysis-overlay failed to compile against your ReSaver.jar (likely a version mismatch) — falling back to STOCK parsing for this read op. See tools/resaver-cli/analysis-overlay/NOTICE.md." >&2
          OVERLAY_OK=0
        fi
      fi
    else
      OVERLAY_OK=0   # overlay not installed (older layout) — stock is fine
    fi
    if [ "$OVERLAY_OK" = "1" ]; then RUNCP="$OVERLAY$S$TOOL$S$CP"; else RUNCP="$TOOL$S$CP"; fi
    ;;
  *)
    RUNCP="$TOOL$S$CP"           # stock only — overlay structurally excluded from write ops
    ;;
esac

[ "${RESAVER_CLI_DEBUG:-}" = "1" ] && echo "[resaver-cli] op=$OP jdk=$JV overlay=${OVERLAY_OK:-n/a} classpath=$RUNCP" >&2

exec java $FLAGS -cp "$RUNCP" ResaverCLI "$@"
