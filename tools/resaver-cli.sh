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
#   --- the install itself ---
#   verify                        check ReSaver.jar and lib/ against the pinned SHA-256s below (no JDK needed)
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

# The pinned ReSaver 6.0.643 files (Nexus mod 5031, `Resaver 6.0.643-5031-6-0-643-1694115572.7z`): ReSaver.jar and
# every file of that archive's target/lib/, which goes here as lib/. The same table is in docs/installing-tools.md
# *Pinned downloads* (tests/test_resaver_cli_verify.py holds the two equal). `required` = the files this driver was
# tested with; the others (the GUI's JavaFX, picocli, the build's JUnit) may be left out, but if present they must
# be the pinned bytes, because lib/* is on the classpath. `bash tools/resaver-cli.sh verify` checks them.
PINNED="af6f632d561de9d2f8b97f860d5f1c997a79fb77d821d31a3e0111fabb22c1d3 required ReSaver.jar
245abad9a39eab1266ac9a8796980f462577e708ef3f6d43be2e008e4b72b9b4 required lib/annotations-16.0.2.jar
de40093737108d75f5bf832cce596308e8f90c36d36383b7aec40dff224d1e64 required lib/j2html-1.5.0.jar
757bfe906193b8b651e79dc26cd67d6b55d0770a2cdfb0381591504f779d4a76 required lib/juniversalchardet-1.0.3.jar
452da15ae42910d6475a0b9d73a404cbfd37d8ebe29558ad54439fe4b0d4e089 required lib/lz4-pure-java-1.7.0.jar
b509448ac506d607319f182537f0b35d71007582ec741832a1f111e5b5b70b38 optional lib/apiguardian-api-1.1.2.jar
901e382f96b53c91a949f91b07201ec5433bf3c864186cb50446a6b0951aa778 optional lib/javafx-base-11-win.jar
df7ef2e994be4a86153be0aaf0dcafa697ba4be0db55caefb8bda85e02d66ead optional lib/javafx-base-11.jar
af625a0b813f051bcb5bd1f00a35af61648264d7b792aabcff032f57dfbfa9a4 optional lib/javafx-controls-11-win.jar
dec3f34ca06ca9be71ce8c72646a31eb751b1df5a2b354dcd10fb64df842da4f optional lib/javafx-controls-11.jar
5a278411a777a1215b1b6c0c6c2192c94857fe0253a6de805c67a9c9d29d29d1 optional lib/javafx-graphics-11-win.jar
6b846071b1fdae103f1b0e80c988d9d0b9cae37391b00f8ea3dc11e1e8e021b0 optional lib/javafx-graphics-11.jar
585ec5d61cbbe53df15e2713527b79364ac281de4fbc11d862a04c52f8ecc578 optional lib/javafx-swing-11-win.jar
ba599dc31f2f8f098346198ce0125c2aee5358f89e13e7f59cdb290ab6807f59 optional lib/javafx-swing-11.jar
d9df47b3c7431883ab1f1f1d820c2980ebcbd753edc4c82b99bf5ec2c533ad2e optional lib/junit-jupiter-5.8.2.module
1808ee87e0f718cd6e25f3b75afc17956ac8a3edc48c7e9bab9f19f9a79e3801 optional lib/junit-jupiter-api-5.8.2.jar
753b7726cdd158bb34cedb94c161e2291896f47832a1e9eda53d970020a8184e optional lib/junit-jupiter-engine-5.8.2.jar
d1c22d6fe5483568c08c8913f34abd2303490c3480ce6c18a2ea31c65e44102a optional lib/junit-jupiter-params-5.8.2.jar
d2e015fca7130e79af2f4608dc54415e4b10b592d77333decb4b1a274c185050 optional lib/junit-platform-commons-1.8.2.jar
0b7d000f8c3e8e5f7d6b819649936e7b9938314e87c8f983805218ea57567e59 optional lib/junit-platform-engine-1.8.2.jar
58812de60898d976fb81ef3b62da05c6604c18fd4a249f5044282479fc286af2 optional lib/opentest4j-1.2.0.jar
b0a5159e926de8084ff066025142270443533656bc599b8bb31d14d11fd138a4 optional lib/picocli-4.6.3.jar"

_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum -- "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 -- "$1" | awk '{print $1}'
  else return 2; fi
}

_json_str() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# `verify`: needs no JDK. Exit 0 = every required file present and every pinned file present is the pinned bytes;
# 1 = a problem (named); 2 = no SHA-256 tool, so nothing was checked.
if [ "${1:-}" = "verify" ]; then
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    echo '{"ok":false,"checked":0,"error":"no sha256sum or shasum on PATH: nothing was checked"}'
    exit 2
  fi
  problems=(); checked=0
  while read -r want kind rel; do
    [ -n "$rel" ] || continue
    f="$TOOL/$rel"
    if [ ! -f "$f" ]; then
      [ "$kind" = required ] && problems+=("$rel is missing")
      continue
    fi
    got="$(_sha256 "$f")"; checked=$((checked + 1))
    [ "$got" = "$want" ] || problems+=("$rel is not the pinned file (SHA-256 $got)")
  done <<< "$PINNED"
  if [ -d "$TOOL/lib" ]; then
    for f in "$TOOL"/lib/*; do
      [ -f "$f" ] || continue
      rel="lib/${f##*/}"
      case "$(printf '%s' "$rel" | tr 'A-Z' 'a-z')" in *.jar) ;; *) continue ;; esac   # only jars are on the classpath
      awk -v r="$rel" '$3 == r { f = 1 } END { exit !f }' <<< "$PINNED" || problems+=("$rel is not part of ReSaver 6.0.643 (lib/* is on the classpath): remove it")
    done
  fi
  if [ "${#problems[@]}" -eq 0 ]; then
    printf '{"ok":true,"checked":%d}\n' "$checked"
    exit 0
  fi
  out=""
  for p in "${problems[@]}"; do out="$out${out:+,}\"$(_json_str "$p")\""; done
  printf '{"ok":false,"checked":%d,"problems":[%s]}\n' "$checked" "$out"
  exit 1
fi

if [ ! -f "$TOOL/ReSaver.jar" ]; then
  echo '{"ok":false,"error":"ReSaver.jar not found in tools/resaver-cli/ — get ReSaver 6.0.643 (FallrimTools, Nexus mod 5031), drop ReSaver.jar + its lib/ (the archive'"'"'s target/lib/) here, then run: bash tools/resaver-cli.sh verify"}'
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
