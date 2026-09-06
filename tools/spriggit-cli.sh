#!/bin/bash
# Wrapper for the Spriggit global tool (spriggit.cli 0.40.0).
#
# Spriggit uses the --OutputPath as a scratch/move target; a deeply-nested path
# (e.g. the claude scratchpad) trips a Windows long-path/permission failure
# (System.UnauthorizedAccessException). This wrapper runs the whole operation in a
# SHALLOW temp workspace (C:/Temp/spriggit-work/<rand>) and copies the result back
# to the caller's requested --OutputPath, so any path works.
#
# Usage (same args as spriggit, paths can be anywhere):
#   bash tools/spriggit-cli.sh serialize   --InputPath "Data/Mod.esp" --OutputPath "<anydir>" \
#        --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion 0.40.0
#   bash tools/spriggit-cli.sh deserialize --InputPath "<anydir>"     --OutputPath "Data/Mod.esp"

set -euo pipefail

WORK="C:/Temp/spriggit-work/$RANDOM$RANDOM$$"
# Separate in/ and out/ subdirs so we can keep the EXACT basename of each path.
# (The basename is the Spriggit ModKey — prefixing it corrupts every FormKey's
#  master reference, so it must be preserved verbatim.)
mkdir -p "$WORK/in" "$WORK/out"
cleanup() { rm -rf "$WORK" 2>/dev/null || true; }
trap cleanup EXIT

# --- parse args, redirecting InputPath/OutputPath through the shallow workspace ---
REAL_IN=""; REAL_OUT=""; NEW_IN=""; NEW_OUT=""
ARGS=()
i=0; argv=("$@")
while [ $i -lt ${#argv[@]} ]; do
  a="${argv[$i]}"
  case "$a" in
    --InputPath)
      REAL_IN="${argv[$((i+1))]}"
      NEW_IN="$WORK/in/$(basename "$REAL_IN")"
      if [ -d "$REAL_IN" ]; then cp -r "$REAL_IN" "$NEW_IN"; else cp "$REAL_IN" "$NEW_IN"; fi
      ARGS+=("--InputPath" "$NEW_IN"); i=$((i+2)); continue ;;
    --OutputPath)
      REAL_OUT="${argv[$((i+1))]}"
      NEW_OUT="$WORK/out/$(basename "$REAL_OUT")"
      ARGS+=("--OutputPath" "$NEW_OUT"); i=$((i+2)); continue ;;
    *)
      ARGS+=("$a"); i=$((i+1)) ;;
  esac
done

echo "[spriggit-cli] workspace: $WORK" >&2
spriggit "${ARGS[@]}"

# --- copy result back to the caller's requested output path ---
if [ -n "$REAL_OUT" ] && [ -e "$NEW_OUT" ]; then
  if [ -d "$NEW_OUT" ]; then
    mkdir -p "$REAL_OUT"
    cp -r "$NEW_OUT/." "$REAL_OUT/"
  else
    mkdir -p "$(dirname "$REAL_OUT")"
    cp "$NEW_OUT" "$REAL_OUT"
  fi
  echo "[spriggit-cli] result -> $REAL_OUT" >&2
elif [ -n "$REAL_OUT" ]; then
  # spriggit exited 0 but wrote nothing to the workspace. Without this branch the
  # unmatched `if` returns 0, so a silent no-op was reported as success -- the caller
  # then copies back nothing and believes the operation completed.
  echo "[spriggit-cli] ERROR: spriggit exited 0 but produced no output at $NEW_OUT" >&2
  echo "[spriggit-cli] Nothing was copied to $REAL_OUT. Treating this as a failure." >&2
  exit 1
fi
